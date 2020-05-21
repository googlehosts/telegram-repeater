# -*- coding: utf-8 -*-
# repeater.py
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
import asyncio
import gettext
import json
import logging
import re
import time
import traceback
from configparser import ConfigParser
from typing import Mapping, Optional, Tuple, TypeVar, Union

import aiofile
import aioredis
import pyrogram.errors
import redis
from pyrogram import (CallbackQuery, CallbackQueryHandler, ChatPermissions,
                      Client, Filters, InlineKeyboardButton,
                      InlineKeyboardMarkup, Message, MessageHandler, api)

import utils
from customservice import CustomServiceBot, JoinGroupVerify
from utils import AuthSystem, MySQLdb
from utils import TextParser as tp
from utils import _rT, get_language

config = ConfigParser()
config.read('config.ini')

logger = logging.getLogger('repeater')

translation = gettext.translation('repeater', 'translations/',
								  languages=[get_language()], fallback=True)

_T = translation.gettext
_cT = TypeVar('_cT')


class TextParser(tp):
	bot_username = ''

	def __init__(self, msg: Message):
		self._msg = self.BuildMessage(msg)
		self.parsed_msg = self.parse_main()
		if msg.chat.id == config.getint('fuduji', 'fudu_group') and self.parsed_msg and self.parsed_msg.startswith(
				'\\//'): self.parsed_msg = self.parsed_msg[1:]
		if msg.chat.id == config.getint('fuduji',
										'target_group') and self.parsed_msg: self.parsed_msg = self.parsed_msg.replace(
			'@{}'.format(TextParser.bot_username), '@{}'.format(config['fuduji']['replace_to_id']))


async def external_load_problem_set() -> Mapping[str, _rT]:
	try:
		async with aiofile.AIOFile('problem_set.json', encoding='utf8') as fin:
			problem_set = json.loads(await fin.read())
		if len(problem_set['problems']['problem_set']) == 0:
			logger.warning('Problem set length is 0')
	except:
		traceback.print_exc()
		logger.error('Error in reading problem set')
		problem_set = {}
	return problem_set


class WaitForDelete:
	def __init__(self, client: Client, chat_id: int, message_ids: Union[int, Tuple[int, ...]]):
		self.client: Client = client
		self.chat_id: int = chat_id
		self.message_ids: Union[int, Tuple[int, ...]] = message_ids

	async def __call__(self) -> None:
		await asyncio.sleep(5)
		await self.client.delete_messages(self.chat_id, self.message_ids)


class OperationTimeoutError(Exception): pass


class OperatorError(Exception): pass


class BotController:
	class ByPassVerify(UserWarning):
		pass

	def __init__(self):
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
		self.conn: Optional[MySQLdb] = None
		self._redis: redis.Redis = None
		self.auth_system: Optional[AuthSystem] = None
		self.warn_evidence_history_channel: int = config.getint('fuduji', 'warn_evidence', fallback=0)

		self.join_group_verify_enable: bool = config.getboolean('join_group_verify', 'enable', fallback=True)
		self.custom_service_enable: bool = config.getboolean('custom_service', 'enable', fallback=True)

		self.join_group_verify: Optional[JoinGroupVerify] = None
		self.revoke_tracker_coro: Optional[utils.InviteLinkTracker] = None
		self.custom_service: Optional[CustomServiceBot] = None
		self.problem_set: Optional[Mapping[str, _rT]] = None
		self.init_handle()

	async def init_connections(self) -> None:
		self._redis = await aioredis.create_redis_pool('redis://localhost')
		self.conn = await MySQLdb.create(config['database']['host'], config['database']['user'],
										 config['database']['passwd'], config['database']['db_name'])
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
	async def create(cls) -> 'BotController':
		self = BotController()
		await self.init_connections()
		return self

	def init_handle(self) -> None:
		self.app.add_handler(MessageHandler(self.handle_edit, Filters.chat(self.target_group) & ~Filters.user(
			self.bot_id) & Filters.edited))
		self.app.add_handler(
			MessageHandler(self.handle_new_member, Filters.chat(self.target_group) & Filters.new_chat_members))
		self.app.add_handler(
			MessageHandler(self.handle_service_messages, Filters.chat(self.target_group) & Filters.service))
		self.app.add_handler(MessageHandler(self.handle_all_media,
											Filters.chat(self.target_group) & ~Filters.user(self.bot_id) & (
													Filters.photo | Filters.video | Filters.document | Filters.animation | Filters.voice)))
		self.app.add_handler(MessageHandler(self.handle_sticker, Filters.chat(self.target_group) & ~Filters.user(
			self.bot_id) & Filters.sticker))
		self.app.add_handler(MessageHandler(self.handle_speak, Filters.chat(self.target_group) & ~Filters.user(
			self.bot_id) & Filters.text))
		self.app.add_handler(MessageHandler(self.handle_incoming, Filters.incoming & Filters.chat(self.fudu_group)))
		self.botapp.add_handler(
			MessageHandler(self.handle_bot_send_media, Filters.chat(self.fudu_group) & Filters.command('SendMedia')))
		self.botapp.add_handler(CallbackQueryHandler(self.handle_callback))

	async def init(self) -> None:
		while not self.botapp.is_connected:
			await asyncio.sleep(0)
		TextParser.bot_username = (await self.botapp.get_me()).username

	async def idle(self) -> None:
		try:
			await self.app.idle()
		except KeyboardInterrupt:
			logger.info('Catched KeyboardInterrupt')

	async def start(self) -> None:
		asyncio.run_coroutine_threadsafe(self.app.start(), asyncio.get_event_loop())
		asyncio.run_coroutine_threadsafe(self.botapp.start(), asyncio.get_event_loop())
		if self.custom_service_enable:
			asyncio.run_coroutine_threadsafe(self.custom_service.start(), asyncio.get_event_loop())
		await self.init()

	async def stop(self) -> None:
		task_pending = []
		if self.join_group_verify_enable:
			self.revoke_tracker_coro.request_stop()
			await self.revoke_tracker_coro.join(1.5)
			if self.revoke_tracker_coro.is_alive:
				logger.warning('revoke_tracker_coro still running!')
			if self.custom_service_enable:
				task_pending.append(asyncio.create_task(self.custom_service.stop()))
		task_pending.append(asyncio.create_task(self.botapp.stop()))
		task_pending.append(asyncio.create_task(self.app.stop()))
		await asyncio.wait(task_pending)
		task_pending.clear()

		if self.join_group_verify_enable:
			await self.join_group_verify.problems.destroy()

		self._redis.close()
		task_pending.append(asyncio.create_task(self.conn.close()))
		task_pending.append(asyncio.create_task(self._redis.wait_closed()))
		await asyncio.wait(task_pending)

	async def handle_service_messages(self, _client: Client, msg: Message) -> None:
		if msg.pinned_message:
			text = self.get_file_type(msg.pinned_message)
			if text == 'text':
				text = msg.pinned_message.text[:20]
			else:
				text = f'a {text}'
			await self.conn.insert_ex(
				(await self.botapp.send_message(self.fudu_group, f'Pined \'{text}\'', disable_web_page_preview=True,
											   reply_markup=InlineKeyboardMarkup(inline_keyboard=[
												   [InlineKeyboardButton(text='UNPIN', callback_data='unpin')]
											   ]))).message_id, msg.message_id
			)
		elif msg.new_chat_title:
			await self.conn.insert_ex(
				(await self.botapp.send_message(self.fudu_group, f'Set group title to <code>{msg.new_chat_title}</code>',
											   'html', disable_web_page_preview=True)).message_id,
				msg.message_id
			)
		else:
			logger.info('Got unexcept service message: %s', repr(msg))

	async def generate_warn_message(self, user_id: int, reason: str) -> str:
		return _T('You were warned.(Total: {})\nReason: <pre>{}</pre>').format(
			await self.conn.query_warn_by_user(user_id), reason)

	async def process_imcoming_command(self, client: Client, msg: Message) -> None:
		r = re.match(r'^/bot (on|off)$', msg.text)
		if r is None: r = re.match(r'^/b?(on|off)$', msg.text)
		if r:
			if not self.auth_system.check_ex(
					msg.reply_to_message.from_user.id if msg.reply_to_message else msg.from_user.id): return
			await self.auth_system.mute_or_unmute(r.group(1),
												  msg.reply_to_message.from_user.id if msg.reply_to_message else msg.from_user.id)
			await msg.delete()

		if msg.text == '/status':
			user_id = msg.reply_to_message.from_user.id if msg.reply_to_message else msg.from_user.id
			status = [str(user_id), ' summary:\n\n', 'A' if self.auth_system.check_ex(user_id) else 'Una',
					  'uthorized user\nBot status: ',
					  CustomServiceBot.return_bool_emoji(not self.auth_system.check_muted(user_id))]
			await WaitForDelete(client, msg.chat.id,
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
			await self.botapp.send_message(msg.chat.id,
										   'Please use bottom to make sure you want to add {} to Administrators'.format(
											   TextParser.parse_user(user_id)),
										   parse_mode='markdown',
										   reply_to_message_id=msg.message_id,
										   reply_markup=InlineKeyboardMarkup(inline_keyboard=[
											   [
												   InlineKeyboardButton(text='Yes, confirm',
																		callback_data=f'promote {user_id}')
											   ],
											   [
												   InlineKeyboardButton(text='Cancel', callback_data='cancel d')
											   ]
										   ]))
			return

		elif msg.text.startswith('/su'):
			if not self.auth_system.check_ex(msg.from_user.id): return
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
			if not self.auth_system.check_ex(msg.from_user.id): return
			await self.botapp.set_chat_title(
				self.target_group,
				msg.text.split(maxsplit=2)[1]
			)

		if msg.reply_to_message:
			if msg.text == '/del':
				try:
					await client.forward_messages(msg.chat.id, self.target_group,
												  await self.conn.get_reply_id_Reverse(msg))
				except:
					await client.send_message(msg.chat.id, traceback.format_exc(), disable_web_page_preview=True)
				try:
					await self.botapp.delete_messages(self.target_group, await self.conn.get_reply_id_Reverse(msg))
					await client.delete_messages(self.fudu_group, [msg.message_id, msg.reply_to_message.message_id])
				except:
					pass

			elif msg.text == '/getid':
				user_id = await self.conn.get_user_id(msg)
				await msg.reply(
					'user_id is `{}`'.format(
						user_id['user_id'] if user_id is not None and user_id['user_id'] else \
							'ERROR_INVALID_USER_ID'
					),
					parse_mode='markdown'
				)

			elif msg.text == '/get' and await self.conn.get_reply_id_Reverse(msg):
				try:
					await client.forward_messages(self.fudu_group, self.target_group,
												  await self.conn.get_reply_id_Reverse(msg))
				except:
					await client.send_message(msg.chat.id, traceback.format_exc().splitlines()[-1])

			elif msg.text == '/getn':
				r = await self.conn.get_msg_name_history_channel_msg_id(msg)
				if r != 0:
					await client.forward_messages(msg.chat.id, config.getint('username_tracker', 'user_name_history'),
												  r)
				else:
					await client.send_message(msg.chat.id, 'ERROR_CHANNEL_MESSAGE_NOT_FOUND',
											  reply_to_message_id=msg.message_id)

			elif msg.text == '/fw':
				message_id = await self.conn.get_reply_id_Reverse(msg)
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
							'What can {} only do? Press the button below.\nThis confirmation message will expire after 20 seconds.'.format(
								TextParser.parse_user(user_id['user_id'])
							),
							reply_to_message_id=msg.message_id,
							parse_mode='markdown',
							reply_markup=InlineKeyboardMarkup(
								inline_keyboard=[
									[
										InlineKeyboardButton(text='READ',
															 callback_data=f"res {restrict_time} read {user_id['user_id']}")
									],
									[
										InlineKeyboardButton(text='SEND_MESSAGES',
															 callback_data=f"res {restrict_time} write {user_id['user_id']}"),
										InlineKeyboardButton(text='SEND_MEDIA',
															 callback_data=f"res {restrict_time} media {user_id['user_id']}")
									],
									[
										InlineKeyboardButton(text='SEND_STICKERS',
															 callback_data=f"res {restrict_time} stickers {user_id['user_id']}"),
										InlineKeyboardButton(text='EMBED_LINKS',
															 callback_data=f"res {restrict_time} link {user_id['user_id']}")
									],
									[
										InlineKeyboardButton(text='Cancel', callback_data='cancel')
									]
								]
							)
						)
					else:
						await self.botapp.send_message(msg.chat.id, 'ERROR_WHITELIST_USER_ID',
													   reply_to_message_id=msg.message_id)
				else:
					await self.botapp.send_message(msg.chat.id, 'ERROR_INVALID_USER_ID',
												   reply_to_message_id=msg.message_id)

			elif msg.text == '/kick':
				user_id = await self.conn.get_user_id(msg)
				if user_id is not None and user_id['user_id']:
					if user_id['user_id'] not in self.auth_system.whitelist:
						await self.botapp.send_message(msg.chat.id,
													   'Do you really want to kick {}?\nIf you really want to kick this user, press the button below.\nThis confirmation message will expire after 15 seconds.'.format(
														   TextParser.parse_user(user_id['user_id'])
													   ),
													   reply_to_message_id=msg.message_id,
													   parse_mode='markdown',
													   reply_markup=InlineKeyboardMarkup(
														   inline_keyboard=[
															   [
																   InlineKeyboardButton(text='Yes, kick it',
																						callback_data=f'kick {msg.from_user.id} {user_id["user_id"]}')
															   ],
															   [
																   InlineKeyboardButton(text='No',
																						callback_data='cancel')
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
				target_id = await self.conn.get_reply_id_Reverse(msg)
				if target_id is None:
					await msg.reply('ERROR_INVALID_MESSAGE_ID')
					return
				await self.botapp.pin_chat_message(self.target_group, target_id, not msg.text.endswith('a'))

			elif msg.text.startswith('/warn'):
				user_id = await self.conn.get_user_id(msg)
				if user_id is None or not user_id['user_id']:
					return
				user_id = user_id['user_id']
				target_id = await self.conn.get_reply_id_Reverse(msg)
				reason = ' '.join(msg.text.split(' ')[1:])
				dry_run = msg.text.split()[0].endswith('d')
				fwd_msg = None
				if self.warn_evidence_history_channel != 0:
					fwd_msg = (await self.app.forward_messages(self.warn_evidence_history_channel, self.target_group,
															  target_id, True)).message_id
				if dry_run:
					await self.botapp.send_message(self.fudu_group, await self.generate_warn_message(user_id, reason),
												   reply_to_message_id=msg.reply_to_message.message_id)
				else:
					warn_id = await self.conn.insert_new_warn(user_id, reason, fwd_msg)
					warn_msg = await self.botapp.send_message(self.target_group,
															  await self.generate_warn_message(user_id, reason),
															  reply_to_message_id=target_id)
					await self.botapp.send_message(self.fudu_group, _T('WARN SENT TO {}, Total warn {} time(s)').format(
						TextParser.parse_user(user_id), await self.conn.query_warn_by_user(user_id)),
												   parse_mode='markdown', reply_to_message_id=msg.message_id,
												   reply_markup=InlineKeyboardMarkup(inline_keyboard=[
													   [InlineKeyboardButton(text=_T('RECALL'),
																			 callback_data=f'warndel {warn_msg.message_id} {warn_id}')]
												   ]))

		else:  # Not reply message
			if msg.text == '/ban':
				await client.send_message(msg.chat.id, _T(
					'Reply to the user you wish to restrict, if you want to kick this user, please use the /kick command.'))

			elif msg.text == '/join':
				await self.botapp.send_message(msg.chat.id, 'Click button to join name history channel',
											   reply_to_message_id=msg.message_id,
											   reply_markup=InlineKeyboardMarkup(inline_keyboard=[
												   [InlineKeyboardButton(text='Click me',
																		 url='https://t.me/joinchat/AAAAAFKmiao-ayZD0M7jrA')]
											   ]))

			elif msg.text.startswith('/grant'):
				user_id = msg.text.split()[-1]
				await self.botapp.send_message(msg.chat.id,
											   'Do you want to grant user {}?'.format(TextParser.parse_user(user_id)),
											   disable_notification=True,
											   reply_to_message_id=msg.message_id, reply_markup=InlineKeyboardMarkup([
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
						TextParser.parse_user(msg.reply_to_message.from_user.id)
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
			'<b>Warning:</b> You are requesting forwarding an authorized user\'s message to the main group, please comfirm your action.',
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
					if await self.conn.query1("SELECT * FROM `ingroup` WHERE `user_id` = %s", new_user_id) is not None:
						await self.conn.execute("DELETE FROM `ingroup` WHERE `user_id` = %s", new_user_id)
						raise BotController.ByPassVerify()
					await self.botapp.kick_chat_member(self.target_group, new_user_id)
					await self.botapp.send_message(self.fudu_group, 'Kicked challenge failure user {}'.format(
						TextParser.parse_user(new_user_id)), 'markdown')
			except BotController.ByPassVerify:
				pass
			except:
				traceback.print_exc()
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
			) \
				if msg.new_chat_members[0].id != msg.from_user.id else \
				await client.send_message(
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
		if msg.via_bot and msg.via_bot.id == 166035794: return
		if await self.conn.get_id(msg.message_id) is None:
			await asyncio.sleep(2)
			if await self.conn.get_id(msg.message_id) is None:
				return logger.error('Editing Failure: get_id return None')
		try:
			await (client.edit_message_text if msg.text else client.edit_message_caption)(self.fudu_group,
																					await self.conn.get_id(
																						msg.message_id),
																					TextParser(msg).get_full_message(),
																					'html')
		except:
			traceback.print_exc()

	async def handle_sticker(self, client: Client, msg: Message) -> None:
		await self.conn.insert(
			msg,
			await client.send_message(
				self.fudu_group,
				'{} {} sticker'.format(
					TextParser(msg).get_full_message(),
					msg.sticker.emoji
				),
				'html',
				True,
				True,
				reply_to_message_id=await self.conn.get_reply_id(msg),
			)
		)

	async def _get_reply_id(self, msg: Message, reverse: bool = False) -> Optional[int]:
		if msg.reply_to_message is None:
			return None
		return await self.conn.get_id(msg.reply_to_message.message_id, reverse)

	async def send_media(self, client: Client, msg: Message, send_to: int, captain: str, reverse: bool = False) -> None:
		msg_type = self.get_file_type(msg)
		while True:
			try:
				_msg = await client.send_cached_media(
					send_to,
					self.get_file_id(msg, msg_type),
					self.get_file_ref(msg, msg_type),
					captain,
					'html',
					True,
					await self._get_reply_id(msg, reverse)
				)
				if reverse:
					await self.conn.insert_ex(_msg.message_id, msg.caption.split()[1])
				else:
					await self.conn.insert(msg, _msg)
				break

			except pyrogram.errors.FloodWait as e:
				logger.warning('Pause %d seconds because 420 flood wait', e.x)
				time.sleep(e.x)
			except:
				traceback.print_exc()
				break

	async def handle_all_media(self, client: Client, msg: Message) -> None:
		await self.send_media(client, msg, self.fudu_group, TextParser(msg).get_full_message())

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
		if msg.text.startswith('/') and re.match(r'^\/\w+(@\w*)?$', msg.text): return
		await self.conn.insert(
			msg,
			await client.send_message(
				self.fudu_group,
				TextParser(msg).get_full_message(),
				'html',
				not msg.web_page,
				True,
				reply_to_message_id=await self.conn.get_reply_id(msg)
			)
		)

	async def handle_bot_send_media(self, client: Client, msg: Message) -> None:
		await self.send_media(client, msg, self.target_group, ' '.join(TextParser(msg).split_offset().split(' ')[2:]),
							  True)

	async def handle_incoming(self, client: Client, msg: Message) -> None:
		await client.send(
			api.functions.channels.ReadHistory(channel=await client.resolve_peer(msg.chat.id), max_id=msg.message_id))
		if msg.reply_to_message:
			await client.send(api.functions.messages.ReadMentions(peer=await client.resolve_peer(msg.chat.id)))
		if msg.text == '/auth' and msg.reply_to_message:
			return await self.func_auth_process(client, msg)

		if not self.auth_system.check_ex(msg.from_user.id): return
		if msg.text and re.match(
				r'^\/(bot (on|off)|del|getn?|fw|ban( ([1-9]\d*)[smhd]|f)?|kick( confirm| -?\d+)?|status|b?o(n|ff)|join|promote( \d+)?|set [a-zA-Z]|pina?|su(do)?|title .*|warnd? .*|grant \d+|report)$',
				msg.text
		):
			return await self.process_imcoming_command(client, msg)
		if msg.text and msg.text.startswith('/') and re.match(r'^\/\w+(@\w*)?$', msg.text): return
		if self.auth_system.check_muted(msg.from_user.id) or (msg.text and msg.text.startswith('//')) or (
				msg.caption and msg.caption.startswith('//')): return
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
					not msg.web_page,
					reply_to_message_id=await self.conn.get_reply_id_Reverse(msg),
				)).message_id, msg.message_id
			)

		elif msg.photo or msg.video or msg.animation or msg.document:
			_type = self.get_file_type(msg)
			await (await client.send_cached_media(
				msg.chat.id,
				self.get_file_id(msg, _type),
				self.get_file_ref(msg, _type),
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
				traceback.print_exc()

		elif msg.sticker:
			await self.conn.insert_ex(
				(await self.botapp.send_sticker(self.target_group, msg.sticker.file_id,
												reply_to_message_id=await self.conn.get_reply_id_Reverse(
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
					await client.edit_message_text(msg.message.chat.id,
												   msg.message.message_id,
												   'Restrictions applied to {} Duration: {}'.format(
													   TextParser.parse_user(_user_id),
													   '{}s'.format(dur) if int(dur) else 'Forever'),
												   parse_mode='markdown',
												   reply_markup=InlineKeyboardMarkup([
													   [InlineKeyboardButton(text='UNBAN',
																			 callback_data='unban {}'.format(_user_id))]
												   ]
												   )
												   )

			elif msg.data.startswith('unban'):
				if await client.restrict_chat_member(self.target_group, int(args[-1]), ChatPermissions(
						can_send_messages=True,
						can_send_stickers=True, can_send_polls=True, can_add_web_page_previews=True,
						can_send_media_messages=True,
						can_pin_messages=True, can_invite_users=True, can_change_info=True
				)):
					await msg.answer('Unban successfully')
					await client.edit_message_reply_markup(msg.message.chat.id, msg.message.message_id)

			elif msg.data.startswith('auth'):
				if time.time() - msg.message.date > 20:
					raise OperationTimeoutError()
				await self.auth_system.add_user(args[1])
				await msg.answer(f'{args[1]} added to the authorized group')
				await msg.message.edit(f'{args[1]} added to the authorized group')

			elif msg.data.startswith('fwd'):
				if time.time() - msg.message.date > 30:
					raise OperationTimeoutError()
				if 'original' in msg.data:
					# Process original forward
					await self.conn.insert_ex((await client.forward_messages(self.target_group, msg.message.chat.id,
																			msg.message.reply_to_message.message_id)).message_id,
											  msg.message.reply_to_message.message_id)
				else:
					await self.conn.insert_ex((await client.send_message(self.target_group, TextParser(
						msg.message.reply_to_message).split_offset(), 'html')).message_id,
											  msg.message.reply_to_message.message_id)
				await msg.answer('Forward successfully')
				await msg.message.delete()

			elif msg.data.startswith('kick'):
				if not msg.data.startswith('kickc') and msg.from_user.id != int(args[-2]):
					raise OperatorError()
				if 'true' not in msg.data:
					if not msg.data.startswith('kickc') and time.time() - msg.message.date > 15:
						raise OperationTimeoutError()
					client_args = [
						msg.message.chat.id,
						msg.message.message_id,
						'Press the button again to kick {}\nThis confirmation message will expire after 10 seconds.'.format(
							TextParser.parse_user(args[-1])
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
						'Please press again to make sure. Do you really want to kick {} ?'.format(args[-1]), True)
				else:
					if msg.message.edit_date:
						if time.time() - msg.message.edit_date > 10:
							raise OperationTimeoutError()
					else:
						if time.time() - msg.message.date > 10:
							raise OperationTimeoutError()
					await client.kick_chat_member(self.target_group, int(args[-1]))
					await msg.answer('Kicked {}'.format(args[-1]))
					await msg.message.edit('Kicked {}'.format(TextParser.parse_user(args[-1])))

			elif msg.data.startswith('promote'):
				if not msg.data.endswith('undo'):
					if time.time() - msg.message.date > 10:
						raise OperationTimeoutError()
					await self.botapp.promote_chat_member(
						self.target_group, int(args[1]), True, can_delete_messages=True, can_restrict_members=True,
						can_invite_users=True, can_pin_messages=True, can_promote_members=True)
					await msg.answer('Promote successfully')
					await msg.message.edit('Promoted {}'.format(TextParser.parse_user(int(args[1]))),
										   parse_mode='markdown',
										   reply_markup=InlineKeyboardMarkup(inline_keyboard=[
											   [InlineKeyboardButton(text='UNDO',
																	 callback_data=' '.join((msg.data, 'undo')))],
											   [InlineKeyboardButton(text='remove button', callback_data='rm')]
										   ])
										   )
				else:
					await self.botapp.promote_chat_member(self.target_group, int(args[1]), False,
														  can_delete_messages=False, can_invite_users=False,
														  can_restrict_members=False)
					await msg.answer('Undo Promote successfully')
					await msg.message.edit('Undo promoted {}'.format(TextParser.parse_user(int(args[1]))),
										   parse_mode='markdown')

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
						if x == 'delete':
							grant_args.update({'can_delete_messages': True})
						if x == 'restrict':
							grant_args.update({'can_restrict_members': True})
						if x == 'pin':
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
						'Do you want to grant user {}?\n\nSelect privileges:\n{}'.format(TextParser.parse_user(args[1]),
																						 '\n'.join(select_privileges)),
						reply_markup=msg.message.reply_markup)

			elif msg.data == 'unpin':
				await self.botapp.unpin_chat_message(self.target_group)
				await msg.message.edit_reply_markup()
				await msg.answer()

			elif msg.data.startswith('warndel'):
				await self.botapp.delete_messages(self.target_group, int(args[1]))
				await self.conn.delete_warn_by_id(args[2])
				await msg.message.edit_reply_markup()
				await msg.answer()

		except OperationTimeoutError:
			await msg.answer('Confirmation time out')
			await client.edit_message_reply_markup(msg.message.chat.id, msg.message.message_id)
		except OperatorError:
			await msg.answer('The operator should be {}.'.format(args[-2]), True)
		except:
			await self.app.send_message(config.getint('custom_service', 'help_group'),
										traceback.format_exc().splitlines()[-1])
			traceback.print_exc()


async def main():
	logging.getLogger("pyrogram").setLevel(logging.WARNING)
	logging.basicConfig(level=logging.DEBUG,
						format='%(asctime)s - %(levelname)s - %(funcName)s - %(lineno)d - %(message)s')
	bot = await BotController.create()
	await bot.start()
	await bot.idle()
	await bot.stop()


if __name__ == '__main__':
	asyncio.get_event_loop().run_until_complete(main())
