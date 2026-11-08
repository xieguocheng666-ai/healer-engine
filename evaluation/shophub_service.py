"""用 ShopHub 自己的虚拟环境运行；只操作本轮新建的专用数据库。"""
import argparse
import json
import os
import re
import sys
from pathlib import Path


def validate_database(name):
    if not re.fullmatch(r'healer_eval_[a-f0-9]{8}', name):
        raise ValueError('Only a generated healer_eval_<8 hex digits> database is allowed')
    return name


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--database', required=True)
    parser.add_argument('--ownership', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8001)
    parser.add_argument('--cleanup', action='store_true')
    args = parser.parse_args()
    database = validate_database(args.database)
    from dotenv import load_dotenv
    import pymysql
    load_dotenv(args.source / '.env')
    os.environ['DB_NAME'] = database
    os.environ['DEBUG_MODE'] = '0'
    connection = pymysql.connect(host=os.getenv('DB_HOST', '127.0.0.1'),
        port=int(os.getenv('DB_PORT', '3306')), user=os.getenv('DB_USER', 'root'),
        password=os.getenv('DB_PASSWORD', ''), charset='utf8mb4', connect_timeout=5,
        cursorclass=pymysql.cursors.DictCursor)
    try:
        with connection.cursor() as cursor:
            if args.cleanup:
                if not args.ownership.exists():
                    print(json.dumps({'cleanup': 'not_owned_no_action'}))
                    return
                marker = json.loads(args.ownership.read_text(encoding='utf-8'))
                if marker != {'database': database, 'source': str(args.source.resolve())}:
                    raise ValueError('Ownership marker does not match')
                cursor.execute(f'DROP DATABASE `{database}`')
                connection.commit()
                cursor.execute('SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s', (database,))
                if cursor.fetchone():
                    raise RuntimeError('Temporary database still exists')
                args.ownership.unlink()
                print(json.dumps({'cleanup': 'database_removed', 'database': database}))
                return
            # CREATE 不使用 IF NOT EXISTS，绝不接管已有数据库。
            cursor.execute(f'CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci')
            args.ownership.write_text(json.dumps({'database': database, 'source': str(args.source.resolve())}), encoding='utf-8')
            cursor.execute(f'USE `{database}`')
            sys.path.insert(0, str(args.source.resolve()))
            from server import db
            for statement in db.DDL:
                cursor.execute(statement)
            db._seed(cursor)
        connection.commit()
    finally:
        connection.close()
    import uvicorn
    uvicorn.run('server.main:app', host='127.0.0.1', port=args.port, log_level='warning')


if __name__ == '__main__':
    main()
