# -*- coding: utf-8 -*-
# main.py
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
from configparser import ConfigParser
import hashlib
import os
import pymysql.cursors
from pyrogram import Client, Filters, ChatAction, api, MessageEntity, Message, PhotoSize, Photo, \
	Video, Animation, Document, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery, User, Sticker, \
	ReplyKeyboardMarkup, KeyboardButton, ForceReply, CallbackQueryHandler, MessageHandler, CallbackQuery
import queue
import re
import io
from threading import Thread, Lock
import time
import traceback
import base64, sys, json
from tg_tools import build_html_parse as bhp
import tg_tools
from customservice import custom_service_bot_class, join_group_verify_class

global bot_username
config = ConfigParser()
config.read('config.ini')

class auth_system_class(object):
	def __init__(self):
		self.authed_user = eval(config['fuduji']['auth_user'])
		self.ignore_user = eval(config['fuduji']['ignore_user'])
		self.whitelist = eval(config['fuduji']['whitelist']) if config.has_option('fuduji', 'whitelist') else []
		self.user_suffix = eval(config['user']['custom_suffix'])
		self.user_config = eval(config['user']['settings']) if config.has_option('user', 'settings') else {}
	def check_ex(self, user_id: int):
		return user_id in self.authed_user or user_id == int(config['account']['owner'])
	def add_user(self, user_id: int):
		self.authed_user.append(int(user_id))
		self.authed_user = list(set(self.authed_user))
		config['fuduji']['auth_user'] = repr(self.authed_user)
	def del_user(self, user_id: int):
		self.authed_user.remove(user_id)
		config['fuduji']['auth_user'] = repr(self.authed_user)
	def check_muted(self, user_id: int):
		return user_id in self.ignore_user
	def mute_user(self, user_id: int):
		self.ignore_user.append(user_id)
		self.ignore_user = list(set(self.ignore_user))
		config['fuduji']['ignore_user'] = repr(self.ignore_user)
	def unmute_user(self, user_id: int):
		try:
			del self.ignore_user[self.ignore_user.index(user_id)]
			config['fuduji']['ignore_user'] = repr(self.ignore_user)
			with open('config.ini') as fout: config.write(fout)
		except: pass
	def check(self, user_id: int):
		return self.check_ex(user_id) and not self.check_muted(user_id)
	def check_full(self, user_id: int):
		return self.check_ex(user_id) or user_id in self.whitelist
	def set_suffix(self, user_id: int, suffix: str):
		self.user_suffix[user_id] = suffix
		config['user']['custom_suffix'] = repr(self.user_suffix)
	def get_suffix(self, user_id: int):
		return self.user_suffix.get(user_id, '') if self.user_config.get(user_id, {}).get('suffix', False) else ''
	def mute_or_unmute(self, r: str, chat_id: int):
		if not self.check_ex(chat_id): return
		try: (self.mute_user if r == 'off' else self.unmute_user)(chat_id)
		except ValueError:
			pass

auth_system = auth_system_class()

class build_html_parse(bhp):
	def __init__(self, msg: Message):
		self._msg = self.gen_msg(msg)
		self.parsed_msg = self.parse_main()
		if msg.chat.id == int(config['fuduji']['fudu_group']) and self.parsed_msg and self.parsed_msg.startswith('\\//'): self.parsed_msg = self.parsed_msg[1:]
		if msg.chat.id == int(config['fuduji']['target_group']) and self.parsed_msg: self.parsed_msg = self.parsed_msg.replace('@{}'.format(bot_username), '@{}'.format(config['fuduji']['replace_to_id']))

class media_path(object):
	def __init__(self, path: str):
		self.path = path

class mysqldb(tg_tools.mysqldb):
	def __init__(self, host: str, user: str, password: str, db: str, emerg_send_message: callable, charset: str = 'utf8'):
		tg_tools.mysqldb.__init__(self, host, user, password, db, charset)
		self.emerg_send_message = emerg_send_message
	def insert_ex(self, id1: int, id2: int, user_id: int = 0):
		self.execute('INSERT INTO `msg_id` (`msg_id`, `target_id`, `timestamp`, `user_id`) VALUES ({}, {}, CURRENT_TIMESTAMP(), {})'.format(id1, id2, user_id))
		self.commit()
	def insert(self, msg: Message, msg_2: Message):
		try:
			self.insert_ex(msg.message_id, msg_2.message_id, msg.from_user.id)
			self.commit()
		except:
			traceback.print_exc()
			self.insert_ex(msg.message_id, msg_2.message_id)

	def get_user_id(self, msg: Message or int):
		return self.query1('SELECT `user_id` FROM `msg_id` WHERE `msg_id` = (SELECT `msg_id` WHERE `target_id` = {})'.format(msg.reply_to_message.message_id if isinstance(msg, Message) else msg))

	def get_id(self, msg_id: int, reverse: bool = False):
		r = self.query1('{} = {}'.format('SELECT `{}` FROM `msg_id` WHERE `{}`'.format(*(('target_id', 'msg_id') if not reverse else ('msg_id', 'target_id'))), msg_id))
		return r['target_id' if not reverse else 'msg_id'] if r else None

	def get_reply_id(self, msg: Message):
		return self.get_id(msg.reply_to_message.message_id) if msg.reply_to_message else None

	def get_reply_id_Reverse(self, msg: Message):
		return self.get_id(msg.reply_to_message.message_id, True) if msg.reply_to_message else None

class mediaSender(Thread):
	Locker = Lock()
	def __init__(self, send_message: callable, conn: mysqldb):
		Thread.__init__(self, daemon = True)
		self.queue = queue.Queue()
		self.send_message = send_message
		self.conn = conn
		self.start()
	def put(self, iterable: tuple, check_mute: bool = False):
		if check_mute and auth_system.check_muted(iterable[1].from_user.id): return
		self.queue.put_nowait(iterable)
	@staticmethod
	def sticker_sender(func: callable, chat_id: int, file_id: str, reply_to_message_id: int):
		return func(chat_id, file_id, reply_to_message_id = reply_to_message_id)
	def sender(self, function: callable, msg: Message, file_id_class: PhotoSize or Video or Animation or Document or Sticker, reversed_: bool):
		if not reversed_: time.sleep(2)
		while True:
			try:
				try:
					r = function(
							int(config['fuduji']['fudu_group']) if not reversed_ else int(config['fuduji']['target_group']),
							file_id_class.path if isinstance(file_id_class, media_path) else file_id_class.file_id,
							build_html_parse(msg).call() if not reversed_ else build_html_parse(msg).split_offset(),
							reply_to_message_id=self.conn.get_reply_id(msg),
							parse_mode='html'
						)
				except TypeError as e:
					if 'got an unexpected keyword argument \'parse_mode\'' in e.args[0]:
						r = self.sticker_sender(
							function,
							int(config['fuduji']['fudu_group']) if not reversed_ else int(config['fuduji']['target_group']),
							file_id_class.file_id,
							self.conn.get_reply_id(msg)
						)
					else: raise e
				finally:
					if isinstance(file_id_class, io.BufferedReader): file_id_class.close()
				if reversed_:
					self.conn.insert_ex(r['message_id'], msg.message_id)
				else:
					self.conn.insert(msg, r)
				break
			except api.errors.exceptions.flood_420.FloodWait as e:
				print('Pause {} seconds because flood 420 wait'.format(e.x))
				traceback.print_exc()
				time.sleep(e.x)
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

def extern_load_problem_set():
	try:
		with open('problem_set.json', encoding='utf8') as fin:
			problem_set = json.load(fin)
		if len(problem_set['problem_set']) == 0:
			print('Problem set length is 0')
	except:
		traceback.print_exc()
		print('Error in reading problem set', file=sys.stderr)
		problem_set = {}
	return problem_set

class sleep_to_delete(Thread):
	def __init__(self, client: Client, chat_id: int, message_ids: int):
		Thread.__init__(self, daemon = True)
		self.client = client
		self.chat_id = chat_id
		self.message_ids = message_ids
		self.start()
	def run(self):
		time.sleep(5)
		self.client.delete_messages(self.chat_id, self.message_ids)

class OperationTimeoutError(Exception): pass

class OperatorError(Exception): pass

class bot_controller(object):

	def __init__(self):
		self.problems_load()
		self.target_group = int(config['fuduji']['target_group'])
		self.fudu_group = int(config['fuduji']['fudu_group'])

		self.bot_id = int(config['account']['api_key'].split(':')[0])
		self.emerg_contact = eval(config['account']['emerg_contact']) \
			if config.has_option('account', 'emerg_contact') and config['account']['emerg_contact'] != '' else \
			int(config['account']['owner'])
		self.app = Client(
			session_name = 'session',
			api_id = config['account']['api_id'],
			api_hash = config['account']['api_hash'],
			app_version = 'repeater'
		)
		self.botapp = Client(
			session_name = config['account']['api_key'],
			api_id = config['account']['api_id'],
			api_hash = config['account']['api_hash']
		)

		self.conn = mysqldb(config['database']['host'], config['database']['user'], config['database']['passwd'], config['database']['db_name'], self.emerg_contact)
		self.media_sender = mediaSender(self.app.send_message, self.conn)
		self.join_group_verify = join_group_verify_class(self.conn, self.botapp, self.target_group, extern_load_problem_set)
		self.revoke_tracker_thread = self.join_group_verify.get_revoke_tracker_thread()
		self.custom_service = custom_service_bot_class(config, self.conn, self.revoke_tracker_thread.send_link)
		self.db_keepAlive = Thread(target = self.conn.keep_alive, daemon = True)
		self.db_keepAlive.start()

	def init(self):
		global bot_username
		bot_username = self.botapp.get_me().username

	def problems_load(self):
		self.problem_set = extern_load_problem_set()

	def idle(self):
		return self.app.idle()

	def start(self):
		self.app.add_handler(MessageHandler(self.handle_edit, Filters.chat(self.target_group) & ~Filters.user(self.bot_id) & Filters.edited))
		self.app.add_handler(MessageHandler(self.handle_new_member, Filters.chat(self.target_group) & Filters.new_chat_members))
		self.app.add_handler(MessageHandler(self.handle_document, Filters.chat(self.target_group) & ~Filters.user(self.bot_id) & Filters.document))
		self.app.add_handler(MessageHandler(self.handle_photo, Filters.chat(self.target_group) & ~Filters.user(self.bot_id) & Filters.photo))
		self.app.add_handler(MessageHandler(self.handle_sticker, Filters.chat(self.target_group) & ~Filters.user(self.bot_id) & Filters.sticker))
		self.app.add_handler(MessageHandler(self.handle_gif, Filters.chat(self.target_group) & ~Filters.user(self.bot_id) & Filters.animation))
		self.app.add_handler(MessageHandler(self.handle_video, Filters.chat(self.target_group) & ~Filters.user(self.bot_id) & Filters.video))
		self.app.add_handler(MessageHandler(self.handle_speak, Filters.chat(self.target_group) & ~Filters.user(self.bot_id) & Filters.text))
		self.app.add_handler(MessageHandler(self.handle_incoming, Filters.incoming & Filters.chat(self.fudu_group)))
		self.botapp.add_handler(CallbackQueryHandler(self.handle_callback))
		self.join_group_verify.init()
		self.app.start()
		self.botapp.start()
		self.init()
		self.custom_service.start()

	def stop(self):
		self.revoke_tracker_thread.set_stop()
		self.revoke_tracker_thread.join(1.5)
		if self.revoke_tracker_thread.is_alive():
			print('[WARN] revoke_tracker_thread still running!')
		self.custom_service.stop()
		self.botapp.stop()
		self.app.stop()

	def emerg_send_message(self, msg_str: str):
		'''
			Send message to emergancy contacts.
		'''
		if isinstance(self.emerg_contact, int):
			self.app.send_message(self.emerg_contact, msg_str, 'html')
		else:
			for user_id in self.emerg_contact:
				self.app.send_message(user_id, msg_str, 'html')

	def process_imcoming_command(self, client: Client, msg: Message):
		r = re.match(r'^\/bot (on|off)$', msg.text)
		if r is None: r = re.match(r'^\/b?(on|off)$', msg.text)
		if r:
			if not auth_system.check_ex(msg.reply_to_message.from_user.id if msg.reply_to_message else msg.from_user.id): return
			auth_system.mute_or_unmute(r.group(1), msg.reply_to_message.from_user.id if msg.reply_to_message else msg.from_user.id)
			client.delete_messages(msg.chat.id, msg.message_id)
		if msg.text == '/status':
			user_id = msg.reply_to_message.from_user.id if msg.reply_to_message else msg.from_user.id
			status = [str(user_id), ' summary:\n\n', 'A' if auth_system.check_ex(user_id) else 'Una' ,'uthorized user\nBot status: ', '✅' if not auth_system.check_muted(user_id) else '❌']
			sleep_to_delete(client, msg.chat.id, (msg.message_id, msg.reply(''.join(status), True).message_id))
			del status
		elif msg.text.startswith('/p'):
			if msg.text.startswith('/promote'):
				if len(msg.text.split()) == 1:
					if msg.reply_to_message is None or not auth_system.check_ex(msg.reply_to_message.from_user.id):
						self.botapp.send_message(msg.chat.id, 'Please reply to an Authorized user.', reply_to_message_id = msg.message_id)
						return
					user_id = msg.reply_to_message.from_user.id
				else:
					user_id = int(msg.text.split()[1])
				self.botapp.send_message(msg.chat.id, 'Please use bottom to make sure you want to add {} to Administrators'.format(build_html_parse.parse_user(user_id)),
					parse_mode = 'markdown',
					reply_to_message_id = msg.message_id,
					reply_markup = InlineKeyboardMarkup(inline_keyboard = [
						[
							InlineKeyboardButton(text = 'Yes, confirm', callback_data = 'promote {}'.format(user_id).encode())
						],
						[
							InlineKeyboardButton(text = 'Cancel', callback_data = b'cancel d')
						]
					]))
			else:
				if not auth_system.check_ex(msg.from_user.id): return
				self.botapp.promote_chat_member(self.target_group, int(msg.from_user.id), True, can_delete_messages = True, can_restrict_members = True, can_invite_users = True, can_pin_messages = True, can_promote_members = True)
				self.botapp.send_message(msg.chat.id, '[Emergency]: Privileges has been promoted', reply_to_message_id = msg.message_id)
			return
		if msg.reply_to_message:
			if msg.text == '/del':
				try:
					client.forward_messages(msg.chat.id, self.target_group, self.conn.get_reply_id_Reverse(msg))
					self.botapp.delete_messages(self.target_group, self.conn.get_reply_id_Reverse(msg))
				except: client.send_message(msg.chat.id, traceback.format_exc(), disable_web_page_preview=True)
				try:
					client.delete_messages(int(config['fuduji']['fudu_group']), [msg.message_id, msg.reply_to_message.message_id])
				except: pass
			elif msg.text == '/getid':
				user_id = self.conn.get_user_id(msg)
				client.send_message(msg.chat.id, 'user_id is `{}`'.format(user_id['user_id'] if user_id is not None and user_id['user_id'] != 0 else 'ERROR_INVALID_USER_ID'), parse_mode='markdown', reply_to_message_id=msg.reply_to_message.message_id)
			elif msg.text == '/get' and self.conn.get_reply_id_Reverse(msg):
				try:
					client.forward_messages(int(config['fuduji']['fudu_group']), self.target_group, self.conn.get_reply_id_Reverse(msg))
				except:
					client.send_message(msg.chat.id, traceback.format_exc().splitlines()[-1])
			elif msg.text == '/getn':
				pass
			elif msg.text == '/fw':
				self.conn.insert_ex(self.botapp.forward_messages(self.target_group, self.target_group, self.conn.get_reply_id_Reverse(msg)).message_id, msg.message_id)
			elif msg.text.startswith('/ban'):
				user_id = self.conn.get_user_id(msg)
				if len(msg.text) == 4:
					restrict_time = 0
				else:
					r = re.match(r'^([1-9]\d*)(s|m|h|d)$', msg.text[5:])
					if r is not None:
						restrict_time = int(r.group(1)) * {'s': 1, 'm': 60, 'h': 60 * 60, 'd': 60 * 60 * 24}.get(r.group(2))
					else:
						self.botapp.send_message(msg.chat.id, 'Usage: `/ban` or `/ban <Duration>`', reply_to_message_id = msg.message_id, parse_mode = 'markdown')
				if user_id is not None and user_id['user_id'] != 0:
					if user_id['user_id'] not in auth_system.whitelist:
						self.botapp.send_message(
							msg.chat.id,
							'What can {} only do? Press the button below.\nThis confirmation message will expire after 20 seconds.'.format(
								build_html_parse.parse_user(user_id['user_id'])
							),
							reply_to_message_id = msg.message_id,
							parse_mode = 'markdown',
							reply_markup = InlineKeyboardMarkup(
								inline_keyboard = [
									[
										InlineKeyboardButton(text = 'READ', callback_data = 'res {} read {}'.format(restrict_time, user_id['user_id']).encode())
									],
									[
										InlineKeyboardButton(text = 'SEND_MESSAGES', callback_data = 'res {} write {}'.format(restrict_time, user_id['user_id']).encode()),
										InlineKeyboardButton(text = 'SEND_MEDIA', callback_data = 'res {} media {}'.format(restrict_time, user_id['user_id']).encode())
									],
									[
										InlineKeyboardButton(text = 'SEND_STICKERS', callback_data = 'res {} stickers {}'.format(restrict_time, user_id['user_id']).encode()),
										InlineKeyboardButton(text = 'EMBED_LINKS', callback_data = 'res {} link {}'.format(restrict_time, user_id['user_id']).encode())
									],
									[
										InlineKeyboardButton(text = 'Cancel', callback_data = b'cancel')
									]
								]
							)
						)
					else:
						self.botapp.send_message(msg.chat.id, 'ERROR_WHITELIST_USER_ID', reply_to_message_id=msg.message_id)
				else:
					self.botapp.send_message(msg.chat.id, 'ERROR_INVALID_USER_ID', reply_to_message_id=msg.message_id)
			elif msg.text == '/kick':
				user_id = self.conn.get_user_id(msg)
				if user_id is not None and user_id['user_id'] != 0:
					if user_id['user_id'] not in auth_system.whitelist:
						self.botapp.send_message(msg.chat.id, 'Do you really want to kick {}?\nIf you really want to kick this user, press the button below.\nThis confirmation message will expire after 15 seconds.'.format(
								build_html_parse.parse_user(user_id['user_id'])
							),
							reply_to_message_id = msg.message_id,
							parse_mode='markdown',
							reply_markup=InlineKeyboardMarkup(
								inline_keyboard = [
									[
										InlineKeyboardButton(text='Yes, kick it', callback_data = b' '.join((b'kick', str(msg.from_user.id).encode(), str(user_id['user_id']).encode())))
									],
									[
										InlineKeyboardButton(text = 'No', callback_data = b'cancel')
									],
								]
							)
						)
					else:
						self.botapp.send_message(msg.chat.id, 'ERROR_WHITELIST_USER_ID', reply_to_message_id=msg.message_id)
				else:
					self.botapp.send_message(msg.chat.id, 'ERROR_INVALID_USER_ID', reply_to_message_id = msg.message_id)
		else: # Not reply message
			if msg.text == '/ban':
				client.send_message(msg.chat.id, 'Reply to the user you wish to restrict, if you want to kick this user, please use the /kick command.')
			elif msg.text == '/join':
				pass
			elif msg.text.startswith('/set'):
				auth_system.user_suffix[msg.from_user.id] = msg.text.split()[-1]
				client.send_message(msg.chat.id, 'Set suffix to `{}`'.format(msg.text.split()[-1]), 'markdown', reply_to_message_id = msg.message_id)

	def func_auth_process(self, client: Client, msg: Message):
		if not auth_system.check_ex(msg.from_user.id):
			msg.reply('Permission denied')
			return
		if msg.reply_to_message.from_user:
			if auth_system.check_ex(msg.reply_to_message.from_user.id):
				msg.reply('Authorized')
			else:
				self.botapp.send_message(
					msg.chat.id,
					'Do you want to authorize {} ?\nThis confirmation message will expire after 20 seconds.'.format(
						build_html_parse.parse_user(msg.reply_to_message.from_user.id)
					),
					reply_to_message_id = msg.message_id,
					parse_mode = 'markdown',
					reply_markup = InlineKeyboardMarkup(
						inline_keyboard = [
							[
								InlineKeyboardButton(text = 'Yes', callback_data = 'auth {} add'.format(msg.reply_to_message.from_user.id).encode()),
								InlineKeyboardButton(text = 'No', callback_data = b'cancel')
							]
						]
					)
				)
		else: client.send_message(msg.chat.id, 'Unexpected error.', reply_to_message_id = msg.message_id)

	def cross_group_forward_request(self, msg: Message):
		kb = [
				[InlineKeyboardButton(text = 'Yes, I know what I\'m doing.', callback_data = b'fwd original')],
				[InlineKeyboardButton(text = 'Yes, but don\'t use forward.', callback_data = b'fwd text')],
				[InlineKeyboardButton(text = 'No, please don\'t.', callback_data = b'cancel d')]
		]
		if msg.text is None: kb.pop(1)
		self.botapp.send_message(
			msg.chat.id,
			'<b>Warning:</b> You are requesting forwarding an authorized user\'s message to the main group, please comfirm your action.',
			'html',
			reply_to_message_id = msg.message_id,
			reply_markup = InlineKeyboardMarkup(inline_keyboard = kb)
		)
		del kb

	def handle_new_member(self, client: Client, msg: Message):
		for new_user_id in (x.id for x in msg.new_chat_members):
			# Exam check goes here
			try:
				if not self.join_group_verify.query_user_passed(new_user_id):
					self.botapp.kick_chat_member(self.target_group, new_user_id)
					self.botapp.send_message(self.fudu_group, 'Kicked challenge failure user {}'.format(build_html_parse.parse_user(new_user_id)), 'markdown',
					)
			except:
				traceback.print_exc()
		self.conn.insert(
			msg,
			client.send_message(
				self.fudu_group,
				'`{}` invite `{}` joined the group'.format(
					build_html_parse.user_name(msg.from_user).full_name,
					'`,`'.join(
						build_html_parse.user_name(user).full_name for user in msg.new_chat_members
					)
				),
				'markdown'
			) \
			if msg.new_chat_members[0].id != msg.from_user.id else \
				client.send_message(
					self.fudu_group,
					'`{}` joined the group'.format(
						'`,`'.join(
							build_html_parse.user_name(user).full_name for user in msg.new_chat_members
						)
					),
					'markdown'
				)
			)

	def handle_edit(self, client: Client, msg: Message):
		if msg.via_bot and msg.via_bot.id == 166035794: return
		if self.conn.get_id(msg.message_id) is None:
			time.sleep(3)
			if self.conn.get_id(msg.message_id) is None:
				print(msg)
				return print('Editing Failure: get_id return None')
		try:
			(client.edit_message_text if msg.text else client.edit_message_caption)(self.fudu_group, self.conn.get_id(msg.message_id), build_html_parse(msg).call(), 'html')
		except:
			traceback.print_exc()

	def handle_document(self, client: Client, msg: Message):
		self.media_sender.put((client.send_document, msg, msg.document, False))

	def handle_photo(self, client: Client, msg: Message):
		self.media_sender.put((client.send_photo, msg, msg.photo.sizes[0], False))

	def handle_sticker(self, client: Client, msg: Message):
		self.conn.insert(
			msg,
			client.send_message(
				self.fudu_group,
				'{} {} sticker'.format(
					build_html_parse(msg).call(),
					msg.sticker.emoji
				),
				'html',
				True,
				reply_to_message_id = self.conn.get_reply_id(msg),
			)
		)

	def handle_gif(self, client: Client, msg: Message):
		self.media_sender.put((client.send_animation, msg, msg.animation, False))

	def handle_video(self, client: Client, msg: Message):
		self.media_sender.put((client.send_video, msg, msg.video, False))

	def handle_speak(self, client: Client, msg: Message):
		if msg.text.startswith('/') and re.match(r'^\/\w+(@\w*)?$', msg.text): return
		self.conn.insert(
			msg,
			client.send_message(
				self.fudu_group,
				build_html_parse(msg).call(),
				'html',
				reply_to_message_id = self.conn.get_reply_id(msg),
				disable_web_page_preview = True
			)
		)

	def handle_incoming(self, client: Client, msg: Message):
		client.send(api.functions.channels.ReadHistory(client.resolve_peer(msg.chat.id), msg.message_id))
		if msg.text == '/auth' and msg.reply_to_message:
			return self.func_auth_process(client, msg)
		if not auth_system.check_ex(msg.from_user.id): return
		if msg.text and re.match(
				r'^\/(bot (on|off)|del|getn?|fw|ban( (([1-9]\d*)(s|m|h|d)|f))?|kick( confirm| -?\d+)?|status|b?o(n|ff)|join|p(romote( \d+)?)?|set [a-zA-Z])$',
				msg.text
			):
			return self.process_imcoming_command(client, msg)
		if msg.text and msg.text.startswith('/') and re.match(r'^\/\w+(@\w*)?$', msg.text): return
		if auth_system.check_muted(msg.from_user.id) or (msg.text and msg.text.startswith('//')) or (msg.caption and msg.caption.startswith('//')): return

		if msg.forward_from or msg.forward_from_chat:
			if msg.forward_from:
				if msg.forward_from.is_self: return
				elif auth_system.check_ex(msg.forward_from.id):
					return self.cross_group_forward_request(msg)
			self.conn.insert_ex(self.botapp.forward_messages(self.target_group, self.fudu_group, msg.message_id).message_id, msg.message_id)
		elif msg.text and (not msg.edit_date or (msg.edit_date and self.conn.get_id(msg.message_id, True) is None)):
			self.conn.insert_ex(
				self.botapp.send_message(
					self.target_group,
					build_html_parse(msg).split_offset(),
					'html',
					True,
					reply_to_message_id=self.conn.get_reply_id_Reverse(msg),
				).message_id, msg.message_id
			)
		elif msg.photo:
			self.media_sender.Locker.acquire()
			msg.download('tmp.jpg')
			self.media_sender.put((self.botapp.send_photo, msg, media_path('downloads/tmp.jpg'), True), True)
		elif msg.video:
			self.media_sender.put((self.botapp.send_video, msg, msg.video, True), True)
		elif msg.document:
			self.media_sender.put((self.botapp.send_document, msg, msg.document, True), True)
		elif msg.edit_date:
			try:
				(self.botapp.edit_message_text if msg.text else self.botapp.edit_message_caption)(
					self.target_group,
					self.conn.get_id(msg.message_id, True),
					build_html_parse(msg).split_offset(),
					parse_mode='html',
					disable_web_page_preview=True
				)
			except: traceback.print_exc()
		elif msg.sticker:
			self.media_sender.put((self.botapp.send_sticker, msg, msg.sticker, True), True)

	def handle_callback(self, client: Client, msg: CallbackQuery):
		msg.data = msg.data.decode(errors = 'ignore')
		try:
			if msg.data.startswith('cancel') or msg.data == 'rm':
				msg.answer(msg.id, 'Canceled' if not msg.data == 'rm' else 'Button removed')
				if msg.data.endswith('d'):
					client.delete_messages(msg.message.chat.id, msg.message.message_id)
				else:
					client.edit_message_reply_markup(msg.message.chat.id, msg.message.message_id)
			if self.join_group_verify is not None and self.join_group_verify.click_to_join(client, msg):
				return
			if msg.data.startswith('res'):
				if time.time() - msg.message.date > 20:
					raise OperationTimeoutError()
				_, dur, _type, _user_id = msg.data.split()
				if client.restrict_chat_member(
					self.target_group,
					int(_user_id),
					int(time.time()) + int(dur),
					**(
						{
							'write': {'can_send_messages': True},
							'media': {'can_send_media_messages': True},
							'stickers': {'can_send_other_messages': True},
							'link': {'can_add_web_page_previews': True},
							'read': {}
						}.get(_type)
					)
				):
					msg.answer('The user is restricted successfully.')
					client.edit_message_text(msg.message.chat.id,
						msg.message.message_id,
						'Restrictions applied to {} Duration: {}'.format(build_html_parse.parse_user(_user_id), '{}s'.format(dur) if int(dur) else 'Forever'),
						parse_mode = 'markdown',
						reply_markup = InlineKeyboardMarkup([
								[InlineKeyboardButton(text = 'UNBAN', callback_data = 'unban {}'.format(_user_id).encode())]
							]
						)
					)

			elif msg.data.startswith('unban'):
				if client.restrict_chat_member(self.target_group, int(msg.data.split()[-1]), 0, True, True, True, True):
					msg.answer('Unban successfully')
					client.edit_message_reply_markup(msg.message.chat.id, msg.message.message_id)
			elif msg.data.startswith('auth'):
				if time.time() - msg.message.date > 20:
					raise OperationTimeoutError()
				auth_system.add_user(msg.data.split()[1])
				msg.answer('{} added to the authorized group'.format(msg.data.split()[1]))
				client.edit_message_text(msg.message.chat.id, msg.message.message_id, '{} added to the authorized group'.format(msg.data.split()[1]))
				with open('config.ini', 'w') as fout: config.write(fout)
			elif msg.data.startswith('fwd'):
				if time.time() - msg.message.date > 30:
					raise OperationTimeoutError()
				if 'original' in msg.data:
					self.conn.insert_ex(client.forward_messages(self.target_group, msg.message.chat.id, msg.message.reply_to_message.message_id).message_id, msg.message.reply_to_message.message_id)
				else:
					self.conn.insert_ex(client.send_message(self.target_group, build_html_parse(msg.message.reply_to_message).split_offset(), 'html').message_id, msg.message.reply_to_message.message_id)
				msg.answer('Forward successfully')
				client.delete_messages(msg.message.chat.id, msg.message.message_id)
			elif msg.data.startswith('kick'):
				if not msg.data.startswith('kickc') and msg.from_user.id != int(msg.data.split()[-2]):
					raise OperatorError()
				if 'true' not in msg.data:
					if not msg.data.startswith('kickc') and time.time() - msg.message.date > 15:
						raise OperationTimeoutError()
					args = [
						msg.message.chat.id,
						msg.message.message_id,
						'Press the button again to kick {}\nThis confirmation message will expire after 10 seconds.'.format(
							build_html_parse.parse_user(msg.data.split()[-1])
						),
					]
					if msg.data.startswith('kickc'):
						args.pop(1)
						r = msg.data.split()
						r.insert(1, msg.from_user.id)
						msg.data = ' '.join(str(x) for x in r)
						del r
					kwargs = {
						'parse_mode': 'markdown',
						'reply_markup': InlineKeyboardMarkup(inline_keyboard=[
							[InlineKeyboardButton(text = 'Yes, please.', callback_data = b' '.join((b'kick true', ' '.join(msg.data.split()[1:]).encode())))],
							[InlineKeyboardButton(text = 'Cancel', callback_data = b'cancel')]
						])
					}
					(client.send_message if msg.data.startswith('kickc') else client.edit_message_text)(*args, **kwargs)
					msg.answer('Please press again to make sure. Do you really want to kick {} ?'.format(msg.data.split()[-1]), True)
				else:
					if msg.message.edit_date:
						if time.time() - msg.message.edit_date > 10:
							raise OperationTimeoutError()
					else:
						if time.time() - msg.message.date > 10:
							raise OperationTimeoutError()
					client.kick_chat_member(self.target_group, int(msg.data.split()[-1]))
					msg.answer('Kicked {}'.format(msg.data.split()[-1]))
					client.edit_message_text(msg.message.chat.id, msg.message.message_id, 'Kicked {}'.format(build_html_parse.parse_user(msg.data.split()[-1])))
					#app.send_message(self.fudu_group, 'Kicked {}'.format(msg.message.entities[0].user.id))
				#client.delete_messages(msg.message.chat.id, msg.message.message_id)
			elif msg.data.startswith('promote'):
				if not msg.data.endswith('undo'):
					if time.time() - msg.message.date > 10:
						raise OperationTimeoutError()
					self.botapp.promote_chat_member(self.target_group, int(msg.data.split()[1]), True, can_delete_messages = True, can_restrict_members = True, can_invite_users = True, can_pin_messages = True, can_promote_members = True)
					msg.answer('Promote successfully')
					client.edit_message_text(msg.message.chat.id, msg.message.message_id, 'Promoted {}'.format(build_html_parse.parse_user(int(msg.data.split()[1]))), parse_mode = 'markdown',
						reply_markup = InlineKeyboardMarkup(inline_keyboard = [
							[InlineKeyboardButton(text = 'UNDO', callback_data = ' '.join((msg.data, 'undo')).encode())],
							[InlineKeyboardButton(text = 'remove button', callback_data = b'rm')]
						])
					)
				else:
					self.botapp.promote_chat_member(self.target_group, int(msg.data.split()[1]), False, can_delete_messages = False, can_invite_users = False, can_restrict_members = False)
					msg.answer('Undo Promote successfully')
					client.edit_message_text(msg.message.chat.id, msg.message.message_id, 'Unpromoted {}'.format(build_html_parse.parse_user(int(msg.data.split()[1]))), parse_mode = 'markdown')
		except OperationTimeoutError:
			msg.answer('Confirmation time out')
			client.edit_message_reply_markup(msg.message.chat.id, msg.message.message_id)
		except OperatorError:
			msg.answer('The operator should be {}.'.format(msg.data.split()[-2]), True)
		except:
			self.app.send_message(int(config['fuduji']['help_group']), traceback.format_exc().splitlines()[-1])
			traceback.print_exc()

def main():
	bot = bot_controller()
	bot.start()
	bot.idle()

def import_from_csv():
	import csv
	with open(sys.argv[2], encoding = 'utf8') as fin:
		s = csv.reader(fin, delimiter = ',')
		problems = []
		for row in s:
			problems.append({'Q': row[0], 'A': row[1]})
	problem_set = extern_load_problem_set()
	problem_set['problem_set'] = problems
	with open('problem_set.json', 'w', encoding='utf8') as fout:
		json.dump(problem_set, fout, indent='\t', separators=(',', ': '), ensure_ascii=False)

if __name__ == '__main__':
	if len(sys.argv) == 3 and sys.argv[1] == 'import':
		import_from_csv()
	else:
		main()