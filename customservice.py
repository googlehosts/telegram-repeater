# -*- coding: utf-8 -*-
# customservice.py
# Copyright (C) 2019 github.com/googlehosts Group:Z
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
from pyrogram import Client, MessageHandler, Message, CallbackQuery, CallbackQueryHandler, \
	Filters, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, \
	ReplyKeyboardRemove, api
from configparser import ConfigParser
import tg_tools
import hashlib
import time
from threading import Lock, Thread
import base64
import queue
import traceback
from datetime import datetime, timedelta
import re
import random

class build_html_parse(tg_tools.build_html_parse):
	def __init__(self, msg: Message):
		self._msg = self.gen_msg(msg)
		self.parsed_msg = self.parse_main()
	def __str__(self):
		return self.parsed_msg

class ticket(object):
	def __init__(self, msg: Message, section: str, status: str):
		self._origin_msg = build_html_parse(msg).parsed_msg
		self.hash_value = custom_service_bot_class.hash_msg(msg)
		self.section = section
		self.status = status
		self.sql = "INSERT INTO `tickets` (`user_id`, `hash`, `timestamp`, `origin_msg`, `section`, `status`) VALUES ({0}, '{1}', CURRENT_TIMESTAMP(), '{2}', '{3}', '{4}')".format(
			msg.chat.id, self.hash_value, base64.b64encode(self._origin_msg.encode()).decode(), self.section, self.status
		)
	def __str__(self):
		return self.sql

class join_group_verify_class(object):
	def __init__(self, conn: tg_tools.mysqldb, botapp: Client, target_group: int, load_problem_set: callable):
		self.problem_set = load_problem_set()
		self.conn = conn
		self.botapp = botapp
		self.target_group = target_group
		self.revoke_tracker_thread = tg_tools.invite_link_tracker(
			self.botapp,
			self.problem_set,
			self.target_group
		)

	def init(self):
		self.botapp.add_handler(MessageHandler(self.handle_bot_private, Filters.private & Filters.text))

	def get_revoke_tracker_thread(self):
		return self.revoke_tracker_thread

	def generate_ticket_keyboard(self):
		if self.problem_set['ticket_bot']['enable']:
			return {
				'reply_markup': InlineKeyboardMarkup(
					inline_keyboard = [
						[InlineKeyboardButton(text = 'I need help.', url = self.problem_set['ticket_bot']['link'])]
					]
				)
			}
		else:
			return {}

	def query_user_passed(self, user_id: int):
		sqlObj = self.conn.query1("SELECT `passed`, `bypass` FROM `exam_user_session` WHERE `user_id` = {}".format(user_id))
		return sqlObj is not None and (sqlObj['passed'] or sqlObj['bypass'])

	def handle_bot_private(self, client: Client, msg: Message):
		if msg.text.startswith('/') and msg.text != '/start newbie': return
		userObj = self.conn.query1("SELECT `problem_id`, `baned`, `bypass`, `retries`, `passed`, `unlimited` FROM `exam_user_session` WHERE `user_id` = {}".format(msg.chat.id))
		if msg.text == '/start newbie':
			try:
				try:
					user = self.botapp.get_chat_member(self.target_group, msg.chat.id)
					return msg.reply('You are already in the group.')
				except api.errors.exceptions.bad_request_400.UserNotParticipant:
					pass
				except:
					traceback.print_exc()
				if userObj is not None:
					if userObj['bypass']:
						self.revoke_tracker_thread.send_link(msg.chat.id, True)
					elif userObj['passed']:
						msg.reply('You have already answered the question.')
					else:
						msg.reply('An existing session is currently active.', True)
				else:
					randomid = random.randint(0, len(self.problem_set['problem_set']) -1)
					self.conn.execute("INSERT INTO `exam_user_session` (`user_id`, `problem_id`, `timestamp`) VALUES ({0}, {1}, CURRENT_TIMESTAMP())".format(msg.chat.id, randomid))
					msg.reply(
						self.problem_set['welcome_msg'],
						parse_mode = 'html',
						disable_web_page_preview = True,
						**self.generate_ticket_keyboard()
					)
					if self.problem_set.get('sample_problem') is not None:
						msg.reply(
							"For example:\n<b>Q:</b> <code>{Q}</code>\n<b>A:</b> <code>{A}</code>".format(
								**self.problem_set['sample_problem']
							),
							parse_mode = 'html',
							disable_web_page_preview = True
						)
					msg.reply(
						self.problem_set['problem_set'][randomid]['Q'],
						parse_mode = 'html',
						disable_web_page_preview = True
					)
			except api.errors.exceptions.bad_request_400.UserIsBlocked:
				print('Caught blocked user {}'.format(msg.chat.id))
				client.send_message(
					self.target_group,
					'The bot is blocked by user {}'.format(build_html_parse.parse_user(msg.chat.id)),
					'markdown'
				)
			except:
				traceback.print_exc()
		else:
			if userObj is not None:
				if (userObj['unlimited'] or userObj['retries'] <= self.problem_set['max_retry']) and msg.text == self.problem_set['problem_set'][userObj['problem_id']]['A']:
					self.conn.execute("UPDATE `exam_user_session` SET `passed` = 1 WHERE `user_id` = {}".format(msg.chat.id))
					self.send_link(msg)
				elif userObj['bypass']:
					self.send_link(msg)
				else:
					userObj['retries'] += 1
					if userObj['retries'] > self.problem_set['max_retry']:
						msg.reply(self.problem_set['max_retry_error'], parse_mode = 'html', disable_web_page_preview = True)
					else:
						msg.reply(self.problem_set['try_again'], parse_mode = 'html', disable_web_page_preview = True)
					self.conn.execute("UPDATE `exam_user_session` SET `retries` = {} WHERE `user_id` = {}".format(userObj['retries'], msg.chat.id))
	
	def click_to_join(self, client: Client, msg: CallbackQuery):
		if msg.data == 'iamready':
			try:
				client.edit_message_reply_markup(msg.message.chat.id, msg.message.message_id)
				self.revoke_tracker_thread.send_link(msg.message.chat.id, True)
				msg.answer('The invitation link has been sent.')
			except:
				traceback.print_exc()
			return True
		return False
	
	def send_link(self, msg: Message):
		if self.problem_set.get('confirm_msg') and self.problem_set['confirm_msg']['enable']:
			msg.reply(
				self.problem_set['confirm_msg']['text'],
				False,
				'html',
				reply_markup = InlineKeyboardMarkup( inline_keyboard = [
					[InlineKeyboardButton( text = self.problem_set['confirm_msg']['button_text'], callback_data = b'iamready')]
				])
			)
		else:
			self.revoke_tracker_thread.send_link(msg.chat.id)

class custom_service_bot_class(object):

	SECTION = [
		'VERIFICATION',
		'OTHER'
	]
	INIT_STATUS = 0
	SELECT_SECTION = 1
	SEND_QUESTION = 2
	SEND_FINISH = 3
	RE_TICKET_ID = re.compile(r'[a-f\d]{32}')

	def __init__(self, config_file: str or ConfigParser, mysql_handle: tg_tools.mysqldb, send_link_callback: callable):

		if isinstance(config_file, ConfigParser):
			self.config = config_file
		else:
			self.config = ConfigParser()
			self.config.read(config_file)

		self.mysqldb = mysql_handle if mysql_handle else tg_tools.mysqldb('localhost', 'root', self.config['database']['passwd'], self.config['database']['db_name'])
		self.bot = Client(
			self.config['account']['custom_api_key'],
			api_id = self.config['account']['api_id'],
			api_hash = self.config['account']['api_hash']
		)

		self.bot_id = int(self.config['account']['custom_api_key'].split(':')[0])
		self.help_group = int(self.config['fuduji']['help_group'])
		self.send_link_callback = send_link_callback
		self.emerg_contact = eval(self.config['account']['emerg_contact']) if self.config.has_option('account', 'emerg_contact') and self.config['account']['emerg_contact'] != '' else self.config['account']['owner']
		self.create_lock = Lock()

	def start(self):
		self.bot.add_handler(MessageHandler(self.handle_start, Filters.command('start') & Filters.private))
		self.bot.add_handler(MessageHandler(self.handle_create, Filters.command('create',) & Filters.private))
		self.bot.add_handler(MessageHandler(self.handle_cancel, Filters.command('cancel') & Filters.private))
		self.bot.add_handler(MessageHandler(self.handle_list, Filters.command('list') & Filters.private))
		self.bot.add_handler(MessageHandler(self.handle_close, Filters.command('close') & Filters.private))
		self.bot.add_handler(MessageHandler(self.handle_reply, Filters.reply & Filters.text & Filters.private))
		self.bot.add_handler(MessageHandler(self.handle_msg, Filters.text & Filters.private))
		self.bot.add_handler(MessageHandler(self.call_superuser_function, Filters.chat(self.help_group) & Filters.reply & Filters.command('m')))
		self.bot.add_handler(MessageHandler(self.handle_group, Filters.reply & Filters.chat(self.help_group)))
		self.bot.add_handler(MessageHandler(self.handle_manual_add_blacklist, Filters.command('a') & Filters.chat(self.help_group)))
		self.bot.add_handler(MessageHandler(self.handle_other, Filters.private))
		self.bot.add_handler(CallbackQueryHandler(self.answer))
		return self.bot.start()

	def stop(self):
		#self.save_session()
		return self.bot.stop()

	def idle(self):
		return self.bot.idle()

	def active(self):
		self.start()
		self.idle()

	def send_emerg_msg(self, text: str):
		if isinstance(self.emerg_contact, str):
			self.bot.send_message(self.emerg_contact, text)
		else:
			for x in self.emerg_contact:
				self.bot.send_message(x, text)

	@staticmethod
	def hash_msg(msg: Message):
		return hashlib.md5(' '.join((str(msg.from_user.id), str(msg.date), str(msg.message_id))).encode()).hexdigest()

	def get_hash_from_reply_msg(self, msg: Message):
		if msg.reply_to_message is None or \
			msg.reply_to_message.text is None or \
			msg.reply_to_message.from_user.id != self.bot_id or \
			msg.reply_to_message.entities is None or \
			msg.reply_to_message.entities[0].type != 'hashtag':
			print(msg.reply_to_message is None, msg.reply_to_message.text is None, msg.reply_to_message.from_user.id != self.bot_id, msg.reply_to_message.entities is None, msg.reply_to_message.entities[0].type != 'hashtag')
			raise ValueError("hash message info error")
		r = self.RE_TICKET_ID.search(msg.reply_to_message.text)
		if r is not None:
			return r.group(0)
		else:
			raise ValueError('hash info not found')

	@staticmethod
	def generate_section_pad():
		return ReplyKeyboardMarkup( keyboard = [
			[KeyboardButton( text = x )] for x in custom_service_bot_class.SECTION
		], resize_keyboard = True, one_time_keyboard = True)

	@staticmethod
	def generate_ticket_keyboard(ticket_id: str, user_id: int, closed: bool = False, other: bool = False):
		kb = [
			InlineKeyboardButton(text = 'Close', callback_data = 'close {}'.format(ticket_id).encode()),
			InlineKeyboardButton(text = 'Send link', callback_data = 'send {}'.format(user_id).encode()),
			InlineKeyboardButton(text = 'Block', callback_data = 'block {}'.format(user_id).encode())
		]
		if closed: kb = kb[2:]
		elif other: kb.pop(1)
		return InlineKeyboardMarkup(
			inline_keyboard = [kb]
		)

	@staticmethod
	def returnYNemoji(i: int):
		return '✅' if i else '❌'

	def handle_list(self, client: Client, msg: Message):
		q = self.mysqldb.query3("SELECT `hash`, `status` FROM `tickets` WHERE `user_id` = {} ORDER BY `timestamp` DESC LIMIT 3".format(msg.chat.id))
		if len(q) == 0 or q is None:
			return msg.reply('You have never used this system before.', True)
		for _ticket in q:
			_ticket['status'] = self.returnYNemoji(_ticket['status'] != 'closed')
		msg.reply('Here are the last three tickets (up to 3)\n#{}'.format('\n#'.join(' '.join(value for _, value in _ticket.items()) for _ticket in q)), True)

	def handle_close(self, client: Client, msg: Message):
		if msg.reply_to_message is not None and msg.text == '/close':
			try:
				ticket_id = self.get_hash_from_reply_msg(msg)
			except ValueError:
				return msg.reply('TICKET NUMBER NOT FOUND\nPlease make sure that you have replied to the message which contains the ticket number.', True)
		else:
			if len(msg.text) < 8:
				return msg.reply('ERROR: COMMAND FORMAT Please use `/close <ticket number>` or **Reply to the message which contains the ticket number** to close the ticket', True, 'markdown', True)
			ticket_id = msg.text.split()[-1]
			if len(ticket_id) != 32:
				return msg.reply('ERROR: TICKET NUMBER FORMAT', True)
		q = self.mysqldb.query1("SELECT `user_id` FROM `tickets` WHERE `hash` = '{}' AND `status` != 'closed'".format(ticket_id))
		if q is None:
			return msg.reply('TICKET NUMBER NOT FOUND or TICKET CLOSED', True)
		if q['user_id'] != msg.chat.id:
			return msg.reply('403 Forbidden(You cannot close a ticket created by others. If this ticket is indeed created by yourself, please report the problem using the same ticket.)', True)
		self.mysqldb.execute("UPDATE `tickets` SET `status` = 'closed' WHERE `user_id` = {} AND `hash` = '{}'".format(msg.chat.id, ticket_id))
		self.mysqldb.execute("UPDATE `tickets_user` SET `last_time` = CURRENT_TIMESTAMP() WHERE `user_id` = {}".format(msg.chat.id))
		self.mysqldb.commit()
		client.send_message(self.help_group, "UPDATE\n[ #{} ]\nThis ticket is already closed by {}".format(ticket_id, tg_tools.build_html_parse.parse_user(msg.chat.id, '创建者')), reply_markup = self.generate_ticket_keyboard(ticket_id, msg.chat.id, True))
		msg.reply('DONE！', True)

	def add_user(self, user_id: int, step: int = 0):
		self.mysqldb.execute("INSERT INTO `tickets_user` (`user_id`, `create_time`, `last_time`, `last_msg_sent`, `step`) VALUES ({}, CURRENT_TIMESTAMP(), DATE_SUB(CURRENT_TIMESTAMP(), INTERVAL 5 minute), CURRENT_TIMESTAMP(), {})".format(user_id, step))

	def change_step(self, user_id: int, step: int, section: str = ''):
		if section == '':
			self.mysqldb.execute("UPDATE `tickets_user` SET `step` = {} WHERE `user_id` = {}".format(step, user_id))
		else:
			self.mysqldb.execute("UPDATE `tickets_user` SET `step` = {0}, `section` = '{2}' WHERE `user_id` = {1}".format(step, user_id, section))
		self.mysqldb.commit()

	def query_status(self, user_id: int):
		return self.mysqldb.query1("SELECT `step`, `section` FROM `tickets_user` WHERE `user_id` = {}".format(user_id))

	def query_user(self, user_id: int):
		return self.mysqldb.query1("SELECT `section` FROM `tickets_user` WHERE `user_id` = {}".format(user_id))

	def set_section(self, user_id: int, section: str):
		self.mysqldb.execute("UPDATE `tickets_user` SET `section` = '{1}' WHERE `user_id` = {0}".format(user_id, section))
		self.mysqldb.commit()

	def query_user_exam_status(self, user_id: int):
		return self.mysqldb.query1("SELECT `baned`, `bypass`, `passed`, `unlimited`, `retries` FROM `exam_user_session` WHERE `user_id` = {}".format(user_id))

	def handle_start(self, client: Client, msg: Message):
		q = self.mysqldb.query1("SELECT `last_msg_sent` FROM `tickets_user` WHERE `user_id` = {}".format(msg.chat.id))
		msg.reply('Welcome to Google Hosts Telegram Ticket System\n\nATTENTION：PLEASE DO NOT ABUSE THIS SYSTEM. Otherwise there is a possibility of getting blocked.\n\n/create - to create a new ticket\n/list - to list recent tickets\n/close - to close the ticket\n/cancel - to reset', True)
		if q is None:
			self.add_user(msg.chat.id)

	def handle_create(self, client: Client, msg: Message):
		if self.flood_check(client, msg):
			return
		q = self.mysqldb.query1("SELECT `hash` FROM `tickets` WHERE `user_id` = {} AND `status` = 'open' LIMIT 1".format(msg.chat.id))
		if q:
			msg.reply('UNABLE TO CREATE A NEW TICKET: An existing ticket is currently open.', True)
			return
		sqlObj = self.mysqldb.query1("SELECT `user_id` FROM `tickets_user` WHERE `user_id` = {}".format(msg.chat.id))
		(self.add_user if sqlObj is None else self.change_step)(msg.chat.id, custom_service_bot_class.SELECT_SECTION)
		msg.reply('You are creating a new ticket.\n\nPlease choose the correct department.', True, reply_markup=self.generate_section_pad())

	def handle_cancel(self, client: Client, msg: Message):
		self.change_step(msg.chat.id, custom_service_bot_class.INIT_STATUS)
		msg.reply('Reset Successful', reply_markup=ReplyKeyboardRemove())

	def handle_reply(self, client: Client, msg: Message):
		if self.flood_check(client, msg):
			return
		ticket_hash = self.get_hash_from_reply_msg(msg)
		sqlObj = self.mysqldb.query1("SELECT `status`, `section` FROM `tickets` WHERE `hash` = '{}' AND `user_id` = {}".format(ticket_hash, msg.chat.id))
		if sqlObj is None or sqlObj['status'] == 'closed':
			msg.reply('TICKET NUMBER NOT FOUND or TICKET CLOSED. REPLY FUNCTION NO LONGER AVAILABLE.', True)
			return
		self.mysqldb.execute("UPDATE `tickets_user` SET `last_time` = CURRENT_TIMESTAMP() WHERE `user_id` = {}".format(msg.chat.id))
		self.mysqldb.commit()
		client.send_message(
			self.help_group,
			'NEW REPLY\n[ #{} ]:\nMESSAGE: {}'.format(ticket_hash, build_html_parse(msg).parsed_msg),
			'html',
			reply_markup = self.generate_ticket_keyboard(ticket_hash, msg.chat.id, sqlObj['section'] != self.SECTION[0])
		)
		msg.reply('The new reply is added successfully!')

	def handle_msg(self, client: Client, msg: Message):
		sqlObj = self.query_status(msg.chat.id)
		if sqlObj is None or sqlObj['step'] not in (custom_service_bot_class.SELECT_SECTION, custom_service_bot_class.SEND_QUESTION):
			if self.flood_check(client, msg):
				return
			return msg.reply('Please use bot command to interact.')
		if sqlObj['step'] == custom_service_bot_class.SELECT_SECTION:
			if msg.text in self.SECTION:
				self.change_step(msg.chat.id, custom_service_bot_class.SEND_QUESTION, msg.text)
				msg.reply('Please describe your problem briefly(up to 500 characters)\n(Please use external links to send pictures.):\n\nATTENTION: Receiving a confirmation message in return indicates that the ticket is created successfully.\n\nUse /cancel to cancel creating the ticket. ', True, reply_markup = ReplyKeyboardRemove())
			else:
				msg.reply('Please use the menu below to choose the correct department.', True)
		elif sqlObj['step'] == custom_service_bot_class.SEND_QUESTION:
			if len(msg.text) > 500:
				msg.reply('The number of characters you have entered is larger than 500. Please re-enter.', True)
				return
			ticket_hash = self.hash_msg(msg)
			self.mysqldb.execute(ticket(msg, sqlObj['section'], 'open').sql)
			self.mysqldb.commit()
			self.change_step(msg.chat.id, custom_service_bot_class.INIT_STATUS)
			msg.reply(
				'The ticket is created successfully!\n[ #{ticket_id} ]\nDepartment: {section}\nMessage: \n{text}\n\nReply to this message to add a new reply to the ticket.'.format(
					ticket_id = ticket_hash,
					text = build_html_parse(msg).parsed_msg,
					section = sqlObj['section']
				),
				parse_mode = 'html'
			)
			msg_id = client.send_message(
				self.help_group,
				'NEW TICKET\n[ #{} ]\nClick {} to check the user profile\nDepartment: {}\nMessage: \n{}'.format(
					ticket_hash,
					build_html_parse.parse_user_ex(msg.chat.id, 'Here'),
					sqlObj['section'],
					build_html_parse(msg).parsed_msg
				),
				'html',
				reply_markup = self.generate_ticket_keyboard(
					ticket_hash,
					msg.chat.id,
					other = sqlObj['section'] != custom_service_bot_class.SECTION[0]
				)
			).message_id
			if sqlObj['section'] == custom_service_bot_class.SECTION[0]:
				client.send_message(
					self.help_group,
					self.generate_user_status(msg.chat.id),
					'markdown',
					reply_to_message_id = msg_id
				)
		else:
			print("throw! user_id: {}, sqlObj = {}".format(msg.chat.id, repr(sqlObj)))

	def generate_user_status(self, user_id: int):
		user_status = self.query_user_exam_status(user_id)
		return 'User {5} status:\nPassed exam: {0}\nBan status: {1}\nBypass: {2}\nUnlimited: {3}\nRetries: {4}'.format(
			self.returnYNemoji(user_status['passed']),
			self.returnYNemoji(user_status['baned']),
			self.returnYNemoji(user_status['bypass']),
			self.returnYNemoji(user_status['unlimited']),
			user_status['retries'],
			build_html_parse.parse_user(user_id)
		) if user_status is not None else '**WARNING: THIS USER HAS NEVER USED THE BOT BEFORE.**'

	def handle_other(self, client: Client, msg: Message):
		q = self.mysqldb.query1("SELECT `last_msg_sent` FROM `tickets_user` WHERE `user_id` = {}".format(msg.chat.id))
		if (datetime.now() - q['last_msg_sent']).total_seconds() < 120:
			return
		msg.reply('Please use bot command to interact. TEXT ONLY.')

	def handle_group(self, client: Client, msg: Message):
		#print(msg)
		if msg.reply_to_message.from_user.id != self.bot_id or (msg.text and msg.text.startswith('/')): return
		ticket_hash = self.get_hash_from_reply_msg(msg)
		sqlObj = self.mysqldb.query1("SELECT * FROM `tickets` WHERE `hash` = '{}'".format(ticket_hash))
		if sqlObj is None:
			return msg.reply('ERROR: TICKET NOT FOUND')
		if sqlObj['status'] == 'closed':
			return msg.reply('This ticket is already closed.')
		msg_reply = client.send_message(sqlObj['user_id'], 'NEW UPDATE!\n[ #{} ]\nMessage: \n{}\n\nReply to this message to add a new reply to the ticket.'.format(ticket_hash, build_html_parse(msg).parsed_msg), 'html')
		msg.reply('REPLY SUCCESSFUL', reply_markup = InlineKeyboardMarkup(inline_keyboard = [
			[
				InlineKeyboardButton( text = 'recall', callback_data = ' '.join(('del', str(msg_reply.chat.id), str(msg_reply.message_id))).encode())
			]
		]))
		sqlObj = self.mysqldb.query1("SELECT `last_time`, `user_id` FROM `tickets_user` WHERE user_id = {}".format(sqlObj['user_id']))
		if (datetime.now() - sqlObj['last_time']).total_seconds() < 120:
			self.mysqldb.execute("UPDATE `tickets_user` SET `last_time` = DATE_SUB(CURRENT_TIMESTAMP(), INTERVAL 3 minute) WHERE `user_id` = %s", (sqlObj['user_id'],))

	def handle_manual_add_blacklist(self, client: Client, msg: Message):
		pass

	@staticmethod
	def generate_confirm_keyboard(first: str, last: str):
		if isinstance(last, list) or isinstance(last, tuple):
			lastg = last
		else:
			lastg = (str(last),)
		return InlineKeyboardMarkup(inline_keyboard = [
			[
				InlineKeyboardButton(text = 'Yes', callback_data = ' '.join((first, 'confirm', *lastg)).encode()),
				InlineKeyboardButton(text = 'No', callback_data = b'cancel')
			]
		])

	def generate_superuser_text(self, user_id: str or int):
		return '\n\n'.join(('Please choose the section below.', self.generate_user_status(user_id), ' '.join(('Last refresh:', time.strftime('%Y-%m-%d %H:%M:%S')))))

	def generate_superuser_detail(self, user_id: str or int):
		return {
			'text': self.generate_superuser_text(user_id),
			'reply_markup': InlineKeyboardMarkup(
				inline_keyboard = [
					[
						InlineKeyboardButton( text = 'BYPASS', callback_data = 'bypass {}'.format(user_id).encode()),
						InlineKeyboardButton( text = 'UNLIMITED RETRIES', callback_data = 'unlimited {}'.format(user_id).encode()),
						InlineKeyboardButton( text = 'REFRESH', callback_data = 'refresh {}'.format(user_id).encode())
					],
					[
						InlineKeyboardButton( text = 'PASS', callback_data = 'setpass {}'.format(user_id).encode()),
						InlineKeyboardButton( text = 'RESET TIMES', callback_data = 'reset {}'.format(user_id).encode())
					],
					[
						InlineKeyboardButton( text = 'RESET USER STATUS', callback_data = 'renew {}'.format(user_id).encode())
					],
					[
						InlineKeyboardButton( text = 'Cancel', callback_data = b'cancel')
					]
				]
			)
		}

	def call_superuser_function(self, client: Client, msg: Message):
		sqlObj = self.mysqldb.query1("SELECT `user_id`, `section` FROM `tickets` WHERE `hash` = '{}'".format(self.get_hash_from_reply_msg(msg)))
		if sqlObj['section'] != self.SECTION[0]:
			return msg.reply('This ticket doesn\'t support admin menus for now.', True)
		user_id = sqlObj['user_id']
		client.send_message(
			self.help_group,
			parse_mode = 'markdown',
			reply_to_message_id = msg.reply_to_message.message_id,
			**self.generate_superuser_detail(user_id)
		)

	def confirm_dialog(self, msg: Message, additional_msg: str, callback_prefix: str, user_id: int = None, ticket_id: str = None):
		if user_id is not None:
			self.bot.send_message(
				self.help_group,
				'Do you really want to {} {}?'.format(additional_msg, build_html_parse.parse_user(user_id)),
				'markdown',
				reply_markup = self.generate_confirm_keyboard(callback_prefix, user_id)
			)
		else:
			self.bot.send_message(
				self.help_group,
				'Do you really want to {} #{}?'.format(additional_msg, ticket_id),
				reply_markup = self.generate_confirm_keyboard(callback_prefix, ticket_id)
			)

	def confirm(self, client: Client, msg: CallbackQuery):
		if time.time() - msg.message.date > 15:
			raise TimeoutError()
		if msg.data.startswith('close'):
			ticket_id = msg.data.split()[-1]
			q = self.mysqldb.query1("SELECT `user_id`, `status` FROM `tickets` WHERE `hash` = '{}'".format(ticket_id))
			if q is None:
				return msg.answer('TICKET NOT FOUND', True)
			if q['status'] == 'closed':
				return msg.answer('TICKET CLOSED')
			self.mysqldb.execute("UPDATE `tickets` SET `status` = 'closed' WHERE `hash` = '{}'".format(ticket_id))
			msg.answer('This ticket is closed.')
			client.send_message(
				self.help_group,
				"UPDATE\n[ #{} ]\nThis ticket is closed by {}.".format(
					ticket_id,
					tg_tools.build_html_parse.parse_user(
						msg.from_user.id,
						tg_tools.build_html_parse.user_name(msg.from_user).full_name
					)
				),
				'markdown',
				reply_markup = self.generate_ticket_keyboard(ticket_id, q['user_id'], True)
			)
			client.send_message(q['user_id'], "Your ticket [ #{} ] is closed".format(ticket_id))
		elif msg.data.startswith('block'):
			self.mysqldb.execute("UPDATE `tickets_user` SET `baned` = 1 WHERE `user_id` = {}".format(msg.data.split()[-1]))
			msg.answer('DONE!')
			self.bot.send_message(
				self.help_group,
				'blocked {}'.format(build_html_parse.parse_user(msg.data.split()[-1], msg.data.split()[-1])),
				parse_mode = 'markdown',
				reply_markup = InlineKeyboardMarkup( inline_keyboard = [
					[InlineKeyboardButton(text = 'UNBAN', callback_data = 'unban {}'.format(msg.data.split()[-1]).encode())]
				])
			)
		elif msg.data.startswith('send'):
			try:
				self.send_link_callback(int(msg.data.split()[-1]), True)
				msg.answer('The invitation link is sent successfully.')
			except:
				client.send_message(self.help_group, traceback.format_exc(), disable_web_page_preview = True)
				msg.answer('Failed to send the invitation link. Please check the console.\n{}'.format(traceback.format_exc().splitlines()[-1]), True)
		elif msg.data.startswith('reset'):
			self.mysqldb.execute('UPDATE `exam_user_session` SET `retries` = 0 WHERE `user_id` = {}'.format(msg.data.split()[-1]))
			msg.answer('Retry times has been reset')
		elif msg.data.startswith('del'):
			try:
				client.delete_messages(int(msg.data.split()[-2]), int(msg.data.split()[-1]))
				msg.answer('message has been deleted')
			except:
				client.send_message(self.help_group, traceback.format_exc(), disable_web_page_preview = True)
				msg.answer('Failed to delete the message. Please check the console.\n{}'.format(traceback.format_exc().splitlines()[-1]), True)
		elif msg.data.startswith('renew'):
			self.mysqldb.execute('DELETE FROM `exam_user_session` WHERE `user_id` = {}'.format(msg.data.split()[-1]))
			msg.answer('User Profile Deleted')
		elif msg.data.startswith('bypass'):
			self.mysqldb.execute('UPDATE `exam_user_session` SET `bypass` = 1 WHERE `user_id` = {}'.format(msg.data.split()[-1]))
			msg.answer('BYPASS SET SUCCESSFULLY')
		elif msg.data.startswith('setpass'):
			self.mysqldb.execute('UPDATE `exam_user_session` SET `passed` = 1 WHERE `user_id` = {}'.format(msg.data.split()[-1]))
			msg.answer('PASS SET SUCCESSFULLY')
		elif msg.data.startswith('unlimited'):
			self.mysqldb.execute('UPDATE `exam_user_session` SET `unlimited` = 1 WHERE `user_id` = {}'.format(msg.data.split()[-1]))
			msg.answer('UNLIMITED RETRIES SET SUCCESSFULLY')
		self.mysqldb.commit()
		client.delete_messages(msg.message.chat.id, msg.message.message_id)

	def send_confirm(self, client: Client, msg: CallbackQuery):
		if msg.data.startswith('close'):
			self.confirm_dialog(msg, 'close this ticket', 'close',ticket_id = msg.data.split()[-1])
		elif msg.data.startswith('block'):
			self.confirm_dialog(msg, 'block this user', 'block', user_id = int(msg.data.split()[-1]))
		elif msg.data.startswith('send'):
			self.confirm_dialog(msg, 'send the link to', 'send', user_id = int(msg.data.split()[-1]))
		elif msg.data.startswith('reset'):
			self.confirm_dialog(msg, 'reset retry times for', 'reset', user_id = int(msg.data.split()[-1]))
		elif msg.data.startswith('del'):
			msg.answer('Please press again to make sure. If you really want to delete this reply', True)
			self.bot.send_message(
				self.help_group,
				'Do you want to delete reply message to {}?'.format(build_html_parse.parse_user(msg.data.split()[-2])),
				'markdown',
				reply_markup = self.generate_confirm_keyboard('del', msg.data[4:])
			)
		elif msg.data.startswith('bypass'):
			self.confirm_dialog(msg, 'set bypass for', 'bypass', int(msg.data.split()[-1]))
		elif msg.data.startswith('renew'):
			self.confirm_dialog(msg, 'reset user status', 'renew', int(msg.data.split()[-1]))
		elif msg.data.startswith('setpass'):
			self.confirm_dialog(msg, 'set pass', 'setpass', int(msg.data.split()[-1]))
		elif msg.data.startswith('unlimited'):
			self.confirm_dialog(msg, 'set unlimited retries for', 'unlimited', int(msg.data.split()[-1]))
		msg.answer()

	def answer(self, client: Client, msg: CallbackQuery):
		msg.data = msg.data.decode(errors = 'ignore')
		if msg.data.startswith('cancel'):
			client.edit_message_reply_markup(msg.message.chat.id, msg.message.message_id)
			msg.answer('Canceled')
		elif msg.data.startswith('unban'):
			self.mysqldb.execute("UPDATE `tickets_user` SET `baned` = 0 WHERE `user_id` = {}".format(msg.data.split()[-1]))
			msg.answer('UNBANED')
			client.edit_message_reply_markup(msg.message.chat.id, msg.message.message_id)
		elif msg.data.startswith('refresh'):
			try:
				client.edit_message_text(
					msg.message.chat.id,
					msg.message.message_id,
					self.generate_superuser_text(msg.data.split()[-1]),
					'markdown',
					reply_markup = msg.message.reply_markup
				)
			except api.errors.exceptions.bad_request_400.MessageNotModified:
				pass
			msg.answer('refreshed')
		elif 'confirm' in msg.data:
			try:
				self.confirm(client, msg)
			except TimeoutError:
				msg.answer('Confirmation time out')
				client.edit_message_reply_markup(msg.message.chat.id, msg.message.message_id)
		elif any(msg.data.startswith(x) for x in ('close', 'block', 'send', 'bypass', 'reset', 'unlimited', 'del', 'renew', 'setpass')):
			self.send_confirm(client, msg)
		else:
			try:
				raise ValueError(msg.data)
			except:
				client.send_message(self.help_group, traceback.format_exc(), disable_web_page_preview = True)

	def flood_check(self, client: Client, msg: Message):
		sq = self.mysqldb.query1("SELECT `last_time`, `last_msg_sent`, `baned` FROM `tickets_user` WHERE `user_id` = {}".format(msg.chat.id))
		if sq and (datetime.now() - sq['last_time']).total_seconds() < 120:
			if msg.text:
				print('Caught flood {}: {}'.format(msg.chat.id, msg.text))
			self.mysqldb.execute("UPDATE `tickets_user` SET `last_msg_sent` = CURRENT_TIMESTAMP() WHERE `user_id` = {}".format(msg.chat.id))
			self.mysqldb.commit()
			if sq['baned']:
				return msg.reply('Due to privacy settings, you are temporarily unable to operate.') is not None
			msg.reply("You are driving too fast. Please try again later.")
			return True
		return False


def main():
	custom_service_bot_class('config.ini', None, None).active()

if __name__ == "__main__":
	main()