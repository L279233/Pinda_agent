"""
============================================================
「麻雀虽小五脏俱全」—— 把你两个真实文件的所有零件拼成一个能跑的小项目。
用 SQLite，不用安装 PostgreSQL，零基础设施就能跑通整条链路。

跑通它 = 看懂你真实的 dependencies.py + migrations.py。

运行：
  pip install fastapi uvicorn "python-jose[cryptography]" aiosqlite sqlalchemy
  uvicorn demo_mini_project:app --reload
  打开 http://127.0.0.1:8000/docs

测试流程（重点！）：
  1. 先调 POST /login，复制返回的 access_token
  2. 点右上角「Authorize」按钮，把 token 粘进去（这一步 = 模拟前端在请求头带 token）
  3. 再调 GET /me，就能看到受保护接口正常返回
  4. 不授权直接调 /me，会返回 401 —— 这就是 get_current_user 在拦人
============================================================
"""

from contextlib import asynccontextmanager

import uvicorn
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy import text
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt

# ====== 配置（真实项目里这些在 config.py 的 get_settings()）======
DATABASE_URL = "sqlite+aiosqlite:///./demo.db"   # SQLite 文件，省去装 PostgreSQL
JWT_SECRET = "only-server-knows-this-secret"
JWT_ALGO = "HS256"

# ====== 连接池（= 你 dependencies.py 的 engine）======
# 注意：真实项目连 PostgreSQL 时这里会写 pool_size=5, max_overflow=10；
#       SQLite 是单文件，连接池意义不大，所以这里省略这两个参数。
engine = create_async_engine(DATABASE_URL, echo=False)

# ====== 会话工厂（= 你 dependencies.py 的 AsyncSessionLocal）======
AsyncSessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False,
)

# ====== 迁移（= 你 migrations.py 的 run_migrations）======
_MIGRATIONS = [
    # 用 CREATE TABLE IF NOT EXISTS 演示「可重复执行也不报错」= 幂等
    # （你真实文件里是 ALTER TABLE ... ADD COLUMN IF NOT EXISTS，道理一样）
    "CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, role TEXT, tenant_id TEXT)",
    "INSERT OR IGNORE INTO users (id, role, tenant_id) VALUES ('user-123', 'student', 'school-1')",
]


async def run_migrations():
    async with AsyncSessionLocal() as session:
        for sql in _MIGRATIONS:
            try:
                await session.execute(text(sql))
            except Exception as e:
                print(f"[MIGRATION WARNING] {sql[:50]}... 失败: {e}")
        await session.commit()
    print("✓ 迁移完成（再启动一次也不会报错，因为是幂等的）")


# ====== get_db（= 你 dependencies.py 的 get_db，demo 第2级 yield）======
async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


# ====== get_current_user（= 你 dependencies.py 的验票员）======
security = HTTPBearer()   # 自动从请求头 Authorization: Bearer xxx 提取 token


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict:
    cred_err = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无效的认证凭据",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        # credentials.credentials 就是前端在请求头里带来的那串纯 token
        payload = jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGO])
        if payload.get("sub") is None:
            raise cred_err
        return {
            "user_id": payload["sub"],
            "role": payload.get("role"),
            "tenant_id": payload.get("tenant_id"),
        }
    except JWTError:
        raise cred_err


# ====== 启动时自动跑迁移（= 真实项目 main.py 的 lifespan）======
@asynccontextmanager
async def lifespan(app: FastAPI):
    await run_migrations()   # 程序一启动就执行补丁
    yield                    # 之后正常对外服务

app = FastAPI(lifespan=lifespan)


# ---- /login：登录发 token（demo 里缺的那一环，token 就是在这造出来的）----
@app.post("/login")
async def login(user_id: str = "user-123"):
    # 真实项目这里会先查数据库核对账号密码，这里简化：直接发 token
    token = jwt.encode(
        {"sub": user_id, "role": "student", "tenant_id": "school-1"},
        JWT_SECRET, algorithm=JWT_ALGO,
    )
    return {"access_token": token}


# ---- /me：受保护接口，同时用到 get_current_user(验票) 和 get_db(查库) ----
@app.get("/me")
async def me(
    current_user: dict = Depends(get_current_user),   # 先验票，拿到是谁
    db: AsyncSession = Depends(get_db),               # 再拿一个数据库会话
):
    # 用 token 里的用户 id 去数据库查这个人（参数化查询，:id 防注入）
    result = await db.execute(
        text("SELECT id, role, tenant_id FROM users WHERE id = :id"),
        {"id": current_user["user_id"]},
    )
    row = result.first()
    return {
        "令牌里的用户": current_user,
        "数据库里查到的": dict(row._mapping) if row else "没查到",
    }


if __name__ == '__main__':
    uvicorn.run(app, host="0.0.0.0", port=8000)