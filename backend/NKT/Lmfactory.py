# 模拟的配置（相当于你们项目的 settings）
class Settings:
    weather_api_key = "w_key_123"
    weather_base_url = "https://api.weather.com"
    news_api_key = "n_key_456"
    news_base_url = "https://api.news.com"

settings = Settings()

# 服务名 -> 使用什么配置 的映射（类似 _AGENT_MODEL_ROUTING）
SERVICE_CONFIG = {
    "weather": {
        "api_key": settings.weather_api_key,
        "base_url": settings.weather_base_url,
    },
    "news": {
        "api_key": settings.news_api_key,
        "base_url": settings.news_base_url,
    },
}

# 模拟的客户端类，代表一个能发请求的“连接”
class APIClient:
    def __init__(self, api_key, base_url):
        self.api_key = api_key
        self.base_url = base_url

    def get(self, endpoint):
        """模拟请求"""
        print(f"请求 {self.base_url}/{endpoint} (key={self.api_key[:4]}***)")
        # 实际会发 http 请求...


class ClientFactory:
    """工厂：管理客户端实例的创建和复用"""

    def __init__(self):
        self._instances = {}          # 缓存，类似 LLMFactory 的 _instances

    def get_client(self, service: str):
        """根据服务名获取客户端（路由 + 缓存）"""
        config = SERVICE_CONFIG[service]       # 路由：服务名 -> 具体配置
        cache_key = f"{service}"               # 缓存键，简单点只用服务名

        if cache_key in self._instances:
            print(f"   [缓存命中] 返回已有的 {service} 客户端")
            return self._instances[cache_key]

        print(f"   [新建] 创建 {service} 客户端")
        client = APIClient(config["api_key"], config["base_url"])
        self._instances[cache_key] = client
        return client


# ---------- 使用效果 ----------
factory = ClientFactory()

# 第一次获取天气客户端 —— 会创建
weather_client1 = factory.get_client("weather")
weather_client1.get("today")

# 第二次获取天气客户端 —— 命中缓存，不会创建新对象
weather_client2 = factory.get_client("weather")
weather_client2.get("tomorrow")

# 获取新闻客户端 —— 不同服务，新建
news_client = factory.get_client("news")
news_client.get("headlines")

print("\nweather_client1 和 weather_client2 是同一个对象吗？",
      weather_client1 is weather_client2)  # True