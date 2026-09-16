# backend/core/llm_factory.py
# LLM Factory：统一封装大模型调用，按 Agent 类型路由。
# 规矩：所有 Agent 必须通过此模块获取模型，禁止直接调用 init_chat_model。

from typing import Type, Any                          # 类型注解用：Type 表示「某个类本身」，Any 表示任意类型
from pydantic import BaseModel                        # 结构化输出的 Schema 都是它的子类
import httpx                                          # HTTP 客户端库（用来自定义网络行为）
from langchain.chat_models import init_chat_model     # 2.3 学的：创建聊天模型（1.x 写法）
from langchain_core.language_models import BaseChatModel  # 聊天模型的基类（类型注解用）
from langchain_core.runnables import Runnable, RunnableLambda
from langchain_core.messages import SystemMessage

from backend.config import get_settings               # 读配置（API Key、base_url 等）
from backend.core.logger import get_logger, configure_logging            # 结构化日志
configure_logging()
logger = get_logger(__name__)                         # 本模块的日志器，name 用当前模块名

# ── 自定义 httpx 客户端 ────────────────────────────────────────
# 保留系统代理支持。部分 Windows 网络无法直连模型服务，强制
# trust_env=False 会让普通调用和结构化调用全部 ConnectionError。
def _proxy_from_settings() -> str | None:
    """读取可选代理；为空时让 httpx 按系统环境变量或直连处理。"""
    proxy = get_settings().deepseek_proxy.strip()
    return proxy or None


_HTTP_ASYNC_CLIENT = httpx.AsyncClient(
    trust_env=True,
    proxy=_proxy_from_settings(),
    timeout=httpx.Timeout(120.0, connect=15.0),
)
_HTTP_SYNC_CLIENT = httpx.Client(
    trust_env=True,
    proxy=_proxy_from_settings(),
    timeout=httpx.Timeout(120.0, connect=15.0),
)

# ── Agent 类型 → 模型标识符 的路由表 ────────────────────────────
# 想给某类业务换模型，只改这里一行即可。
_AGENT_MODEL_ROUTING: dict[str, str] = {
    "qa":               "deepseek-chat",   # 智能问答
    "exam_subjective":  "deepseek-chat",   # 试卷-简答题批改
    "exam_code":        "deepseek-chat",   # 试卷-代码题批改（coder 已并入 chat）
    "resume":           "deepseek-chat",   # 简历审查
    "interview":        "deepseek-chat",   # 模拟面试
    "intent":           "deepseek-chat",   # 意图识别
    "summarize":        "deepseek-chat",   # 对话摘要压缩
}

class LLMFactory:
    """大模型工厂（统一获取模型的唯一入口）。
    用 @classmethod 定义方法，意味着不用创建对象、直接用 LLMFactory.get_llm(...) 调用。

    用法：
        llm = LLMFactory.get_llm("qa")                              # 普通模型
        structured = LLMFactory.get_structured_llm("resume", 某Schema)  # 结构化输出模型
        response = await llm.ainvoke(messages)
    """

    _instances: dict[str, BaseChatModel] = {}   # 类变量：模型实例缓存（缓存键 → 模型），全类共享

    @classmethod
    def _get_settings(cls):
        """内部小工具：取配置对象。"""
        return get_settings()

    @classmethod
    def _build_model_kwargs(cls, model_key: str) -> dict[str, Any]:
        """内部方法：组装 init_chat_model 需要的所有参数（DeepSeek 走 OpenAI 兼容接口）。"""
        settings = cls._get_settings()             # 取配置
        # 模型名称由环境配置控制；保留 agent 路由 key 以兼容现有调用方。
        if model_key == "deepseek-chat":
            model_id = settings.deepseek_model_chat
        elif model_key == "deepseek-coder":
            model_id = settings.deepseek_model_coder
        else:
            raise ValueError(f"未配置模型路由：{model_key}")

        return {
            "model": model_id,                     # 模型名，如 "deepseek-chat"
            "model_provider": "openai",            # 强制走 langchain-openai（DeepSeek 兼容 OpenAI 接口）
            "temperature": 0,                      # 默认 0：评分/批改要稳定输出
            "api_key": settings.deepseek_api_key,  # 来自 .env.local
            "base_url": settings.deepseek_base_url,# DeepSeek 接口地址
            "max_retries": 0,                      # 模型层不重试；重试统一由 retry.py（3.5）管
            "http_async_client": _HTTP_ASYNC_CLIENT,  # 用上面绕过代理的异步客户端
            "http_client": _HTTP_SYNC_CLIENT,         # 同步客户端
        }

    @classmethod
    def get_llm(
        cls,
        agent_type: str,             # Agent 类型，必须在路由表里
        temperature: float = 0,      # 温度：对话类可传 0.3~0.7，评分类保持 0
        streaming: bool = False,     # 是否流式输出（问答/面试对话用）
    ) -> BaseChatModel:
        """按 Agent 类型获取模型实例（带缓存）。
        相同 (模型, 温度, 是否流式) 的组合只会创建一次，之后复用。"""
        if agent_type not in _AGENT_MODEL_ROUTING:        # 校验：不认识的类型直接报错（早暴露问题）
            raise ValueError(
                f"未知 agent_type: '{agent_type}'，"
                f"可用类型：{list(_AGENT_MODEL_ROUTING.keys())}"
            )

        model_key = _AGENT_MODEL_ROUTING[agent_type]      # 查路由表，拿到模型标识符

        # 用「模型_温度_是否流式」拼一个缓存键：不同组合各缓存一份
        cache_key = f"{model_key}_{temperature}_{streaming}"
        if cache_key not in cls._instances:               # 缓存里没有才新建
            kwargs = cls._build_model_kwargs(model_key)   # 组装基础参数
            kwargs["temperature"] = temperature           # 覆盖温度
            kwargs["streaming"] = streaming               # 设置是否流式

            llm = init_chat_model(**kwargs)               # 真正创建模型（** 表示把字典展开成关键字参数）
            cls._instances[cache_key] = llm               # 存进缓存

            logger.info(                                  # 记一条结构化日志，便于观察
                "llm_factory.model_initialized",
                agent_type=agent_type, model_key=model_key,
                temperature=temperature, streaming=streaming,
            )

        return cls._instances[cache_key]                  # 返回缓存中的实例

    @classmethod
    def get_structured_llm(
        cls,
        agent_type: str,
        output_schema: Type[BaseModel],   # 期望的输出结构（一个 Pydantic 模型类）
        temperature: float = 0,
    ) -> Runnable:
        """获取「绑定了结构化输出 Schema」的模型。
        调用它的 ainvoke 后，直接返回一个 output_schema 类型的对象（不是文本）。"""
        llm = cls.get_llm(agent_type, temperature=temperature)             # 先拿普通模型
        # V4 Flash defaults to thinking mode and rejects the `tool_choice` sent by
        # function_calling. JSON mode avoids tool_choice; inject the Pydantic schema
        # because OpenAI-compatible JSON mode requires explicit format instructions.
        schema = output_schema.model_json_schema()
        format_message = SystemMessage(content=(
            "只输出一个符合以下 JSON Schema 的 JSON 对象，不要输出 Markdown、解释或思考过程。"
            f"\nJSON Schema:\n{schema}"
        ))

        def add_schema_instruction(messages: Any) -> list[Any]:
            if isinstance(messages, list):
                return [format_message, *messages]
            return [format_message, messages]

        return RunnableLambda(add_schema_instruction) | llm.with_structured_output(
            output_schema, method="json_mode"
        )

    @classmethod
    def get_configurable_llm(cls, temperature: float = 0) -> BaseChatModel:
        """获取「运行时可动态切换模型」的实例（少数需要在运行时决定用哪个模型的场景用）。"""
        settings = cls._get_settings()
        return init_chat_model(
            model=settings.deepseek_model_chat,
            model_provider="openai",
            temperature=temperature,
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            http_async_client=_HTTP_ASYNC_CLIENT,
            http_client=_HTTP_SYNC_CLIENT,
        )

    @classmethod
    def clear_cache(cls) -> None:
        """清空模型实例缓存（测试时用）。"""
        cls._instances.clear()
        logger.info("llm_factory.cache_cleared")


# ── 模块级便捷函数（Agent 代码里的推荐写法）────────────────────────
# 比写 LLMFactory.get_llm(...) 更简洁，直接 from llm_factory import get_llm 即可。

def get_llm(agent_type: str, temperature: float = 0, streaming: bool = False) -> BaseChatModel:
    """LLMFactory.get_llm 的便捷入口。"""
    return LLMFactory.get_llm(agent_type, temperature=temperature, streaming=streaming)


def get_structured_llm(agent_type: str, output_schema: Type[BaseModel]) -> Runnable:
    """LLMFactory.get_structured_llm 的便捷入口。"""
    return LLMFactory.get_structured_llm(agent_type, output_schema)


if __name__ == '__main__':
    # from pydantic import BaseModel, Field
    # # 先配置日志
    #
    # # ① 按类型拿到模型实例
    # llm = LLMFactory.get_llm("qa")
    # print("① 是 BaseChatModel:", isinstance(llm, BaseChatModel), "|", type(llm).__name__)
    #
    # # ② 相同参数有缓存，返回同一实例
    # print("② 缓存命中(同一实例):", LLMFactory.get_llm("qa") is llm)
    # #
    # # ③ 未知 agent_type 会报错
    # try:
    #     LLMFactory.get_llm("not_exist")
    # except ValueError as e:
    #     print("③ 未知类型报错:", str(e)[:38], "...")
    #
    #
    # # ④ 结构化输出：绑定 Pydantic，返回 Runnable
    # class Demo(BaseModel):
    #     name: str = Field(description="姓名")
    #
    #
    # print("④ 结构化模型是 Runnable:", isinstance(LLMFactory.get_structured_llm("resume", Demo), Runnable))
    #
    # # ⑤ 清空缓存
    # LLMFactory.clear_cache()
    # print("⑤ clear_cache 后缓存为空:", len(LLMFactory._instances) == 0)

    from langchain_core.messages import HumanMessage
    import asyncio
    async def main():
        llm = get_llm("qa")  # 通过工厂拿模型
        resp = await llm.ainvoke([HumanMessage(content="用一句话介绍 Python")])
        print("DeepSeek 回复:", resp.text)  # .text 取文本（回顾 2.3）

    asyncio.run(main())
