import requests, json, time

BASE = 'http://localhost:8000/api/v1'

r = requests.post(f'{BASE}/auth/login',
    json={'username': 'student01', 'password': 'Student@123456'})
token = r.json()['access_token']
headers = {'Authorization': f'Bearer {token}'}
print('✅ 登录成功')

# 上传简历
with open('/Users/ligang/Desktop/EduAgent/tests/fixtures/简历-王雪纯.pdf', 'rb') as f:
    r = requests.post(f'{BASE}/resume/upload',
        headers=headers,
        files={'file': ('简历-王雪纯.pdf', f, 'application/pdf')})
print(f'上传状态码: {r.status_code}')
data = r.json()
review_id = data['review_id']
print(f'review_id: {review_id}')
print(f'消息: {data["message"]}')

# 轮询等待完成（最多120秒）
print('\n等待审查完成...')
for i in range(24):
    time.sleep(5)
    r = requests.get(f'{BASE}/resume/reviews/{review_id}', headers=headers)
    data = r.json()
    status = data.get('status')
    print(f'  [{i*5+5}s] status={status}')
    if status == 'done':
        break

if data.get('status') != 'done':
    print('❌ 超时，审查未完成')
    exit(1)

print('\n✅ 简历审查完成！')
print(f'综合得分: {data["weighted_score"]} / 100')

print('\n=== 六维度评分 ===')
for d in data.get('dimension_scores', []):
    print(f'  {d["dimension"]}（权重{int(d["weight"]*100)}%）: {d["score"]}分')
    for issue in d.get('issues', [])[:2]:
        print(f'    ⚠ {issue}')
    for s in d.get('suggestions', [])[:1]:
        print(f'    → {s}')

print('\n=== 问题诊断（前5条）===')
for i, issue in enumerate(data.get('issues', [])[:5]):
    print(f'  [{issue["priority"].upper()}] {issue["dimension"]} - {issue["description"][:60]}')
    print(f'    位置: {issue["location"]}')

print('\n=== 整体评价 ===')
summary = data.get('summary', {})
print(f'  综合评语: {summary.get("overall_comment", "")}')
print(f'  匹配度: {summary.get("fit_assessment", "")}')
print('  核心亮点:')
for h in summary.get('highlights', []):
    print(f'    ✓ {h}')
print('  主要改进:')
for c in summary.get('core_improvements', []):
    print(f'    → {c}')
