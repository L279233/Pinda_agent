# 1. 定义基类异常
class CoffeeMachineError(Exception):
    """所有咖啡机相关异常的父类"""
    pass

# 2. 定义具体的自定义异常（继承基类）
class NoBeansError(CoffeeMachineError):
    """咖啡豆用完了"""
    pass

class NoWaterError(CoffeeMachineError):
    """水箱没水了"""
    pass

# 3. 定义一个可能抛出这些异常的函数
def make_coffee(beans_left, water_left):
    """做一杯咖啡，如果原料不足就抛出自定义异常"""
    if beans_left <= 0:
        raise NoBeansError("咖啡豆数量为0，请补充豆子")
    if water_left <= 0:
        raise NoWaterError("水箱没水了，请加水")
    # 正常制作
    print("☕ 咖啡制作成功！香喷喷的～")
    # 扣减原料
    beans_left -= 10
    water_left -= 50
    return beans_left, water_left

# 4. 测试代码（模拟不同情况）
print("===== 测试1: 正常情况 =====")
beans = 50
water = 200
try:
    beans, water = make_coffee(beans, water)
except CoffeeMachineError as e:   # 基类可以捕获所有子类异常
    print(f"❌ 出错了：{e}")
else:
    print(f"剩余豆子：{beans}，剩余水：{water}")

print("\n===== 测试2: 没豆子 =====")
beans = 0
water = 200
try:
    beans, water = make_coffee(beans, water)
except NoBeansError as e:        # 精确捕获“没豆子”
    print(f"❌ 豆子没了：{e}")
except NoWaterError as e:
    print(f"❌ 没水了：{e}")
#
# print("\n===== 测试3: 没水 =====")
# beans = 30
# water = 0
# try:
#     beans, water = make_coffee(beans, water)
# except NoBeansError:
#     print("❌ 没豆子了，请加豆子")
# except NoWaterError:
#     print("❌ 没水了，请加水")    # 这里会匹配到