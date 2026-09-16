"""
asyncio.run(协程):进入异步世界的开机键,最外层用一次。
await 协程:在 async 函数内部调用异步函数,用它执行并拿结果。
判断用哪个:外面有 async def 包着我 → await;没有 → asyncio.run。
不能在 async 函数内部再调 asyncio.run(循环已在跑,会报错)。
await X() 会真正执行 X 并返回结果;不加 await,X() 只是个不运行的协程。
============================================================
三层兜底机制【可独立运行】演示。

真实的 retry.py 依赖项目内部模块(backend.core.*)和 LangChain，新手跑不起来。
这里把那些依赖都换成最简单的「假」替身，核心的三层兜底逻辑和真实代码完全一致。
无需安装任何东西，直接运行即可：

    python retry_demo.py

下面用 5 个场景，让你看到三层防线分别在什么情况下触发。
============================================================
"""

import asyncio
from functools import wraps
from typing import Callable, Any


# ============================================================
# 准备工作：造几个「假」的项目依赖，好让脚本能独立跑起来
# ============================================================

# 假异常（替代 backend.core.exceptions 里的）
class LLMAPIError(Exception): pass
class MilvusConnectionError(Exception): pass
class InvalidInputError(Exception): pass
class AuthenticationError(Exception): pass

# 假日志器（替代 backend.core.logger）——其实就是带前缀的 print
class _Logger:
    def info(self, msg, **kw):    print(f"    [INFO ] {msg}")
    def warning(self, msg, **kw): print(f"    [WARN ] {msg}")
    def error(self, msg, **kw):   print(f"    [ERROR] {msg}")
logger = _Logger()

# 假的 AIMessage（替代 langchain_core.messages.AIMessage）
class AIMessage:
    def __init__(self, content): self.content = content
    def __repr__(self): return f"AIMessage({self.content!r})" # 加了 !r,带引号。作用是:定义"这个对象被显示成文字时长什么样"——比如你 print(它)、把它放进列表打印、或在控制台直接敲它的名字时,Python 就会自动调用 __repr__,拿它返回的字符串来显示。

# 假大模型（替代 backend.core.llm_factory.get_llm）
class _FakeLLM:
    async def ainvoke(self, messages):
        await asyncio.sleep(0.1)                       # 假装在调用大模型，等一下
        return AIMessage("这是 LLM 不依赖知识库直接生成的回答")
def get_llm(name): return _FakeLLM()


# ============================================================
# 以下是核心逻辑，和真实 retry.py 基本一致
# ============================================================

RETRYABLE_ERRORS = (LLMAPIError, MilvusConnectionError, TimeoutError, ConnectionError)
NON_RETRYABLE_ERRORS = (InvalidInputError, AuthenticationError)
MAX_RETRIES = 2
RETRY_DELAYS = [0.5, 1.0]   # demo 里缩短了（真实项目是 1秒 / 3秒），方便快速看效果


def with_retry(agent_type: str = ""):
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> Any:

            # ── 第一层：自动重试 ──
            last_error: Exception | None = None
            for attempt in range(MAX_RETRIES + 1):
                try:
                    result = await asyncio.wait_for(func(*args, **kwargs), timeout=30.0)
                    print(f'result: {result}')
                    if attempt > 0:
                        logger.info(f"✓ 重试成功（第 {attempt + 1} 次尝试）")
                    return result
                except NON_RETRYABLE_ERRORS:
                    logger.error("遇到不可重试的错误，直接抛出（不重试、不降级）")
                    raise
                except Exception as e:
                    last_error = e
                    if attempt < MAX_RETRIES:
                        delay = RETRY_DELAYS[attempt]
                        logger.warning(f"调用失败，{delay}秒后重试（第 {attempt + 1}/{MAX_RETRIES} 次）: {e}")
                        await asyncio.sleep(delay)
                    else:
                        logger.error(f"重试 {MAX_RETRIES} 次均失败: {e}")

            # ── 第二层：Agent 级降级 ──
            try:
                fallback_result = await AgentFallbackHandler.handle(
                    agent_type=agent_type, original_error=last_error,
                    func=func, args=args, kwargs=kwargs,
                )
                logger.info("✓ 降级策略执行成功")
                return fallback_result
            except Exception as fallback_error:
                logger.error(f"降级策略也失败了，进入系统兜底: {fallback_error}")

            # ── 第三层：系统级兜底 ──
            return _system_fallback_response(agent_type)

        return wrapper
    return decorator


class AgentFallbackHandler:
    @classmethod
    async def handle(cls, agent_type, original_error, func, args, kwargs) -> Any:
        fallback_map = {
            "qa":              cls._qa_fallback,
            "exam_subjective": cls._exam_subjective_fallback,
        }
        handler = fallback_map.get(agent_type)
        if handler is not None:
            return await handler(original_error, func, args, kwargs)
        raise original_error   # 没有对应降级策略 → 继续抛 → 落到第三层

    @classmethod
    async def _qa_fallback(cls, error, func, args, kwargs) -> dict:
        logger.info("QA 降级：跳过知识库检索，改用 LLM 直接回答")
        state = args[0] if args else kwargs.get("state", {})
        messages = state.get("messages", [])
        # print(f'messages: {messages}')
        if not messages:
            raise ValueError("QA 降级失败：无法获取用户消息")
        llm = get_llm("qa")
        response = await llm.ainvoke(messages)
        content = "⚠️ 知识库暂时不可用，以下为 AI 直接生成的回答，仅供参考：\n" + response.content
        return {"messages": messages + [AIMessage(content)], "fallback_used": True}

    @classmethod
    async def _exam_subjective_fallback(cls, error, func, args, kwargs) -> dict:
        logger.info("批改降级：标记需教师复核")
        state = args[0] if args else kwargs.get("state", {})
        return {**state, "fallback_used": True, "needs_teacher_review": True}


def _system_fallback_response(agent_type: str) -> dict:
    messages = {
        "qa":   "非常抱歉，智能问答服务暂时不可用，请稍后再试，或直接联系老师提问。",
        "exam": "非常抱歉，试卷批改服务暂时不可用，您的提交已保存，待恢复后将自动处理。",
    }
    content = messages.get(agent_type, "服务暂时不可用，请稍后再试。")
    return {"content": content, "fallback_used": True, "system_fallback": True}


# ============================================================
# 测试场景：造一个「可控失败」的假 Agent，分 5 种情况观察效果
# ============================================================

def make_flaky_agent(fail_times, error=None):
    """造一个『前 fail_times 次失败、之后成功』的假 Agent 函数。"""
    error = error or LLMAPIError("模拟大模型API超时")
    counter = {"n": 0}
    async def agent(state, config=None):
        counter["n"] += 1
        if counter["n"] <= fail_times:
            raise error
        return {"messages": state["messages"] + [AIMessage("正常的智能回答！")], "ok": True}
    return agent


def banner(title):
    print("\n" + "=" * 64)
    print(f"  {title}")
    print("=" * 64)


async def main():
    state = {"messages": ["用户：你好"]}

    # 场景1：第一次就成功 —— 三层都不会触发
    # banner("场景1：调用一次就成功（第一层都不用重试）")
    # agent = make_flaky_agent(fail_times=0)
    # print(agent)
    # wrapped = with_retry(agent_type="qa")(agent)
    # print("  最终结果:", await wrapped(state))
    #
    # # # 场景2：失败2次后成功 —— 第一层「重试」救场
    # banner("场景2：前2次失败、第3次成功（第一层：自动重试 救场）")
    # agent = make_flaky_agent(fail_times=2)
    # wrapped = with_retry(agent_type="qa")(agent)
    # print("  最终结果:", await wrapped(state))

    # # # 场景3：一直失败 + 有QA降级策略 —— 落到第二层「降级」
    # banner("场景3：一直失败，但有QA降级策略（第二层：Agent降级 救场）")
    # agent = make_flaky_agent(fail_times=999)
    # wrapped = with_retry(agent_type="qa")(agent)
    # print("  最终结果:", await wrapped(state))
    #
    # 场景4：一直失败 + 没有该类型降级策略 —— 落到第三层「系统兜底」
    # banner("场景4：一直失败，且无对应降级策略（第三层：系统兜底）")
    # agent = make_flaky_agent(fail_times=999)
    # wrapped = with_retry(agent_type="exam")(agent)   # "exam" 没有降级策略
    # print("  最终结果:", await wrapped(state))
    # #
    # 场景5：不可重试的错误 —— 立即抛出，连重试都不做
    banner("场景5：遇到不可重试错误（如API Key无效，立即抛出，不重试）")
    agent = make_flaky_agent(fail_times=999, error=AuthenticationError("模拟API Key无效"))
    wrapped = with_retry(agent_type="qa")(agent)
    print("  最终结果:", await wrapped(state))



if __name__ == "__main__":
    asyncio.run(main())