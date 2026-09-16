import client from './client'

export interface CandidateTask {
  type: 'resume' | 'exam' | 'interview'
  available: boolean
  completed: boolean
  open_at?: string | null
  deadline?: string | null
}

export interface CandidateApplication {
  application_id: string
  position_id: string
  position_title: string
  notice: string
  tasks: CandidateTask[]
}

export interface PositionItem {
  position_id: string
  code: string
  title: string
  department?: string | null
  jd_text: string
  requirements: Record<string, unknown>
  apply_deadline?: string | null
  application_id?: string | null
}

export const applicationsApi = {
  listPositions: () =>
    client.get<{ items: PositionItem[] }>('/positions'),

  create: (positionId: string) =>
    client.post<CandidateApplication>('/applications', { position_id: positionId }),

  listMine: () =>
    client.get<{ items: CandidateApplication[] }>('/applications'),

  getTasks: (applicationId: string) =>
    client.get<CandidateApplication>(`/applications/${applicationId}/tasks`),
}
