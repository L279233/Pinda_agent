import logging

# 配置日志格式
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(message)s')

# 直接使用
logging.info("用户登录成功")

user_id = "123"
role = "student"
logging.info(f"用户登录成功  user_id={user_id} role={role}")

import logging


class MyLogger:
    def __init__(self):
        logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(message)s')
        self._logger = logging.getLogger("myapp")

    def info(self, message, **kwargs):
        # 把 key=value 转成字符串
        extra = ""
        if kwargs:
            parts = [f"{k}={v}" for k, v in kwargs.items()]
            extra = " | " + " | ".join(parts)
        self._logger.info(f"{message}{extra}")


# 使用
logger = MyLogger()
logger.info("用户登录成功", user_id="123", role="student")
logger.info("查询数据库", sql="select * from users", cost_ms=12)