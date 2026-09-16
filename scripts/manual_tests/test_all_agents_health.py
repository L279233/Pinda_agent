# scripts/manual_tests/test_all_agents_health.py
# 4 个 Agent 健康冒烟测试
# 运行前提：后端已启动（uvicorn backend.main:app --reload --port 8000）

import sys
import json
import httpx

sys.path.insert(0, ".")

BASE_URL = "http://localhost:8000/api/v1"


def login():
    resp = httpx.post(
        f"{BASE_URL}/auth/login",
        json={"username": "student01", "password": "Student@123456"},
        trust_env=False,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


if __name__ == "__main__":
    token   = login()
    headers = {"Authorization": f"Bearer {token}"}
    results = []

    # QA Agent
    try:
        chunks = []
        with httpx.stream(
            "POST",
            f"{BASE_URL}/qa/stream",
            headers={**headers, "Content-Type": "application/json"},
            json={"session_id": "health001", "message": "什么是多态？"},
            timeout=30.0,
        ) as r:
            for line in r.iter_lines():
                if line.startswith("data:"):
                    chunks.append(json.loads(line[5:].strip()))
        results.append(("QA Agent", "✅" if any(c.get("type") == "done" for c in chunks) else "❌"))
    except Exception as e:
        results.append(("QA Agent", f"❌ {e}"))

    # 简历审查 Agent（仅测试接口响应，不上传文件）
    try:
        resp = httpx.get(f"{BASE_URL}/resume/reviews", headers=headers, trust_env=False, timeout=10.0)
        results.append(("Resume Agent", "✅" if resp.status_code == 200 else f"❌ HTTP {resp.status_code}"))
    except Exception as e:
        results.append(("Resume Agent", f"❌ {e}"))

    # 试卷批改 Agent（查询历史列表）
    try:
        resp = httpx.get(f"{BASE_URL}/exam/submissions", headers=headers, trust_env=False, timeout=10.0)
        results.append(("Exam Agent", "✅" if resp.status_code == 200 else f"❌ HTTP {resp.status_code}"))
    except Exception as e:
        results.append(("Exam Agent", f"❌ {e}"))

    # 面试 Agent（查询历史列表）
    try:
        resp = httpx.get(f"{BASE_URL}/interview/sessions", headers=headers, trust_env=False, timeout=10.0)
        results.append(("Interview Agent", "✅" if resp.status_code == 200 else f"❌ HTTP {resp.status_code}"))
    except Exception as e:
        results.append(("Interview Agent", f"❌ {e}"))

    print("\nAgent 健康检查结果：")
    for name, status in results:
        print(f"  {name}: {status}")
