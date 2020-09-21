#!/usr/bin/env python
# -*- coding: utf-8 -*-
# transfer2pg.py
# Copyright (C) 2019-2020 github.com/googlehosts Group:Z
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
from configparser import ConfigParser
from datetime import datetime
from typing import Any, Callable, List, Tuple, Union

import aiomysql
import asyncpg

config = ConfigParser()
config.read('config.ini')
host = config.get('database', 'host')
port = config.get('pgsql', 'port')  # only for pgsql
muser = config.get('database', 'user')
mpasswd = config.get('database', 'passwd')
puser = config.get('pgsql', 'user')
ppasswd = config.get('pgsql', 'passwd')
mdatabase = config.get('database', 'db_name')
pdatabase = config.get('pgsql', 'database')


async def main() -> None:
    pgsql_connection = await asyncpg.connect(host=host, port=port, user=puser, password=ppasswd, database=pdatabase)
    mysql_connection = await aiomysql.create_pool(
        host=host,
        user=muser,
        password=mpasswd,
        db=mdatabase,
        charset='utf8mb4',
        cursorclass=aiomysql.cursors.Cursor,
    )
    if input('Do you want to delete all data? [y/N]: ').strip().lower() == 'y':
        await clean(pgsql_connection)
        print('Clear database successfully')
    else:
        print('Skipped clear database')
    async with mysql_connection.acquire() as conn:
        async with conn.cursor() as cursor:
            await exec_and_insert(cursor, "SELECT * FROM answer_history", pgsql_connection,
                                  '''INSERT INTO "answer_history" VALUES ($1, $2, $3, $4)''')
            await exec_and_insert(cursor, "SELECT * FROM auth_user", pgsql_connection,
                                  '''INSERT INTO "auth_user" VALUES ($1, $2, $3, $4)''', transfer_stage_1)
            await exec_and_insert(cursor, "SELECT * FROM banlist", pgsql_connection,
                                  '''INSERT INTO "banlist" VALUES ($1)''')
            await exec_and_insert(cursor, "SELECT * FROM exam_user_session", pgsql_connection,
                                  '''INSERT INTO "exam_user_session" VALUES ($1, 1, $2, $3, $4, $5, $6, $7, $8)''',
                                  transfer_stage_2)
            await exec_and_insert(cursor, "SELECT * FROM msg_id", pgsql_connection,
                                  '''INSERT INTO "msg_id" VALUES ($1, $2, $3, $4)''')
            await exec_and_insert(cursor, "SELECT * FROM reasons", pgsql_connection,
                                  '''INSERT INTO reasons VALUES ($1, $2, $3, $4, $5)''')
            await exec_and_insert(cursor, "SELECT * FROM tickets", pgsql_connection,
                                  '''INSERT INTO tickets VALUES ($1, $2, $3, $4, $5, $6, $7)''')
            await exec_and_insert(cursor, "SELECT * FROM tickets_user", pgsql_connection,
                                  '''INSERT INTO tickets_user VALUES ($1, $2, $3, $4, $5, $6, $7)''',
                                  transfer_stage_3)
    await pgsql_connection.close()
    mysql_connection.close()
    await mysql_connection.wait_closed()


def transfer_stage_1(obj: Tuple[int, str, str, str]) -> Tuple[Union[bool, Any], ...]:
    def str2bool(x: str) -> bool:
        return True if x == 'Y' else False
    return tuple(map(lambda x: str2bool(x) if isinstance(x, str) else x, obj))


def transfer_stage_2(obj: Tuple[int, int, datetime, int, int, int, int, int]
                     ) -> Tuple[int, int, datetime, bool, bool, bool, bool, int]:
    return tuple((*obj[:3], *(bool(obj[i]) for i in range(3, 7)), obj[7], ))


def transfer_stage_3(obj: Tuple[int, datetime, datetime, int, datetime, int, str]
                     ) -> Tuple[int, datetime, datetime, bool, datetime, int, str]:
    return tuple((*obj[:3], bool(obj[3]), *obj[4:]))


async def exec_and_insert(cursor, sql: str, pg_connection, insert_sql: str,
                          process: Callable[[Any], Any] = None) -> None:
    print('Processing table:', sql[13:])
    await cursor.execute(sql)
    for sql_obj in await cursor.fetchall():
        if process is not None:
            sql_obj = process(sql_obj)
        await pg_connection.execute(insert_sql, *sql_obj)
    return


async def clean(pgsql_connection: asyncpg.connection) -> None:
    await pgsql_connection.execute('''TRUNCATE "answer_history"''')
    await pgsql_connection.execute('''TRUNCATE "auth_user"''')
    await pgsql_connection.execute('''TRUNCATE "banlist"''')
    await pgsql_connection.execute('''TRUNCATE "exam_user_session"''')
    await pgsql_connection.execute('''TRUNCATE "msg_id"''')
    await pgsql_connection.execute('''TRUNCATE "reasons"''')
    await pgsql_connection.execute('''TRUNCATE "tickets"''')
    await pgsql_connection.execute('''TRUNCATE "tickets_user"''')

if __name__ == '__main__':
    asyncio.get_event_loop().run_until_complete(main())
