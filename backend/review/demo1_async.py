import time

def fetch(name, seconds):
    print(f"开始 {name}")
    time.sleep(seconds)          # 模拟一次耗时的网络请求
    print(f"完成 {name}")

# start = time.time()
# fetch("请求A", 2)
# fetch("请求B", 2)
# fetch("请求C", 2)
# print(f"总耗时：{time.time() - start:.1f} 秒")

import asyncio

async def say_hello():
    print("Hello")
    await asyncio.sleep(1)        # 异步地等 1 秒（不阻塞事件循环）
    print("Async")
    return "完成"

async def main():
    result = await say_hello()    # 用 await 等它跑完并拿到返回值
    print("拿到结果：", result)

# asyncio.run(main())               # 从同步世界进入异步世界的唯一入口

import asyncio
import time

async def fetch(name, seconds):
    print(f"开始 {name}")
    await asyncio.sleep(seconds)     # 注意：换成异步 sleep
    print(f"完成 {name}")
    return f"{name} 的结果"

async def main():
    start = time.time()
    # results = await asyncio.gather(   # 三个任务同时开始，一起等
    #     fetch("请求A", 2),
    #     fetch("请求B", 2),
    #     fetch("请求C", 2),
    # )
    #
    results = await asyncio.gather( *[fetch("请求A%s"%i, 2) for i in range(3)])
    print("所有结果：", results)
    print(f"总耗时：{time.time() - start:.1f} 秒")

# asyncio.run(main())

import asyncio
import time

def heavy_sync_work(n):              # 一个阻塞的同步函数（模拟本地模型推理）
    print("同步重活开始……")
    time.sleep(2)                    # 故意阻塞 2 秒
    print("同步重活结束")
    return n * n

async def main():
    loop = asyncio.get_running_loop()        # 拿到当前事件循环
    # 第一个参数 None 表示用默认线程池；后面依次是「要执行的函数」和「它的参数」
    result = await loop.run_in_executor(None, heavy_sync_work, 10)
    print("结果：", result)

# asyncio.run(main())


import asyncio

# 模块级集合：持有所有后台任务的强引用，防止被 GC 提前回收
# 带一个下划线 _ 的变量/函数，“默认”不被 from module import * 导入，但可以被“指名道姓”地成功导入。
_background_tasks: set[asyncio.Task] = set()

async def grade_exam(exam_id):
    print(f"开始批改试卷 {exam_id}……")
    await asyncio.sleep(2)               # 模拟耗时的批改过程
    print(f"试卷 {exam_id} 批改完成")

async def submit():
    task = asyncio.create_task(grade_exam("EX-001"))  # 丢到后台
    _background_tasks.add(task)                         # 关键①：强引用，防 GC
    task.add_done_callback(_background_tasks.discard)   # 关键②：跑完自动移除
    print("接口立即返回：已收到，正在后台批改")

async def main():
    await submit()
    await asyncio.sleep(3)    # 模拟服务持续运行，给后台任务跑完的时间
    print("当前后台任务数：", len(_background_tasks))

# asyncio.run(main())

# _dada = "带下划线"
# dada = "不带下划线"

import asyncio
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan():
    print("【启动】加载模型 / 建立数据库连接")
    yield "你好"                          # yield 之前 = 启动逻辑；之后 = 关闭逻辑
    print("【关闭】释放资源 / 清理缓存")

async def main():
    async with lifespan() as source:
        print(f"应用运行中，拿到的资源是：{source}")
        print("应用运行中，处理请求……")

# asyncio.run(main())


import asyncio


# 这是一个纯粹的异步生成器，跟 with / contextlib 毫无关系
async def my_async_generator():
    for i in range(5):
        await asyncio.sleep(0.5)  # 模拟异步 I/O（比如爬网页、读数据库）
        yield f"第 {i} 块数据"  # 每次产出（yield）一个值


async def main():
    # 消费它：必须用 async for，就像同步生成器用 for 一样
    async for chunk in my_async_generator():
        print(f"接收到：{chunk}")

    print("全部接收完毕！")


# asyncio.run(main())


async def main():
    gen = my_async_generator()

    # 手动拿第一次
    first = await anext(gen)
    print(f"手动拿到的：{first}")

    # 继续用 async for 拿剩下的
    async for chunk in gen:
        print(f"剩下的：{chunk}")

asyncio.run(main())



