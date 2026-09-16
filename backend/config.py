# backend/config.py
from pydantic_settings import BaseSettings
from functools import lru_cache
import os
env_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env.local")


class Settings(BaseSettings):
    # ── 数据库（PostgreSQL）──
    db_host: str = "localhost"
    db_port: int = 5433          # 本机已有 PG:5432，EduAgent 隔离到 5433
    db_name: str = "eduagent"
    db_user: str = ""
    db_password: str = ""

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    # ── Milvus ──
    milvus_host: str = "localhost"
    milvus_port: int = 19531     # 本机已有 Milvus:19530，EduAgent 隔离到 19531

    # ── 简历对象存储（MinIO / S3 兼容）──
    object_storage_enabled: bool = False
    minio_endpoint: str = "http://localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "pinda"
    minio_region: str = "us-east-1"

    # ── 大模型 ──
    deepseek_api_key: str = ""
    # DeepSeek 官方 OpenAI 兼容端点。具体模型通过环境变量覆盖。
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model_chat: str = "deepseek-v4-flash"
    deepseek_model_coder: str = "deepseek-v4-flash"
    # 可选 HTTP/SOCKS 网关，例如 http://127.0.0.1:7890；留空表示直连
    deepseek_proxy: str = ""

    # ── 本地模型路径 ──
    reranker_model_path: str = "./models/reranker/bge-reranker-large"
    classifier_model_path: str = "./models/classifier/all-MiniLM-L6-v2"
    finetuned_classifier_path: str= "./models/classifier/query-classifier-finetuned"  # 与训练时 output_dir 保持一致
    bge_m3_model_path: str = "./models/embedding/bge-m3"

    # ── JWT ──
    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 10080

    # ── MCP Servers ──
    kb_mcp_server_url:  str = "http://localhost:8000/mcp/kb"
    web_search_mcp_url: str = "http://localhost:8000/mcp/web-search"

    # ── Web Search（Tavily 可选，留空则自动切换 DuckDuckGo）──
    tavily_api_key: str = ""

    # ── 应用 ──
    app_env: str = "local"
    app_debug: bool = False
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "INFO"
    default_tenant_id: str = "tenant_default"
    # LangGraph 状态持久化后端：memory 适合本地开发，postgres 用于生产。
    langgraph_checkpointer: str = "memory"
    class Config:
        env_file = env_file
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
if __name__ == '__main__':
    s = get_settings()
    print("jwt_secret_key:", s.jwt_secret_key)
    # print("db_port:", s.db_port)
    print("database_url:", s.database_url)
    # print("default_tenant_id:", s.default_tenant_id)
    # print("两次 get_settings 是同一对象(lru_cache):", get_settings() is get_settings())
