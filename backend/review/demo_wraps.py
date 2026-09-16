'''
装饰器的本质 = 一个"接收函数、返回函数"的函数
前提:函数也是"对象",可以被传来传去
这是装饰器的地基。函数名后面不加括号,指的是函数本身,可以赋值、传递:
# '''
# def hello():
#     '''打招呼'''
#     print("hi")
# print(hello.__name__)
# print(hello.__doc__)
#
# # f = hello      # 把函数本身赋给 f(注意没有括号!)
# # f()            # 输出 hi —— f 现在就是 hello
# ##todo: 第1级:装饰器的本质 = 一个"接收函数、返回函数"的函数
# def my_decorator(func):        # 接收一个函数 func
#     def wrapper():             # 定义一个新函数,在原函数前后加点料
#         print("【前】")
#         func()                # 调用原函数
#         print("【后】")
#     return wrapper            # 返回这个新函数
# #
# def say_hi():
#     print("hi")

# say_hi = my_decorator(say_hi)  # 手动"装饰":用包装版替换掉原来的 say_hi
# say_hi()
# 输出:  【前】 / hi / 【后】
#todo: 第2级:@ 只是上面那行的简写(语法糖)
# @my_decorator        # 这一行,完全等价于 say_hi = my_decorator(say_hi)
# def say_hi():
#     print("hi")
#
# say_hi()             # 同样输出: 【前】 / hi / 【后】
#todo:第3级:让 wrapper 能接收任意参数 —— *args, **kwargs
def my_decorator(func):
    def wrapper(*args, **kwargs):       # 接住所有参数
        print("【前】")
        result = func(*args, **kwargs)  # 原样转交参数,并接住返回值
        print("【后】")
        return result                   # 把原函数的结果返回出去(别漏!)
    return wrapper

@my_decorator
def add(a, b):
    return a + b
#
print(add(3, 5))   # 输出: 【前】 / 【后】 / 8
print(add.__name__)        # wrapper      ← 名字竟然变成了 wrapper!
print(add.__doc__)         # None         ← 那句文档没了!
#
#
@my_decorator
def login():  pass

@my_decorator
def logout(): pass

print(login.__name__, logout.__name__)
# 不加 wraps  →  wrapper wrapper   ← 两个函数名字一样,日志/报错里根本分不清谁是谁!
# 加了 wraps  →  login logout      ← 各叫各的名,清清楚楚

#todo:第4级:加 @wraps(func) 保住身份(刚学过)
# '''
# @wraps(func) 把"被装饰函数的身份信息"(名字、文档等)复制到 wrapper 上,
# 让装饰之后的函数对外"看起来还是原来那个函数"。
# '''
from functools import wraps
def my_decorator(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)
    return wrapper

@my_decorator
def say_hello():
    """打个招呼"""
    print("hello")

print(say_hello.__name__)   # 输出: wrapper   ← 名字被冒名顶替了！
print(say_hello.__doc__)    # 输出: None      ← 原来的文档丢了！
from functools import wraps
# # todo:第5级:带参数的装饰器
def repeat(times):                    # 最外层:接收【装饰器的参数】 times
    def decorator(func):              # 中间层:接收【被装饰的函数】
        # @wraps(func)
        def wrapper(*args, **kwargs): # 最内层:实际执行
            for _ in range(times):
                result = func(*args, **kwargs)
        return wrapper
    return decorator
#
@repeat(times=3)                      # 重复执行3次 #  greet = repeat(3)(greet)
def greet(name):
    print(f"hi {name}")
greet("Tom")
# 输出:  hi Tom / hi Tom / hi Tom
print(greet.__name__)

'''
装饰器最内层永远是 wrapper(接住"调用时的实参");
往外一层接住"被装饰的函数";
如果装饰器自己还要参数,就再往外包一层接住"装饰器的参数"。
'''
