"""
============================================================
最小可运行 demo —— 单独搞懂 JWT（那张带防伪章的小纸条）
（不需要数据库、不需要起服务器，就是个普通脚本）

运行方法：
    1. 安装依赖：  pip install "python-jose[cryptography]"
    2. 直接运行：  python demo_jwt.py
============================================================
"""

from jose import jwt, JWTError

SECRET = "only-server-knows-this-secret"   # 密钥：只有服务器知道，真实项目放在配置/环境变量里
ALGO = "HS256"                             # 加密算法


# ===== 第1步：登录成功时，把用户信息装进纸条，盖上防伪章 =====
payload = {
    "sub": "user-123",       # 用户ID
    "role": "student",       # 角色
    "tenant_id": "school-1", # 机构
}
token = jwt.encode(payload, SECRET, algorithm=ALGO)

print("【生成的 token】")
print(token)
print()

# 这串 token 其实就是【三段用点号连接】：头部 . 载荷 . 签名
print("【它由三段组成】")
for i, part in enumerate(token.split("."), 1):
    print(f"  第{i}段：{part}")
print()


# ===== 第2步：之后每次收到请求，验章 + 取出里面的内容 =====
decoded = jwt.decode(token, SECRET, algorithms=[ALGO])
print("【验章通过，取回内容】")
print(" ", decoded)
print("  用户ID =", decoded.get("sub"), " 角色 =", decoded.get("role"))
print()
# 👆 get_current_user 干的就是这一步：decode 出来，再取 sub/role/tenant_id。


# ===== 第3步：如果有人伪造或篡改 token，会怎样？ =====
fake_token = token[:-3] + "xxx"   # 偷偷把签名最后3个字符改掉
print("【有人篡改了 token，尝试验证...】")
try:
    jwt.decode(fake_token, SECRET, algorithms=[ALGO])
except JWTError as e:
    print("  验证失败！服务器一眼识破伪造：", e)
    print("  → 这时 get_current_user 就会返回 401（未授权）")
