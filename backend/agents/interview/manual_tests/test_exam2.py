import requests, json, time

BASE = 'http://localhost:8000/api/v1'

# 1. 登录
r = requests.post(f'{BASE}/auth/login', json={'username': 'student01', 'password': 'Student@123456'})
student_token = r.json()['access_token']
student_headers = {'Authorization': f'Bearer {student_token}'}

r = requests.post(f'{BASE}/auth/login', json={'username': 'teacher01', 'password': 'Teacher@123456'})
teacher_token = r.json()['access_token']
teacher_headers = {'Authorization': f'Bearer {teacher_token}', 'Content-Type': 'application/json'}
print('✅ 登录成功（学员+教师）')

# 2. 提交试卷
print('\n=== 提交试卷 ===')
with open('/Users/ligang/Desktop/EduAgent/tests/fixtures/test_exam_answer.docx', 'rb') as f:
    r = requests.post(f'{BASE}/exam/submit',
        headers=student_headers,
        data={'exam_id': 'aaaaaaaa-0000-0000-0000-000000000001'},
        files={'file': ('test_exam_answer.docx', f,
                        'application/vnd.openxmlformats-officedocument.wordprocessingml.document')})
print(f'状态码: {r.status_code}')
if r.status_code != 202:
    print(f'错误: {r.text}')
    exit(1)
submission_id = r.json()['submission_id']
print(f'submission_id: {submission_id}')

# 3. 轮询，最多等4分钟
print('\n等待 AI 批改完成（最多4分钟）...')
for i in range(48):
    time.sleep(5)
    r = requests.get(f'{BASE}/exam/pending-reviews', headers=teacher_headers)
    if r.status_code == 200:
        items = r.json().get('items', [])
        sub = next((x for x in items if x['submission_id'] == submission_id), None)
        if sub:
            print(f'\n  [{(i+1)*5}s] ✅ 进入待确认列表！')
            pr = sub['pre_review']
            print(f'  AI预批改得分: {pr["total_score"]}/{pr["full_score"]}')
            print(f'  需复核题数: {pr["needs_review_count"]}')
            if sub.get('weak_points'):
                wp_tags = [wp.get('tag', '?') for wp in sub['weak_points'][:3]]
                print(f'  前3个薄弱点: {wp_tags}')
            break
    if (i+1) % 6 == 0:
        print(f'  [{(i+1)*5}s] 仍在处理中...')
else:
    print('❌ 超时，处理未完成')
    exit(1)

# 4. 教师获取详情
print('\n=== 获取预批改详情 ===')
r = requests.get(f'{BASE}/exam/submissions/{submission_id}/review', headers=teacher_headers)
print(f'状态码: {r.status_code}')
if r.status_code == 200:
    d = r.json()
    s = d['pre_review_summary']
    print(f'总分: {s["total_score"]}/{s["full_score"]} ({s["score_rate"]*100:.1f}%)')
    for q in s.get('by_question', [])[:4]:
        print(f'  Q{q["question_no"]} ({q["question_type"]}): {q.get("score",0)}/{q.get("full_score",0)}分, 需复核={q.get("needs_review",False)}')
    print(f'薄弱点数: {len(d.get("weak_points", []))}')
    if d.get('weak_points_summary'):
        print(f'薄弱点摘要: {d["weak_points_summary"][:150]}')

# 5. 教师确认
print('\n=== 教师确认发布 ===')
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
        print(f'摘要: {result["weak_points_summary"][:200]}')
else:
    print(f'错误: {r.text[:200]}')
