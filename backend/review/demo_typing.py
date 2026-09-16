"""
一次性搞懂：TypedDict / Optional / Annotated / reducer，以及它们和普通类的区别。
"""

from typing import Annotated, Optional, TypedDict, get_type_hints


def line(title: str) -> None:
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


# ==============================================================
# 第 1 节：TypedDict 在运行时就是一个普通 dict
# ==============================================================
line("第 1 节：TypedDict 运行时 == dict")


class QAState(TypedDict):
    student_id: str
    course_id: Optional[str]                       # 值可以是 str 或 None
    confidence: float
    retrieved_chunks: list[dict]                   # 普通字段（无 reducer）→ 会被覆盖
    messages: Annotated[list[str], lambda old, new: old + new]  # 带 reducer → 会被追加


# 构造方式：它就是个字典，下面两种写法等价
state1: QAState = {"student_id": "s1", "course_id": None,
                   "confidence": 0.0, "retrieved_chunks": [], "messages": []}
state2 = QAState(student_id="s2", course_id="java",
                 confidence=0.9, retrieved_chunks=[], messages=[])

print("type(state1)      ->", type(state1))         # <class 'dict'>
print("isinstance dict   ->", isinstance(state1, dict))   # True
print("取值用中括号       ->", state1["student_id"])       # ✅
# print(state1.student_id)  # ❌ AttributeError：它没有属性访问

# TypedDict 不支持 isinstance 检查，证明它不是"真正的类"
try:
    isinstance(state1, QAState)
except TypeError as e:
    print("isinstance(QAState)->", "报错:", e)


# ==============================================================
# 第 2 节：和普通类对比（有 self / 方法 / 真实例 vs 只是数据形状）
# ==============================================================
# line("第 2 节：普通类 vs TypedDict")

#
# class NormalClass:
#     def __init__(self, x: int):
#         self.x = x                # 有 self，有实例属性
#
#     def doubled(self) -> int:      # 有方法/行为
#         return self.x * 2
#
#
# obj = NormalClass(10)
# print("普通类：属性访问  obj.x      ->", obj.x)
# print("普通类：调用方法  obj.doubled->", obj.doubled())
# print("普通类 isinstance         ->", isinstance(obj, NormalClass))  # True
#
# print("TypedDict：取值   state['confidence'] ->", state2["confidence"])
# print("结论：普通类描述【对象=数据+行为】；TypedDict 只描述【字典的字段和类型】，"
#       "没有方法、没有真实例，访问用 ['key']。")
#
#
# # ==============================================================
# # 第 3 节：Optional = 值可以是 None（不是 key 可以缺失）
# # ==============================================================
# line("第 3 节：Optional[str] == str | None")
#
# a: Optional[str] = "java"     # 合法
# b: Optional[str] = None       # 合法
# print("course_id 可以是字符串 ->", a)
# print("course_id 也可以是 None ->", b)
# print("注意：Optional 说的是【值能为 None】，不是【这个 key 可以不存在】。"
#       "key 能否缺失由 TypedDict 的 total=False / NotRequired 控制。")
#

# ==============================================================
# 第 4 节：Annotated = 真正的类型 + 附加元数据，并把元数据读出来
# ==============================================================
line("第 4 节：Annotated[类型, 元数据] 怎么读元数据")

# 必须 include_extras=True，否则 Annotated 的元数据会被丢掉
hints = get_type_hints(QAState, include_extras=True)
print(f"hints-->{hints}")
for field, hint in hints.items():
    if hasattr(hint, "__metadata__"):
        # Annotated 类型：__origin__ 是真正的类型，__metadata__ 是附加的元数据元组
        print(f"  {field:18s} 类型={hint.__origin__}  元数据(reducer)={hint.__metadata__}")
    else:
        print(f"  {field:18s} 类型={hint}  元数据=无")

print("LangGraph 就是这样读出 messages 上挂的 reducer 的——"
      "类型检查器忽略元数据，框架靠反射把它取出来用。")


# ==============================================================
# 第 5 节：reducer 的作用 —— 带 reducer 的字段【合并】，普通字段【覆盖】
# ==============================================================
line("第 5 节：模拟 LangGraph 更新状态（覆盖 vs 追加）")


def get_reducer(hint):
    """从字段的类型注解里找出 reducer（Annotated 的可调用元数据）"""
    if hasattr(hint, "__metadata__"):
        for meta in hint.__metadata__:
            if callable(meta):
                return meta
    return None


def apply_node_update(state: dict, update: dict) -> dict:
    """模拟 LangGraph：把某个节点返回的局部更新合并进 state"""
    new_state = dict(state)
    for key, value in update.items():
        reducer = get_reducer(hints.get(key))
        if reducer is not None and key in new_state:
            # 有 reducer → 调用它合并旧值和新值（例如把新消息追加到历史后面）
            new_state[key] = reducer(new_state[key], value)
        else:
            # 没有 reducer → 直接覆盖
            new_state[key] = value
    return new_state


# 初始状态
state: QAState = {"student_id": "s1", "course_id": "java",
                  "confidence": 0.0, "retrieved_chunks": [], "messages": ["你好"]}
print("初始        :", {"messages": state["messages"], "chunks": state["retrieved_chunks"]})

# 节点 A 返回：往 messages 追加一条，重新写 retrieved_chunks
state = apply_node_update(state, {"messages": ["我想学 Spring"],
                                  "retrieved_chunks": [{"id": "c1"}]})
print("节点A 之后  :", {"messages": state["messages"], "chunks": state["retrieved_chunks"]})

# 节点 B 返回：再追加一条消息，又重新写 retrieved_chunks
state = apply_node_update(state, {"messages": ["IOC 是什么"],
                                  "retrieved_chunks": [{"id": "c2"}]})
print("节点B 之后  :", {"messages": state["messages"], "chunks": state["retrieved_chunks"]})

print("\n看清楚：")
print("  messages          带 reducer → 三条消息全部【累积】下来")
print("  retrieved_chunks  无 reducer → 每次都被【整体覆盖】，只剩最后一次的 c2")
print("\n这就是为什么对话历史要用 Annotated[..., add_messages]，"
      "而召回结果、置信度这类每轮重算的字段用普通字段就行。")