from fastapi import FastAPI, APIRouter
import uvicorn

'''
APIRouter。把它想象成 “文件夹/模块化工具箱”。
当你的项目只有 5 个接口时，全写在 app 上没问题；
但当你有 50 个接口（比如 qa、exam、resume 模块），全挤在 app 上就会乱成一锅粥。
APIRouter 的作用就是：
把同一组相关的接口（比如所有 /reviews 相关的）装进一个独立的“盒子”里，
最后再把盒子挂载到主 app 上。
'''
app = FastAPI()

# 1. 创建一个路由器（像一个小型 App）
user_router = APIRouter(prefix="/users", tags=["用户"])

# 2. 在这个路由器上定义接口（而不是在 app 上）
@user_router.get("/")
def get_users():
    return {"message": "获取用户列表"}

@user_router.get("/{user_id}")
def get_user(user_id: int):
    return {"message": f"获取用户 {user_id}"}

# 3. 将这个路由器挂载到主 app 上
app.include_router(user_router)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)