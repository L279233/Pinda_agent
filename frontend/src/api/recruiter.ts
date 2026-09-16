import client from './client'

export interface RecruiterApplicationItem {
  application_id: string
  candidate_id: string
  candidate_name?: string | null
  position_id: string
  position_title: string
  application_status: string
  resume_decision: string
  exam_status: string
  interview_status: string
  final_decision?: string | null
  resume_score?: number | null
  exam_score?: number | null
  exam_full_score?: number | null
  interview_score?: number | null
  created_at?: string | null
  updated_at?: string | null
}

export interface RecruiterPosition {
  position_id: string
  code: string
  title: string
  department?: string | null
  jd_text: string
  requirements: Record<string, unknown>
  evaluation_weights: Record<string, number>
  resume_pass_score: number
  exam_window_hours: number
  interview_window_hours: number
  exam_id?: string | null
  exam_title?: string | null
  status: 'draft' | 'open' | 'closed'
  apply_deadline?: string | null
  application_count: number
  created_at?: string | null
  updated_at?: string | null
}

export interface PositionPayload {
  code: string
  title: string
  department?: string | null
  jd_text: string
  requirements: Record<string, unknown>
  resume_pass_score: number
  exam_window_hours: number
  interview_window_hours: number
  exam_id?: string | null
  status: 'draft' | 'open' | 'closed'
  apply_deadline?: string | null
}

export interface RecruiterExam {
  exam_id: string
  title: string
  description?: string | null
  assigned_position_id?: string | null
  assigned_position_title?: string | null
}

export interface KnowledgeDocument {
  document_id: string
  filename: string
  document_type: 'position_jd' | 'policy' | 'company' | 'faq' | 'question_bank'
  position_id?: string | null
  position_title?: string | null
  status: 'processing' | 'ready' | 'failed'
  use_context: boolean
  chunk_count?: number | null
  error_msg?: string | null
  created_by_name?: string | null
  created_at: string
  updated_at: string
}

export const recruiterApi = {
  list: (params?: { position_id?: string; status?: string; page?: number; page_size?: number }) =>
    client.get<{ items: RecruiterApplicationItem[]; total: number; page: number; page_size: number }>('/recruiter/applications', { params }),
  detail: (applicationId: string) => client.get(`/recruiter/applications/${applicationId}`),
  decide: (applicationId: string, decision: string, comment?: string) =>
    client.post(`/recruiter/applications/${applicationId}/decision`, { decision, comment }),
  resumeDecision: (applicationId: string, decision: 'passed' | 'rejected', comment?: string) =>
    client.post(`/recruiter/applications/${applicationId}/resume-decision`, { decision, comment }),
  listPositions: () =>
    client.get<{ items: RecruiterPosition[] }>('/recruiter/positions'),
  createPosition: (payload: PositionPayload) =>
    client.post<RecruiterPosition>('/recruiter/positions', payload),
  updatePosition: (positionId: string, payload: Partial<PositionPayload>) =>
    client.patch<RecruiterPosition>(`/recruiter/positions/${positionId}`, payload),
  listExams: () =>
    client.get<{ items: RecruiterExam[] }>('/recruiter/exams'),
  listKnowledgeDocuments: () =>
    client.get<{ items: KnowledgeDocument[] }>('/recruiter/knowledge-documents'),
  uploadKnowledgeDocument: (payload: {
    file: File
    document_type: KnowledgeDocument['document_type']
    position_id?: string
    use_context: boolean
  }) => {
    const form = new FormData()
    form.append('file', payload.file)
    form.append('document_type', payload.document_type)
    if (payload.position_id) form.append('position_id', payload.position_id)
    form.append('use_context', String(payload.use_context))
    return client.post<{ document_id: string; status: string; message: string }>(
      '/recruiter/knowledge-documents',
      form,
    )
  },
}
