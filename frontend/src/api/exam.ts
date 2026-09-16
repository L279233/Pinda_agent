import client from './client'

export interface ExamSubmitResponse {
  submission_id: string
  status: string
  message: string
}

export interface OnlineExamQuestion {
  question_id: string
  question_no: number
  question_type: string
  content: string
  score: number
}

export interface OnlineExam {
  application_id: string
  position_title: string
  questions: OnlineExamQuestion[]
}

export interface CandidateSubmissionStatus {
  submission_id: string
  status: 'processing' | 'submitted' | 'failed'
  message: string
  retry_allowed?: boolean
}

export interface PendingReviewItem {
  submission_id: string
  student_name: string
  exam_title: string
  submitted_at: string
  pre_review: {
    total_score: number
    full_score: number
    needs_review_count: number
  }
  weak_points: Array<{ tag: string; wrong_count: number; total_count?: number; question_nos?: number[]; suggestion?: string }>
}

export interface ReviewDetail {
  submission_id: string
  student_id: string
  pre_review_summary: {
    total_score: number
    full_score: number
    by_question: Array<{
      question_id: string
      question_no: number
      question_type: string
      full_score: number
      score: number
      content?: string
      student_answer: string
      correct_answer?: string
      ai_feedback: string
      needs_review: boolean
      point_results?: Array<{
        point_score: number
        point_desc: string
        earned: boolean
        missing?: string
      }>
      test_cases_passed?: number
      test_cases_total?: number
      sandbox_skipped?: boolean
      quality_feedback?: string[]
      teacher_comment?: string
      final_score?: number
    }>
  }
  weak_points: Array<{ tag: string; wrong_count: number; total_count?: number; question_nos?: number[]; suggestion?: string }>
  weak_points_summary: string
}

export interface ConfirmRequest {
  action: 'approve' | 'modify'
  modifications: Array<{
    question_id: string
    new_score?: number
    comment?: string
  }>
}

export interface ConfirmResponse {
  submission_id: string
  status: string
  final_score: number
  full_score: number
  score_rate: number
  weak_points: Array<{ tag: string; wrong_count: number; total_count?: number; question_nos?: number[]; suggestion?: string }>
  weak_points_summary: string
}

export interface MySubmissionItem {
  submission_id: string
  position_title: string
  status: 'processing' | 'submitted' | 'failed'
  message: string
  submitted_at: string
}

export const examApi = {
  getOnline: (applicationId: string) =>
    client.get<OnlineExam>(`/exam/online/${applicationId}`),

  submitOnline: (applicationId: string, answers: Array<{ question_id: string; answer: string }>) =>
    client.post<ExamSubmitResponse>('/exam/online/submit', {
      application_id: applicationId,
      answers,
    }),

  listMySubmissions: () =>
    client.get<{ items: MySubmissionItem[] }>('/exam/my-submissions'),

  submit: (applicationId: string, file: File) => {
    const form = new FormData()
    form.append('application_id', applicationId)
    form.append('file', file)
    return client.post<ExamSubmitResponse>('/exam/submit', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },

  getPendingReviews: () =>
    client.get<{ items: PendingReviewItem[]; total: number }>('/exam/pending-reviews'),

  getSubmissionReview: (submissionId: string) =>
    client.get<CandidateSubmissionStatus>(`/exam/my-submissions/${submissionId}`),

  getSubmissionReviewTeacher: (submissionId: string) =>
    client.get<ReviewDetail>(`/exam/submissions/${submissionId}/review`),

  confirmReview: (submissionId: string, data: ConfirmRequest) =>
    client.post<ConfirmResponse>(`/exam/submissions/${submissionId}/confirm`, data),
}
