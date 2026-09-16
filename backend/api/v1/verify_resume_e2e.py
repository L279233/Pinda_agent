#!/usr/bin/env python3
"""
简历审查 Agent —— 端到端本地验证脚本

它会自动：登录 → 上传 PDF → 轮询查询 → 打印完整审查报告。

【前提】先在另一个终端把后端服务跑起来（常驻进程，别关）：
    uvicorn backend.main:app --reload --port 8000

【用法】
    python verify_resume_e2e.py --file /path/to/简历.pdf
    python verify_resume_e2e.py --file 简历.pdf --base-url http://localhost:8000 \
           --username student01 --password 'Student@123456'

依赖：pip install httpx
"""

import argparse
import sys
import time

import httpx


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="简历审查 Agent 端到端验证")
    p.add_argument("--file", required=True, help="要上传的 PDF 简历路径")
    p.add_argument("--base-url", default="http://localhost:8000", help="后端服务地址")
    p.add_argument("--username", default="student01", help="登录用户名")
    p.add_argument("--password", default="Student@123456", help="登录密码")
    p.add_argument("--interval", type=float, default=3.0, help="轮询间隔（秒）")
    p.add_argument("--timeout", type=float, default=180.0, help="最长等待（秒）")
    return p.parse_args()


def login(client: httpx.Client, username: str, password: str) -> str:
    """登录，返回 access_token。"""
    resp = client.post("/login", json={"username": username, "password": password})
    if resp.status_code != 200:
        sys.exit(f"❌ 登录失败（{resp.status_code}）：{resp.text}")
    token = resp.json().get("access_token")
    if not token:
        sys.exit(f"❌ 登录响应里没有 access_token：{resp.text}")
    print(f"✅ 登录成功（用户：{username}）")
    return token


def upload(client: httpx.Client, headers: dict, file_path: str) -> str:
    """上传 PDF，返回 review_id。"""
    try:
        f = open(file_path, "rb")
    except OSError as e:
        sys.exit(f"❌ 打不开文件 {file_path}：{e}")
    with f:
        resp = client.post("/resume/upload", headers=headers,
                           files={"file": (file_path.split("/")[-1], f, "application/pdf")})
    if resp.status_code != 202:
        sys.exit(f"❌ 上传失败（{resp.status_code}）：{resp.text}")
    data = resp.json()
    review_id = data["review_id"]
    print(f"✅ 上传成功 → review_id = {review_id}")
    print(f"   状态：{data['status']}（审查在后台进行，开始轮询…）\n")
    return review_id


def poll(client: httpx.Client, headers: dict, review_id: str,
         interval: float, timeout: float) -> dict:
    """轮询查询，直到 done / failed / 超时。"""
    deadline = time.time() + timeout
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        resp = client.get(f"/resume/reviews/{review_id}", headers=headers)
        if resp.status_code != 200:
            sys.exit(f"❌ 查询失败（{resp.status_code}）：{resp.text}")
        data = resp.json()
        status = data["status"]
        print(f"  第 {attempt} 次轮询 → status: {status}")
        if status in ("done", "failed"):
            return data
        time.sleep(interval)
    sys.exit("⏰ 超时：仍未完成。请检查后端日志（搜 resume.background_task_failed / 是否配了可用的 DEEPSEEK_API_KEY）。")


def print_report(data: dict) -> None:
    """漂亮地打印审查报告。"""
    if data["status"] == "failed":
        print("\n❌ 审查失败：", data.get("error_msg", "（无错误信息）"))
        return

    print("\n" + "=" * 48)
    print("            简历审查报告")
    print("=" * 48)
    print(f"综合得分：{data.get('weighted_score')} / 100\n")

    print("【六维度评分】")
    for d in data.get("dimension_scores", []):
        weight = d.get("weight", 0)
        print(f"  · {d.get('dimension')}：{d.get('score')} 分（权重 {int(weight * 100)}%）")
        for issue in d.get("issues", []):
            print(f"      - 问题：{issue}")
        for sug in d.get("suggestions", []):
            print(f"      - 建议：{sug}")

    print("\n【问题诊断】")
    issues = data.get("issues", [])
    if not issues:
        print("  （无）")
    for it in issues:
        loc = it.get("location", "")
        print(f"  [{it.get('priority', '').upper()}] {it.get('description', '')}"
              + (f"（{loc}）" if loc else ""))
        if it.get("suggestion"):
            print(f"        → {it['suggestion']}")

    summary = data.get("summary") or {}
    print("\n【整体评价】")
    print("  亮点：", "；".join(summary.get("highlights", [])) or "（无）")
    print("  核心改进：", "；".join(summary.get("core_improvements", [])) or "（无）")
    print("  综合评语：", summary.get("overall_comment", "（无）"))
    print("  岗位匹配：", summary.get("fit_assessment", "（无）"))
    print("=" * 48)


def main() -> None:
    args = parse_args()
    with httpx.Client(base_url=args.base_url, timeout=30.0) as client:
        token = login(client, args.username, args.password)
        headers = {"Authorization": f"Bearer {token}"}
        review_id = upload(client, headers, args.file)
        data = poll(client, headers, review_id, args.interval, args.timeout)
        print_report(data)


if __name__ == "__main__":
    main()
