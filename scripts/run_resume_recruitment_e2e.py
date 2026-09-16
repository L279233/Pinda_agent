"""Run the authorized local resume recruitment E2E check."""
import argparse
import sys
import time
from pathlib import Path

import httpx


def login(client: httpx.Client, username: str, password: str) -> str:
    response = client.post("/auth/login", json={"username": username, "password": password})
    response.raise_for_status()
    return response.json()["access_token"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000/api/v1")
    parser.add_argument("--timeout", type=float, default=300)
    args = parser.parse_args()
    pdf = Path(args.file)
    if pdf.suffix.lower() != ".pdf" or not pdf.is_file():
        raise SystemExit("必须提供存在的 PDF 文件")
    with httpx.Client(base_url=args.base_url, timeout=60.0) as client:
        candidate_token = login(client, "student01", "Student@123456")
        candidate_headers = {"Authorization": f"Bearer {candidate_token}"}
        positions = client.get("/positions", headers=candidate_headers)
        positions.raise_for_status()
        position = next(item for item in positions.json()["items"] if "本地测试" in item["title"])
        application_id = position.get("application_id")
        if not application_id:
            created = client.post("/applications", headers=candidate_headers, json={"position_id": position["position_id"]})
            created.raise_for_status()
            application_id = created.json()["application_id"]
        with pdf.open("rb") as handle:
            uploaded = client.post(
                "/resume/upload", headers=candidate_headers,
                data={"application_id": application_id},
                files={"file": (pdf.name, handle, "application/pdf")},
            )
        uploaded.raise_for_status()
        review_id = uploaded.json()["review_id"]
        forbidden = {"weighted_score", "scores", "dimension_scores", "issues", "summary", "report", "ranking", "decision"}
        deadline = time.time() + args.timeout
        candidate_result = {}
        while time.time() < deadline:
            candidate_result = client.get(f"/resume/reviews/{review_id}", headers=candidate_headers)
            candidate_result.raise_for_status()
            candidate_payload = candidate_result.json()
            if forbidden.intersection(candidate_payload):
                raise RuntimeError(f"候选人响应暴露内部字段：{forbidden.intersection(candidate_payload)}")
            if candidate_payload["status"] in {"done", "failed", "submitted"}:
                break
            time.sleep(3)
        else:
            raise TimeoutError("简历评审超过等待时间")
        recruiter_token = login(client, "teacher01", "Teacher@123456")
        recruiter_headers = {"Authorization": f"Bearer {recruiter_token}"}
        recruiter = client.get(f"/recruiter/applications/{application_id}", headers=recruiter_headers)
        recruiter.raise_for_status()
        payload = recruiter.json()
        print({
            "application_id": application_id,
            "review_id": review_id,
            "candidate_status": candidate_payload.get("status"),
            "candidate_keys": sorted(candidate_payload),
            "recruiter_resume_status": (payload.get("resume") or {}).get("status"),
            "application": payload.get("application"),
        })


if __name__ == "__main__":
    main()
