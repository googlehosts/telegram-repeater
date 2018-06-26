# -*- coding: utf-8 -*-
# main.py
# Copyright (C) 2018 github.com/googlehosts Group:Z 
#
# This module is part of googlehosts/telegram-repeater and is released under
# the AGPL v3 License: https://www.gnu.org/licenses/agpl-3.0.txt
from configparser import ConfigParser
from pyrogram import Client, Filters, api
from threading import Thread
import telepot
import os, re, traceback
import signal

global app, bot
config = ConfigParser()
config.read('config.ini')

class auth_system:
	authed_user = eval(config['fuduji']['auth_user'])
	ignore_user = eval(config['fuduji']['ignore_user'])
	@staticmethod
	def check(user_id):
		return user_id in auth_system.authed_user or user_id == int(config['account']['owner'])
	@staticmethod
	def add_user(user_id):
		auth_system.authed_user.append(user_id)
		auth_system.authed_user = list(set(auth_system.authed_user))
		config['fuduji']['auth_user'] = repr(auth_system.authed_user)
	@staticmethod
	def del_user(user_id):
		# TODO
		pass
	@staticmethod
	def check_muted(user_id):
		return user_id in auth_system.ignore_user
	@staticmethod
	def mute_user(user_id):
		auth_system.ignore_user.append(user_id)
		auth_system.ignore_user = list(set(auth_system.ignore_user))
		config['fuduji']['ignore_user'] = repr(auth_system.ignore_user)
	@staticmethod
	def unmute_user(user_id):
		try:
			del auth_system.ignore_user[auth_system.ignore_user.index(user_id)]
			config['fuduji']['ignore_user'] = repr(auth_system.ignore_user)
		except:
			traceback.print_exc()

def exit_func():
	with open('config.ini', 'w') as fout:
		config.write(fout)
	app.stop()
	os._exit(0)

def get_reply_id(msg):
	try:
		return msg['reply_to_message']['message_id']
	except TypeError:
		return None

def main():
	#@app.on_message(Filters.new_chat_members)
	#def handle(client, msg):
	#	if msg['chat']['id'] != 0:
	#		return
	#	for x in msg['new_chat_members']:
	#		client.send(api.functions.channels.EditBanned(client.resolve_peer(msg['chat']['id']),
	#			client.resolve_peer(x['id']), api.types.ChannelBannedRights(0, view_messages=False)))
	#		client.send_message(msg['chat']['id'], 'Auto kicked {}'.format(x['id']))
	
	@app.on_message(Filters.group & Filters.text)
	def handle_speak(client, msg):
		r = re.match(r'^\/bot (on|off)$', msg['text'])
		if r and auth_system.check(msg['from_user']['id']):
			client.delete_messages(msg['chat']['id'], msg['message_id'])
			if r.group(1) == 'off':
				auth_system.mute_user(msg['from_user']['id'])
			else:
				auth_system.unmute_user(msg['from_user']['id'])
			return
		if not (msg['chat']['id'] == int(config['fuduji']['target_group']) and auth_system.check(msg['from_user']['id']) and \
			not auth_system.check_muted(msg['from_user']['id'])):
			return
		client.delete_messages(msg['chat']['id'], msg['message_id'])
		if get_reply_id(msg) is not None:
			bot.sendMessage(msg['chat']['id'], msg['text'], reply_to_message_id=get_reply_id(msg))
			#client.send_message(msg['chat']['id'], msg['text'], reply_to_message_id=get_reply_id(msg))
		else:
			bot.sendMessage(msg['chat']['id'], msg['text'])

	@app.on_message(Filters.command('a'))
	def handle_add_auth(client, msg):
		client.send(api.functions.messages.ReadHistory(client.resolve_peer(msg['chat']['id']), msg['message_id']))
		if len(msg['text']) < 4:
			client.send_message(msg['chat']['id'], 'Current repeater status: {}'.format(not auth_system.check_muted(msg['chat']['id'])))
			return
		if msg['text'][3:] == config['fuduji']['auth_token']:
			if not auth_system.check(msg['chat']['id']):
				auth_system.add_user(msg['chat']['id'])
				client.send_message(msg['chat']['id'], 'Passed the certification')
			else:
				client.send_message(msg['chat']['id'], 'Please do not double submit certification')
	app.start()
	signal.signal(signal.SIGINT, exit_func)
	app.idle()

def init():
	global app, bot
	bot = telepot.Bot(config['account']['api_key'])
	app = Client(session_name='session',
		api_id=config['account']['api_id'],
		api_hash=config['account']['api_hash'])

if __name__ == '__main__':
	init()
	main()