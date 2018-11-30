#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# main.py
# Copyright (C) 2018 github.com/googlehosts Group:Z
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
from configparser import ConfigParser
from datetime import datetime
import io
import os
import pymysql.cursors
from pyrogram import Client, Filters, ChatAction, api, MessageEntity, Message, PhotoSize, \
	Video, Animation, Document, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery, User, Sticker
import queue
import re
import telepot
from telepot.exception import TelegramError
from threading import Thread, Lock
import time
import traceback
import urllib3.exceptions

global bot_username, current_user_id

config = ConfigParser()
config.read('config.ini')

bot = telepot.Bot(config['account']['api_key'])
app = Client(session_name='session',
	api_id=config['account']['api_id'],
	api_hash=config['account']['api_hash'],
	app_version='repeater')
botapp = Client(session_name=config['account']['api_key'],
	api_id=config['account']['api_id'],
	api_hash=config['account']['api_hash'])

class auth_system(object):
	authed_user = eval(config['fuduji']['auth_user'])
	ignore_user = eval(config['fuduji']['ignore_user'])
	@staticmethod
	def check_ex(user_id: int):
		return user_id in auth_system.authed_user or user_id == int(config['account']['owner'])
	@staticmethod
	def add_user(user_id: int):
		auth_system.authed_user.append(int(user_id))
		auth_system.authed_user = list(set(auth_system.authed_user))
		config['fuduji']['auth_user'] = repr(auth_system.authed_user)
	@staticmethod
	def del_user(user_id: int):
		auth_system.authed_user.remove(user_id)
		config['fuduji']['auth_user'] = repr(auth_system.authed_user)
	@staticmethod
	def check_muted(user_id: int):
		return user_id in auth_system.ignore_user
	@staticmethod
	def mute_user(user_id: int):
		auth_system.ignore_user.append(user_id)
		auth_system.ignore_user = list(set(auth_system.ignore_user))
		config['fuduji']['ignore_user'] = repr(auth_system.ignore_user)
	@staticmethod
	def unmute_user(user_id: int):
		try:
			del auth_system.ignore_user[auth_system.ignore_user.index(user_id)]
			config['fuduji']['ignore_user'] = repr(auth_system.ignore_user)
			with open('config.ini', 'w') as fout: config.write(fout)
		except: pass
	@staticmethod
	def check(user_id: int):
		return auth_system.check_ex(user_id) and not auth_system.check_muted(user_id)

class mediaSender(Thread):
	Locker = Lock()
	def __init__(self):
		Thread.__init__(self, daemon=True)
		self.queue = queue.Queue()
		self.start()
	def put(self, iterable: tuple, check_mute: bool = False):
		if check_mute and auth_system.check_muted(iterable[1].from_user.id): return
		self.queue.put_nowait(iterable)
	@staticmethod
	def sticker_sender(func, chat_id: int, file_id: str, reply_to_message_id: int):
		try:
			return func(chat_id, file_id, reply_to_message_id = reply_to_message_id)
		except TelegramError as e:
			if e.description == 'Bad Request: reply message not found':
				return func(chat_id, file_id)
			else: raise e
	def sender(self, function, msg: Message, file_id_class: PhotoSize or Video or Animation or Document or Sticker, reversed_: bool):
		if not reversed_: time.sleep(2)
		while True:
			try:
				try:
					r = function(
							int(config['fuduji']['fudu_group']) if not reversed_ else int(config['fuduji']['target_group']),
							file_id_class if isinstance(file_id_class, io.BufferedReader) else file_id_class.file_id,
							build_html_parse(msg).call() if not reversed_ else build_html_parse(msg).split_offset(),
							reply_to_message_id=get_reply_id(msg),
							parse_mode='html'
						)
				except TelegramError as e:
					if e.description == 'Bad Request: reply message not found':
						r = function(
								int(config['fuduji']['fudu_group']) if not reversed_ else int(config['fuduji']['target_group']),
								file_id_class if isinstance(file_id_class, io.BufferedReader) else file_id_class.file_id,
								build_html_parse(msg).call() if not reversed_ else build_html_parse(msg).split_offset(),
								parse_mode='html'
							)
					else: raise e
				except TypeError as e:
					if 'got an unexpected keyword argument \'parse_mode\'' in e.args[0]:
						r = self.sticker_sender(
							function,
							int(config['fuduji']['fudu_group']) if not reversed_ else int(config['fuduji']['target_group']),
							file_id_class.file_id,
							get_reply_id(msg)
						)
					else: raise e
				finally:
					if isinstance(file_id_class, io.BufferedReader): file_id_class.close()
				if reversed_:
					conn.insert_ex(r['message_id'], msg.message_id)
				else:
					conn.insert(msg, r)
				break
			except api.errors.exceptions.flood_420.FloodWait as e:
				print('Pause {} seconds because flood 420 wait'.format(e.x))
				traceback.print_exc()
				time.sleep(e.x)
			except urllib3.exceptions.ReadTimeoutError:
				print('Pause 5 seconds for ReadTimeoutError')
				traceback.print_exc()
				time.sleep(5)
			except:
				traceback.print_exc()
				break
			finally:
				self.Locker.acquire(False)
				self.Locker.release()
	def run(self):
		while True:
			function, msg, file_id_class, reversed_ = self.queue.get()
			Thread(target=self.sender, args=(function, msg, file_id_class, reversed_), daemon=True).start()

media_sender = mediaSender()

def mute_or_unmute(r, chat_id):
	try: (auth_system.mute_user if r == 'off' else auth_system.unmute_user)(chat_id)
	except ValueError:
		pass

class mysqldb(object):
	def __init__(self, host: str, user: str, password: str, db: str, charset: str = 'utf8'):
		self.mysql_connection = pymysql.connect(host = host,
			user = user,
			password = password,
			db = db,
			charset = charset,
			cursorclass = pymysql.cursors.DictCursor
		)
		self.cursor = self.mysql_connection.cursor()
		self.lock = Lock()
	def commit(self):
		with self.lock:
			self.cursor.close()
			self.mysql_connection.commit()
			self.cursor = self.mysql_connection.cursor()
	def query1(self, sql: str, args: tuple = ()):
		self.execute(sql, args)
		return self.cursor.fetchone()
	def execute(self, sql: str, args: tuple = ()):
		with self.lock:
			self.cursor.execute(sql, args)
	def close(self):
		with self.lock:
			self.cursor.close()
			self.mysql_connection.commit()
	def insert_ex(self, id1: int, id2: int, user_id: int = 0):
		self.execute('INSERT INTO `msg_id` (`msg_id`, `target_id`, `timestamp`, `user_id`) VALUES ({}, {}, CURRENT_TIMESTAMP(), {})'.format(id1, id2, user_id))
		self.commit()
	def insert(self, msg: Message, msg_2: Message):
		try:
			self.insert_ex(msg.message_id, msg_2.message_id, msg.from_user.id)
		except:
			traceback.print_exc()
			self.insert_ex(msg.message_id, msg_2.message_id)
	def get_user_id(self, msg: Message or int):
		return self.query1('SELECT `user_id` FROM `msg_id` WHERE `msg_id` = (SELECT `msg_id` WHERE `target_id` = {})'.format(msg.reply_to_message.message_id if isinstance(msg, Message) else msg))

conn = mysqldb(config['database']['host'], config['database']['user'], config['database']['passwd'], config['database']['db_name'])

class build_html_parse(object):
	class gen_msg(object):
		def __init__(self, msg: Message):
			self.user_id = msg.from_user.id
			self.text = (msg.text if msg.text else msg.caption if msg.caption else '').encode('utf-16-le')
			self.entities = msg.entities if msg.text else msg.caption_entities
			self.user_name = ''.join((msg.from_user.first_name, (' {}'.format(msg.from_user.last_name) if msg.from_user.last_name else '')))
			try:
				self.forward_from = msg.forward_from_chat.title if msg.forward_from_chat else (msg.forward_from.first_name + (' {}'.format(msg.forward_from.last_name) if msg.forward_from.last_name else '')) if msg.forward_from else ''
			except TypeError:
				print(msg)
				self.forward_from = 'Error: unable to get the name of the account you wish to forward from'
			self.forward_fom_id = msg.forward_from_chat.id if msg.forward_from_chat else msg.forward_from.id if msg.forward_from else None
	_dict = {
		'italic': ('i', 'i'),
		'bold': ('b', 'b'),
		'code': ('code', 'code'),
		'pre': ('pre', 'pre'),
		'text_link': ('a href="{}"', 'a')
	}
	def __init__(self, msg: Message):
		self._msg = self.gen_msg(msg)
		self.parsed_msg = self.prase_main()
		if msg.chat.id == int(config['fuduji']['fudu_group']) and self.parsed_msg and self.parsed_msg.startswith('\\//'): self.parsed_msg = self.parsed_msg[1:]
		if msg.chat.id == int(config['fuduji']['target_group']) and self.parsed_msg: self.parsed_msg = self.parsed_msg.replace('@{}'.format(bot_username), '@{}'.format(config['fuduji']['replace_to_id']))
	@staticmethod
	def escape(text: str):
		return text.replace('&', '&amp;') if text is not None else ''
	@staticmethod
	def parse_tag(_entry: MessageEntity):
		r = build_html_parse._dict[_entry.type]
		return ''.join(('<', r[0].format('_entry.url'), '>\\n</', r[1], '>')).split('\\n')
	def _split_loc_func(self):
		self._split_loc_ex = [(_entry.offset * 2, (_entry.length + _entry.offset) * 2, self.parse_tag(_entry)) for _entry in self._msg.entities if _entry.type in ('italic', 'bold', 'code', 'pre', 'text_link')]
		self._split_loc = [item for loc in [[_split[0], _split[1]] for _split in self._split_loc_ex] for item in loc]
		self._tag = [_split[2] for _split in self._split_loc_ex]
		del self._split_loc_ex
	def prase_main(self):
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
	def split_offset(self):
		return self.parsed_msg
	def call(self):
		return ''.join(('<b>',
			self._msg.user_name[:30],
			' (↩️ {})'.format(self._msg.forward_from[:30]) if self._msg.forward_from != '' else '',
			'</b>: ',
			self.parsed_msg
		))
	@staticmethod
	def parse_user(user_id: int, user_name: str or None = None):
		if user_name is None:
			user_name = str(user_id)
		return ''.join(('[', user_name, '](tg://user?id=', str(user_id), ')'))

def get_id(msg_id: int, reverse: bool = False):
	r = conn.query1('{} = {}'.format('SELECT `{}` FROM `msg_id` WHERE `{}`'.format(*(('target_id', 'msg_id') if not reverse else ('msg_id', 'target_id'))), msg_id))
	return r['target_id' if not reverse else 'msg_id'] if r else None

def get_reply_id(msg: Message):
	return get_id(msg.reply_to_message.message_id) if msg.reply_to_message else None

def get_reply_id_Reverse(msg: Message):
	return get_id(msg.reply_to_message.message_id, True) if msg.reply_to_message else None

def process_imcoming_command(client: Client, msg: Message):
	r = re.match(r'^\/bot (on|off)$', msg.text)
	if r is None: r = re.match(r'^\/b(on|off)$', msg.text)
	if r:
		mute_or_unmute(r.group(1), msg.reply_to_message.from_user.id if msg.reply_to_message else msg.from_user.id)
		client.delete_messages(msg.chat.id, msg.message_id)
	if msg.text == '/status':
		user_id = msg.reply_to_message.from_user.id if msg.reply_to_message else msg.from_user.id
		status = [str(user_id), ' summary:\n\n', 'A' if auth_system.check_ex(user_id) else 'Una' ,'uthorized user\nBot stauts: ', '✅' if not auth_system.check_muted(user_id) else '❌']
		sleep_to_delete(client, msg.chat.id, (msg.message_id, client.send_message(msg.chat.id, ''.join(status), reply_to_message_id = msg.message_id).message_id))
		del status
	if msg.reply_to_message:
		if msg.text == '/del':
			try:
				client.forward_messages(msg.chat.id, int(config['fuduji']['target_group']), get_reply_id_Reverse(msg))
				bot.deleteMessage((int(config['fuduji']['target_group']), get_reply_id_Reverse(msg)))
				#client.delete_messages(int(config['fuduji']['target_group']), get_reply_id_Reverse(msg))
			except: client.send_message(msg.chat.id, traceback.format_exc(), disable_web_page_preview=True)
			try:
				client.delete_messages(int(config['fuduji']['fudu_group']), [msg.message_id, msg.reply_to_message.message_id])
			except: pass
		elif msg.text == '/getid':
			user_id = conn.get_user_id(msg)
			client.send_message(msg.chat.id, 'user_id is `{}`'.format(user_id['user_id'] if user_id is not None and user_id['user_id'] != 0 else 'ERROR_INVALID_USER_ID'), parse_mode='markdown', reply_to_message_id=msg.reply_to_message.message_id)
		elif msg.text == '/get' and get_reply_id_Reverse(msg):
			try:
				client.forward_messages(int(config['fuduji']['fudu_group']), int(config['fuduji']['target_group']), get_reply_id_Reverse(msg))
			except:
				client.send_message(msg.chat.id, traceback.format_exc().splitlines()[-1])
		elif msg.text == '/fw':
			conn.insert_ex(botapp.forward_messages(int(config['fuduji']['target_group']), int(config['fuduji']['target_group']), get_reply_id_Reverse(msg)).message_id, msg.message_id)
		elif msg.text.startswith('/ban'):
			user_id = conn.get_user_id(msg)
			if len(msg.text) == 4:
				restrict_time = 10 * 60
			else:
				r = re.match(r'^([1-9]\d*)(s|m|h|d)$', msg.text[5:])
				if r is not None:
					restrict_time = int(r.group(1)) * {'s': 1, 'm': 60, 'h': 60 * 60, 'd': 60 * 60 * 24}.get(r.group(2))
				else:
					botapp.send_message(msg.chat.id, 'Usage: `/ban` or `/ban <Duration>`', reply_to_message_id = msg.message_id, parse_mode = 'markdown')
			if user_id is not None and user_id['user_id'] != 0:
				botapp.send_message(
					msg.chat.id,
					'What can {} only do? Press the button below.\nThis confirmation message will expire after 20 seconds.'.format(
						build_html_parse.parse_user(user_id['user_id'])
					),
					reply_to_message_id = msg.message_id,
					parse_mode = 'markdown',
					reply_markup = InlineKeyboardMarkup(
						inline_keyboard = [
							[
								InlineKeyboardButton(text = 'READ', callback_data = 'res {} read'.format(restrict_time).encode())
							],
							[
								InlineKeyboardButton(text = 'SEND_MESSAGES', callback_data = 'res {} write'.format(restrict_time).encode()),
								InlineKeyboardButton(text = 'SEND_MEDIA', callback_data = 'res {} media'.format(restrict_time).encode())
							],
							[
								InlineKeyboardButton(text = 'SEND_STICKERS', callback_data = 'res {} stickers'.format(restrict_time).encode()),
								InlineKeyboardButton(text = 'EMBED_LINKS', callback_data = 'res {} link'.format(restrict_time).encode())
							],
							[
								InlineKeyboardButton(text = 'Cancel', callback_data = b'cancel')
							]
						]
					)
				)
			else:
				botapp.send_message(msg.chat.id, 'ERROR_INVALID_USER_ID', reply_to_message_id=msg.message_id)
		elif msg.text == '/kick':
			user_id = conn.get_user_id(msg)
			if user_id is not None and user_id['user_id'] != 0:
				botapp.send_message(msg.chat.id, 'Do you really want to kick {}?\nIf you really want to kick this user, press the button below.\nThis confirmation message will expire after 15 seconds.'.format(
						build_html_parse.parse_user(user_id['user_id'])
					),
					reply_to_message_id=msg.message_id, parse_mode='markdown', reply_markup=InlineKeyboardMarkup(inline_keyboard=[
					[InlineKeyboardButton(text='Yes, kick it', callback_data = b' '.join((b'kick', str(msg.from_user.id).encode(), str(user_id['user_id']).encode())))],
					[InlineKeyboardButton(text = 'No', callback_data = b'cancel')],
				]))
			else:
				botapp.send_message(msg.chat.id, 'ERROR_INVALID_USER_ID', reply_to_message_id = msg.message_id)
	else: # Not reply message
		if msg.text == '/ban':
			client.send_message(msg.chat.id, 'Reply to the user you wish to restrict, if you want to kick this user, please use the /kick command.')

class sleep_to_delete(Thread):
	def __init__(self, client: Client, chat_id: int, message_ids: int or tuple):
		Thread.__init__(self, daemon = True)
		self.client = client
		self.chat_id = chat_id
		self.message_ids = message_ids
		self.start()
	def run(self):
		time.sleep(5)
		self.client.delete_messages(self.chat_id, self.message_ids)

def auth_process(client: Client, msg: Message):
	if not auth_system.check_ex(msg.from_user.id):
		client.send_message(msg.chat.id, 'Permission denied', reply_to_message_id = msg.message_id)
		return
	if msg.reply_to_message.from_user:
		if auth_system.check_ex(msg.reply_to_message.from_user.id):
			client.send_message(msg.chat.id, 'Authorized', reply_to_message_id = msg.message_id)
		else:
			botapp.send_message(msg.chat.id, 'Do you want to authorize {} ?\nThis confirmation message will expire after 20 seconds.'.format(build_html_parse.parse_user(msg.reply_to_message.from_user.id)),
			reply_to_message_id = msg.message_id,
			parse_mode = 'markdown',
			reply_markup = InlineKeyboardMarkup(inline_keyboard = [
				[InlineKeyboardButton(text = 'Yes', callback_data = 'auth {} add'.format(msg.reply_to_message.from_user.id).encode()), InlineKeyboardButton(text = 'No', callback_data = b'cancel')]
			]))
	else: client.send_message(msg.chat.id, 'Unexpected error.', reply_to_message_id = msg.message_id)

class OperationTimeoutError(Exception): pass

class OperatorError(Exception): pass

@botapp.on_callback_query()
def handle_callback(client: Client, msg: CallbackQuery):
	try:
		if msg.data == 'cancel':
			client.answer_callback_query(msg.id, 'Canceled')
			client.edit_message_reply_markup(msg.message.chat.id, msg.message.message_id)
		elif msg.message.entities and msg.message.entities[0].type == 'text_mention':
			if msg.data.startswith('res'):
				if time.time() - msg.message.date > 20:
					raise OperationTimeoutError()
				_, dur, _type = msg.data.split()
				if client.restrict_chat_member(int(config['fuduji']['target_group']), msg.message.entities[0].user.id, int(time.time()) + int(dur), **(
						{
							'write': {'can_send_messages': True},
							'media': {'can_send_media_messages': True},
							'stickers': {'can_send_other_messages': True},
							'link': {'can_add_web_page_previews': True},
							'read': {}
						}.get(_type)
					)):
					client.answer_callback_query(msg.id, 'The user is restricted successfully.')
					client.edit_message_text(msg.message.chat.id, msg.message.message_id, 'Restrictions applied to {} Duration: {}s'.format(build_html_parse.parse_user(msg.message.entities[0].user.id), dur), parse_mode='markdown',
						reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(text = 'UNBAN', callback_data='unban {}'.format(msg.message.entities[0].user.id).encode())]]))
			elif msg.data.startswith('unban'):
				if client.restrict_chat_member(int(config['fuduji']['target_group']), int(msg.data.split()[-1]), 0, True, True, True, True):
					client.answer_callback_query(msg.id, 'Unban successfully')
					client.edit_message_reply_markup(msg.message.chat.id, msg.message.message_id)
			elif msg.data.startswith('kick'):
				if msg.from_user.id != int(msg.data.split()[-2]):
					raise OperatorError()
				if 'true' not in msg.data:
					if time.time() - msg.message.date > 15:
						raise OperationTimeoutError()
					client.answer_callback_query(msg.id, 'Please press again to make sure. Do you really want to kick {} ?'.format(msg.data.split()[-1]), True)
					client.edit_message_text(
						msg.message.chat.id, msg.message.message_id,
						'Press the button again to kick {}\nThis confirmation message will expire after 10 seconds.'.format(
							build_html_parse.parse_user(msg.data.split()[-1])
						),
						parse_mode = 'markdown',
						reply_markup = InlineKeyboardMarkup(inline_keyboard=[
							[InlineKeyboardButton(text = 'Yes, please.', callback_data = b' '.join((b'kick true', ' '.join(msg.data.split()[1:]).encode())))],
							[InlineKeyboardButton(text = 'Cancel', callback_data = b'cancel')]
						])
					)
				else:
					if time.time() - msg.message.edit_date > 10:
						raise OperationTimeoutError()
					client.kick_chat_member(int(config['fuduji']['target_group']), int(msg.data.split()[-1]))
					client.answer_callback_query(msg.id, 'Kicked {}'.format(msg.data.split()[-1]))
					client.edit_message_text(msg.message.chat.id, msg.message.message_id, 'Kicked {}'.format(build_html_parse.parse_user(msg.data.split()[-1])))
			elif msg.data.startswith('auth'):
				if time.time() - msg.message.date > 20:
					raise OperationTimeoutError()
				auth_system.add_user(msg.data.split()[1])
				client.answer_callback_query(msg.id, '{} added to the authorized group'.format(msg.data.split()[1]))
				client.edit_message_text(msg.message.chat.id, msg.message.message_id, '{} added to the authorized group'.format(msg.data.split()[1]))
				#client.edit_message_reply_markup(msg.message.chat.id, msg.message.message_id)
				with open('config.ini', 'w') as fout: config.write(fout)
	except OperationTimeoutError:
		client.answer_callback_query(msg.id, 'Confirmation time out')
		client.edit_message_reply_markup(msg.message.chat.id, msg.message.message_id)
	except OperatorError:
		client.answer_callback_query(msg.id, 'The operator should be {}.'.format(msg.data.split()[-2]), True)
	except:
		app.send_message(msg.message.chat.id, traceback.format_exc().splitlines()[-1])
		traceback.print_exc()

@app.on_message(Filters.chat(int(config['fuduji']['target_group'])) & ~Filters.user(int(config['fuduji']['bot_id'])) & Filters.edited)
def handle_edit(client: Client, msg: Message):
	if get_id(msg.message_id) is None:
		time.sleep(3)
		if get_id(msg.message_id) is None: return print('Editing Failure: get_id return None')
	try:
		(client.edit_message_text if msg.text else client.edit_message_caption)(int(config['fuduji']['fudu_group']), get_id(msg.message_id), build_html_parse(msg).call(), parse_mode='html')
	except:
		traceback.print_exc()

@app.on_message(Filters.chat(int(config['fuduji']['target_group'])) & Filters.new_chat_members)
def handle_new_member(client: Client, msg: Message):
	conn.insert(msg, client.send_message(int(config['fuduji']['fudu_group']), '`{}` join the group'.format('`,`'.join((str(user.id) for user in msg.new_chat_members))), parse_mode = 'markdown'))

@app.on_message(Filters.chat(int(config['fuduji']['target_group'])) & ~Filters.user(int(config['fuduji']['bot_id'])) & Filters.document)
def handle_document(client: Client, msg: Message):
	media_sender.put((client.send_document, msg, msg.document, False))

@app.on_message(Filters.chat(int(config['fuduji']['target_group'])) & ~Filters.user(int(config['fuduji']['bot_id'])) & Filters.photo)
def handle_photo(client: Client, msg: Message):
	media_sender.put((client.send_photo, msg, msg.photo.sizes[0], False))

@app.on_message(Filters.chat(int(config['fuduji']['target_group'])) & ~Filters.user(int(config['fuduji']['bot_id'])) & Filters.sticker)
def handle_sticker(client: Client, msg: Message):
	conn.insert(msg, client.send_message(int(config['fuduji']['fudu_group']), '{} {} sticker'.format(build_html_parse(msg).call(), msg.sticker.emoji), reply_to_message_id = get_reply_id(msg),
		parse_mode='html', disable_web_page_preview=True))

@app.on_message(Filters.chat(int(config['fuduji']['target_group'])) & ~Filters.user(int(config['fuduji']['bot_id'])) & Filters.animation)
def handle_gif(client: Client, msg: Message):
	media_sender.put((client.send_animation, msg, msg.animation, False))

@app.on_message(Filters.chat(int(config['fuduji']['target_group'])) & ~Filters.user(int(config['fuduji']['bot_id'])) & Filters.video)
def handle_video(client: Client, msg: Message):
	media_sender.put((client.send_video, msg, msg.video, False))

@app.on_message(Filters.chat(int(config['fuduji']['target_group'])) & ~Filters.user(int(config['fuduji']['bot_id'])) & Filters.text)
def handle_speak(client: Client, msg: Message):
	if msg.text.startswith('/') and re.match(r'^\/\w+(@\w*)?$', msg.text): return
	conn.insert(msg, client.send_message(int(config['fuduji']['fudu_group']), build_html_parse(msg).call(), reply_to_message_id=get_reply_id(msg),
		parse_mode='html', disable_web_page_preview=True))

@app.on_message(Filters.incoming & Filters.chat(int(config['fuduji']['fudu_group'])))
def handle_incoming(client: Client, msg: Message):
	client.send(api.functions.channels.ReadHistory(client.resolve_peer(msg.chat.id), msg.message_id))
	if msg.text == '/auth' and msg.reply_to_message: return auth_process(client, msg)
	if not auth_system.check_ex(msg.from_user.id): return
	if msg.text and re.match(r'^\/(bot (on|off)|del|get|fw|ban( ([1-9]\d*)(s|m|h|d))?|kick( confirm| -?\d+)?|status|bon|boff)$', msg.text):
		return process_imcoming_command(client, msg)
	if msg.text and msg.text.startswith('/') and re.match(r'^\/[a-zA-Z_]+(@[a-zA-Z_]+)?$', msg.text): return
	if auth_system.check_muted(msg.from_user.id) or (msg.text and msg.text.startswith('//')): return
	if msg.forward_from or msg.forward_from_chat:
		if msg.forward_from and (msg.forward_from.is_self or auth_system.check_ex(msg.forward_from.id)): return
		conn.insert_ex(bot.forwardMessage(int(config['fuduji']['target_group']), int(config['fuduji']['fudu_group']), msg.message_id)['message_id'], msg.message_id)
	elif msg.text and (not msg.edit_date or (msg.edit_date and get_id(msg.message_id, True) is None)):
		try:
			conn.insert_ex(bot.sendMessage(int(config['fuduji']['target_group']), build_html_parse(msg).split_offset(),
				reply_to_message_id=get_reply_id_Reverse(msg), parse_mode='html',
				disable_web_page_preview=True)['message_id'], msg.message_id)
		except TelegramError as e:
			if e.description == 'Bad Request: reply message not found':
				conn.insert_ex(bot.sendMessage(int(config['fuduji']['target_group']), build_html_parse(msg).split_offset(),
					parse_mode='html', disable_web_page_preview=True)['message_id'], msg.message_id)
			elif 'Bad Request: can\'t parse entities' in e.description:
				client.send_message('Catched error: {}\nPlease try again.'.format(e.description))
			else: raise
	elif msg.photo:
		media_sender.Locker.acquire()
		msg.download('tmp.jpg')
		media_sender.put((bot.sendPhoto, msg, open('downloads/tmp.jpg', 'rb'), True), True)
	elif msg.video:
		media_sender.put((bot.sendVideo, msg, msg.video, True), True)
	#elif msg.animation:
	#	media_sender.put((botapp.send_animation, msg, msg.animation, True), True)
	elif msg.document:
		media_sender.put((bot.sendDocument, msg, msg.document, True), True)
		client.sendan
	elif msg.edit_date:
		try:
			(bot.editMessageText if msg.text else bot.editMessageCaption)((int(config['fuduji']['target_group']),
				get_id(msg.message_id, True)), build_html_parse(msg).split_offset(), parse_mode='html')
		except: traceback.print_exc()
	elif msg.sticker:
		media_sender.put((bot.sendSticker, msg, msg.sticker, True), True)

def main():
	app.start()
	botapp.start()
	global current_user_id, bot_username
	current_user_id = app.get_me().id
	bot_username = botapp.get_me().username
	app.idle()

if __name__ == '__main__':
	main()