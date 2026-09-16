import asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

engine = create_async_engine("sqlite+aiosqlite:///demo.db")
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

async def main():
    async with AsyncSessionLocal() as session:
        # 建表（项目里由 init_db.sql 完成，这里为了演示临时建一张）
        await session.execute(text("DROP TABLE IF EXISTS users"))
        await session.execute(text("""
            CREATE TABLE users (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                username  TEXT,
                role      TEXT,
                is_active BOOLEAN
            )
        """))

        # 增（INSERT）：参数化
        await session.execute(
            text("INSERT INTO users (username, role, is_active) VALUES (:u, :r, :a)"),
            [{"u": "student01", "r": "student", "a": True},{"u": "student02", "r": "student02", "a": True}]
        )
        await session.execute(
            text("INSERT INTO users (username, role, is_active) VALUES (:u, :r, :a)"),
            {"u": "teacher01", "r": "teacher", "a": True},
        )

        # 查（SELECT）
        result = await session.execute(
            text("SELECT id, username, role FROM users WHERE role = :r"),
            {"r": "student"},
        )
        row = result.fetchone()
        print(f'type(row): {row}')
        print("查到学生：", row.id, row.username, row.role)
        print("查到学生：", row[0], row[1], row[2])

        # 改（UPDATE）
        await session.execute(
            text("UPDATE users SET is_active = :a WHERE username = :u"),
            {"a": False, "u": "student01"},
        )

        # 删（DELETE）
        await session.execute(
            text("DELETE FROM users WHERE username = :u"),
            {"u": "teacher01"},
        )

        # 提交事务：让上面所有改动真正生效
        await session.commit()

        # 验证：看看现在表里剩什么
        all_rows = (await session.execute(text("SELECT username, is_active FROM users")))
        print(f'all_rows1={all_rows}')
        rows = all_rows.fetchall()
        print(f'all_rows2={rows}')
        all_rows = (await session.execute(text("SELECT username, is_active FROM users"))).mappings().all()
        print(f'all_rows2={all_rows}')
        print("最终数据：", [dict(r) for r in all_rows])

asyncio.run(main())