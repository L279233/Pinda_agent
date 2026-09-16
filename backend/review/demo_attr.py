class MsgV1:
    text = "Hello World"   # 字符串属性（不可调用）

class MsgV2:
    def text(self):        # 方法（可调用）
        return "Hello World"

class MsgV3:
    pass                   # 没有 text

# ----- 测试 -----
msg1 = MsgV1()
msg2 = MsgV2()
msg3 = MsgV3()

# 1. 检查 msg1
print(hasattr(msg1, "text"))                    # True
print(callable(getattr(msg1, "text", None)))   # False (字符串不可调用)
# 结果：True and not False -> True  (符合条件，认为是纯文本)

# 2. 检查 msg2
print(hasattr(msg2, "text"))                    # True
print(callable(getattr(msg2, "text", None)))   # True (方法是可调用的)
# 结果：True and not True -> False (不符合条件，跳过，因为这是方法)

# 3. 检查 msg3
print(hasattr(msg3, "text"))                    # False
# 短路逻辑：第一个条件就 False 了，根本不会执行后面的 getattr 判断
# 结果：False (不符合条件)