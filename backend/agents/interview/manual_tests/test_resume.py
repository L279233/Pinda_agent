import requests, json, sys

BASE = 'http://localhost:8000/api/v1'

r = requests.post(f'{BASE}/auth/login',
    json={'username': 'student01', 'password': 'Student@123456'})
token = r.json()['access_token']
headers = {'Authorization': f'Bearer {token}'}
print('✅ 登录成功')

review_id = 'e09883d8-572b-40da-82d9-d7f4036936b7'
r = requests.get(f'{BASE}/resume/reviews/{review_id}', headers=headers)
data = r.json()

print(f'\n审查状态: {data["status"]}')
print(f'综合得分: {data["weighted_score"]} / 100')

print('\n=== 六维度评分（原始结构）===')
for d in data.get('dimension_scores', []):
    print(json.dumps(d, ensure_ascii=False, indent=2))
    print('---')

print('\n=== 问题诊断（第1条原始结构）===')
issues = data.get('issues', [])
if issues:
    print(json.dumps(issues[0], ensure_ascii=False, indent=2))

print('\n=== 整体评价（原始结构）===')
print(json.dumps(data.get('summary', {}), ensure_ascii=False, indent=2))
