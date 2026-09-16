import requests, json, time

BASE = 'http://localhost:8000/api/v1'

# 1. 学员登录
r = requests.post(f'{BASE}/auth/login',
    json={'username': 'student01', 'password': 'Student@123456'})
student_token = r.json()['access_token']
student_headers = {'Authorization': f'Bearer {student_token}'}
print('✅ 学员登录成功')

# 2. 教师登录
r = requests.post(f'{BASE}/auth/login',
    json={'username': 'teacher01', 'password': 'Teacher@123456'})
teacher_token = r.json()['access_token']
teacher_headers = {'Authorization': f'Bearer {teacher_token}', 'Content-Type': 'application/json'}
print('✅ 教师登录成功')

# 3. 学员提交试卷
print('\n=== 提交试卷 ===')
exam_id = 'aaaaaaaa-0000-0000-0000-000000000001'
with open('/Users/ligang/Desktop/EduAgent/tests/fixtures/test_exam_answer.docx', 'rb') as f:
    r = requests.post(f'{BASE}/exam/submit',
        headers=student_headers,
        data={'exam_id': exam_id},
        files={'file': ('test_exam_answer.docx', f, 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')})
print(f'状态码: {r.status_code}')
if r.status_code != 202:
    print(f'错误: {r.text}')
    exit(1)
data = r.json()
submission_id = data['submission_id']
print(f'submission_id: {submission_id}')
print(f'消息: {data["message"]}')

# 4. 轮询等待 AI 批改完成（状态变为 pending_review）
print('\n等待 AI 批改完成...')
for i in range(36):  # 最多等3分钟
    time.sleep(5)
    r = requests.get(f'{BASE}/exam/pending-reviews', headers=teacher_headers)
    if r.status_code == 200:
        items = r.json().get('items', [])
        submission = next((x for x in items if x['submission_id'] == submission_id), None)
        if submission:
            print(f'\n  [{i*5+5}s] 待确认列表中找到提交！')
            print(f'  学员: {submission["student_name"]}')
            print(f'  AI预批改: {submission["pre_review"]["total_score"]}/{submission["pre_review"]["full_score"]}分')
            print(f'  需复核题数: {submission["pre_review"]["needs_review_count"]}')
            if submission["weak_points"]:
                print(f'  薄弱点: {[wp.get("tag", wp.get("knowledge_tag","?")) for wp in submission["weak_points"][:3]]}')
            break
    else:
        print(f'  [{i*5+5}s] 状态: {r.status_code}')
else:
    print('❌ 等待超时')
    exit(1)

# 5. 教师获取详情
print('\n=== 获取预批改详情 ===')
r = requests.get(f'{BASE}/exam/submissions/{submission_id}/review', headers=teacher_headers)
print(f'状态码: {r.status_code}')
if r.status_code == 200:
    detail = r.json()
    summary = detail['pre_review_summary']
    print(f'总分: {summary.get("total_score")}/{summary.get("full_score")} ({round(summary.get("score_rate",0)*100)}%)')
    print(f'需复核: {summary.get("needs_review_count")} 题')
    print(f'薄弱点数: {len(detail.get("weak_points", []))}')
    if detail.get("weak_points_summary"):
        print(f'薄弱点摘要: {detail["weak_points_summary"][:100]}')

# 6. 教师 Approve
print('\n=== 教师批准发布 ===')
r = requests.post(f'{BASE}/exam/submissions/{submission_id}/confirm',
    headers=teacher_headers,
    json={'action': 'approve', 'modifications': []})
print(f'状态码: {r.status_code}')
if r.status_code == 200:
    result = r.json()
    print(f'✅ 发布成功！')
    print(f'最终得分: {result["final_score"]}/{result["full_score"]} ({result["score_rate"]*100:.1f}%)')
    print(f'薄弱点数: {len(result["weak_points"])}')
    if result.get("weak_points_summary"):
        print(f'薄弱点摘要: {result["weak_points_summary"][:150]}')
else:
    print(f'错误: {r.text}')
