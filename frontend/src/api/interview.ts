import client from './client'

export interface StartSessionRequest {
  application_id: string
}

export interface StartSessionResponse {
  session_id: string
  target_position: string
  status: string
  message: string
}

export interface ChatRequest {
  message: string
}

export interface ChatResponse {
  session_id: string
  reply: string
  current_stage: string
  total_turns: number
  is_finished: boolean
  submission?: CandidateInterviewStatus
}

export interface ChatDonePayload {
  type: 'done'
  reply: string          // AI 本轮回复全文（generate_response_node 用 ainvoke，token 不流式）
  current_stage: string
  total_turns: number
  is_finished: boolean
  submission?: CandidateInterviewStatus
}

export interface ChatStreamCallbacks {
  onToken: (chunk: string) => void
  onDone: (payload: ChatDonePayload) => void
  onError: (err: Error) => void
}

export interface DimensionEval {
  dimension: string
  score: number
  comment: string
}

export interface InterviewReport {
  session_id: string
  target_position: string
  overall_score: number
  dimensions: DimensionEval[]
  strengths: string[]
  improvements: string[]
  overall_comment: string
  recommended_topics: string[]
  next_step_advice: string
}

export interface SessionListItem {
  session_id: string
  target_position: string
  status: 'in_progress' | 'submitted'
  message: string
  created_at: string
}

export interface CandidateInterviewStatus {
  session_id: string
  status: 'in_progress' | 'submitted'
  message: string
}

export const interviewApi = {
  startSession: (data: StartSessionRequest) =>
    client.post<StartSessionResponse>('/interview/sessions', data),

  chat: (sessionId: string, data: ChatRequest) =>
    client.post<ChatResponse>(`/interview/sessions/${sessionId}/chat`, data),

  /** 通过当前站点代理消费 SSE，避免前后端地址或跨域配置不一致。 */
  chatStream(sessionId: string, message: string, callbacks: ChatStreamCallbacks): void {
    const token = localStorage.getItem('edu-agent-token') || ''
    const apiBase = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? ''
    let settled = false

    const dispatch = (line: string) => {
      if (!line.startsWith('data:')) return
      const raw = line.slice(5).trim()
      if (!raw) return

      let payload: Record<string, any>
      try {
        payload = JSON.parse(raw)
      } catch (error) {
        throw new Error(`面试响应格式错误：${error instanceof Error ? error.message : '无法解析数据'}`)
      }

      if (payload.type === 'token') {
        callbacks.onToken(String(payload.content ?? ''))
      } else if (payload.type === 'done') {
        settled = true
        callbacks.onDone(payload as unknown as ChatDonePayload)
      } else if (payload.type === 'error') {
        settled = true
        callbacks.onError(new Error(String(payload.message ?? '流式输出异常')))
      }
    }

    fetch(`${apiBase}/api/v1/interview/sessions/${sessionId}/chat/stream`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ message }),
    })
      .then(async (resp) => {
        if (!resp.ok || !resp.body) {
          let detail = ''
          try {
            const payload = await resp.json() as { detail?: string }
            detail = payload.detail ?? ''
          } catch { /* 非 JSON 错误响应 */ }
          settled = true
          callbacks.onError(new Error(detail || `面试服务请求失败（HTTP ${resp.status}）`))
          return
        }

        const reader = resp.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''

        while (true) {
          const { done, value } = await reader.read()
          if (done) break

          buffer += decoder.decode(value, { stream: true })
          const parts = buffer.split(/\r?\n\r?\n/)
          buffer = parts.pop() ?? ''

          for (const part of parts) {
            for (const line of part.split(/\r?\n/)) {
              dispatch(line)
            }
          }
        }

        if (buffer.trim()) {
          for (const line of buffer.split(/\r?\n/)) {
            dispatch(line)
          }
        }

        if (!settled) {
          settled = true
          callbacks.onError(new Error('连接已断开，未收到完整响应'))
        }
      })
      .catch((error: unknown) => {
        if (!settled) {
          settled = true
          callbacks.onError(error instanceof Error ? error : new Error('面试请求失败'))
        }
      })
  },

  getReport: (sessionId: string) =>
    client.get<CandidateInterviewStatus>(`/interview/sessions/${sessionId}/report`),

  listSessions: () =>
    client.get<{ items: SessionListItem[]; total: number }>('/interview/sessions'),
}
