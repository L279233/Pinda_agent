import requests, json

BASE = 'http://localhost:8000/api/v1'

r = requests.post(f'{BASE}/auth/login',
    json={'username': 'student01', 'password': 'Student@123456'})
token = r.json()['access_token']
headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
print('✅ 登录成功')

# 1. 创建面试会话
print('\n=== 开始面试会话 ===')
r = requests.post(f'{BASE}/interview/sessions', headers=headers,
    json={'target_position': 'Java后端开发工程师'})
print(f'状态码: {r.status_code}')
if r.status_code != 201:
    print(f'错误: {r.text}')
    exit(1)
data = r.json()
session_id = data['session_id']
print(f'session_id: {session_id}')
print(f'面试官开场白:\n{data["message"]}\n')

# 2. 第一轮回答（暖场阶段）
print('=== 第1轮对话 ===')
r = requests.post(f'{BASE}/interview/sessions/{session_id}/chat',
    headers=headers,
    json={'message': '你好，我叫王雪纯，是一名应届硕士毕业生，主要做Java后端开发，有项目经验。'})
print(f'状态码: {r.status_code}')
if r.status_code != 200:
    print(f'错误: {r.text}')
    exit(1)
data = r.json()
print(f'阶段: {data["current_stage"]}, 轮次: {data["total_turns"]}')
print(f'面试官:\n{data["reply"][:300]}\n')

# 3. 第二轮
print('=== 第2轮对话 ===')
r = requests.post(f'{BASE}/interview/sessions/{session_id}/chat',
    headers=headers,
    json={'message': '我主要使用Spring Boot、MySQL、Redis，也接触过LangChain做了一些AI应用开发。毕业于北京理工大学，计算机科学专业。'})
print(f'状态码: {r.status_code}')
data = r.json()
print(f'阶段: {data["current_stage"]}, 轮次: {data["total_turns"]}')
print(f'面试官:\n{data["reply"][:300]}\n')

# 4. 第三轮
print('=== 第3轮对话 ===')
r = requests.post(f'{BASE}/interview/sessions/{session_id}/chat',
    headers=headers,
    json={'message': 'Java中HashMap的工作原理？它和Hashtable有什么区别？'})
print(f'状态码: {r.status_code}')
data = r.json()
print(f'阶段: {data["current_stage"]}, 轮次: {data["total_turns"]}, 已结束: {data["is_finished"]}')
print(f'面试官:\n{data["reply"][:400]}\n')

print('✅ 模拟面试 Agent 测试通过！')
print(f'(session_id={session_id} 可继续追加对话)')
