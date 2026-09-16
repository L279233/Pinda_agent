from dataclasses import dataclass, field
@dataclass
class Test:
    name: str
    age: int

t = Test("小明", 18)
print(t)
# # 自动输出（超清晰）：Test(name='小明', age=18)
#
# # ------- 对比普通类（没重写 __repr__） -------
class Test2:
    def __init__(self, name, age):
        self.name = name
        self.age = age

t2 = Test2("小明", 18)
print(t2)
# 输出（天书）：<__main__.Test2 object at 0x000001A2B3C4D5E0>

# @dataclass
# class Test:
#     name: str
#     age: int
#
# a = Test("小明", 18)
# b = Test("小明", 18)
# c = a
#
# print(a == b)  # True （因为字段值相同，@dataclass 自动做了比较）
# print(a == c)  # True
#
# # ------- 对比普通类（没重写 __eq__） -------
# class Test2:
#     def __init__(self, name, age):
#         self.name = name
#         self.age = age
#
# a2 = Test2("小明", 18)
# b2 = Test2("小明", 18)
# print(a2 == b2)  # False！！！（明明内容一样，但因为内存地址不同，返回 False）

from dataclasses import dataclass, field
import time
from datetime import datetime

@dataclass
class DocumentChunk:
    # ... 其他字段 ...
    updated_at: int = field(default_factory=lambda: int(time.time()))

    @property
    def updated_at_readable(self) -> str:
        """方便调试查看，返回 '2026-06-16 15:07:33' 格式"""
        return datetime.fromtimestamp(self.updated_at).strftime('%Y-%m-%d %H:%M:%S')

# 使用示例
chunk = DocumentChunk()
print(chunk.updated_at)            # 输出: 1781593653 （给机器看的）
print(chunk.updated_at_readable)   # 输出: 2026-06-16 15:07:33 （给你看的）