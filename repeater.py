#!/usr/bin/env python
# -*- coding: utf-8 -*-
# repeater.py
# Copyright (C) 2018-2021 github.com/googlehosts Group:Z
#
# This module is part of googlehosts/telegram-repeater and is released under
# the AGPL v3 License: https://www.gnu.org/licenses/agpl-3.0.txt
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
from __future__ import annotations
import asyncio
import gettext
import json
import logging
import re
import sys
import time
import traceback
from configparser import ConfigParser
from typing import Callable, Dict, Mapping, Optional, Tuple, TypeVar, Union

import aioredis
import coloredlogs
import pyrogram
import pyrogram.errors
from pyrogram import Client, ContinuePropagation, filters, raw
from pyrogram.handlers import CallbackQueryHandler, MessageHandler
from pyrogram.types import (CallbackQuery, ChatPermissions,
                            InlineKeyboardButton, InlineKeyboardMarkup,
                            Message, User)

import utils
from customservice import CustomServiceBot, JoinGroupVerify
from utils import AuthSystem, PgSQLdb
from utils import TextParser as tp
from utils import get_language

config = ConfigParser()
config.read('config.ini')

logger = logging.getLogger('telegram-repeater').getChild('main')

translation = gettext.translation('repeater', 'translations/',
                                  languages=[get_language()], fallback=True)

_T = translation.gettext
_cT = TypeVar('_cT')


class TextParser(tp):
    bot_username = ''

    def __init__(self, msg: Message):
        self._msg = self.BuildMessage(msg)
        self.parsed_msg = self.parse_main()
        if msg.chat.id == config.getint('fuduji', 'fudu_group') and \
                self.parsed_msg and self.parsed_msg.startswith('\\//'):
            self.parsed_msg = self.parsed_msg[1:]
        if msg.chat.id == config.getint('fuduji', 'target_group') and self.parsed_msg:
            self.parsed_msg = self.parsed_msg.replace(
                f'@{TextParser.bot_username}', f"@{config['fuduji']['replace_to_id']}")


_problemT = TypeVar('_problemT', Dict, str, bool, int)


def external_load_problem_set() -> Dict[str, _problemT]:
    try:
        with open('problem_set.json', encoding='utf8') as fin:
            problem_set = json.load(fin)
        if len(problem_set['problems']['problem_set']) == 0:
            logger.warning('Problem set length is 0')
    except:
        logger.exception('Error in reading problem set!')
        problem_set = {}
    return problem_set


class WaitForDelete:
    def __init__(self, client: Client, chat_id: int, message_ids: Union[int, Tuple[int, ...]]):
        self.client: Client = client
        self.chat_id: int = chat_id
        self.message_ids: Union[int, Tuple[int, ...]] = message_ids

    async def run(self) -> None:
        await asyncio.sleep(5)
        await self.client.delete_messages(self.chat_id, self.message_ids)

    def __call__(self) -> None:
        asyncio.run_coroutine_threadsafe(self.run(), asyncio.get_event_loop())


class OperationTimeoutError(Exception):
    """Raise this exception if operation time out"""


class OperatorError(Exception):
    """Raise this exception if operator mismatch"""


class BotController:
    class ByPassVerify(UserWarning):
        pass

    def __init__(self):
        # self.problems_load()
        logger.debug('Loading bot configure')
        self.target_group: int = config.getint('fuduji', 'target_group')
        self.fudu_group: int = config.getint('fuduji', 'fudu_group')
        self.bot_id: int = int(config['account']['api_key'].split(':')[0])
        self.app: Client = Client(
            session_name='session',
            api_id=config['account']['api_id'],
            api_hash=config['account']['api_hash'],
            app_version='repeater'
        )
        self.botapp: Client = Client(
            session_name='beyondbot',
            api_id=config['account']['api_id'],
            api_hash=config['account']['api_hash'],
            bot_token=config['account']['api_key'],
        )
        logger.debug('Loading other configure')
        self.conn: Optional[PgSQLdb] = None
        self._redis: Optional[aioredis.Redis] = None
        self.auth_system: Optional[AuthSystem] = None
        self.warn_evidence_history_channel: int = config.getint('fuduji', 'warn_evidence', fallback=0)

        self.join_group_verify_enable: bool = config.getboolean('join_group_verify', 'enable', fallback=True)
        self.custom_service_enable: bool = config.getboolean('custom_service', 'enable', fallback=True)

        self.join_group_verify: Optional[JoinGroupVerify] = None
        self.revoke_tracker_coro: Optional[utils.InviteLinkTracker] = None
        self.custom_service: Optional[CustomServiceBot] = None
        self.problem_set: Optional[Mapping[str, _problemT]] = None
        self.init_handle()
        logger.debug('Service status: join group verify: %s, custom service: %s',
                     self.join_group_verify_enable, self.custom_service_enable)
        logger.debug('__init__ method completed')

    async def init_connections(self) -> None:
        self._redis = await aioredis.create_redis_pool('redis://localhost')
        self.conn = await PgSQLdb.create(
            config['pgsql']['host'],
            config.getint('pgsql', 'port'),
            config['pgsql']['user'],
            config['pgsql']['passwd'],
            config['pgsql']['database']
        )
        self.auth_system = await AuthSystem.initialize_instance(self.conn, config.getint('account', 'owner'))
        if self.join_group_verify_enable:
            self.join_group_verify = await JoinGroupVerify.create(self.conn, self.botapp, self.target_group,
                                                                  self.fudu_group, external_load_problem_set,
                                                                  self._redis)
            self.join_group_verify.init()
            self.revoke_tracker_coro = self.join_group_verify.revoke_tracker_coro
            if self.custom_service_enable:
                self.custom_service = CustomServiceBot(config, self.conn, self.join_group_verify.send_link, self._redis)

    @classmethod
    async def create(cls) -> BotController:
        self = BotController()
        await self.init_connections()
        return self

    def init_handle(self) -> None:
        self.app.add_handler(
            MessageHandler(self.handle_edit, filters.chat(self.target_group) & ~filters.user(
                self.bot_id) & filters.edited))
        self.app.add_handler(
            MessageHandler(self.handle_new_member, filters.chat(self.target_group) & filters.new_chat_members))
        self.app.add_handler(
            MessageHandler(self.handle_service_messages, filters.chat(self.target_group) & filters.service))
        self.app.add_handler(
            MessageHandler(
                self.handle_all_media,
                filters.chat(self.target_group) & ~filters.user(self.bot_id) & (
                    filters.photo | filters.video | filters.document | filters.animation | filters.voice)
            )
        )
        self.app.add_handler(MessageHandler(self.handle_dice, filters.chat(self.target_group) & ~filters.user(
            self.bot_id) & filters.media))
        self.app.add_handler(MessageHandler(self.handle_sticker, filters.chat(self.target_group) & ~filters.user(
            self.bot_id) & filters.sticker))
        self.app.add_handler(MessageHandler(self.handle_speak, filters.chat(self.target_group) & ~filters.user(
            self.bot_id) & filters.text))
        self.app.add_handler(MessageHandler(self.handle_incoming, filters.incoming & filters.chat(self.fudu_group)))
        self.botapp.add_handler(
            MessageHandler(self.handle_bot_send_media, filters.chat(self.fudu_group) & filters.command('SendMedia')))
        self.botapp.add_handler(CallbackQueryHandler(self.handle_callback))

    async def init(self) -> None:
        while not self.botapp.is_connected:
            await asyncio.sleep(.5)
        TextParser.bot_username = (await self.botapp.get_me()).username

    @staticmethod
    async def idle() -> None:
        await pyrogram.idle()

    async def start(self) -> None:
        await asyncio.gather(self.app.start(), self.botapp.start())
        if self.custom_service_enable:
            asyncio.run_coroutine_threadsafe(self.custom_service.start(), asyncio.get_event_loop())
        await self.init()

    async def stop(self) -> None:
        task_pending = []
        if self.join_group_verify_enable:
            self.revoke_tracker_coro.request_stop()
            await self.revoke_tracker_coro.join(1.5)
            if self.revoke_tracker_coro.is_alive:
                logger.warning('revoke_tracker_coroutine still running!')
            if self.custom_service_enable:
                task_pending.append(asyncio.create_task(self.custom_service.stop()))
        task_pending.append(asyncio.create_task(self.botapp.stop()))
        task_pending.append(asyncio.create_task(self.app.stop()))
        await asyncio.wait(task_pending)
        task_pending.clear()

        if self.join_group_verify_enable:
            await self.join_group_verify.problems.destroy()

        self._redis.close()
        await asyncio.gather(self.conn.close(), self._redis.wait_closed())

    async def handle_service_messages(self, _client: Client, msg: Message) -> None:
        if msg.pinned_message:
            text = self.get_file_type(msg.pinned_message)
            if text == 'text':
                text = msg.pinned_message.text[:20]
            else:
                text = f'a {text}'
            await self.conn.insert_ex(
                (await self.botapp.send_message(
                    self.fudu_group, f'Pined \'{text}\'',
                    disable_web_page_preview=True,
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text='UNPIN', callback_data='unpin')]
                    ]))
                ).message_id,
                msg.message_id
            )
        elif msg.new_chat_title:
            await self.conn.insert_ex(
                (await self.botapp.send_message(self.fudu_group,
                                                f'Set group title to <code>{msg.new_chat_title}</code>',
                                                'html', disable_web_page_preview=True)).message_id,
                msg.message_id
            )
        else:
            logger.info('Got unexpect service message: %s', repr(msg))

    async def generate_warn_message(self, user_id: int, reason: str) -> str:
        return _T('You were warned.(Total: {})\nReason: <pre>{}</pre>').format(
            await self.conn.query_warn_by_user(user_id), reason)

    async def process_incoming_command(self, client: Client, msg: Message) -> None:
        r = re.match(r'^/bot (on|off)$', msg.text)
        if r is None:
            r = re.match(r'^/b?(on|off)$', msg.text)
        if r:
            if not self.auth_system.check_ex(
                    msg.reply_to_message.from_user.id if msg.reply_to_message else msg.from_user.id): return
            await self.auth_system.mute_or_unmute(
                r.group(1),
                msg.reply_to_message.from_user.id if msg.reply_to_message else msg.from_user.id
            )
            await msg.delete()

        if msg.text == '/status':
            user_id = msg.reply_to_message.from_user.id if msg.reply_to_message else msg.from_user.id
            status = [str(user_id), ' summary:\n\n', 'A' if self.auth_system.check_ex(user_id) else 'Una',
                      'uthorized user\nBot status: ',
                      CustomServiceBot.return_bool_emoji(not self.auth_system.check_muted(user_id))]
            WaitForDelete(client, msg.chat.id,
                          (msg.message_id, (await msg.reply(''.join(status), True)).message_id))()
            del status

        elif msg.text.startswith('/promote'):
            if len(msg.text.split()) == 1:
                if msg.reply_to_message is None or not self.auth_system.check_ex(msg.reply_to_message.from_user.id):
                    await self.botapp.send_message(msg.chat.id, 'Please reply to an Authorized user.',
                                                   reply_to_message_id=msg.message_id)
                    return
                user_id = msg.reply_to_message.from_user.id
            else:
                user_id = int(msg.text.split()[1])
            await self.botapp.send_message(
                msg.chat.id,
                'Please use bottom to make sure you want to add {} to Administrators'.format(
                    TextParser.parse_user_markdown(user_id)),
                parse_mode='markdown',
                reply_to_message_id=msg.message_id,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text='Yes, confirm',
                            callback_data=f'promote {user_id}'
                        )
                    ],
                    [
                        InlineKeyboardButton(text='Cancel', callback_data='cancel d')
                    ]
                ]))
            return

        elif msg.text.startswith('/su'):
            if not self.auth_system.check_ex(msg.from_user.id):
                return
            await self.botapp.promote_chat_member(
                self.target_group,
                int(msg.from_user.id),
                True,
                can_delete_messages=True,
                can_pin_messages=True,
                can_promote_members=True
            )
            await self.botapp.send_message(
                msg.chat.id,
                'Access Granted',
                disable_notification=True,
                reply_to_message_id=msg.message_id
            )

        elif msg.text.startswith('/title'):
            if not self.auth_system.check_ex(msg.from_user.id):
                return
            await self.botapp.set_chat_title(
                self.target_group,
                msg.text.split(maxsplit=2)[1]
            )

        if msg.reply_to_message:
            if msg.text == '/del':
                message_id = await self.conn.get_reply_id_reverse(msg)
                if message_id is None:
                    await self.botapp.send_message(msg.chat.id, 'MESSAGE_ID_NOT_FOUND',
                                                   reply_to_message_id=msg.message_id)
                    return
                try:
                    await client.forward_messages(msg.chat.id, self.target_group, message_id)
                except:
                    await client.send_message(msg.chat.id, traceback.format_exc(), disable_web_page_preview=True)
                try:
                    await self.botapp.delete_messages(self.target_group, message_id)
                    await client.delete_messages(self.fudu_group, [msg.message_id, msg.reply_to_message.message_id])
                except:
                    pass

            elif msg.text == '/getid':
                user_id = await self.conn.get_user_id(msg)
                await msg.reply(
                    'user_id is `{}`'.format(
                        user_id['user_id'] if user_id is not None and user_id['user_id'] else 'ERROR_INVALID_USER_ID'
                    ),
                    parse_mode='markdown'
                )

            elif msg.text == '/get' and await self.conn.get_reply_id_reverse(msg):
                try:
                    await client.forward_messages(self.fudu_group, self.target_group,
                                                  await self.conn.get_reply_id_reverse(msg))
                except:
                    await client.send_message(msg.chat.id, traceback.format_exc().splitlines()[-1])

            elif msg.text == '/fw':
                message_id = await self.conn.get_reply_id_reverse(msg)
                if message_id is None:
                    await msg.reply('ERROR_INVALID_MESSAGE_ID')
                    return
                await self.conn.insert_ex(
                    (await self.botapp.forward_messages(self.target_group, self.target_group, message_id)).message_id,
                    msg.message_id)

            elif msg.text.startswith('/ban'):
                user_id = await self.conn.get_user_id(msg)
                if len(msg.text) == 4:
                    restrict_time = 0
                else:
                    r = re.match(r'^([1-9]\d*)([smhd])$', msg.text[5:])
                    if r is not None:
                        restrict_time = int(r.group(1)) * {'s': 1, 'm': 60, 'h': 60 * 60, 'd': 60 * 60 * 24}.get(
                            r.group(2))
                    else:
                        await self.botapp.send_message(msg.chat.id, 'Usage: `/ban` or `/ban <Duration>`',
                                                       'markdown', reply_to_message_id=msg.message_id)
                        return
                if user_id is not None and user_id['user_id']:
                    if user_id['user_id'] not in self.auth_system.whitelist:
                        await self.botapp.send_message(
                            msg.chat.id,
                            'What can {} only do? Press the button below.\n'
                            'This confirmation message will expire after 20 seconds.'.format(
                                TextParser.parse_user_markdown(user_id['user_id'])
                            ),
                            reply_to_message_id=msg.message_id,
                            parse_mode='markdown',
                            reply_markup=InlineKeyboardMarkup(
                                inline_keyboard=[
                                    [
                                        InlineKeyboardButton(
                                            text='READ',
                                            callback_data=f"res {restrict_time} read {user_id['user_id']}")
                                    ],
                                    [
                                        InlineKeyboardButton(
                                            text='SEND_MESSAGES',
                                            callback_data=f"res {restrict_time} write {user_id['user_id']}"),
                                        InlineKeyboardButton(
                                            text='SEND_MEDIA',
                                            callback_data=f"res {restrict_time} media {user_id['user_id']}")
                                    ],
                                    [
                                        InlineKeyboardButton(
                                            text='SEND_STICKERS',
                                            callback_data=f"res {restrict_time} stickers {user_id['user_id']}"),
                                        InlineKeyboardButton(
                                            text='EMBED_LINKS',
                                            callback_data=f"res {restrict_time} link {user_id['user_id']}")
                                    ],
                                    [
                                        InlineKeyboardButton(text='Cancel', callback_data='cancel')
                                    ]
                                ]
                            )
                        )
                    else:
                        await self.botapp.send_message(
                            msg.chat.id,
                            'ERROR_WHITELIST_USER_ID',
                            reply_to_message_id=msg.message_id
                        )
                else:
                    await self.botapp.send_message(
                        msg.chat.id,
                        'ERROR_INVALID_USER_ID',
                        reply_to_message_id=msg.message_id
                    )

            elif msg.text == '/kick':
                user_id = await self.conn.get_user_id(msg)
                if user_id is not None and user_id['user_id']:
                    if user_id['user_id'] not in self.auth_system.whitelist:
                        await self.botapp.send_message(
                            msg.chat.id,
                            'Do you really want to kick {}?\n'
                            'If you really want to kick this user, press the button below.\n'
                            'This confirmation message will expire after 15 seconds.'.format(
                                TextParser.parse_user_markdown(user_id['user_id'])
                            ),
                            reply_to_message_id=msg.message_id,
                            parse_mode='markdown',
                            reply_markup=InlineKeyboardMarkup(
                                inline_keyboard=[
                                    [
                                        InlineKeyboardButton(
                                            text='Yes, kick it',
                                            callback_data=f'kick {msg.from_user.id} '
                                                          f'{user_id["user_id"]}'
                                        )
                                    ],
                                    [
                                        InlineKeyboardButton(
                                            text='No',
                                            callback_data='cancel'
                                        )
                                    ],
                                ]
                            )
                        )
                    else:
                        await self.botapp.send_message(msg.chat.id, 'ERROR_WHITELIST_USER_ID',
                                                       reply_to_message_id=msg.message_id)
                else:
                    await self.botapp.send_message(msg.chat.id, 'ERROR_INVALID_USER_ID',
                                                   reply_to_message_id=msg.message_id)

            elif msg.text.startswith('/pin'):
                target_id = await self.conn.get_reply_id_reverse(msg)
                if target_id is None:
                    await msg.reply('ERROR_INVALID_MESSAGE_ID')
                    return
                await self.botapp.pin_chat_message(self.target_group, target_id, not msg.text.endswith('a'))

            elif msg.text.startswith('/warn'):
                user_id = await self.conn.get_user_id(msg)
                if user_id is None or not user_id['user_id']:
                    return
                user_id = user_id['user_id']
                target_id = await self.conn.get_reply_id_reverse(msg)
                reason = ' '.join(msg.text.split(' ')[1:])
                dry_run = msg.text.split()[0].endswith('d')
                fwd_msg = None
                if self.warn_evidence_history_channel != 0:
                    fwd_msg = (await self.app.forward_messages(
                        self.warn_evidence_history_channel,
                        self.target_group,
                        target_id,
                        True)).message_id
                if dry_run:
                    await self.botapp.send_message(self.fudu_group, await self.generate_warn_message(user_id, reason),
                                                   reply_to_message_id=msg.reply_to_message.message_id)
                else:
                    warn_id = await self.conn.insert_new_warn(user_id, reason, fwd_msg)
                    warn_msg = await self.botapp.send_message(self.target_group,
                                                              await self.generate_warn_message(user_id, reason),
                                                              reply_to_message_id=target_id)
                    await self.botapp.send_message(
                        self.fudu_group,
                        _T('WARN SENT TO {}, Total warn {} time(s)').format(
                            TextParser.parse_user_markdown(user_id),
                            await self.conn.query_warn_by_user(user_id)
                        ),
                        parse_mode='markdown', reply_to_message_id=msg.message_id,
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [
                                InlineKeyboardButton(
                                    text=_T('RECALL'),
                                    callback_data=f'warndel {warn_msg.message_id} {warn_id}'
                                )
                            ]
                        ]))

        else:  # Not reply message
            if msg.text == '/ban':
                await client.send_message(
                    msg.chat.id, _T(
                        'Reply to the user you wish to restrict, '
                        'if you want to kick this user, please use the /kick command.'))

            elif msg.text == '/report':
                if self.join_group_verify_enable:
                    _problem_total_count = \
                        (await self.conn.query1('''SELECT COUNT(*) FROM "exam_user_session"'''))['count']
                    result = []
                    for problem_id in range(self.join_group_verify.problem_list.length):
                        total_count, correct_count = await asyncio.gather(self.conn.query1(
                            '''SELECT COUNT(*) FROM "exam_user_session" WHERE "problem_id" = $1''', problem_id),
                            self.conn.query1(
                                '''SELECT COUNT(*) FROM "exam_user_session"
                                 WHERE "problem_id" = $1 and "passed" = true''',
                                problem_id))
                        result.append(
                            '`{}`: `{:.2f}`% / `{:.2f}`%'.format(problem_id,
                                                                 correct_count['count'] * 100 / total_count['count'],
                                                                 total_count['count'] * 100 / _problem_total_count))
                    await msg.reply('Problem answer correct rate:\n{}'.format('\n'.join(result)))

            elif msg.text.startswith('/grant'):
                user_id = msg.text.split()[-1]
                await self.botapp.send_message(
                    msg.chat.id,
                    'Do you want to grant user {}?'.format(
                        TextParser.parse_user_markdown(user_id)),
                    disable_notification=True,
                    reply_to_message_id=msg.message_id,
                    reply_markup=InlineKeyboardMarkup(
                        [
                            [InlineKeyboardButton('CHANGE INFO', f'grant {user_id} info'),
                             InlineKeyboardButton('PIN', f'grant {user_id} pin')],
                            [InlineKeyboardButton('RESTRICT', f'grant {user_id} restrict'),
                             InlineKeyboardButton('DELETE', f'grant {user_id} delete')],
                            [InlineKeyboardButton('confirm', f'grant {user_id} confirm'),
                             InlineKeyboardButton('[DEBUG]Clear', f'grant {user_id} clear')],
                            [InlineKeyboardButton('cancel', 'cancel')]
                        ]))

    async def func_auth_process(self, _client: Client, msg: Message) -> None:
        if not self.auth_system.check_ex(msg.from_user.id):
            await msg.reply('Permission denied')
            return
        if msg.reply_to_message.from_user:
            if self.auth_system.check_ex(msg.reply_to_message.from_user.id):
                await msg.reply('Authorized')
            else:
                await self.botapp.send_message(
                    msg.chat.id,
                    'Do you want to authorize {} ?\nThis confirmation message will expire after 20 seconds.'.format(
                        TextParser.parse_user_markdown(msg.reply_to_message.from_user.id)
                    ),
                    reply_to_message_id=msg.message_id,
                    parse_mode='markdown',
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[
                            [
                                InlineKeyboardButton(text='Yes', callback_data='auth {} add'.format(
                                    msg.reply_to_message.from_user.id)),
                                InlineKeyboardButton(text='No', callback_data='cancel')
                            ]
                        ]
                    )
                )
        else:
            await msg.reply('Unexpected error.')

    async def cross_group_forward_request(self, msg: Message) -> None:
        kb = [
            [InlineKeyboardButton(text='Yes, I know what I\'m doing.', callback_data='fwd original')],
            [InlineKeyboardButton(text='Yes, but don\'t use forward.', callback_data='fwd text')],
            [InlineKeyboardButton(text='No, please don\'t.', callback_data='cancel d')]
        ]
        if msg.text is None: kb.pop(1)
        await self.botapp.send_message(
            msg.chat.id,
            '<b>Warning:</b> You are requesting forwarding an authorized user\'s '
            'message to the main group, please confirm your action.',
            'html',
            reply_to_message_id=msg.message_id,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)
        )
        del kb

    async def handle_new_member(self, client: Client, msg: Message) -> None:
        for new_user_id in (x.id for x in msg.new_chat_members):
            # Exam check goes here
            try:
                if not await self.join_group_verify.query_user_passed(new_user_id):
                    await self.botapp.kick_chat_member(self.target_group, new_user_id)
                    await self.botapp.send_message(self.fudu_group, 'Kicked challenge failure user {}'.format(
                        TextParser.parse_user_markdown(new_user_id)), 'markdown')
            except BotController.ByPassVerify:
                pass
            except:
                logger.exception('Exception occurred!')
            if await self.conn.query_user_in_banlist(new_user_id):
                await self.botapp.kick_chat_member(msg.chat.id, new_user_id)
        await self.conn.insert(
            msg,
            await client.send_message(
                self.fudu_group,
                '`{}` invite `{}` joined the group'.format(
                    TextParser.UserName(msg.from_user).full_name,
                    '`,`'.join(
                        TextParser.UserName(user).full_name for user in msg.new_chat_members
                    )
                ),
                'markdown'
            ) if msg.new_chat_members[0].id != msg.from_user.id else await client.send_message(
                    self.fudu_group,
                    '`{}` joined the group'.format(
                        '`,`'.join(
                            TextParser.UserName(user).full_name for user in msg.new_chat_members
                        )
                    ),
                    'markdown'
                )
        )

    async def handle_edit(self, client: Client, msg: Message) -> None:
        if msg.via_bot and msg.via_bot.id == 166035794:
            return
        target_id = await self.conn.get_id(msg.message_id)
        if target_id is None:
            logging.warning('Sleep 2 seconds for edit')
            await asyncio.sleep(2)
            target_id = await self.conn.get_id(msg.message_id)
            if target_id is None:
                return logger.error('Editing Failure: get_id return None')
        try:
            await (client.edit_message_text if msg.text else client.edit_message_caption)(
                self.fudu_group,
                target_id,
                TextParser(msg).get_full_message(),
                'html'
            )
        except pyrogram.errors.MessageNotModified:
            logging.warning('Editing Failure: MessageNotModified')
        except:
            logger.exception('Exception occurred!')

    async def handle_sticker(self, client: Client, msg: Message) -> None:
        await self.conn.insert(
            msg,
            await client.send_message(
                self.fudu_group,
                f'{TextParser(msg).get_full_message()} {msg.sticker.emoji} sticker',
                parse_mode='html',
                disable_web_page_preview=True,
                disable_notification=True,
                reply_to_message_id=await self.conn.get_reply_id(msg),
            )
        )

    async def _get_reply_id(self, msg: Message, reverse: bool = False) -> Optional[int]:
        if msg.reply_to_message is None:
            return None
        return await self.conn.get_id(msg.reply_to_message.message_id, reverse)

    async def send_media(self, client: Client, msg: Message, send_to: int, caption: str, reverse: bool = False) -> None:
        msg_type = self.get_file_type(msg)
        while True:
            try:
                _msg = await client.send_cached_media(
                    send_to,
                    self.get_file_id(msg, msg_type),
                    # self.get_file_ref(msg, msg_type),
                    caption=caption,
                    parse_mode='html',
                    disable_notification=True,
                    reply_to_message_id=await self._get_reply_id(msg, reverse)
                )
                if reverse:
                    await self.conn.insert_ex(_msg.message_id, int(msg.caption.split()[1]))
                else:
                    await self.conn.insert(msg, _msg)
                break

            except pyrogram.errors.FloodWait as e:
                logger.warning('Pause %d seconds because 420 flood wait', e.x)
                await asyncio.sleep(e.x)
            except:
                logger.exception('Exception occurred!')
                break

    async def handle_all_media(self, client: Client, msg: Message) -> None:
        await self.send_media(client, msg, self.fudu_group, TextParser(msg).get_full_message())

    async def handle_dice(self, client: Client, msg: Message) -> None:
        if msg.dice:
            await self.conn.insert(
                msg,
                await client.send_message(
                    self.fudu_group,
                    '{} {} dice[{}]'.format(
                        TextParser(msg).get_full_message(),
                        msg.dice.emoji,
                        msg.dice.value
                    ),
                    'html',
                    disable_web_page_preview=True,
                    disable_notification=True,
                    reply_to_message_id=await self.conn.get_reply_id(msg)
                )
            )
        else:
            raise ContinuePropagation()

    @staticmethod
    def get_file_id(msg: Message, _type: str) -> str:
        return getattr(msg, _type).file_id

    @staticmethod
    def get_file_ref(msg: Message, _type: str) -> str:
        return getattr(msg, _type).file_ref

    @staticmethod
    def get_file_type(msg: Message) -> str:
        return 'photo' if msg.photo else \
            'video' if msg.video else \
            'animation' if msg.animation else \
            'sticker' if msg.sticker else \
            'voice' if msg.voice else \
            'document' if msg.document else \
            'text' if msg.text else 'error'

    async def handle_speak(self, client: Client, msg: Message) -> None:
        if msg.text.startswith('/') and re.match(r'^/\w+(@\w*)?$', msg.text):
            return
        await self.conn.insert(
            msg,
            await client.send_message(
                self.fudu_group,
                TextParser(msg).get_full_message(),
                'html',
                disable_web_page_preview=not msg.web_page,
                disable_notification=True,
                reply_to_message_id=await self.conn.get_reply_id(msg)
            )
        )

    async def handle_bot_send_media(self, client: Client, msg: Message) -> None:
        def parse_caption(caption: str) -> str:
            obj = caption.split(maxsplit=3)
            return '' if len(obj) < 3 else obj[-1]

        await self.send_media(client, msg, self.target_group, parse_caption(TextParser(msg).split_offset()),
                              True)

    async def handle_incoming(self, client: Client, msg: Message) -> None:
        # NOTE: Remove debug code and other handle code from offical version
        await client.send(
            raw.functions.channels.ReadHistory(channel=await client.resolve_peer(msg.chat.id), max_id=msg.message_id))
        if msg.reply_to_message:
            await client.send(raw.functions.messages.ReadMentions(peer=await client.resolve_peer(msg.chat.id)))
        if msg.text == '/auth' and msg.reply_to_message:
            return await self.func_auth_process(client, msg)

        if not self.auth_system.check_ex(msg.from_user.id):
            return
        if msg.text and re.match(
                r'^/(bot (on|off)|del|get|fw|ban( ([1-9]\d*)[smhd]|f)?|kick( confirm| -?\d+)?|status|b?o(n|ff)|join|'
                r'promote( \d+)?|set [a-zA-Z]|pina?|su(do)?|title .*|warnd? .*|grant \d+|report)$',
                msg.text
        ):
            return await self.process_incoming_command(client, msg)
        if msg.text and msg.text.startswith('/') and re.match(r'^/\w+(@\w*)?$', msg.text):
            return
        if self.auth_system.check_muted(msg.from_user.id) or (msg.text and msg.text.startswith('//')) or (
                msg.caption and msg.caption.startswith('//')):
            return
        if msg.forward_from or msg.forward_from_chat or msg.forward_sender_name:
            if msg.forward_from:
                if msg.forward_from.is_self:
                    return
                elif self.auth_system.check_ex(msg.forward_from.id):
                    return await self.cross_group_forward_request(msg)
            await self.conn.insert_ex(
                (await self.botapp.forward_messages(self.target_group, self.fudu_group, msg.message_id)).message_id,
                msg.message_id)

        elif msg.text and (
                not msg.edit_date or (msg.edit_date and await self.conn.get_id(msg.message_id, True) is None)):
            await self.conn.insert_ex(
                (await self.botapp.send_message(
                    self.target_group,
                    TextParser(msg).split_offset(),
                    'html',
                    disable_web_page_preview=not msg.web_page,
                    reply_to_message_id=await self.conn.get_reply_id_reverse(msg),
                )).message_id, msg.message_id
            )

        elif msg.photo or msg.video or msg.animation or msg.document:
            _type = self.get_file_type(msg)
            await (await client.send_cached_media(
                msg.chat.id,
                self.get_file_id(msg, _type),
                # self.get_file_ref(msg, _type),
                f'/SendMedia {msg.message_id} {TextParser(msg).split_offset()}',
                parse_mode='html',
                disable_notification=True,
                reply_to_message_id=msg.reply_to_message.message_id if msg.reply_to_message else None
            )).delete()

        elif msg.edit_date:
            try:
                await (self.botapp.edit_message_text if msg.text else self.botapp.edit_message_caption)(
                    self.target_group,
                    await self.conn.get_id(msg.message_id, True),
                    TextParser(msg).split_offset(),
                    parse_mode='html',
                    disable_web_page_preview=not msg.web_page
                )
            except:
                logger.exception('Exception occurred!')

        elif msg.sticker:
            await self.conn.insert_ex(
                (await self.botapp.send_sticker(self.target_group, msg.sticker.file_id,
                                                reply_to_message_id=await self.conn.get_reply_id_reverse(
                                                    msg))).message_id,
                msg.message_id
            )

    async def handle_callback(self, client: Client, msg: CallbackQuery) -> None:
        if msg.message.chat.id < 0 and msg.message.chat.id != self.fudu_group: return
        args = msg.data.split()
        try:
            if msg.data.startswith('cancel') or msg.data == 'rm':
                if msg.data.endswith('d'):
                    await msg.message.delete()
                else:
                    await msg.edit_message_reply_markup()

            if self.join_group_verify_enable and \
                    self.join_group_verify is not None and \
                    await self.join_group_verify.click_to_join(client, msg):
                return

            if msg.data.startswith('res'):
                if time.time() - msg.message.date > 20:
                    raise OperationTimeoutError()
                _, dur, _type, _user_id = args
                if await client.restrict_chat_member(
                        self.target_group,
                        int(_user_id),
                        {
                            'write': ChatPermissions(can_send_messages=True),
                            'media': ChatPermissions(can_send_media_messages=True),
                            'stickers': ChatPermissions(can_send_stickers=True),
                            'link': ChatPermissions(can_add_web_page_previews=True),
                            'read': ChatPermissions()
                        }.get(_type),
                        int(time.time()) + int(dur)
                ):
                    await msg.answer('The user is restricted successfully.')
                    await client.edit_message_text(
                        msg.message.chat.id,
                        msg.message.message_id,
                        'Restrictions applied to {} Duration: {}'.format(
                            TextParser.parse_user_markdown(_user_id),
                            f'{dur}s' if int(dur) else 'Forever'),
                        parse_mode='markdown',
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(
                            text='UNBAN', callback_data=f'unban {_user_id}')]])
                    )

            elif msg.data.startswith('unban'):
                if await client.restrict_chat_member(self.target_group, int(args[-1]), ChatPermissions(
                        can_send_messages=True,
                        can_send_stickers=True,
                        can_send_polls=True,
                        can_add_web_page_previews=True,
                        can_send_media_messages=True,
                        can_send_animations=True,
                        can_pin_messages=True,
                        can_invite_users=True,
                        can_change_info=True
                )):
                    await asyncio.gather(msg.answer('Unban successfully'),
                                         client.edit_message_reply_markup(msg.message.chat.id, msg.message.message_id))

            elif msg.data.startswith('auth'):
                if time.time() - msg.message.date > 20:
                    raise OperationTimeoutError()
                await self.auth_system.add_user(args[1])
                await asyncio.gather(msg.answer(f'{args[1]} added to the authorized group'),
                                     msg.message.edit(f'{args[1]} added to the authorized group'))

            elif msg.data.startswith('fwd'):
                if time.time() - msg.message.date > 30:
                    raise OperationTimeoutError()
                if 'original' in msg.data:
                    # Process original forward
                    await self.conn.insert_ex(
                        (await client.forward_messages(
                            self.target_group,
                            msg.message.chat.id,
                            msg.message.reply_to_message.message_id)
                         ).message_id,
                        msg.message.reply_to_message.message_id
                    )
                else:
                    await self.conn.insert_ex((await client.send_message(self.target_group, TextParser(
                        msg.message.reply_to_message).split_offset(), 'html')).message_id,
                                              msg.message.reply_to_message.message_id)
                await asyncio.gather(msg.answer('Forward successfully'), msg.message.delete())

            elif msg.data.startswith('kick'):
                # _TODO: Process parallel request (deprecated)
                if not msg.data.startswith('kickc') and msg.from_user.id != int(args[-2]):
                    raise OperatorError()
                if 'true' not in msg.data:
                    if not msg.data.startswith('kickc') and time.time() - msg.message.date > 15:
                        raise OperationTimeoutError()
                    client_args = [
                        msg.message.chat.id,
                        msg.message.message_id,
                        'Press the button again to kick {}\n'
                        'This confirmation message will expire after 10 seconds.'.format(
                            TextParser.parse_user_markdown(args[-1])
                        ),
                    ]
                    if msg.data.startswith('kickc'):
                        client_args.pop(1)
                        r = list(client_args)
                        r.insert(1, msg.from_user.id)
                        msg.data = ' '.join(map(str, r))
                        del r
                    kwargs = {
                        'parse_mode': 'markdown',
                        'reply_markup': InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text='Yes, please.',
                                                  callback_data=' '.join(('kick true', ' '.join(map(str, args[1:])))))],
                            [InlineKeyboardButton(text='Cancel', callback_data='cancel')]
                        ])
                    }
                    await (client.send_message if msg.data.startswith('kickc') else client.edit_message_text)(
                        *client_args, **kwargs)
                    await msg.answer(
                        f'Please press again to make sure. Do you really want to kick {args[-1]} ?', True)
                else:
                    if msg.message.edit_date:
                        if time.time() - msg.message.edit_date > 10:
                            raise OperationTimeoutError()
                    else:
                        if time.time() - msg.message.date > 10:
                            raise OperationTimeoutError()
                    await client.kick_chat_member(self.target_group, int(args[-1]))
                    await asyncio.gather(msg.answer(f'Kicked {args[-1]}'),
                                         msg.message.edit(f'Kicked {TextParser.parse_user_markdown(args[-1])}'))

            elif msg.data.startswith('promote'):
                if not msg.data.endswith('undo'):
                    if time.time() - msg.message.date > 10:
                        raise OperationTimeoutError()
                    await self.botapp.promote_chat_member(
                        self.target_group,
                        int(args[1]),
                        True,
                        can_delete_messages=True,
                        can_restrict_members=True,
                        can_invite_users=True,
                        can_pin_messages=True,
                        can_promote_members=True,
                    )
                    await msg.answer('Promote successfully')
                    await msg.message.edit(
                        f'Promoted {TextParser.parse_user_markdown(int(args[1]))}',
                        parse_mode='markdown',
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text='UNDO', callback_data=' '.join((msg.data, 'undo')))],
                            [InlineKeyboardButton(text='remove button', callback_data='rm')]])
                    )
                else:
                    await self.botapp.promote_chat_member(
                        self.target_group, int(args[1]),
                        False,
                        can_delete_messages=False,
                        can_invite_users=False,
                        can_restrict_members=False
                    )
                    await asyncio.gather(
                        msg.answer('Undo Promote successfully'),
                        msg.message.edit(
                            f'Undo promoted {TextParser.parse_user_markdown(int(args[1]))}',
                            parse_mode='markdown')
                    )

            elif msg.data.startswith('grant'):
                _redis_key_str = f'promote_{msg.message.chat.id}_{args[1]}'
                if args[2] == 'confirm':
                    select_privileges = await self._redis.get(_redis_key_str)
                    await self._redis.delete(_redis_key_str)
                    if select_privileges is None:
                        raise OperationTimeoutError()
                    grant_args = {}
                    for x in map(lambda x: x.strip(), select_privileges.decode().split(',')):
                        if x == 'info':
                            grant_args.update({'can_change_info': True})
                        elif x == 'delete':
                            grant_args.update({'can_delete_messages': True})
                        elif x == 'restrict':
                            grant_args.update({'can_restrict_members': True})
                        elif x == 'pin':
                            grant_args.update({'can_pin_messages': True})
                    await self.botapp.promote_chat_member(self.target_group, int(args[1]), **grant_args)
                    await msg.message.edit('Undo grant privileges', reply_markup=InlineKeyboardMarkup(
                        [[InlineKeyboardButton('UNDO', f'grant {args[1]} undo')]]))
                    await msg.answer()
                elif args[2] == 'undo':
                    await self.botapp.promote_chat_member(self.target_group, int(args[1]), False,
                                                          can_delete_messages=False, can_restrict_members=False)
                    await msg.message.edit_reply_markup()
                    await msg.answer()
                elif args[2] == 'clear':
                    self._redis.delete(_redis_key_str)
                    await msg.answer()
                else:
                    if time.time() - msg.message.date > 40:
                        raise OperationTimeoutError()
                    # original_msg = msg.message.text.splitlines()[0]
                    select_privileges = self._redis.get(_redis_key_str)
                    if select_privileges is None:
                        select_privileges = [args[2]]
                        self._redis.set(_redis_key_str, select_privileges[0])
                        self._redis.expire(_redis_key_str, 60)
                    else:
                        select_privileges = list(map(lambda x: x.strip(), select_privileges.decode().split(',')))
                        if args[2] in select_privileges:
                            if len(select_privileges) == 1:
                                return await msg.answer('You should choose at least one privilege.', True)
                            select_privileges.remove(args[2])
                        else:
                            select_privileges.append(args[2])
                        await self._redis.set(_redis_key_str, ','.join(select_privileges))
                    await msg.message.edit(
                        'Do you want to grant user {}?\n\nSelect privileges:\n{}'.format(
                            TextParser.parse_user_markdown(args[1]),
                            '\n'.join(select_privileges)),
                        reply_markup=msg.message.reply_markup)
                    # return await msg.answer(f'Promoted {args[2]} permission')

            elif msg.data == 'unpin':
                await self.botapp.unpin_chat_message(self.target_group)
                await asyncio.gather(msg.message.edit_reply_markup(),
                                     msg.answer())

            elif msg.data.startswith('warndel'):
                await self.botapp.delete_messages(self.target_group, int(args[1]))
                await self.conn.delete_warn_by_id(int(args[2]))
                await asyncio.gather(msg.message.edit_reply_markup(),
                                     msg.answer())

        except OperationTimeoutError:
            await asyncio.gather(msg.answer('Confirmation time out'),
                                 client.edit_message_reply_markup(msg.message.chat.id, msg.message.message_id))
        except OperatorError:
            await msg.answer(f'The operator should be {args[-2]}.', True)
        except:
            await self.app.send_message(config.getint('custom_service', 'help_group'),
                                        traceback.format_exc().splitlines()[-1])
            logger.exception('Exception occurred!')


async def main():
    bot = await BotController.create()
    await bot.start()
    await bot.idle()
    await bot.stop()


if __name__ == '__main__':
    coloredlogs.install(logging.DEBUG,
                        fmt='%(asctime)s,%(msecs)03d - %(levelname)s - %(funcName)s - %(lineno)d - %(message)s')
    if '--debug-pyrogram' in sys.argv:
        # logging.getLogger('pyrogram').setLevel(logging.INFO)
        pyrogram_file_handler = logging.FileHandler('pyrogram.log')
        pyrogram_file_handler.setFormatter(
            coloredlogs.ColoredFormatter(
                '%(asctime)s,%(msecs)03d - %(levelname)s - %(funcName)s - %(lineno)d - %(message)s'))
        logging.getLogger('pyrogram').addHandler(pyrogram_file_handler)
    else:
        logging.getLogger("pyrogram").setLevel(logging.WARNING)

    file_handler = logging.FileHandler('log.log')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(coloredlogs.ColoredFormatter(
        '%(asctime)s,%(msecs)03d - %(levelname)s - %(funcName)s - %(lineno)d - %(message)s'))
    logging.getLogger('telegram-repeater').addHandler(file_handler)
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
    loop.close()
