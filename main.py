# -*- coding: utf-8 -*-
# main.py
# Copyright (C) 2018 github.com/googlehosts Group:Z 
#
# This module is part of googlehosts/telegram-repeater and is released under
# the AGPL v3 License: https://www.gnu.org/licenses/agpl-3.0.txt
from configparser import ConfigParser
from pyrogram import Client, Filters, api, MessageEntity, Message
from threading import Thread
import telepot
import os, re, traceback
import signal
import queue, time

global app, bot
config = ConfigParser()
config.read('config.ini')

class auth_system:
	authed_user = eval(config['fuduji']['auth_user'])
	ignore_user = eval(config['fuduji']['ignore_user'])
	@staticmethod
	def check_ex(user_id: int):
		return user_id in auth_system.authed_user or user_id == int(config['account']['owner'])
	@staticmethod
	def add_user(user_id: int):
		auth_system.authed_user.append(user_id)
		auth_system.authed_user = list(set(auth_system.authed_user))
		config['fuduji']['auth_user'] = repr(auth_system.authed_user)
	@staticmethod
	def del_user(user_id: int):
		# TODO
		pass
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
		except:
			traceback.print_exc()
	@staticmethod
	def check(user_id: int):
		return auth_system.check_ex(user_id) and not auth_system.check_muted(user_id)

def exit_func():
	with open('config.ini', 'w') as fout:
		config.write(fout)
	app.stop()
	os._exit(0)

class botSender(Thread):
	def __init__(self):
		Thread.__init__(self)
		self.daemon = True
		self.queue = queue.Queue()
		self.start()
	def run(self):
		while True:
			r = self.queue.get()
			time.sleep(2)
			while True:
				try:
					r[0](r[1], r[2], r[3], reply_to_message_id=r[4].message_id if r[4] else None)
					break
				except:
					traceback.print_exc()
					time.sleep(5)

bot_sender = botSender()

def mute_or_unmute(r, chat_id):
	if r == 'off':
		auth_system.mute_user(chat_id)
	else:
		auth_system.unmute_user(chat_id)

class build_html_parse:
	_dict = {
		'italic': ('i', 'i'),
		'bold': ('b', 'b'),
		'code': ('code', 'code'),
		'pre': ('pre', 'pre'),
		'text_link': ('a href="{}"', 'a')
	}
	def __init__(self, msg: Message):
		self._msg = msg
	@staticmethod
	def replace2(_text: str):
		return _text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;') #.replace('\n', '<br>')
	@staticmethod
	def get_placeholder(_entry: MessageEntity, website=None):
		r = build_html_parse._dict[_entry.type]
		return (r[0].format(website), r[1])
	@staticmethod
	def parse_tag(_entry: MessageEntity):
		return '<{}>\\n</{}>'.format(*build_html_parse.get_placeholder(_entry, _entry.url)).split('\\n')
	def get_split_loc(self):
		self._split_loc_ex = [(_entry.offset, _entry.length + _entry.offset, self.parse_tag(_entry)) for _entry in self._msg['entities'] if _entry.type in ('italic', 'bold', 'code', 'pre', 'text_link')]
		self._split_loc = [item for loc in [[_split[0], _split[1]] for _split in self._split_loc_ex] for item in loc]
		self._tag = [_split[2] for _split in self._split_loc_ex]
	def split_offset(self):
		if self._msg.entities is None: return self.replace2(self._msg.text)
		self.get_split_loc()
		# if all msg is bot command or url
		if not len(self._split_loc): return self.replace2(self._msg.text)
		_msg_parsed = [self._msg.text[:self._split_loc[0]]]
		for _loc in range(1, len(self._split_loc)):
			#assert isinstance(_loc, int)
			#print(self._split_loc[_loc - 1], self._split_loc[_loc])
			_msg_parsed.append('{}{}{}'.format(self._tag[_loc//2][0], self.replace2(self._msg.text[self._split_loc[_loc - 1]: self._split_loc[_loc]]), self._tag[_loc//2][1]) if _loc % 2 else self.replace2(self._msg.text[self._split_loc[_loc - 1]: self._split_loc[_loc]]))
		_msg_parsed.append(self._msg.text[self._split_loc[-1]:])
		return ''.join(_msg_parsed)

def main():
	#@app.on_message(Filters.new_chat_members)
	#def handle(client, msg):
	#	if msg['chat']['id'] != 0:
	#		return
	#	for x in msg['new_chat_members']:
	#		client.send(api.functions.channels.EditBanned(client.resolve_peer(msg['chat']['id']),
	#			client.resolve_peer(x['id']), api.types.ChannelBannedRights(0, view_messages=False)))
	#		client.send_message(msg['chat']['id'], 'Auto kicked {}'.format(x['id']))

	@app.on_message(Filters.chat(int(config['fuduji']['target_group'])) & Filters.sticker)
	def handle_sticker(client: Client, msg: Message):
		if not auth_system.check(msg.from_user.id): return
		client.delete_messages(msg.chat.id, msg.message_id)
		bot.sendSticker(msg.chat.id, msg.sticker.file_id, reply_to_message_id=msg.reply_to_message.message_id if msg.reply_to_message else None)

	@app.on_message(Filters.chat(int(config['fuduji']['target_group'])) & Filters.document)
	def handle_document(client: Client, msg: Message):
		if not auth_system.check(msg.from_user.id): return
		client.delete_messages(msg.chat.id, msg.message_id)
		bot_sender.queue.put_nowait((bot.sendDocument, msg.chat.id, msg.document.file_id, msg.caption, msg.reply_to_message))

	@app.on_message(Filters.chat(int(config['fuduji']['target_group'])) & Filters.forwarded)
	def handle_forward(client: Client, msg:Message):
		if not auth_system.check(msg.from_user.id): return
		bot.forwardMessage(msg.chat.id, msg.chat.id, msg.message_id)
		client.delete_messages(msg.chat.id, msg.message_id)

	@app.on_message(Filters.chat(int(config['fuduji']['target_group'])) & Filters.photo)
	def handle_photo(client: Client, msg: Message):
		if not auth_system.check(msg.from_user.id): return
		client.delete_messages(msg.chat.id, msg['message_id'])
		bot_sender.queue.put_nowait((bot.sendPhoto, msg.chat.id, msg['photo'][0]['file_id'], msg.caption, msg.reply_to_message))

	@app.on_message(Filters.chat(int(config['fuduji']['target_group'])) & Filters.gif)
	def handle_gif(client: Client, msg: Message):
		if not auth_system.check(msg.from_user.id): return
		client.delete_messages(msg.chat.id, msg['message_id'])
		bot_sender.queue.put_nowait((bot.sendVideo, msg.chat.id, msg.gif.file_id, msg.caption, msg.reply_to_message))

	@app.on_message(Filters.chat(int(config['fuduji']['target_group'])) & Filters.video)
	def handle_video(client: Client, msg: Message):
		if not auth_system.check(msg.from_user.id): return
		client.delete_messages(msg.chat.id, msg['message_id'])
		bot_sender.queue.put_nowait((bot.sendVideo, msg.chat.id, msg.video.file_id, msg.caption, msg.reply_to_message))

	@app.on_message(Filters.chat(int(config['fuduji']['target_group'])) & Filters.text)
	def handle_speak(client: Client, msg: Message):
		#r = re.match(r'^\/locate (\d+)$', msg['text'])
		#if r:
		#	client.send_message(msg.chat.id, 'located', reply_to_message_id=int(r.group(1)))
		#	return
		if not auth_system.check_ex(msg.from_user.id):
			return
		r = re.match(r'^\/bot (on|off)$', msg['text'])
		if r:
			client.delete_messages(msg.chat.id, msg['message_id'])
			if msg.reply_to_message:
				mute_or_unmute(r.group(1), msg.reply_to_message.from_user.id)
				return
			mute_or_unmute(r.group(1), msg.from_user.id)
			return
		if msg.text == '/del' and msg.reply_to_message:
			#print('processing delete')
			client.delete_messages(msg.chat.id, (msg.reply_to_message.message_id, msg.message_id))
			return
		# Repeat
		if auth_system.check_muted(msg.from_user.id):
			return
		client.delete_messages(msg.chat.id, msg['message_id'])
		bot.sendMessage(msg.chat.id, build_html_parse(msg).split_offset(), reply_to_message_id=msg.reply_to_message.message_id if msg.reply_to_message else None,
			parse_mode='html', disable_web_page_preview=True)
			#client.send_message(msg.chat.id, msg['text'], reply_to_message_id=get_reply_id(msg))

	@app.on_message(Filters.command('a'))
	def handle_add_auth(client: Client, msg: Message):
		client.send(api.functions.messages.ReadHistory(client.resolve_peer(msg.chat.id), msg['message_id']))
		if len(msg['text']) < 4:
			return
		if msg['text'][3:] == config['fuduji']['auth_token']:
			if not auth_system.check_ex(msg.chat.id):
				auth_system.add_user(msg.chat.id)
				client.send_message(msg.chat.id, 'Passed the certification')
			else:
				client.send_message(msg.chat.id, 'Please do not double submit certification')

	app.start()
	signal.signal(signal.SIGINT, exit_func)
	app.idle()

def init():
	global app, bot
	app = Client(session_name='session',
		api_id=config['account']['api_id'],
		api_hash=config['account']['api_hash'])
	bot = telepot.Bot(config['account']['api_key'])

if __name__ == '__main__':
	init()
	main()