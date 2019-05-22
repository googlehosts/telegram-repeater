# -*- coding: utf-8 -*-
# tg_tools.py
# Copyright (C) 2018-2019 github.com/googlehosts Group:Z
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
from pyrogram import Message, User, MessageEntity, InlineKeyboardMarkup, InlineKeyboardButton, Client
from queue import Queue
from threading import Thread, Event, Lock
import time
import configparser
import pymysql
import traceback

class build_html_parse(object):
	class gen_msg(object):
		def __init__(self, msg: Message):
			self.text = (msg.text if msg.text else msg.caption if msg.caption else '').encode('utf-16-le')
			self.entities = msg.entities if msg.text else msg.caption_entities
			self.user_name, self.user_id = build_html_parse.user_name(msg.from_user).get_name_id()
			try:
				self.forward_from = msg.forward_from_chat.title if msg.forward_from_chat else ('DELETED' if msg.forward_from.is_deleted else (msg.forward_from.first_name + (' {}'.format(msg.forward_from.last_name) if msg.forward_from.last_name else ''))) if msg.forward_from else ''
			except TypeError:
				print(msg)
				self.forward_from = 'Error: unable to get the name of the account you wish to forward from'
			self.forward_fom_id = msg.forward_from_chat.id if msg.forward_from_chat else msg.forward_from.id if msg.forward_from else None
	class user_name(object):
		def __init__(self, user: User):
			self.first_name = user.first_name
			self.last_name = user.last_name if user.last_name else ''
			self.full_name = user.first_name if self.last_name == '' else ' '.join((self.first_name, self.last_name))
			self.id = user.id
			self.user = user
		def get_name_id(self):
			return self.full_name, self.id
		def __str__(self):
			return self.full_name
	_dict = {
		'italic': ('i', 'i'),
		'bold': ('b', 'b'),
		'code': ('code', 'code'),
		'pre': ('pre', 'pre'),
		'text_link': ('a href="{}"', 'a')
	}
	def __init__(self, msg: Message):
		raise NotImplementedError('This function should be overrided')
		self._msg = self.gen_msg(None)
		self.parsed_msg = ''
	@staticmethod
	def escape(text: str):
		return text
	@staticmethod
	def parse_tag(_entry: MessageEntity):
		r = build_html_parse._dict[_entry.type]
		return ''.join(('<', r[0].format(_entry.url), '>\\n</', r[1], '>')).split('\\n')
	def _split_loc_func(self):
		self._split_loc_ex = [(_entry.offset * 2, (_entry.length + _entry.offset) * 2, self.parse_tag(_entry)) for _entry in self._msg.entities if _entry.type in ('italic', 'bold', 'code', 'pre', 'text_link')]
		self._split_loc = [item for loc in [[_split[0], _split[1]] for _split in self._split_loc_ex] for item in loc]
		self._tag = [_split[2] for _split in self._split_loc_ex]
		del self._split_loc_ex
	def parse_main_ex(self):
		if self._msg.entities is None: return self.escape(self._msg.text.decode('utf-16-le'))
		self._split_loc_func()
		if not len(self._split_loc): return self.escape(self._msg.text.decode('utf-16-le'))
		self._split_loc.insert(0, 0)
		self._split_loc.append(len(self._msg.text))
		msg_list = []
		for index in range(1, len(self._split_loc)):
			self._msg.text[self._split_loc[index - 1]:self._split_loc[index]].decode('utf-16-le')
			if (index + 1) % 2:
				msg_list.append(''.join((self._tag[index // 2 - 1][0], self.escape(self._msg.text[self._split_loc[index - 1]:self._split_loc[index]].decode('utf-16-le')), self._tag[index // 2 - 1][1])))
			else:
				msg_list.append(self.escape(self._msg.text[self._split_loc[index - 1]:self._split_loc[index]].decode('utf-16-le')))
		return ''.join(msg_list)
	def parse_main(self):
		return self.parse_main_ex()
	def split_offset(self):
		return self.parsed_msg
	def call(self):
		return ''.join(('<b>',
			self._msg.user_name[:30],
			' (↩️ {})'.format(self._msg.forward_from[:30]) if self._msg.forward_from != '' else '',
			'</b>: ',
			self.parsed_msg
		)).replace('Now loading', '')
	@staticmethod
	def parse_user(user_id: int, user_name: str or None = None):
		if user_name is None:
			user_name = str(user_id)
		return ''.join(('[', user_name, '](tg://user?id=', str(user_id), ')'))
	@staticmethod
	def parse_user_ex(user_id: int, user_name: str or None = None):
		if user_name is None:
			user_name = str(user_id)
		return ''.join(('<a href="tg://user?id=', str(user_id), '">', user_name, '</a>'))
	@staticmethod
	def markdown_replace(name: str):
		for x in ('['):
			name = name.replace(x, ''.join(('\\', x)))
		return name

class mysqldb(object):
	def __init__(self, host: str, user: str, password: str, db: str, charset: str = 'utf8'):
		self.host = host
		self.user = user
		self.password = password
		self.db = db
		self.charset = charset
		self.last_execute = time.time()
		self.init_connection()
		self.lock = Lock()
		self.query_lock = Lock()
	def init_connection(self):
		self.mysql_connection = pymysql.connect(host = self.host,
			user = self.user,
			password = self.password,
			db = self.db,
			charset = self.charset,
			cursorclass = pymysql.cursors.DictCursor
		)
		self.cursor = self.mysql_connection.cursor()
	def commit(self):
		with self.lock:
			self.cursor.close()
			self.mysql_connection.commit()
			self.cursor = self.mysql_connection.cursor()
	def query1(self, sql: str, args: tuple = ()):
		with self.query_lock:
			self.execute(sql, args)
			return self.cursor.fetchone()
	def query3(self, sql: str, args: tuple = ()):
		with self.query_lock:
			self.execute(sql, args)
			return self.cursor.fetchmany(3)
	def execute(self, sql: str, args: tuple = (), *, exception: pymysql.Error or None = None):
		with self.lock:
			self.cursor.execute(sql, args)
			self.last_execute = time.time()
	def ping(self):
		self.mysql_connection.ping()
	def close(self):
		with self.lock:
			self.mysql_connection.commit()
			self.cursor.close()
			self.mysql_connection.close()
	def keep_alive(self, interval: float = 300.0):
		while True:
			if time.time() - self.last_execute > interval:
				self.ping()
				self.last_execute = time.time()
			time.sleep(interval - time.time() + self.last_execute + 5)

class revoke_thread(Thread):
	def __init__(self, queue: Queue, client: Client, generate_keyboard: callable):
		Thread.__init__(self, daemon=True)
		self.queue = queue
		self.client = client
		self.generate_keyboard = generate_keyboard
		self.start()
	def run(self):
		while not self.queue.empty():
			self.client.edit_message_reply_markup(*self.queue.get_nowait(), reply_markup=self.generate_keyboard())
		del self.queue

class invite_link_tracker(Thread):
	class user_tracker(object):
		def __init__(self, message_id: int, timestamp: float):
			self.message_id = message_id
			self.timestamp = timestamp
	def __init__(self, client: Client, problem_set: dict, chat_id: int):
		Thread.__init__(self, daemon = True)
		self.client = client
		self.chat_id = chat_id
		self.user_dict = {}
		self.revoke_time = problem_set['revoke_time'] + 10
		self.join_group_msg = problem_set['success_msg']
		self.tricket_msg = problem_set['ticket_bot']['text']
		self.last_revoke_time = 0.0
		self.current_link = ''
		self.stop_event = Event()
		self.start()
	def do_revoke(self):
		self.current_link = self.client.export_chat_invite_link(self.chat_id)
		self.revoke_users()
		self.last_revoke_time = time.time()
	def revoke_users(self):
		current_time = time.time()
		pending_delete = []
		need_update_user = Queue()
		for user_id, user_tracker_ in self.user_dict.items():
			if current_time - user_tracker_.timestamp > self.revoke_time:
				pending_delete.append(user_id)
			else:
				need_update_user.put_nowait((user_id, user_tracker_.message_id))
		for user_id in pending_delete:
			self.user_dict.pop(user_id, None)
		if not need_update_user.empty():
			revoke_thread(need_update_user, self.client, self.generate_keyboard).join()
		del pending_delete, need_update_user, current_time
	def get(self):
		return self.current_link
	def set_stop(self):
		self.stop_event.set()
	def generate_keyboard(self):
		return InlineKeyboardMarkup(
			inline_keyboard = [
				[
					InlineKeyboardButton(text = 'Join group', url = self.current_link)
				],
			]
		)
	def send_link(self, chat_id: int, from_ticket: bool = False):
		self.user_dict.update(
			{
				chat_id: invite_link_tracker.user_tracker(
					self.client.send_message(
						chat_id,
						self.join_group_msg if from_ticket else self.tricket_msg,
						'html',
						reply_markup = self.generate_keyboard()
					).message_id,
					time.time()
				)
			}
		)
	def run(self):
		# Wait start:
		while not self.client.is_started:
			time.sleep(0.01)
		# Do revoke first. (init process)
		self.do_revoke()
		while not self.stop_event.is_set():
			try:
				if len(self.user_dict) > 0:
					if time.time() - self.last_revoke_time > 30:
						self.do_revoke()
			except:
				traceback.print_exc()
			else:
				self.stop_event.wait(1)