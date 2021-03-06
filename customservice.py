#!/usr/bin/env python
# -*- coding: utf-8 -*-
# customservice.py
# Copyright (C) 2019-2021 github.com/googlehosts Group:Z
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
import base64
import gettext
import hashlib
import logging
import random
import re
import time
import traceback
from configparser import ConfigParser
from datetime import datetime
from typing import (Awaitable, Callable, Dict, List, Mapping, Optional,
                    Sequence, Tuple, TypeVar, Union)

import aioredis
import asyncpg
import pyrogram
import pyrogram.errors
from pyrogram import Client, filters
from pyrogram.handlers import CallbackQueryHandler, MessageHandler
from pyrogram.types import (CallbackQuery, InlineKeyboardButton,
                            InlineKeyboardMarkup, KeyboardButton, Message,
                            ReplyKeyboardMarkup, ReplyKeyboardRemove)

import utils

logger = logging.getLogger('telegram-repeater').getChild('customservice')

translation = gettext.translation('customservice', 'translations/',
                                  languages=[utils.get_language()], fallback=True)

_T = translation.gettext

_problemT = TypeVar('_problemT', Dict, str, bool, int)
_anyT = TypeVar('_anyT')


class TextParser(utils.TextParser):
    def __init__(self, msg: Message):
        super().__init__()
        self._msg = self.BuildMessage(msg)
        self.parsed_msg = self.parse_main()

    def __str__(self) -> str:
        return self.parsed_msg


class Ticket:
    def __init__(self, msg: Message, section: str, status: str):
        self._origin_msg = TextParser(msg).parsed_msg
        self.hash_value = CustomServiceBot.hash_msg(msg)
        self.section = section
        self.status = status
        self.sql = (
            '''INSERT INTO "tickets" ("user_id", "hash", "timestamp", "origin_msg", "section", "status") 
            VALUES ($1, $2, CURRENT_TIMESTAMP, $3, $4, $5)''',
            msg.chat.id, self.hash_value, base64.b64encode(self._origin_msg.encode()).decode(), self.section,
            self.status
        )

    def __str__(self) -> Tuple[str, int, str, str, str, str]:
        return self.sql


class RemovePunctuations:
    def __init__(self, enable: bool, items: List[str]):
        self.enable = enable
        self.items = items

    def replace(self, text: str) -> str:
        if not self.enable:
            return text
        return ''.join(x for x in text if x not in self.items)


class ProblemSet:
    _self = None

    def __init__(self, redis_conn: aioredis.Redis, problem_set: Mapping[str, _anyT],
                 remove_punctuations: RemovePunctuations):
        self._redis: aioredis.Redis = redis_conn
        self._prefix: str = utils.get_random_string()
        self.version: int = problem_set['version']
        self.problem_length: int = len(problem_set['problems']['problem_set'])
        self.sample_problem: Dict[str, str] = problem_set['problems'].get('sample_problem')
        self._has_sample: bool = bool(self.sample_problem)
        self.remove_punctuations: RemovePunctuations = remove_punctuations

    async def init(self, problem_set: Mapping[str, _anyT]):
        if self.sample_problem:
            await self._redis.mset({f'{self._prefix}_{key}_sample': item for key, item in self.sample_problem.items()})
        for x in range(self.problem_length):
            problems = problem_set['problems']['problem_set']
            if problems[x].get('use_regular_expression'):
                await self._redis.set(f'{self._prefix}_re_{x}', 1)
            await self._redis.set(f'{self._prefix}_Q_{x}', problems[x]['Q'])
            await self._redis.set(f'{self._prefix}_A_{x}', self.remove_punctuations.replace(problems[x]['A']))
            await self._redis.set(f'{self._prefix}_OA_{x}', problems[x]['A'])

    @classmethod
    async def create(cls, redis_conn: aioredis.Redis, problem_set: Dict[str, _anyT],
                     remove_punctuations: RemovePunctuations) -> 'ProblemSet':
        self = ProblemSet(redis_conn, problem_set, remove_punctuations)
        await self.init(problem_set)
        return self

    async def destroy(self) -> None:
        for x in range(self.problem_length):
            await self._redis.delete(f'{self._prefix}_re_{x}')
            await self._redis.delete(f'{self._prefix}_Q_{x}')
            await self._redis.delete(f'{self._prefix}_A_{x}')
            await self._redis.delete(f'{self._prefix}_OA_{x}')
        if self._has_sample:
            await self._redis.delete(f'{self._prefix}_Q_sample')
            await self._redis.delete(f'{self._prefix}_A_sample')

    def get_random_number(self) -> int:
        return random.randint(0, self.problem_length - 1)

    async def get(self, key: int) -> Dict[str, str]:
        return {'use_regular_expression': await self._redis.get(f'{self._prefix}_re_{key}'),
                'Q': (await self._redis.get(f'{self._prefix}_Q_{key}')).decode(),
                'A': (await self._redis.get(f'{self._prefix}_A_{key}')).decode()}

    async def get_origin(self, key: int) -> str:
        return (await self._redis.get(f'{self._prefix}_OA_{key}')).decode()

    @property
    def length(self) -> int:
        return self.problem_length

    @property
    def has_sample(self) -> bool:
        return self._has_sample

    async def get_sample(self) -> Optional[Mapping[str, str]]:
        if not self._has_sample:
            return None
        return {'Q': (await self._redis.get(f'{self._prefix}_Q_sample')).decode(),
                'A': (await self._redis.get(f'{self._prefix}_A_sample')).decode()}

    @staticmethod
    def get_instance() -> ProblemSet:
        if ProblemSet._self is None:
            raise RuntimeError()
        return ProblemSet._self

    @staticmethod
    async def init_instance(redis_conn: aioredis.Redis, problem_set: Dict[str, _problemT],
                            remove_punctuations: RemovePunctuations) -> 'ProblemSet':
        ProblemSet._self = await ProblemSet.create(redis_conn, problem_set, remove_punctuations)
        return ProblemSet._self


class JoinGroupVerify:

    class ProblemVersionException(Exception):
        pass

    def __init__(self, conn: utils.PgSQLdb, botapp: Client, target_group: int, working_group: int):
        self.conn: utils.PgSQLdb = conn
        self.botapp: Client = botapp
        self.target_group: int = target_group
        self.working_group: int = working_group
        self._revoke_tracker_coro: utils.InviteLinkTracker = None  # type: ignore
        self._keyboard: Dict[str, InlineKeyboardMarkup] = {}
        self._welcome_msg: Optional[str] = None  # type: ignore
        self.remove_punctuations: Optional[RemovePunctuations] = None
        self.problems: Optional[ProblemSet] = None
        self.max_retry: Optional[int] = None  # type: ignore
        self.max_retry_error: Optional[str] = None  # type: ignore
        self.max_retry_error_detail: Optional[str] = None  # type: ignore
        self.try_again: Optional[str] = None  # type: ignore
        self._send_link_confirm: Optional[bool] = None  # type: ignore
        self._confirm_message: Optional[str] = None  # type: ignore
        self._confirm_button_text: Optional[str] = None  # type: ignore

    def init(self) -> None:
        self.botapp.add_handler(MessageHandler(self.handle_bot_private, filters.private & filters.text))

    def init_other_object(self, problem_set: Dict[str, _anyT]):
        self._revoke_tracker_coro: utils.InviteLinkTracker = utils.InviteLinkTracker(
            self.botapp,
            problem_set,
            self.target_group
        )
        self._welcome_msg: str = problem_set['messages']['welcome_msg']
        self.max_retry: int = problem_set['configs']['max_retry']
        self.max_retry_error: str = problem_set['messages']['max_retry_error']
        self.max_retry_error_detail: str = problem_set['messages']['max_retry_error_detail']
        self.try_again: str = problem_set['messages']['try_again']
        self._send_link_confirm: bool = problem_set.get('confirm_msg') and problem_set['confirm_msg'].get(
            'enable')  # type: ignore
        if self._send_link_confirm:
            self._confirm_message: str = problem_set['confirm_msg']['text']
            self._confirm_button_text: str = problem_set['confirm_msg']['button_text']
        if problem_set['ticket_bot']['enable']:
            self._keyboard = {
                'reply_markup': InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text=_T('I need help.'), url=problem_set['ticket_bot']['link'])]
                    ]
                )
            }
        self._revoke_tracker_coro.start()

    @classmethod
    async def create(cls, conn: utils.PgSQLdb, botapp: Client, target_group: int, working_group: int,
                     load_problem_set: Callable[[], Dict[str, _problemT]], redis_conn: aioredis.Redis):
        self = JoinGroupVerify(conn, botapp, target_group, working_group)
        problem_set = load_problem_set()
        self.remove_punctuations = RemovePunctuations(
            **problem_set['configs'].get('ignore_punctuations', {'enable': False, 'items': []}))
        self.problems = await ProblemSet.init_instance(redis_conn, problem_set, self.remove_punctuations)
        self.init_other_object(problem_set)
        return self

    @property
    def problem_list(self) -> ProblemSet:
        if self.problems is None:
            raise RuntimeError()
        return self.problems

    @property
    def revoke_tracker_coro(self) -> utils.InviteLinkTracker:
        return self._revoke_tracker_coro

    async def query_user_passed(self, user_id: int) -> bool:
        sql_obj = await self.conn.query1('''SELECT "passed", "bypass" FROM "exam_user_session" WHERE "user_id" = $1''',
                                         user_id)
        return sql_obj is not None and (sql_obj['passed'] or sql_obj['bypass'])

    async def handle_bot_private(self, client: Client, msg: Message) -> None:
        if msg.text.startswith('/') and msg.text != '/start newbie':
            return
        user_obj = await self.conn.query1(
            '''SELECT "problem_id", "problem_version", "baned", "bypass", "retries", "passed", "unlimited" 
            FROM "exam_user_session" WHERE "user_id" = $1''',
            msg.chat.id)
        if msg.text == '/start newbie':
            try:
                try:
                    # raise Exception
                    user = await self.botapp.get_chat_member(self.target_group, msg.chat.id)
                    # print(user.status)
                    # if user.status in ('member', 'administrator', 'creator', 'restricted'):
                    if user.status == 'left':
                        raise ValueError('left')
                    await msg.reply(_T('You are already in the group.'))
                    return
                except pyrogram.errors.exceptions.bad_request_400.UserNotParticipant:
                    pass
                except:
                    logger.exception('Exception occurred while checking user status')
                if user_obj is not None:
                    if user_obj['bypass']:
                        await self._revoke_tracker_coro.send_link(msg.chat.id, True)
                    elif user_obj['passed']:
                        await msg.reply(_T('You have already answered the question.'))
                    elif user_obj['baned']:
                        await msg.reply(_T('Due to privacy settings, you are temporarily unable to join this group.'))
                    else:
                        await msg.reply(_T('An existing session is currently active.'), True)
                else:
                    random_id = self.problems.get_random_number()

                    # Query user status
                    await self.conn.execute(
                        '''INSERT INTO "exam_user_session" ("user_id", "problem_version", "problem_id", "timestamp")
                        VALUES ($1, $3, $2, CURRENT_TIMESTAMP)''',
                        msg.chat.id, random_id, self.problems.version)
                    await msg.reply(
                        self._welcome_msg,
                        parse_mode='html',
                        disable_web_page_preview=True,
                        **self._keyboard
                    )

                    # Send sample problem
                    if self.problems.has_sample:
                        await msg.reply(
                            _T('For example:\n</b> <code>{Q}</code>\n<b>A:</b> <code>{A}</code>').format(
                                **await self.problems.get_sample()
                            ),
                            parse_mode='html',
                            disable_web_page_preview=True
                        )

                    # Send problem body
                    await msg.reply(
                        (await self.problems.get(random_id))['Q'],
                        # self.problem_set['problems']['problem_set'][random_id]['Q'],
                        parse_mode='html',
                        disable_web_page_preview=True
                    )

            except pyrogram.errors.exceptions.bad_request_400.UserIsBlocked:
                logger.warning('Caught blocked user %s', msg.chat.id)
                await client.send_message(
                    self.working_group,
                    _T('The bot is blocked by user {}').format(TextParser.parse_user_markdown(msg.chat.id)),
                    'markdown'
                )
            except:
                logger.exception('Unexpect exception occurred in check newbie function')
        else:
            if user_obj is None:
                return
            if user_obj['problem_version'] != self.problems.version:
                await msg.reply(_T('Problem version updated, please request new problem by submitting a ticket.'))
                return
            if user_obj['unlimited'] or user_obj['retries'] <= self.max_retry:
                if self.valid_answer(msg, await self.problems.get(user_obj['problem_id'])):
                    await self.conn.execute('''UPDATE "exam_user_session" SET "passed" = true WHERE "user_id" = $1''',
                                            msg.chat.id)
                    await self.send_link(msg)
                    return
                elif user_obj['bypass']:  # and user_obj['passed']:
                    await self.conn.execute('''UPDATE "exam_user_session" SET "passed" = true WHERE "user_id" = $1''',
                                            msg.chat.id)
                    await self.send_link(msg)
                    return
            retries = user_obj['retries'] + 2
            if retries > self.max_retry:
                if retries == self.max_retry + 1:
                    await msg.reply(
                        '\n\n'.join((self.max_retry_error, self.max_retry_error_detail)),
                        parse_mode='html', disable_web_page_preview=True
                    )
                    logger.debug('%d %s', msg.chat.id, repr(msg.text))
                    await self._insert_answer_history(msg)
                else:
                    await msg.reply(self.max_retry_error_detail, parse_mode='html',
                                    disable_web_page_preview=True)
            else:
                await msg.reply(self.try_again, parse_mode='html', disable_web_page_preview=True)
                logger.debug('%d %s', msg.chat.id, repr(msg.text))
                await self._insert_answer_history(msg)
            await self.conn.execute('''UPDATE "exam_user_session" SET "retries" = $1 WHERE "user_id" = $2''',
                                    retries, msg.chat.id)

    async def _insert_answer_history(self, msg: Message) -> None:
        await self.conn.execute('''INSERT INTO "answer_history" ("user_id", "body") VALUES ($1, $2)''',
                                msg.chat.id, msg.text[:200])

    async def check_joined_group(self, user_id: int) -> None:
        logger.debug('Track %d status', user_id)
        await asyncio.sleep(30)  # Wait up to 30 second
        try:
            await self.botapp.get_chat_member(self.target_group, user_id)
        except pyrogram.errors.exceptions.bad_request_400.UserNotParticipant:
            await self.conn.insert_user_to_banlist(user_id)
            await self.botapp.send_message(self.working_group, 'Baned not joined group user {}'.format(
                TextParser.parse_user_markdown(user_id)), 'markdown')
            logger.info('Baned not joined group user %d', user_id)

    async def click_to_join(self, client: Client, msg: CallbackQuery) -> bool:
        if msg.data == 'iamready':
            if not await self.query_user_passed(msg.message.chat.id):
                await msg.answer(_T('Function is not ready, please try again later.'), True)
                logger.warning('User clicked but function is not ready during request link')
            else:
                try:
                    await client.edit_message_reply_markup(msg.message.chat.id, msg.message.message_id)
                    await self._revoke_tracker_coro.send_link(msg.message.chat.id, True)
                    await msg.answer()
                except:
                    logger.exception('Exception occurred on process click function')
            return True
        return False

    async def send_link(self, msg: Message, from_ticket: bool = False) -> None:
        if self._send_link_confirm:
            reply_obj = dict(
                text=self._confirm_message,
                parse_mode='html',
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=self._confirm_button_text, callback_data='iamready')]
                ])
            )
            if isinstance(msg, int):
                reply_obj.update(dict(chat_id=msg))
                await self.botapp.send_message(**reply_obj)
            else:
                await msg.reply(**reply_obj)
        else:
            await self._revoke_tracker_coro.send_link(msg.chat.id, from_ticket)

    def valid_answer(self, msg: Message, problem_body: Dict[str, str]) -> bool:
        text = self.remove_punctuations.replace(msg.text)
        if problem_body.get('use_regular_expression', False):
            b = bool(re.match(problem_body['A'], text))
        else:
            b = text == problem_body['A']
        logger.debug('verify %s %s == %s', b, text, problem_body['A'])
        return b


class CustomServiceBot:
    INIT_STATUS = 0
    SELECT_SECTION = 1
    SEND_QUESTION = 2
    SEND_FINISH = 3
    RE_TICKET_ID = re.compile(r'[a-f\d]{32}')

    def __init__(self, config_file: Union[str, ConfigParser], pgsql_handle: utils.PgSQLdb,
                 send_link_callback: Optional[Callable[[Message, bool], Awaitable]], redis_conn: aioredis.Redis):

        if isinstance(config_file, ConfigParser):
            config = config_file
        else:
            config = ConfigParser()
            config.read(config_file)

        self.pgsqldb: utils.PgSQLdb = pgsql_handle
        self._redis: aioredis.Redis = redis_conn
        self.bot_id: int = int(config['custom_service']['custom_api_key'].split(':')[0])
        self.bot: Client = Client(
            session_name=str(self.bot_id),
            bot_token=config['custom_service']['custom_api_key'],
            api_id=config['account']['api_id'],
            api_hash=config['account']['api_hash']
        )

        self.help_group: int = config.getint('custom_service', 'help_group')
        self.send_link_callback: Optional[Callable[[Message, bool], Awaitable]] = send_link_callback

        self.SECTION: List[str] = [
            _T("VERIFICATION"),
            _T("OTHER")
        ]

        self.init_handle()

    def init_handle(self) -> None:
        self.bot.add_handler(MessageHandler(self.handle_start, filters.command('start') & filters.private))
        self.bot.add_handler(MessageHandler(self.handle_create, filters.command('create') & filters.private))
        self.bot.add_handler(MessageHandler(self.handle_cancel, filters.command('cancel') & filters.private))
        self.bot.add_handler(MessageHandler(self.handle_list, filters.command('list') & filters.private))
        self.bot.add_handler(MessageHandler(self.handle_close, filters.command('close') & filters.private))
        self.bot.add_handler(MessageHandler(self.handle_reply, filters.reply & filters.text & filters.private))
        self.bot.add_handler(MessageHandler(self.handle_msg, filters.text & filters.private))
        self.bot.add_handler(MessageHandler(self.query_user_status,
                                            filters.chat(self.help_group) & filters.command('q')))
        self.bot.add_handler(MessageHandler(self.call_superuser_function,
                                            filters.chat(self.help_group) & filters.reply & filters.command('m')))
        self.bot.add_handler(MessageHandler(self.handle_group, filters.reply & filters.chat(self.help_group)))
        self.bot.add_handler(MessageHandler(self.handle_other, filters.private))
        self.bot.add_handler(CallbackQueryHandler(self.answer))

    async def start(self) -> Client:
        return await self.bot.start()

    async def stop(self) -> Client:
        return await self.bot.stop()

    @staticmethod
    async def idle() -> None:
        await pyrogram.idle()

    async def active(self) -> None:
        await self.start()
        await self.idle()

    @staticmethod
    def hash_msg(msg: Message) -> str:
        return hashlib.md5(' '.join(map(str, (msg.from_user.id, msg.date, msg.message_id))).encode()).hexdigest()

    def get_hash_from_reply_msg(self, msg: Message) -> str:
        if msg.reply_to_message is None or \
                msg.reply_to_message.text is None or \
                msg.reply_to_message.from_user.id != self.bot_id or \
                msg.reply_to_message.entities is None or \
                msg.reply_to_message.entities[0].type != 'hashtag':
            raise ValueError("hash message info error")
        r = self.RE_TICKET_ID.search(msg.reply_to_message.text)
        if r is not None:
            return r.group(0)
        else:
            raise ValueError('hash info not found')

    def generate_section_pad(self) -> ReplyKeyboardMarkup:
        return ReplyKeyboardMarkup(keyboard=[
            [KeyboardButton(text=x)] for x in self.SECTION
        ], resize_keyboard=True, one_time_keyboard=True)

    @staticmethod
    def generate_ticket_keyboard(ticket_id: str, user_id: int, closed: bool = False,
                                 other: bool = False) -> InlineKeyboardMarkup:
        kb = [
            InlineKeyboardButton(text=_T('Close'), callback_data=f'close {ticket_id}'),
            InlineKeyboardButton(text=_T('Send link'), callback_data=f'send {user_id}'),
            InlineKeyboardButton(text=_T('Block'), callback_data=f'block {user_id}')
        ]
        if closed:
            kb = kb[2:]
        elif other:
            kb.pop(1)
        return InlineKeyboardMarkup(
            inline_keyboard=[kb]
        )

    @staticmethod
    def return_bool_emoji(i: _anyT) -> str:
        return '\u2705' if i else '\u274c'

    async def handle_list(self, _client: Client, msg: Message) -> None:
        q = [dict(x) for x in (await self.pgsqldb.query(
            '''SELECT "hash", "status" FROM "tickets" WHERE "user_id" = $1 ORDER BY "timestamp" DESC LIMIT 3''',
            msg.chat.id))]
        if not q:
            await msg.reply(_T('You have never used this system before.'), True)
            return
        for _ticket in q:
            _ticket['status'] = self.return_bool_emoji(_ticket['status'] != 'closed')  # type: ignore
        await msg.reply(_T('Here are the last three tickets (up to 3)\n#{}').format(
            '\n#'.join(' '.join(value for _, value in _ticket.items()) for _ticket in q)), True)

    async def handle_close(self, client: Client, msg: Message) -> None:
        if msg.reply_to_message is not None and msg.text == '/close':
            try:
                ticket_id = self.get_hash_from_reply_msg(msg)
            except ValueError:
                await msg.reply(_T(
                    'TICKET NUMBER NOT FOUND\n'
                    'Please make sure that you have replied to the message which contains the ticket number.'),
                    True)
                return
        else:
            if len(msg.text) < 8:
                await msg.reply(_T(
                    'ERROR: COMMAND FORMAT Please use `/close <ticket number>` or'
                    ' **Reply to the message which contains the ticket number** to close the ticket'),
                    True, 'markdown', disable_notification=True)
                return
            ticket_id = msg.text.split()[-1]
            if len(ticket_id) != 32:
                await msg.reply(_T('ERROR: TICKET NUMBER FORMAT'), True)
                return
        q = await self.pgsqldb.query1('''SELECT "user_id" FROM "tickets" WHERE "hash" = $1 AND "status" != 'closed' ''',
                                      ticket_id)
        if q is None:
            await msg.reply(_T('TICKET NUMBER NOT FOUND or TICKET CLOSED'), True)
            return
        if q['user_id'] != msg.chat.id:
            await msg.reply(_T(
                '403 Forbidden(You cannot close a ticket created by others. '
                'If this ticket is indeed created by yourself, please report the problem using the same ticket.)'),
                True)
            return
        await self.pgsqldb.execute('''UPDATE "tickets" SET "status" = 'closed' WHERE "user_id" = $1 AND "hash" = $2''',
                                   msg.chat.id, ticket_id)
        await self._update_last_time(msg)
        await client.send_message(self.help_group,
                                  _T('UPDATE\n[ #{} ]\nThis ticket is already closed by {}').format(
                                      ticket_id,
                                      utils.TextParser.parse_user_markdown(msg.chat.id, _T('Creator'))),
                                  reply_markup=self.generate_ticket_keyboard(ticket_id, msg.chat.id, other=True))
        await msg.reply(_T('Close ticket success.'), True)

    async def add_user(self, user_id: int, step: int = 0) -> None:
        await self.pgsqldb.execute(
            '''INSERT INTO "tickets_user" ("user_id", "create_time", "step") VALUES ($1, CURRENT_TIMESTAMP, $2)''',
            user_id, step)

    async def change_step(self, user_id: int, step: int, section: str = '') -> None:
        if section == '':
            await self.pgsqldb.execute('''UPDATE "tickets_user" SET "step" = $1 WHERE "user_id" = $2''', step, user_id)
        else:
            await self.pgsqldb.execute('''UPDATE "tickets_user" SET "step" = $1, "section" = $2 WHERE "user_id" = $3''',
                                       step, section, user_id)

    async def query_status(self, user_id: int) -> Optional[asyncpg.Record]:
        return await self.pgsqldb.query1('''SELECT "step", "section" FROM "tickets_user" WHERE "user_id" = $1''',
                                         user_id)

    async def query_user(self, user_id: int) -> Optional[asyncpg.Record]:
        return await self.pgsqldb.query1('''SELECT "section" FROM "tickets_user" WHERE "user_id" = $1''', user_id)

    async def set_section(self, user_id: int, section: str) -> None:
        await self.pgsqldb.execute('''UPDATE "tickets_user" SET "section" = $1 WHERE "user_id" = $2''', section,
                                   user_id)

    async def query_user_exam_status(self, user_id: int) -> Optional[asyncpg.Record]:
        return await self.pgsqldb.query1(
            '''SELECT "problem_id", "baned", "bypass", "passed", "unlimited", "retries"
             FROM "exam_user_session" WHERE "user_id" = $1''',
            user_id)

    async def handle_start(self, _client: Client, msg: Message) -> None:
        q = await self.pgsqldb.query1('''SELECT "last_msg_sent" FROM "tickets_user" WHERE "user_id" = $1''',
                                      msg.chat.id)
        await msg.reply(_T(
            'Welcome to Google Hosts Telegram Ticket System\n\n'
            'ATTENTION:PLEASE DO NOT ABUSE THIS SYSTEM. Otherwise there is a possibility of getting blocked.\n\n'
            '/create - to create a new ticket\n'
            '/list - to list recent tickets\n'
            '/close - to close the ticket\n'
            '/cancel - to reset'),
            True)
        if q is None:
            await self.add_user(msg.chat.id)

    async def handle_create(self, client: Client, msg: Message) -> None:
        if await self.flood_check(client, msg):
            return
        q = await self.pgsqldb.query1(
            '''SELECT "hash" FROM "tickets" WHERE "user_id" = $1 AND "status" = 'open' LIMIT 1''',
            msg.chat.id)
        if q:
            await msg.reply(_T('UNABLE TO CREATE A NEW TICKET: An existing ticket is currently open.'), True)
            return
        sql_obj = await self.pgsqldb.query1('''SELECT "user_id" FROM "tickets_user" WHERE "user_id" = $1''',
                                            msg.chat.id)
        await (self.add_user if sql_obj is None else self.change_step)(msg.chat.id, CustomServiceBot.SELECT_SECTION)
        await msg.reply(_T('You are creating a new ticket.\n\nPlease choose the correct department.'), True,
                        reply_markup=self.generate_section_pad())

    async def handle_cancel(self, _client: Client, msg: Message) -> None:
        await self.change_step(msg.chat.id, CustomServiceBot.INIT_STATUS)
        await msg.reply(_T('Reset Successful'), reply_markup=ReplyKeyboardRemove())

    async def handle_reply(self, client: Client, msg: Message) -> None:
        if await self.flood_check(client, msg):
            return
        try:
            ticket_hash = self.get_hash_from_reply_msg(msg)
        except ValueError:
            return
        # print(self.get_hash_from_reply_msg(msg))
        sql_obj = await self.pgsqldb.query1(
            '''SELECT "status", "section" FROM "tickets" WHERE "hash" = $1 AND "user_id" = $2''',
            ticket_hash, msg.chat.id)
        if sql_obj is None or sql_obj['status'] == 'closed':
            await msg.reply(_T('TICKET NUMBER NOT FOUND or TICKET CLOSED. REPLY FUNCTION NO LONGER AVAILABLE.'), True)
            return
        await self._update_last_time(msg)
        await client.send_message(
            self.help_group,
            _T("\'NEW REPLY\n[ #{} ]:\nMESSAGE: {}").format(ticket_hash, TextParser(msg).parsed_msg),
            'html',
            reply_markup=self.generate_ticket_keyboard(ticket_hash, msg.chat.id, sql_obj['section'] != self.SECTION[0])
        )
        await msg.reply(_T('The new reply is added successfully!'))

    async def handle_msg(self, client: Client, msg: Message) -> None:
        sql_obj = await self.query_status(msg.chat.id)
        if sql_obj is None or sql_obj['step'] not in (CustomServiceBot.SELECT_SECTION, CustomServiceBot.SEND_QUESTION):
            if await self.flood_check(client, msg):
                return
            await msg.reply(_T('Please use bot command to interact.'))
            return
        if sql_obj['step'] == CustomServiceBot.SELECT_SECTION:
            if msg.text in self.SECTION:
                await self.change_step(msg.chat.id, CustomServiceBot.SEND_QUESTION, msg.text)
                await msg.reply(_T(
                    'Please describe your problem briefly(up to 500 characters)\n'
                    '(Please use external links to send pictures.):\n\n'
                    'ATTENTION: Receiving a confirmation message in return '
                    'indicates that the ticket is created successfully.\n\n'
                    'Use /cancel to cancel creating the ticket.'),
                    True, reply_markup=ReplyKeyboardRemove())
            else:
                await msg.reply(_T('Please use the menu below to choose the correct department.'), True)
        elif sql_obj['step'] == CustomServiceBot.SEND_QUESTION:
            if len(msg.text) > 500:
                await msg.reply(_T('The number of characters you have entered is larger than 500. Please re-enter.'),
                                True)
                return
            ticket_hash = self.hash_msg(msg)
            await self.pgsqldb.execute(*Ticket(msg, sql_obj['section'], 'open').sql)
            await self.change_step(msg.chat.id, CustomServiceBot.INIT_STATUS)
            await msg.reply(
                _T(
                    'The ticket is created successfully!\n[ #{ticket_id} ]\nDepartment: {section}\n'
                    'Message: \n{text}\n\nReply to this message to add a new reply to the ticket.').format(
                    ticket_id=ticket_hash,
                    text=TextParser(msg).parsed_msg,
                    section=sql_obj['section']
                ),
                parse_mode='html'
            )
            msg_id = (await client.send_message(
                self.help_group,
                _T('NEW TICKET\n[ #{} ]\nClick {} to check the user profile\nDepartment: {}\nMessage: \n{}').format(
                    ticket_hash,
                    TextParser.parse_user_html(msg.chat.id, _T('Here')),
                    sql_obj['section'],
                    TextParser(msg).parsed_msg
                ),
                'html',
                reply_markup=self.generate_ticket_keyboard(
                    ticket_hash,
                    msg.chat.id,
                    other=sql_obj['section'] != self.SECTION[0]
                )
            )).message_id
            if sql_obj['section'] == self.SECTION[0]:
                await client.send_message(
                    self.help_group,
                    await self.generate_user_status(msg.chat.id),
                    'html',
                    reply_to_message_id=msg_id
                )
        else:
            logger.error("throw! user_id: %d, sql_obj = %s", msg.chat.id, repr(sql_obj))

    @staticmethod
    async def generate_question_and_answer(user_session: asyncpg.Record) -> str:
        _text = 'Question: <code>{Q}</code>\n{question_type} Answer: <code>{A}</code>'.format(
            **await ProblemSet.get_instance().get(user_session['problem_id']),
            question_type='Except' if ProblemSet.get_instance().remove_punctuations.enable else 'Standard')
        if ProblemSet.get_instance().remove_punctuations.enable:
            _text += f'\nStandard Answer: <code>' \
                     f'{await ProblemSet.get_instance().get_origin(user_session["problem_id"])}</code>'
        return _text

    async def __generate_answer_history(self, user_id: int) -> str:
        sql_obj = await self.pgsqldb.query(
            '''SELECT "body", "timestamp" FROM "answer_history" WHERE "user_id" = $1 ORDER BY "id" DESC LIMIT 3''',
            user_id)
        if sql_obj is None:
            return 'QUERY ERROR (user_id => %d)' % user_id
        if ProblemSet.get_instance().remove_punctuations.enable:
            return '\n\n'.join('<code>{}</code> <pre>{}</pre>\nOriginal answer: <pre>{}</pre>'.format(
                x['timestamp'], ProblemSet.get_instance().remove_punctuations.replace(x['body']), x['body']) for x in
                               sql_obj)
        return '\n\n'.join(f'<code>{x["timestamp"]}</code> <pre>{x["body"]}</pre>' for x in sql_obj)

    async def _generate_answer_history(self, user_id: int, retries: int) -> str:
        sql_obj = await self.pgsqldb.query1('''SELECT COUNT(*) FROM "answer_history" WHERE "user_id" = $1''',
                                            user_id)
        if retries > 0 or sql_obj['count'] > 0:
            return '\n\nAnswer History:\n{}'.format(await self.__generate_answer_history(user_id))
        return ''

    async def generate_question_rate(self, user_session: Mapping[str, int]) -> str:
        problem_id = user_session['problem_id']
        total_count = (
            await self.pgsqldb.query1('''SELECT COUNT(*) FROM "exam_user_session" WHERE "problem_id" = $1''',
                                      problem_id))['count']
        correct_count = (await self.pgsqldb.query1(
            '''SELECT COUNT(*) FROM "exam_user_session" WHERE "problem_id" = $1 and "passed" = true''',
            problem_id))['count']
        rate = (correct_count / total_count) * 100
        return '\n\nProblem {} correct rate: {:.2f}%'.format(problem_id, rate)

    async def generate_user_status(self, user_id: int) -> str:
        user_status = await self.query_user_exam_status(user_id)
        return ('User {5} status:\nPassed exam: {0}\nBan status: {1}\nBypass: {2}\nUnlimited: {3}\n'
                'Retries: {4}\n\n{6}{7}{8}').format(
            self.return_bool_emoji(user_status['passed']),
            self.return_bool_emoji(user_status['baned']),
            self.return_bool_emoji(user_status['bypass']),
            self.return_bool_emoji(user_status['unlimited']),
            user_status['retries'],
            TextParser.parse_user_html(user_id),
            await self.generate_question_and_answer(user_status),
            await self.generate_question_rate(user_status),
            await self._generate_answer_history(user_id, user_status['retries'])  # type: ignore
        ) if user_status is not None else '<b>{}</b>'.format(_T('WARNING: THIS USER HAS NEVER USED THE BOT BEFORE.'))

    async def handle_other(self, _client: Client, msg: Message) -> None:
        if time.time() - await self._query_last_msg_send(msg) < 120:
            return
        await msg.reply(_T('Please use bot command to interact. TEXT ONLY.'))
        await self._update_last_msg_send(msg)

    async def handle_group(self, client: Client, msg: Message) -> None:
        if msg.reply_to_message.from_user.id != self.bot_id or (msg.text and msg.text.startswith('/')):
            return
        try:
            ticket_hash = self.get_hash_from_reply_msg(msg)
        except ValueError:
            return
        sql_obj = await self.pgsqldb.query1('''SELECT * FROM "tickets" WHERE "hash" = $1''', ticket_hash)
        if sql_obj is None:
            await msg.reply(_T('ERROR: TICKET NOT FOUND'))
            return
        if sql_obj['status'] == 'closed':
            await msg.reply(_T('This ticket is already closed.'))
            return
        try:
            msg_reply = await client.send_message(sql_obj['user_id'],
                                                  _T(
                                                      'NEW UPDATE!\n[ #{} ]\nMessage: \n{}\n\n'
                                                      'Reply to this message to add a new reply to the ticket').format(
                                                      ticket_hash, TextParser(msg).parsed_msg
                                                  ), 'html')
            await msg.reply(_T('REPLY [ #{} ] SUCCESSFUL').format(ticket_hash),
                            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                [
                                    InlineKeyboardButton(text=_T('recall'),
                                                         callback_data=f'del '
                                                                       f'{msg_reply.chat.id} {msg_reply.message_id}')
                                ]
                            ]))
            r = await self._query_last_time(msg)
            if time.time() - r < 120:
                await self._redis.delete(f'CSLAST_{sql_obj["user_id"]}')
        except pyrogram.errors.UserIsBlocked:
            await msg.reply(_T('Replay [ #{} ] fail,user blocked this bot.').format(ticket_hash))
        except pyrogram.errors.RPCError:
            await msg.reply(_T('Replay [ #{} ] fail, {}\n'
                               'View console to get more information').format(ticket_hash,
                                                                              traceback.format_exc().splitlines()[
                                                                                  -1]))
            raise

    @staticmethod
    def generate_confirm_keyboard(first: str, last: Union[str, Sequence[str]]) -> InlineKeyboardMarkup:
        if isinstance(last, list) or isinstance(last, tuple):
            lastg = last
        else:
            lastg = (str(last),)
        return InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text='Yes', callback_data=' '.join((first, 'confirm', *lastg))),
                InlineKeyboardButton(text='No', callback_data='cancel')
            ]
        ])

    async def generate_superuser_text(self, user_id: Union[str, int]) -> str:
        return '\n\n'.join(
            (_T("Please choose the section below"), await self.generate_user_status(user_id),  # type: ignore
             ' '.join((_T('Last refresh:'), str(datetime.now().replace(microsecond=0))))))

    async def generate_superuser_detail(self, user_id: Union[str, int]) -> Dict[str, _anyT]:
        return {
            'text': await self.generate_superuser_text(user_id),
            'reply_markup': InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(text=_T('BYPASS'), callback_data=f'bypass {user_id}'),
                        InlineKeyboardButton(text=_T('UNLIMITED RETRIES'), callback_data=f'unlimited {user_id}'),
                        InlineKeyboardButton(text=_T('REFRESH'), callback_data=f'refresh {user_id}')
                    ],
                    [
                        InlineKeyboardButton(text=_T('PASS'), callback_data=f'setpass {user_id}'),
                        InlineKeyboardButton(text=_T('RESET TIMES'), callback_data=f'reset {user_id}')
                    ],
                    [
                        InlineKeyboardButton(text=_T('RESET USER STATUS'), callback_data=f'renew {user_id}')
                    ],
                    [
                        InlineKeyboardButton(text='INSERT USER PROFILE', callback_data=f'insert {user_id}')
                    ],
                    [
                        InlineKeyboardButton(text=_T('Cancel'), callback_data='cancel')
                    ]
                ]
            )
        }

    async def query_user_status(self, _client: Client, msg: Message) -> None:
        if len(msg.command) < 2:
            await msg.reply('Arguments should contain user_id')
            return
        await self.get_user_status(int(msg.command[1]), msg.message_id)

    async def get_user_status(self, user_id: int, reply_to_message_id: int) -> None:
        await self.bot.send_message(
            self.help_group,
            parse_mode='html',
            reply_to_message_id=reply_to_message_id,
            **await self.generate_superuser_detail(user_id)
        )

    async def call_superuser_function(self, _client: Client, msg: Message) -> None:
        sql_obj = await self.pgsqldb.query1('''SELECT "user_id", "section" FROM "tickets" WHERE "hash" = $1''',
                                            self.get_hash_from_reply_msg(msg))
        if sql_obj['section'] != self.SECTION[0]:
            await msg.reply(_T("This ticket doesn't support admin menus for now."), True)
            return
        user_id = sql_obj['user_id']
        await self.get_user_status(user_id, msg.reply_to_message.message_id)

    async def confirm_dialog(self, msg: CallbackQuery, additional_msg: str, callback_prefix: str,
                             id_: Union[str]) -> None:
        asyncio.run_coroutine_threadsafe(msg.answer(), asyncio.get_event_loop())
        if len(id_) < 32:
            await self.bot.send_message(
                self.help_group,
                _T('Do you really want to {} {}?').format(additional_msg, TextParser.parse_user_markdown(id_)),
                'markdown',
                reply_markup=self.generate_confirm_keyboard(callback_prefix, id_)
            )
        else:
            await self.bot.send_message(
                self.help_group,
                _T('Do you really want to {} #{}?').format(additional_msg, id_),
                reply_markup=self.generate_confirm_keyboard(callback_prefix, id_)
            )

    async def confirm(self, client: Client, msg: CallbackQuery) -> None:
        if time.time() - msg.message.date > 15:
            raise TimeoutError()
        if msg.data.startswith('close'):
            ticket_id = msg.data.split()[-1]
            q = await self.pgsqldb.query1('''SELECT "user_id", "status" FROM "tickets" WHERE "hash" = $1''', ticket_id)
            if q is None:
                return await msg.answer(_T('TICKET NOT FOUND'), True)
            if q['status'] == 'closed':
                return await msg.answer(_T('This ticket is already closed.'))
            await self.pgsqldb.execute('''UPDATE "tickets" SET "status" = 'closed' WHERE "hash" = $1''', ticket_id)
            await msg.answer(_T('This ticket is already closed.'))
            await client.send_message(
                self.help_group,
                _T('UPDATE\n[ #{} ]\nThis ticket is closed by {}.').format(
                    ticket_id,
                    utils.TextParser.parse_user_markdown(
                        msg.from_user.id,
                        utils.TextParser.UserName(msg.from_user).full_name
                    )
                ),
                'markdown',
                reply_markup=self.generate_ticket_keyboard(ticket_id, q['user_id'], True)
            )
            await client.send_message(q['user_id'], _T('Your ticket [ #{} ] is closed').format(ticket_id))
        elif msg.data.startswith('block'):
            await self.pgsqldb.execute('''UPDATE "tickets_user" SET "banned" = true WHERE "user_id" = $1''',
                                       int(msg.data.split()[-1]))
            await msg.answer(_T('DONE!'))
            await self.bot.send_message(
                self.help_group,
                _T('blocked {}').format(TextParser.parse_user_markdown(msg.data.split()[-1], msg.data.split()[-1])),
                parse_mode='markdown',
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=_T('UNBAN'), callback_data='unban {}'.format(msg.data.split()[-1]))]
                ])
            )
        elif msg.data.startswith('send'):
            try:
                await self.send_link_callback(int(msg.data.split()[-1]), True)
                await msg.answer(_T('The invitation link is sent successfully.'))
            except:
                await client.send_message(self.help_group, traceback.format_exc(), disable_web_page_preview=True)
                await msg.answer(_T('Failed to send the invitation link. Please check the console.\n{}').format(
                    traceback.format_exc().splitlines()[-1]), True)
        elif msg.data.startswith('reset'):
            await self.pgsqldb.execute('''UPDATE "exam_user_session" SET "retries" = 0 WHERE "user_id" = $1''',
                                       int(msg.data.split()[-1]))
            await msg.answer('Retry times has been reset')
        elif msg.data.startswith('del'):
            try:
                await client.delete_messages(int(msg.data.split()[-2]), int(msg.data.split()[-1]))
                await msg.answer('message has been deleted')
            except:
                await client.send_message(self.help_group, traceback.format_exc(), disable_web_page_preview=True)
                await msg.answer(_T('Failed to delete the message. Please check the console.\n{}').format(
                    traceback.format_exc().splitlines()[-1]), True)
        elif msg.data.startswith('renew'):
            await self.pgsqldb.execute('''DELETE FROM "exam_user_session" WHERE "user_id" = $1''',
                                       int(msg.data.split()[-1]))
            await msg.answer(_T('DONE!'))
        elif msg.data.startswith('bypass'):
            await self.pgsqldb.execute('''UPDATE "exam_user_session" SET "bypass" = true WHERE "user_id" = $1''',
                                       int(msg.data.split()[-1]))
            await msg.answer(_T('DONE!'))
        elif msg.data.startswith('setpass'):
            await self.pgsqldb.execute('''UPDATE "exam_user_session" SET "passed" = true WHERE "user_id" = $1''',
                                       int(msg.data.split()[-1]))
            await msg.answer(_T('DONE!'))
        elif msg.data.startswith('unlimited'):
            await self.pgsqldb.execute('''UPDATE "exam_user_session" SET "unlimited" = true WHERE "user_id" = $1''',
                                       int(msg.data.split()[-1]))
            await msg.answer(_T('DONE!'))
        elif msg.data.startswith('insert'):
            await self.pgsqldb.execute('''INSERT INTO "exam_user_session" ("user_id", "problem_id") VALUES ($1, 2)''',
                                       int(msg.data.split()[-1]))
            await msg.answer(_T('DONE!'))
        await client.delete_messages(msg.message.chat.id, msg.message.message_id)

    async def send_confirm(self, _client: Client, msg: CallbackQuery) -> None:

        def make_msg_handle(additional_msg: str, callback_prefix: str):
            async def wrapper():
                await self.confirm_dialog(msg, additional_msg, callback_prefix, msg.data.split()[-1])

            return wrapper

        if msg.data.startswith('del'):
            await msg.answer('Please press again to make sure. If you really want to delete this reply', True)
            await self.bot.send_message(
                self.help_group,
                'Do you want to delete reply message to {}?'.format(
                    TextParser.parse_user_markdown(msg.data.split()[-2])),
                'markdown',
                reply_markup=self.generate_confirm_keyboard('del', msg.data[4:])
            )
        command_mapping = {
            'close': make_msg_handle(_T('close this ticket'), 'close'),
            'block': make_msg_handle(_T('block this user'), 'block'),
            'send': make_msg_handle(_T('send the link to'), 'send'),
            'reset': make_msg_handle(_T('reset retry times for'), 'reset'),
            'bypass': make_msg_handle(_T('set bypass for'), 'bypass'),
            'renew': make_msg_handle(_T('reset user status'), 'renew'),
            'setpass': make_msg_handle(_T('set pass'), 'setpass'),
            'unlimited': make_msg_handle(_T('set unlimited retries for'), 'unlimited'),
            'insert': make_msg_handle('insert new profile', 'insert')
        }
        for name, func in command_mapping.items():
            if msg.data.startswith(name):
                await func()
                break

    async def answer(self, client: Client, msg: CallbackQuery) -> None:
        if msg.data.startswith('cancel'):
            await client.edit_message_reply_markup(msg.message.chat.id, msg.message.message_id)
            await msg.answer('Canceled')
        elif msg.data.startswith('unban'):
            await self.pgsqldb.execute('''UPDATE "tickets_user" SET "banned" = false WHERE "user_id" = $1''',
                                       int(msg.data.split()[-1]))
            await msg.answer('UNBANED')
            await client.edit_message_reply_markup(msg.message.chat.id, msg.message.message_id)
        elif msg.data.startswith('refresh'):
            try:
                await client.edit_message_text(
                    msg.message.chat.id,
                    msg.message.message_id,
                    await self.generate_superuser_text(int(msg.data.split()[-1])),
                    'html',
                    reply_markup=msg.message.reply_markup
                )
            except pyrogram.errors.exceptions.bad_request_400.MessageNotModified:
                pass
            await msg.answer()
        elif 'confirm' in msg.data:
            try:
                await self.confirm(client, msg)
            except TimeoutError:
                await asyncio.gather(msg.answer('Confirmation time out'),
                                     client.edit_message_reply_markup(msg.message.chat.id, msg.message.message_id))
        elif any(msg.data.startswith(x) for x in
                 ('close', 'block', 'send', 'bypass', 'reset', 'unlimited', 'del', 'renew', 'setpass', 'insert')):
            await self.send_confirm(client, msg)
        else:
            try:
                raise ValueError(msg.data)
            except:
                await client.send_message(self.help_group, traceback.format_exc(), disable_web_page_preview=True)

    async def _query_last_time(self, msg: Message) -> int:
        return await self._query_redis_time(f'CSLAST_{msg.chat.id}')

    async def _query_last_msg_send(self, msg: Message) -> int:
        return await self._query_redis_time(f'CSLASTMSG_{msg.chat.id}')

    async def _query_redis_time(self, key: str) -> int:
        r = await self._redis.get(key)
        return 0 if r is None else int(r.decode())

    async def _update_redis_time(self, key: str) -> None:
        await self._redis.set(key, str(int(time.time())), expire=180)

    async def _update_last_time(self, msg: Message) -> None:
        await self._update_redis_time(f'CSLAST_{msg.chat.id}')

    async def _update_last_msg_send(self, msg: Message) -> None:
        await self._update_redis_time(f'CSLASTMSG_{msg.chat.id}')

    async def flood_check(self, _client: Client, msg: Message) -> bool:
        r = await self._query_last_time(msg)
        if time.time() - r < 120:
            if msg.text:
                logger.warning('Caught flood %s: %s', msg.chat.id, msg.text)
            await self._update_last_msg_send(msg)
            sq = await self.pgsqldb.query1('''SELECT "banned" FROM "tickets_user" WHERE "user_id" = $1''', msg.chat.id)
            if sq and sq['baned']:
                return await msg.reply(
                    _T('Due to privacy settings, you are temporarily unable to operate.')) is not None
            await msg.reply(_T('You are driving too fast. Please try again later.'))
            return True
        return False
