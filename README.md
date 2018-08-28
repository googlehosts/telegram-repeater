# Telegram Repeater

Repeater deletes your message and replaces it with the message the bot sends without changing the content.

## Feature

* Support rich text messages
* Support media except voice message
* Reply function available

## Operating Environment

Python 3.4 and above is required

The following libraries are required:

- pyrogram
- telepot

## Configure

* If you don't have `api_id` and `api_hash`, obtain them from [telegram](https://my.telegram.org/apps)
* Prepare two accounts, one normal, one bot
* Both accounts must be in the target group and the ordinary account must have permission to delete messages
* Copy `config.ini.default` to `config.ini`
* Parse your own `api_key` and `api_hash` in `config.ini`
* Parse your bot `api_token` in `api_key` field
* Parse the target group id in `config.ini`
* If you use your own account, parse your id in `owner` field
* Change authorized password in `auth_token` field

## Instruction

* Use `python3 main.py` or other command lines to run the program
* Log in using the account you set in the `owner` field
* If you have the authorized password, you can send `/a [auth_token]` to the owner account to get the permission
* To turn off the repeater, send `/bot off` to the target group, vice versa.

## License

[![](https://www.gnu.org/graphics/agplv3-155x51.png)](https://www.gnu.org/licenses/agpl-3.0.txt)

Copyright (C) 2018 github.com/googlehosts Group:Z

This program is free software: you can redistribute it and/or modify it under the terms of the GNU Affero General Public License as published by the Free Software Foundation, either version 3 of the License, or any later version.

This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License along with this program. If not, see <https://www.gnu.org/licenses/>.
