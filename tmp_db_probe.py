import asyncio
import asyncpg

async def attempt(user, password):
    try:
        conn = await asyncpg.connect(host='127.0.0.1', port=5432, user=user, password=password, database='postgres')
        await conn.close()
        print(user, 'OK')
    except Exception as e:
        print(user, 'ERR', type(e).__name__, e)

async def main():
    creds = [
        ('macro_user', 'MyMacro2026Pass'),
        ('postgres', ''),
        ('postgres', 'postgres'),
        ('postgres', 'password'),
        ('postgres', 'MyMacro2026Pass'),
    ]
    for user, password in creds:
        await attempt(user, password)

asyncio.run(main())
