# backend/api/v1/auth.py
# 登录认证接口：/login（签发 Token）与 /me（验证鉴权）

# ── 1. 标准库 / 第三方核心库导入 ──────────────────────────────
import asyncio
# 异步 IO 库。用于将耗时的同步操作（如密码校验）放到线程池执行，避免阻塞整个事件循环。

from datetime import datetime, timedelta, timezone
# datetime：获取当前时间点；timedelta：表示时间差（如“30分钟”）；timezone：时区对象，这里用 UTC 保证全球服务器时间一致。

from fastapi import APIRouter, HTTPException, status, Depends
# APIRouter：路由注册器，用于将接口挂载到主应用；HTTPException：主动抛出 HTTP 错误响应；
# status：HTTP 状态码常量（如 401）；Depends：FastAPI 依赖注入系统，声明函数执行前需要先执行另一个函数。

from pydantic import BaseModel, Field
# BaseModel：定义请求/响应体的数据结构，自动做类型校验和序列化；Field：给字段加描述、默认值或校验规则。

from sqlalchemy.ext.asyncio import AsyncSession
# SQLAlchemy 异步数据库会话。用于执行异步 SQL 查询（非阻塞）。

from sqlalchemy import text
# text()：把原生 SQL 字符串包起来，让 SQLAlchemy 能执行它（绕过 ORM 映射，直接写裸 SQL）。

from jose import jwt
# python-jose 库的 jwt 模块。用于生成（encode）和验证（decode）JWT 令牌。

from passlib.context import CryptContext
# Passlib 的密码上下文管理器。统一处理 bcrypt 等哈希算法的加密和验证。

from backend.config import get_settings
# 自定义配置模块。懒加载方式获取全局配置对象（含密钥、过期时间等）。

from backend.dependencies import get_db, get_current_user
# 自定义依赖：get_db 提供数据库会话；get_current_user 解析请求头中的 Token 并返回用户信息。

from backend.core.logger import get_logger
# 自定义日志模块。带上调用方信息（__name__）生成结构化日志。

# ── 2. 兼容性补丁（解决 passlib 与新版 bcrypt 的兼容问题）─────
import bcrypt as _bcrypt_mod, types as _types

# 导入 bcrypt 库和 types 工具库。

if not hasattr(_bcrypt_mod, "__about__"):
    # passlib 1.7.4 会尝试读取 bcrypt.__about__.__version__，但 bcrypt>=4.0 删掉了这个属性。
    # 判断如果没有 __about__，就手动造一个假的命名空间对象塞进去。
    _about = _types.SimpleNamespace(__version__=getattr(_bcrypt_mod, "__version__", "4.x"))
    # 用 SimpleNamespace 创建一个临时对象，包含 __version__ 属性。如果 bcrypt 自身有 __version__ 就用它，否则默认 "4.x"。
    _bcrypt_mod.__about__ = _about
    # 将伪造的 __about__ 挂载到 bcrypt 模块上，欺骗 passlib 让其能正常初始化。

# ── 3. 本模块初始化 ──────────────────────────────────────────
router = APIRouter()
# 创建路由实例。后续用 @router.post 或 @router.get 装饰器定义具体接口。

logger = get_logger(__name__)
# 生成当前模块的日志记录器，日志中会附带 "backend.api.v1.auth" 这个来源。

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# 初始化密码哈希工具。指定使用 bcrypt 算法，deprecated="auto" 表示如果有旧哈希会自动升级。

# ── 4. Pydantic 请求/响应模型定义 ──────────────────────────
class LoginRequest(BaseModel):
    """登录请求体。"""
    username: str = Field(..., description="用户名或邮箱")
    # Field(...) 中的 ... 表示该字段必填（required）。description 用于生成 OpenAPI 文档。
    password: str = Field(..., description="密码")


class TokenResponse(BaseModel):
    """登录成功的响应体。"""
    access_token: str  # JWT 令牌字符串
    token_type: str = "bearer"  # 令牌类型，OAuth2 标准固定为 "bearer"
    expires_in: int  # 有效期，单位秒（前端用来倒计时或本地存储过期判断）
    role: str  # 用户角色（如 student/teacher/admin）
    user_id: str  # 用户唯一标识


# ── 5. 辅助函数：签发 JWT ──────────────────────────────────
def _create_access_token(data: dict, expires_minutes: int) -> str:
    """把身份信息 + 过期时间打包，用密钥签名成 JWT 字符串。"""
    settings = get_settings()
    # 获取全局配置（懒加载，拿到的都是环境变量或 .env 文件里的值）。

    payload = data.copy()
    # 浅拷贝一份原始字典，防止后续修改（比如添加 exp）污染调用方传入的原始数据。

    expire = datetime.now(timezone.utc) + timedelta(minutes=expires_minutes)
    # 计算过期时间点：当前 UTC 时刻 + 配置的分钟数。

    payload["exp"] = expire
    # 往载荷里塞入 "exp"（Expiration Time）字段，这是 JWT 官方标准字段，解码时会自动校验。
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    # 用密钥和算法（如 HS256）对 payload 进行数字签名，生成最终的三段式 JWT 字符串。


# ── 6. 登录接口 ──────────────────────────────────────────────
@router.post("/login", response_model=TokenResponse)
# 装饰器：声明这是一个 POST 请求，路径为 /login，成功时返回的数据格式必须符合 TokenResponse 模型。
async def login(
        req: LoginRequest,  # 自动从请求体解析 JSON，校验字段后注入。
        db: AsyncSession = Depends(get_db),  # 依赖注入：从依赖链获取异步数据库会话。
):
    """用户登录，返回 JWT Access Token（支持用户名或邮箱登录）。"""
    settings = get_settings()
    # 虽然这里只用来拿配置，但为了保持一致性也获取一下（实际上下面代码没用到 settings，可优化，但留着无害）。

    # ── 6a. 查数据库（用户名或邮箱） ──────────────────────────
    result = await db.execute(
        text(
            "SELECT id, password_hash, role, tenant_id, is_active "
            "FROM users WHERE username = :val OR email = :val LIMIT 1"
        ),
        # text() 把字符串 SQL 包装成可执行对象。:val 是命名占位符，防止 SQL 注入。
        {"val": req.username},
        # 参数绑定：把用户输入的 username 同时匹配数据库的 username 和 email 字段。
    )
    row = result.fetchone()
    # fetchone() 取第一行结果。如果没找到，返回 None。

    # ── 6b. 校验用户存在性与账号状态 ────────────────────────
    if not row:
        # 用户不存在。为了安全，不区分“用户名不存在”和“密码错误”，统一给“用户名或密码错误”。
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")

    if not row.is_active:
        # 账号被禁用（is_active 字段为 False）。
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号已被禁用，请联系管理员")
        # 注意这里用 403（禁止访问）而不是 401（未认证），因为用户身份存在，但被权限策略拦截了。

    # ── 6c. 密码校验（放在线程池执行） ──────────────────────
    loop = asyncio.get_running_loop()
    # 获取当前正在运行的事件循环。

    password_ok = await loop.run_in_executor(
        None,  # None 表示使用默认的 ThreadPoolExecutor（线程池）
        pwd_context.verify,  # 同步函数：验证明文密码与哈希是否匹配。
        req.password,  # verify 的第一个参数：明文
        row.password_hash,  # verify 的第二个参数：哈希值
    )
    # run_in_executor 会将同步阻塞函数扔给工作线程去跑，主协程挂起等待结果。
    # 这样 ~100ms 的 bcrypt 计算就不会阻塞其他高并发请求的处理。

    if not password_ok:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
        # 密码不匹配，抛出 401。

    # ── 6d. 签发 Token ──────────────────────────────────────
    token = _create_access_token(
        data={
            "sub": str(row.id),
            # "sub"（Subject）是 JWT 标准字段，通常存用户唯一标识。
            "role": row.role,
            # 自定义字段：存角色，方便后续鉴权中间件直接读取，免去查数据库。
            "tenant_id": row.tenant_id,
            # 自定义字段：存租户 ID，用于多租户数据隔离。
        },
        expires_minutes=settings.jwt_access_token_expire_minutes,
        # 过期时间从配置读取（如 30 分钟）。
    )
    logger.info("auth.login_success", user_id=str(row.id), role=row.role)
    # 结构化日志记录登录成功，便于排查审计。

    return TokenResponse(
        access_token=token,
        expires_in=settings.jwt_access_token_expire_minutes * 60,  # 配置是分钟，转换成秒返回给前端。
        role=row.role,
        user_id=str(row.id),
    )


# ── 7. 获取当前用户信息接口（鉴权测试）─────────────────────
@router.get("/me")
async def get_me(
        current_user: dict = Depends(get_current_user),
        # 依赖注入：执行 get_current_user 函数。
        # get_current_user 内部会解析 Authorization 请求头，解码 JWT，校验过期和签名，
        # 并将解码后的 payload 以 dict 形式返回。如果校验失败，它会自动抛出 401。
):
    """获取当前登录用户信息（用于验证 Token 是否有效）。"""
    return current_user
    # 如果走到了这里，说明 Token 有效，直接返回用户信息给前端。


# ── 8. 模块自测（不依赖数据库，仅测试密码哈希和 JWT 编解码）──
if __name__ == "__main__":
    # 这个 if 块仅在直接 python auth.py 运行时执行，被 FastAPI 导入时不执行。

    # ① 密码哈希 + 校验（被注释掉了，解开即可测试）
    h = pwd_context.hash("Student@123456")
    print(f'hash: {h}')
    print("正确密码校验:", pwd_context.verify("Student@123456", h))
    print("错误密码校验:", pwd_context.verify("wrong", h))

    # ② Token 签发 + 解码（被注释掉了，解开即可测试）
    from jose import jwt as _jwt

    # 重命名导入，避免与顶部导入冲突（其实顶级已经导入了，这里为了演示重新引入）。

    s = get_settings()
    print(s.jwt_secret_key)
    # 打印当前加载的 JWT 密钥（确认配置是否读对了）。

    tk = _create_access_token({"sub": "u-1", "role": "student", "tenant_id": "tenant_default"}, 10)
    print(tk)
    decoded = _jwt.decode(tk, s.jwt_secret_key, algorithms=[s.jwt_algorithm])
    print(decoded)
    # print("解码出 sub:", decoded["sub"], "| role:", decoded["role"])
