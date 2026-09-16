def divide(a, b):
    try:
        result = a / b
        print(f"结果是 {result}")
    except ZeroDivisionError:          # 专门抓"除以0"这种错
        print("错误:不能除以 0")
    except TypeError:                  # 专门抓"类型不对"这种错
        print("错误:数字不能和文字相除")
        # raise

# divide(10, 2)     # 输出: 结果是 5.0        ← 没出错,两个 except 都不走
# divide(10, 0)     # 输出: 错误:不能除以 0    ← 触发 ZeroDivisionError,走第1个 except
# divide(10, "x")   # 输出: 错误:数字不能和文字相除  ← 触发 TypeError,走第2个 except

'''
关键点1:最多只有一个 except 会执行
哪怕 try 里能出好几种错,一次运行也只会真的发生一种错,所以只会触发一个 except。
Python 找到第一个匹配的就执行、然后结束,不会继续往下试别的 except。
关键点2:顺序很重要——"具体的"要放在"宽泛的"前面
Exception 是所有异常的"总爹",except Exception 能抓任何错误。所以如果你把它放最前面,后面更具体的 except 就永远轮不到:
def divide(a, b):
    try:
        return a / b
    except Exception:              # ← 放最前面:它把所有错都抓走了
        print("发生了某种错误")
    except ZeroDivisionError:      # ← 死代码!永远执行不到
        print("不能除以 0")
divide(10, 0)   # 输出: 发生了某种错误   ← 不是"不能除以0"!

顺便:一个 except 也能抓"好几种"错
如果好几种错你想用同样的方式处理,不用写好几个 except,给它一个元组就行:
try:
    ...
except (ZeroDivisionError, TypeError):   # 这两种错,同一种处理
    print("出错了")
'''

for attempt in range(5):            # 最多试 5 次
    try:
        print(f"第 {attempt + 1} 次尝试...")
        if attempt < 2:             # 前两次(attempt=0、1)故意失败
            raise ConnectionError("连接失败")
        print("✓ 成功!")
        break                       # ← 成功了就跳出循环,不再重试
    except ConnectionError as e:
        print(f"  出错: {e},继续下一次")
        raise