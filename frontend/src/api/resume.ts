import client from './client'

export interface ResumeUploadResponse {
  review_id: string
  status: string
  message: string
}

export interface DimensionScore {
  key: string
  dimension: string
  score: number
  weight: number
  issues: string[]
  suggestions: string[]
}

export interface IssueItem {
  priority: 'high' | 'medium' | 'low'
  dimension: string
  description: string
  location: string
  suggestion: string
}

export interface ResumeSummary {
  highlights: string[]
  core_improvements: string[]
  overall_comment: string
  fit_assessment: string
  decision?: 'pass' | 'reject' | 'manual_review'
  evidence?: string[]
  risk_flags?: string[]
  manual_review_required?: boolean
}

export interface ReviewDetail {
  review_id: string
  status: 'processing' | 'submitted' | 'done' | 'failed'
  message?: string
  error_msg?: string
  retry_allowed?: boolean
  weighted_score?: number
  dimension_scores?: DimensionScore[]
  issues?: IssueItem[]
  summary?: ResumeSummary
}

export interface ReviewListItem {
  review_id: string
  status: string
  created_at: string
}

export const resumeApi = {
  upload: (file: File, applicationId: string) => {
    const form = new FormData()
    form.append('file', file)
    form.append('application_id', applicationId)
    return client.post<ResumeUploadResponse>('/resume/upload', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },

  getReview: (reviewId: string) =>
    client.get<ReviewDetail>(`/resume/reviews/${reviewId}`),

  listReviews: () =>
    client.get<{ items: ReviewListItem[]; total: number }>('/resume/reviews'),

}
