# scripts/manual_tests/test_unified_chat.py
# 统一入口 /api/v1/chat/stream 端到端验证脚本
# 运行前提：后端已启动（uvicorn backend.main:app --reload --port 8000）

import sys
import json
import httpx

sys.path.insert(0, ".")

BASE_URL   = "http://localhost:8000/api/v1"
STUDENT_UN = "student01"
STUDENT_PW = "Student@123456"


def login(username, password) -> str:
    resp = httpx.post(
        f"{BASE_URL}/auth/login",
        json={"username": username, "password": password},
        trust_env=False,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def chat_stream(token: str, session_id: str, message: str) -> list[dict]:
    events = []
    with httpx.stream(
        "POST",
        f"{BASE_URL}/chat/stream",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"session_id": session_id, "message": message},
        timeout=30.0,
    ) as resp:
        for line in resp.iter_lines():
            if not line.startswith("data:"):
                continue
            data_str = line[len("data:"):].strip()
            if not data_str:
                continue
            events.append(json.loads(data_str))
    return events


def print_events(events: list[dict]):
    for e in events:
        t = e.get("type", "?")
        if t == "token":
            print(f"  [token] {e.get('content','')[:60]}")
        elif t == "routing_decision":
            print(f"  [routing_decision] agent={e.get('agent_type')} mode={e.get('execution_mode')} reason={e.get('reason','')[:40]}")
        elif t == "guidance":
            print(f"  [guidance] {e.get('message','')[:60]}")
        elif t == "pipeline_plan":
            steps = e.get("steps", [])
            print(f"  [pipeline_plan] title={e.get('title')} steps={len(steps)}")
            for s in steps:
                print(f"    step{s['step']}: {s['label']} → {s['action_url']}")
        elif t == "progress":
            print(f"  [progress] {e.get('stage')}")
        elif t == "meta":
            print(f"  [meta] answer_mode={e.get('answer_mode')} sources={len(e.get('sources',[]))}")
        elif t == "done":
            print(f"  [done]")
        elif t == "error":
            print(f"  [error] {e.get('message')}")


def section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print("="*60)


if __name__ == "__main__":
    token = login(STUDENT_UN, STUDENT_PW)
    print(f"登录成功: token={token[:20]}...")

    # 测试1：问候 → 前置拦截
    section("① 问候 → 前置拦截（应无 routing_decision 事件）")
    events = chat_stream(token, "s001", "你好")
    print_events(events)
    has_routing = any(e.get("type") == "routing_decision" for e in events)
    print(f"验证：无 routing_decision = {not has_routing}  {'✅' if not has_routing else '❌'}")

    # 测试2：感谢 → 前置拦截
    section("② 感谢 → 前置拦截")
    events = chat_stream(token, "s002", "谢谢你的帮助！")
    types  = [e.get("type") for e in events]
    print(f"事件序列：{types}")

    # 测试3：技术问答 → QA 路由
    section("③ 技术问答 → QA 路由（应有 routing_decision + token + meta）")
    events = chat_stream(token, "s003", "什么是 HashMap 的扩容机制？")
    print_events(events)
    types  = [e.get("type") for e in events]
    print(f"事件序列：{types}")

    # 测试4：简历意图 → 引导跳转
    section("④ 简历意图 → guidance 引导（应无 token 事件）")
    events = chat_stream(token, "s004", "帮我看看我的简历，提些建议")
    print_events(events)
    has_token = any(e.get("type") == "token" for e in events)
    print(f"验证：无 token 事件 = {not has_token}  {'✅' if not has_token else '❌'}")

    # 测试5：面试意图 → 引导跳转
    section("⑤ 面试意图 → guidance 引导")
    events = chat_stream(token, "s005", "我想练习一下面试")
    print_events(events)

    # 测试6：求职全链路 → pipeline_plan
    section("⑥ 求职意图 → pipeline_plan（两步骤卡片）")
    events = chat_stream(token, "s006", "我要准备求职了，帮我从简历到面试全套搞定")
    print_events(events)
    has_plan = any(e.get("type") == "pipeline_plan" for e in events)
    print(f"验证：收到 pipeline_plan = {has_plan}  {'✅' if has_plan else '❌'}")

    print(f"\n{'='*60}")
    print("  统一入口集成验证完成 ✅")
    print("="*60)
