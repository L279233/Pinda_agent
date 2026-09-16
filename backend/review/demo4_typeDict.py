# 1. 导入需要的类型
from typing import TypedDict

class SimpleState(TypedDict):
    input: str
    output: str

# 2. 创建 SimpleState 类型的字典（就像普通字典一样）
state1 = SimpleState(input="hello", output="world")
# 或者用等号赋值
state2: SimpleState = {
    "input": "你好",
    "output": "世界"
}

# 3. 访问字段
print(state1["input"])   # 输出: hello
print(state2["output"])  # 输出: 世界

# 4. 修改字段（和普通字典完全一样）
state1["output"] = "Python"
print(state1)            # 输出: {'input': 'hello', 'output': 'Python'}

# 5. 演示类型检查的好处（如果你在使用 PyCharm / VSCode + Pylance）
# 下面的代码会触发编辑器的警告（但运行时仍可执行，因为 Python 本身不强制类型）
state3: SimpleState = {"input": 123, "output": "wrong"}   # 警告：input 应该是 str，但给了 int
state4: SimpleState = {"input": "ok"}                     # 警告：缺少 output 字段
state5: SimpleState = {"input": "a", "output": "b", "extra": 1}  # 警告：多出的字段 extra