# -*- coding: utf-8 -*-
# utils.py
# Copyright (C) 2018-2020 github.com/googlehosts Group:Z
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
import ast
import asyncio
import concurrent.futures
import logging
import random
import string
import time
import traceback
from configparser import ConfigParser
from dataclasses import dataclass
from typing import (Dict, List, Mapping, Optional, Sequence, SupportsBytes,
                    Tuple, TypeVar, Union)

import aiomysql
from pyrogram import (Client, InlineKeyboardButton, InlineKeyboardMarkup,
                      Message, MessageEntity, User)

logger = logging.getLogger(__name__)

_rT = TypeVar('_rT', str, Optional[int], SupportsBytes)
_anyT = TypeVar('_anyT')
_kT = TypeVar('_kT', str, int)


class TextParser:
    class BuildMessage:
        def __init__(self, msg: Message):
            self.text: bytes = (msg.text if msg.text else msg.caption if msg.caption else '').encode('utf-16-le')
            self.chat_id: int = msg.chat.id
            self.entities: List[MessageEntity] = msg.entities if msg.text else msg.caption_entities
            self.user_name, self.user_id = TextParser.UserName(msg.from_user).get_name_id()
            self.message_id: int = msg.message_id
            try:
                self.forward_from: str = msg.forward_from_chat.title if msg.forward_from_chat else \
                    ('DELETED' if msg.forward_from.is_deleted else (msg.forward_from.first_name + (' {}'.format(
                        msg.forward_from.last_name) if msg.forward_from.last_name else ''))) if msg.forward_from else msg.forward_sender_name if msg.forward_sender_name else ''
            except TypeError:
                print(msg)
                self.forward_from = 'Error: unable to get the name of the account you wish to forward from'
            self.forward_fom_id: Optional[
                int] = msg.forward_from_chat.id if msg.forward_from_chat else msg.forward_from.id if msg.forward_from else None

    class UserName:
        def __init__(self, user: User):
            self.first_name: str = user.first_name
            self.last_name: str = user.last_name if user.last_name else ''
            self.full_name: str = user.first_name if self.last_name == '' else ' '.join(
                (self.first_name, self.last_name))
            self.id: int = user.id
            self.user: User = user

        def get_name_id(self) -> Tuple[str, int]:
            return self.full_name, self.id

        def __str__(self) -> str:
            return self.full_name

    _dict = {
        'italic': ('i', 'i'),
        'bold': ('b', 'b'),
        'code': ('code', 'code'),
        'pre': ('pre', 'pre'),
        'text_link': ('a href="{}"', 'a'),
        'strike': ('del', 'del'),
        'underline': ('u', 'u'),
        'text_mention': ('a href=tg://user?id={}', 'a')
    }

    filter_keyword = tuple(key for key, _ in _dict.items())

    def __init__(self):
        self._msg: Message = None
        self.parsed_msg: str = ''

    def parse_html_msg(self) -> str:
        result = []
        tag_stack = []
        if self._msg.entities is None:
            return self._msg.text.decode('utf-16-le')
        start_pos = set(_entity.offset * 2 for _entity in self._msg.entities if _entity.type in self.filter_keyword)
        if not len(start_pos):
            return self._msg.text.decode('utf-16-le')
        _close_tag_pos = -1
        _close_tag = ''
        _last_cut = 0
        for _pos in range(len(self._msg.text) + 1):
            while _close_tag_pos == _pos:
                result.append(self._msg.text[_last_cut:_pos])
                _last_cut = _pos
                result.append(f'</{_close_tag}>'.encode('utf-16-le'))
                if not len(tag_stack):
                    break
                _close_tag, _close_tag_pos = tag_stack.pop()
            if _pos in start_pos:
                result.append(self._msg.text[_last_cut:_pos])
                _last_cut = _pos
                for _entity in self._msg.entities:
                    if _entity.offset * 2 == _pos:
                        format_value = _entity.url
                        if format_value is None and _entity.user:
                            format_value = _entity.user.id
                        result.append(f'<{self._dict[_entity["type"]][0]}>'.format(format_value).encode('utf-16-le'))
                        tag_stack.append((self._dict[_entity.type][1], (_entity.offset + _entity.length) * 2))
                if _close_tag_pos <= _pos:
                    _close_tag, _close_tag_pos = tag_stack.pop()
        result.append(self._msg.text[_last_cut:])
        return b''.join(result).decode('utf-16-le')

    def parse_main(self) -> str:
        return self.parse_html_msg()

    def split_offset(self) -> str:
        return self.parsed_msg

    def get_full_message(self) -> str:
        return ''.join(('<b>',
                        self._msg.user_name[:30],
                        ' (\u21a9 {})'.format(self._msg.forward_from[:30]) if self._msg.forward_from != '' else '',
                        '</b>',
                        '<a href="https://t.me/c/',
                        str(-self._msg.chat_id - 1000000000000),
                        '/',
                        str(self._msg.message_id),
                        '">:</a> ',
                        self.parsed_msg
                        ))

    @staticmethod
    def parse_user(user_id: int, user_name: Optional[str] = None) -> str:
        if user_name is None:
            user_name = str(user_id)
        return f'[{user_name}](tg://user?id={user_id})'

    @staticmethod
    def parse_user_ex(user_id: int, user_name: Optional[str] = None) -> str:
        if user_name is None:
            user_name = str(user_id)
        return f'<a href="tg://user?id={user_id}">{user_name}</a>'

    @staticmethod
    def markdown_replace(name: str) -> str:
        for x in ('['):
            name = name.replace(x, ''.join(('\\', x)))
        return name


class MySQLdb:

    def __init__(
            self,
            host: str,
            user: str,
            password: str,
            db: str,
            charset: str = 'utf8mb4',
            cursorclass: aiomysql.Cursor = aiomysql.DictCursor
    ):
        self.logger: logging.Logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.DEBUG)
        self.host: str = host
        self.user: str = user
        self.password: str = password
        self.db: str = db
        self.charset: str = charset
        self.cursorclass: aiomysql.Cursor = cursorclass
        self.execute_lock: asyncio.Lock = asyncio.Lock()
        self.mysql_connection = None

    async def create_connect(self) -> None:
        self.mysql_connection = await aiomysql.create_pool(
            host=self.host,
            user=self.user,
            password=self.password,
            db=self.db,
            charset=self.charset,
            cursorclass=self.cursorclass,
        )

    @classmethod
    async def create(cls,
                     host: str,
                     user: str,
                     password: str,
                     db: str,
                     charset: str = 'utf8mb4',
                     cursorclass: aiomysql.Cursor = aiomysql.DictCursor,
                     ) -> 'MySQLdb':
        self = MySQLdb(host, user, password, db, charset, cursorclass)
        await self.create_connect()
        return self

    async def promise_query1(self, sql: str, args: Union[Sequence[_anyT], _anyT] = ()) -> Mapping[_kT, _rT]:
        obj = await self.query1(sql, args)
        if obj is None:
            raise RuntimeError()
        return obj

    async def query(self, sql: str, args: Union[Sequence[_anyT], _anyT] = ()) -> Tuple[Mapping[_kT, _rT], ...]:
        async with self.mysql_connection.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, args)
                return await cur.fetchall()

    async def query1(self, sql: str, args: Union[Sequence[_anyT], _anyT] = ()) -> Optional[Mapping[_kT, _rT]]:
        async with self.mysql_connection.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, args)
                return await cur.fetchone()

    async def execute(self, sql: str, args: Union[Sequence[_anyT], Sequence[Sequence[_anyT]], _anyT] = (),
                      many: bool = False) -> None:
        async with self.mysql_connection.acquire() as conn:
            async with conn.cursor() as cur:
                await (cur.executemany if many else cur.execute)(sql, args)
            await conn.commit()

    async def close(self) -> None:
        self.mysql_connection.close()
        await self.mysql_connection.wait_closed()

    async def insert_ex(self, id1: int, id2: int, user_id: Optional[int] = None) -> None:
        await self.execute(
            'INSERT INTO `msg_id` (`msg_id`, `target_id`, `timestamp`, `user_id`) VALUES (%s, %s, CURRENT_TIMESTAMP(), %s)',
            (id1, id2, user_id))

    async def insert(self, msg: Message, msg_2: Message) -> None:
        try:
            await self.insert_ex(msg.message_id, msg_2.message_id, msg.from_user.id)
        except:
            traceback.print_exc()
            await self.insert_ex(msg.message_id, msg_2.message_id)

    async def get_user_id(self, msg: Union[Message, int]) -> Optional[Mapping[_kT, _rT]]:
        return await self.query1(
            'SELECT `user_id` FROM `msg_id` WHERE `msg_id` = (SELECT `msg_id` FROM `msg_id` WHERE `target_id` = %s)',
            (msg if isinstance(msg, int) else msg.reply_to_message.message_id))

    async def get_id(self, msg_id: int, reverse: bool = False) -> Optional[int]:
        r = await self.query1('{} = %s'.format('SELECT `{}` FROM `msg_id` WHERE `{}`'.format(
            *(('target_id', 'msg_id') if not reverse else ('msg_id', 'target_id')))), msg_id)
        return r['target_id' if not reverse else 'msg_id'] if r else None

    async def get_reply_id(self, msg: Message) -> Optional[int]:
        return await self.get_id(msg.reply_to_message.message_id) if msg.reply_to_message else None

    async def get_reply_id_Reverse(self, msg: Message) -> Optional[int]:
        return await self.get_id(msg.reply_to_message.message_id, True) if msg.reply_to_message else None

    async def get_msg_name_history_channel_msg_id(self, msg: Message) -> int:
        return (await self.query1(
            'SELECT `channel_msg_id` FROM `username` WHERE `user_id` = (SELECT `user_id` FROM `msg_id` WHERE `target_id` = %s)',
            msg.reply_to_message.message_id))['channel_msg_id']

    async def insert_new_warn(self, user_id: int, msg: str, msg_id: Optional[int]) -> int:
        await self.execute("INSERT INTO `reasons` (`user_id`, `text`, `msg_id`) VALUE (%s, %s, %s)",
                           (user_id, msg, msg_id))
        return (await self.query1("SELECT LAST_INSERT_ID()"))['LAST_INSERT_ID()']

    async def delete_warn_by_id(self, warn_id: int) -> None:
        await self.execute("DELETE FROM `reasons` WHERE `user_id` = %s", warn_id)

    async def query_warn_by_user(self, user_id: int) -> int:
        return (await self.query1("SELECT COUNT(*) FROM `reasons` WHERE `user_id` = %s", user_id))['COUNT(*)']

    async def query_warn_reason_by_id(self, reason_id: int) -> str:
        return (await self.promise_query1("SELECT `text` FROM `reasons` WHERE `id` = %s", reason_id))['text']

    async def query_user_in_banlist(self, user_id: int) -> bool:
        return await self.query1("SELECT * FROM `banlist` WHERE `id` = %s", user_id) is not None


class InviteLinkTracker:
    @dataclass
    class _UserTracker:
        message_id: int
        timestamp: float

    def __init__(self, client: Client, problem_set: dict, chat_id: int):
        self.client: Client = client
        self.chat_id: int = chat_id
        self.user_dict: Dict[int, InviteLinkTracker._UserTracker] = {}
        self.revoke_time: int = problem_set['configs']['revoke_time'] + 10
        self.join_group_msg: str = problem_set['messages']['success_msg']
        self.tricket_msg: str = problem_set['messages']['join_group_message']
        self.last_revoke_time: float = 0.0
        self.current_link: str = ''
        self.stop_event: asyncio.Event = asyncio.Event()
        self.future: Optional[concurrent.futures.Future] = None

    def start(self) -> concurrent.futures.Future:
        if self.future is not None:
            return self.future
        self.future = asyncio.run_coroutine_threadsafe(self._boost_run(), asyncio.get_event_loop())
        return self.future

    async def do_revoke(self) -> None:
        self.current_link = await self.client.export_chat_invite_link(self.chat_id)
        await self.revoke_users()
        self.last_revoke_time = time.time()

    async def revoke_users(self) -> None:
        current_time = time.time()
        pending_delete = []
        need_update_user = asyncio.Queue()
        for user_id, user_tracker in self.user_dict.items():
            if current_time - user_tracker.timestamp > self.revoke_time:
                pending_delete.append(user_id)
            else:
                need_update_user.put_nowait((user_id, user_tracker.message_id))
        for user_id in pending_delete:
            self.user_dict.pop(user_id, None)
        while not need_update_user.empty():
            await self.client.edit_message_reply_markup(*need_update_user.get_nowait(),
                                                        reply_markup=self.generate_keyboard())
        del pending_delete, need_update_user, current_time

    def get(self) -> str:
        return self.current_link

    async def join(self, timeout: float = 0) -> None:
        if self.future is None:
            return
        if timeout > 0:
            while not self.future.done():
                for _ in range(int(timeout // .05)):
                    if self.future.done():
                        return
                    await asyncio.sleep(.05)
        else:
            await asyncio.sleep(0)

    @property
    def is_alive(self) -> bool:
        return self.future is not None and not self.future.done()

    def request_stop(self) -> None:
        self.stop_event.set()

    def generate_keyboard(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text='Join group', url=self.current_link)
                ]
            ]
        )

    async def send_link(self, chat_id: int, from_ticket: bool = False) -> None:
        self.user_dict.update(
            {
                chat_id: InviteLinkTracker._UserTracker(
                    # NOTE: KNOWN ISSUE, IF NEVER CONTACT FROM THIS BOT
                    (await self.client.send_message(
                        chat_id,
                        self.join_group_msg if from_ticket else self.tricket_msg,
                        'html',
                        reply_markup=self.generate_keyboard()
                    )).message_id,
                    time.time()
                )
            }
        )

    async def _boost_run(self) -> None:
        while not self.client.is_connected:
            await asyncio.sleep(0)
        await self.do_revoke()
        while not self.stop_event.is_set():
            try:
                if self.user_dict:
                    if time.time() - self.last_revoke_time > 30:
                        await self.do_revoke()
            except:
                traceback.print_exc()
            else:
                if not self.stop_event.is_set():
                    await asyncio.sleep(1)


def get_random_string(length: int = 8) -> str:
    return ''.join(random.choices(string.ascii_lowercase, k=length))


class _AuthSystem:

    def __init__(self, conn: MySQLdb):
        self.conn = conn
        self.authed_user: List[int] = []
        self.non_ignore_user: List[int] = []
        self.whitelist: List[int] = []

    async def init(self, owner: Optional[int] = None) -> None:
        sqlObj = await self.conn.query("SELECT * FROM `auth_user`")
        self.authed_user = [row['id'] for row in sqlObj if row['authorized'] == 'Y']
        self.non_ignore_user = [row['id'] for row in sqlObj if row['muted'] == 'N']
        self.whitelist = [row['id'] for row in sqlObj if row['whitelist'] == 'Y']
        if owner is not None and owner not in self.authed_user:
            self.authed_user.append(owner)

    @classmethod
    async def create(cls, conn: MySQLdb, owner: Optional[int] = None) -> '_AuthSystem':
        self = _AuthSystem(conn)
        await self.init(owner)
        return self

    def check_ex(self, user_id: int) -> bool:
        return user_id in self.authed_user

    async def add_user(self, user_id: int) -> None:
        self.authed_user.append(user_id)
        self.authed_user = list(set(self.authed_user))
        if self.query_user(user_id) is not None:
            await self.update_user(user_id, 'authorized', 'Y')
        else:
            await self.conn.execute("INSERT INTO `auth_user` (`id`, `authorized`) VALUE (%s, 'Y')", user_id)

    async def update_user(self, user_id: int, column_name: str, value: str) -> None:
        await self.conn.execute("UPDATE `auth_user` SET `{}` = %s WHERE `id` = %s".format(column_name),
                                (value, user_id))

    async def query_user(self, user_id: int) -> Optional[Mapping[_kT, _rT]]:
        return await self.conn.query1("SELECT * FROM `auth_user` WHERE `id` = %s", user_id)

    async def del_user(self, user_id: int) -> None:
        self.authed_user.remove(user_id)
        await self.update_user(user_id, 'authorized', 'N')

    def check_muted(self, user_id: int) -> bool:
        return user_id not in self.non_ignore_user

    async def unmute_user(self, user_id: int):
        self.non_ignore_user.append(user_id)
        self.non_ignore_user = list(set(self.non_ignore_user))
        await self.update_user(user_id, 'muted', 'N')

    async def mute_user(self, user_id: int) -> None:
        self.non_ignore_user.remove(user_id)
        await self.update_user(user_id, 'muted', 'Y')

    def check(self, user_id: int) -> bool:
        return self.check_ex(user_id) and not self.check_muted(user_id)

    def check_full(self, user_id: int) -> bool:
        return self.check_ex(user_id) or user_id in self.whitelist

    async def mute_or_unmute(self, r: str, chat_id: int) -> None:
        if not self.check_ex(chat_id): return
        try:
            await (self.mute_user if r == 'off' else self.unmute_user)(chat_id)
        except ValueError:
            pass


class AuthSystem(_AuthSystem):
    class_self = None

    @staticmethod
    def get_instance():
        if AuthSystem.class_self is None:
            raise RuntimeError('Instance not initialize')
        return AuthSystem.class_self

    @staticmethod
    async def initialize_instance(conn: MySQLdb, owner: int = None) -> 'AuthSystem':
        AuthSystem.class_self = await AuthSystem.create(conn, owner)
        return AuthSystem.class_self

    @classmethod
    async def create(cls, conn: MySQLdb, owner: Optional[int] = None) -> 'AuthSystem':
        self = AuthSystem(conn)
        await self.init(owner)
        return self

    @staticmethod
    async def config2mysqldb(config: ConfigParser, conn: MySQLdb) -> None:
        await conn.execute("TRUNCATE TABLE `auth_user`")
        authed_user = ast.literal_eval(config['fuduji']['auth_user'])
        ignore_user = ast.literal_eval(config['fuduji']['ignore_user'])
        whitelist = ast.literal_eval(config['fuduji']['whitelist']) if config.has_option('fuduji', 'whitelist') else []
        await conn.execute("INSERT INTO `auth_user` (`id`, `authorized`) VALUES (%s, 'Y')", ((x,) for x in authed_user),
                           True)
        for x in ignore_user:
            await conn.execute("UPDATE `auth_user` SET `muted` = 'Y' WHERE `id` = %s", x)
        for x in whitelist:
            if await conn.query1("SELECT * FROM `auth_user` WHERE `id` = %s", x) is not None:
                await conn.execute("UPDATE `auth_user` SET `whitelist` = 'Y' WHERE `id` = %s", x)
            else:
                await conn.execute("INSERT INTO `auth_user` (`id`, `whitelist`) VALUE (%s, 'Y')", x)


def get_language() -> str:
    config = ConfigParser()
    config.read('config.ini')
    return config.get('i18n', 'language', fallback='en_US')
