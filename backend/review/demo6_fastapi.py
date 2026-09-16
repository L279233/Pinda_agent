"""
============================================================
最小可运行 demo —— 单独演示 3 个概念：Depends、yield、async
（不碰数据库、不碰 JWT，把干扰都去掉，只看机制本身）

运行方法：
    1. 安装依赖：  pip install fastapi uvicorn
    2. 启动服务：  uvicorn demo_fastapi:app --reload
    3. 浏览器打开： http://127.0.0.1:8000/docs
       逐个点 lesson1 / lesson2 / lesson3 试一下，
       同时【盯着启动服务的终端窗口看打印顺序】，这才是重点！
# ============================================================
# """
# import uvicorn
# from fastapi import FastAPI, Depends
# import asyncio
#
# app = FastAPI()
#
#
# # ========== 第1课：Depends 替你调用函数并把结果塞进参数 ==========
#
# def give_number():
#     print(">> give_number() 被调用了")   # 注意：是谁调用的它？是 FastAPI，不是你
#     return 42
#
#
# @app.get("/lesson1")
# def lesson1(num: int = Depends(give_number)):
#     # 关键：我们从来没写过 num = give_number()
#     # 是 FastAPI 看到 Depends，替我们调用了它，再把返回值 42 塞进 num。
#     # 这就是“依赖注入”。get_db、get_current_user 都是这样被调用的。
#     return {"你拿到的数字": num}
#
#
# # ========== 第2课：yield = 借出去 → 暂停 → 用完回来收尾 ==========
#
# def borrow_notebook():
#     print(">> 拿出笔记本（请求开始）")
#     notebook = []
#     try:
#         yield notebook                       # 把笔记本交给接口用，函数在这里【暂停】
#     finally:
#         print(">> 收回笔记本（请求结束），里面写了：", notebook)
#
#
# @app.get("/lesson2")
# def lesson2(nb: list = Depends(borrow_notebook)):
#     nb.append("我写了一行字")
#     return {"笔记本内容": nb}
#     # 访问这个接口后，去终端看打印顺序：
#     #   先 “拿出笔记本”  →  接口返回  →  最后 “收回笔记本”
#     # 这就是 get_db() 的原理：yield 出去的是数据库会话，用完后自动关闭。
#
#
# # ========== 第3课：async / await —— 碰到“要等”的操作就用它 ==========
#
# @app.get("/lesson3")
# async def lesson3():
#     print(">> 开始等待（假装正在查数据库...）")
#     await asyncio.sleep(2)                    # await = 这里要等2秒，期间服务器可以去服务别人
#     print(">> 等完了，返回结果")
#     return {"消息": "等了2秒才回来。真实项目里，这2秒就是在查数据库"}
# if __name__ == '__main__':
#     uvicorn.run(app)
import asyncio
import json

import uvicorn
from fastapi import FastAPI, Depends, HTTPException, File, UploadFile, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

app = FastAPI(title="EduAgent Demo")

# ---------- 2.5.2 健康检查 ----------
@app.get("/health")
async def health_check():
    return {"status": "ok"}

# ---------- 2.5.3 登录（请求体 + 响应模型） ----------
class LoginRequest(BaseModel):
    username: str = Field(..., description="用户名或邮箱")
    password: str = Field(..., description="密码")

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str

@app.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest):
    if req.username == "student01" and req.password == "Student@123456":
        return TokenResponse(access_token="fake-token-abc", role="student")
    # 为了演示，错误的也返回 guest
    return TokenResponse(access_token="", token_type="bearer", role="guest")

# ---------- 2.5.4 路径参数与查询参数 ----------
@app.get("/reviews/{review_id}")
async def get_review(review_id: str):
    return {"review_id": review_id, "status": "completed"}

@app.get("/reviews")
async def list_reviews(page: int = 1, size: int = 10):
    return {"page": page, "size": size}

# ---------- 2.5.5 依赖注入（模拟数据库 + 模拟鉴权） ----------
# 为了让你在 Postman 里能直接测通，这里不强制要求传 Token，而是直接返回模拟用户。
# 但为了演示真实鉴权流程，我加一个可选的 Bearer Token 校验（非必须，以便测试）。
# 这里我们使用文档中的 get_current_user 模拟版本。
async def get_db():
    return {"db": "fake_session"}

# 模拟的当前用户（不校验 Token，直接返回）
async def get_current_user():
    return {"user_id": "test_user", "role": "student"}

@app.get("/my-reviews")
async def my_reviews(
    db = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    return {"db1":db["db"], "user": current_user["user_id"], "data": "这是受保护的数据"}

# 如果你想知道带上真实 Authorization 头怎么测，这里额外写一个真实的 Bearer 校验示例（选看）
# 为了简化，依然不验签，只从Header取token展示
bearer_scheme = HTTPBearer(auto_error=False)  # auto_error=False 允许不带Token

@app.get("/secure-endpoint")
async def secure_endpoint(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)
):
    if credentials is None:
        raise HTTPException(status_code=401, detail="请提供 Token")
    token = credentials.credentials
    return {"message": f"收到您的 Token: {token[:10]}..."}

# ---------- 2.5.6 文件上传 ----------
@app.post("/upload", status_code=202)
async def upload(file: UploadFile = File(...)):
    content = await file.read()
    return {"filename": file.filename, "size": len(content)}

# ---------- 2.5.7 SSE 流式响应 ----------
@app.post("/chat/stream")
async def chat_stream():
    async def event_generator():
        answer = "装饰器是一种包装函数的语法。"
        for char in answer:
            await asyncio.sleep(0.1)  # 模拟逐字生成
            yield {"data": json.dumps({"type": "token", "content": char},ensure_ascii=False)}
        yield {"data": json.dumps({"type": "done"})}
    return EventSourceResponse(event_generator())

# ---------- 2.5.8 错误处理 ----------
def find_review(review_id: str):
    return None  # 模拟查不到

@app.get("/reviews-with-error/{review_id}")
async def get_review_with_error(review_id: str):
    review = find_review(review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="审查记录不存在")
    return review
if __name__ == '__main__':
    uvicorn.run(app, host="0.0.0.0", port=8003)


