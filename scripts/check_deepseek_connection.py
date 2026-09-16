"""Send one minimal request using the configured LangChain model."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_core.messages import HumanMessage

from backend.config import get_settings
from backend.core.llm_factory import LLMFactory


async def main() -> None:
    settings = get_settings()
    print(f"endpoint={settings.deepseek_base_url}")
    print(f"model={settings.deepseek_model_chat}")
    if not settings.deepseek_api_key:
        raise RuntimeError("DEEPSEEK_API_KEY 未配置")
    llm = LLMFactory.get_llm("qa")
    response = await llm.ainvoke([HumanMessage(content="ping")])
    print(f"response={getattr(response, 'content', response)}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(f"error_type={type(exc).__name__}")
        print(f"error={exc}")
        raise
