# Telegram Repeater

The bot forwards your messages to another group.

## Feature

* Support rich text messages.
* Support media (except voice messages).
* Reply function available.
* Anonymity: Protect the identity of the user.
* Authorised users in this group can manage the target group function, such as `/ban`, `/del`, `/kick`, etc.
* Use MySQL engine to mark the time, message ID and user ID.
* When the bot is mentioned in the target group, the user ID specified in the config file will also be mentioned in this group.
* Once promoted to admin, the bot can add new admins. 
* Full support for dynamic invitation links. 
* Full support for the ticket system. 

## Operating Environment

Python 3.4 and above is required

The following libraries are required:

- pyrogram (==0.11.0)
- pymysql

## Configure

* If you don't have `api_id` and `api_hash`, obtain them from [telegram](https://my.telegram.org/apps)
* Prepare two accounts, an ordinary one and a bot one.
* The bot account should be in the target group and the ordinary account should have permission to delete messages. 
* Copy `config.ini.default` to `config.ini`.
* Parse your own `api_key` and `api_hash` in `config.ini`.
* Parse your bot `api_token` in `api_key` field.
* Parse the target group id in `config.ini`.
* Parse the another group id in `config.ini`.
* If you use your own account, parse your id in `owner` field.
* Replace `replace_to_id` field with the user ID that the bot will be replaced with. 
* Import the preset database file into MySQL database

### Additional settings for the ticket system
* Parse the bot's token in the `custom_api_key` field of the configuration file. 
* Parse the group ID of the ticket system in config.ini. (The group ID of the ticket system should be different from the target group.)

## Instruction

* Use `python3 main.py` or other command lines to run the program.
* Log in using the account you set in the `owner` field.
* If you want to authorize a certain user, you should invite the user to this group first, then use `/auth`.
* To turn off the repeater, send `/bot off` (`/boff` also available) to the target group, vice versa.

## Available Commands

Command | Description | Reply to the message
---|---|---
`/bon` or `/boff` | Switch on/off the bot | False
`/status` | check the user's authorization status| False
`/auth` | authorize to another user | True
`/ban` | put restrictions on the target user, a certain length of time can be specified (e.g. `/ban` 1m means to restrict the user for one minute) | True
`/kick` | remove the user from the target group | True
`/fw` | forward a message to the target group using the bot account | True
`/get` | forward the original message to this group | True
`/del` | delete the selected message in the target group | True
`/p` | gain admin access immediately for yourself in the target group | False
`/promote` | authorise other users to become admins | True

## Special Thanks

Special thanks to Group:J, who helped me with the translation.

## License

[![](https://www.gnu.org/graphics/agplv3-155x51.png)](https://www.gnu.org/licenses/agpl-3.0.txt)

Copyright (C) 2018-2019 github.com/googlehosts Group:Z

This program is free software: you can redistribute it and/or modify it under the terms of the GNU Affero General Public License as published by the Free Software Foundation, either version 3 of the License, or any later version.

This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License along with this program. If not, see <https://www.gnu.org/licenses/>.
