import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/LoginView.vue'),
      meta: { public: true },
    },
    {
      path: '/',
      component: () => import('@/components/layout/AppLayout.vue'),
      meta: { requiresAuth: true },
      children: [
        {
          path: '',
          redirect: '/dashboard',
        },
        {
          path: 'dashboard',
          name: 'dashboard',
          component: () => import('@/views/DashboardView.vue'),
        },
        {
          path: 'positions',
          name: 'positions',
          component: () => import('@/views/applications/PositionsView.vue'),
          meta: { requiresCandidate: true },
        },
        {
          path: 'applications',
          name: 'applications',
          component: () => import('@/views/applications/MyApplicationsView.vue'),
          meta: { requiresCandidate: true },
        },
        {
          path: 'applications/:applicationId/tasks',
          name: 'application-tasks',
          component: () => import('@/views/applications/ApplicationTasksView.vue'),
          meta: { requiresCandidate: true },
        },
        {
          path: 'notifications',
          name: 'notifications',
          component: () => import('@/views/applications/NotificationsView.vue'),
          meta: { requiresCandidate: true },
        },
        {
          path: 'recruiter/applications',
          name: 'recruiter-applications',
          component: () => import('@/views/recruiter/ApplicationsView.vue'),
          meta: { requiresRecruiter: true },
        },
        {
          path: 'recruiter/applications/:applicationId',
          name: 'recruiter-application-detail',
          component: () => import('@/views/recruiter/ApplicationDetailView.vue'),
          meta: { requiresRecruiter: true },
        },
        {
          path: 'recruiter/positions',
          name: 'recruiter-positions',
          component: () => import('@/views/recruiter/PositionsManageView.vue'),
          meta: { requiresRecruiter: true },
        },
        {
          path: 'recruiter/knowledge',
          name: 'recruiter-knowledge',
          component: () => import('@/views/recruiter/KnowledgeDocumentsView.vue'),
          meta: { requiresRecruiter: true },
        },
        // AI 助手（统一入口）
        {
          path: 'chat',
          name: 'chat',
          component: () => import('@/views/UnifiedChatView.vue'),
        },
        // 旧 QA 地址保留兼容，统一进入带 Agent 识别的智能问答页面
        {
          path: 'qa',
          name: 'qa',
          redirect: to => ({ path: '/chat', query: to.query }),
        },
        // 招聘任务执行页（必须从任务中心携带 application_id 进入）
        {
          path: 'exam',
          name: 'exam',
          redirect: '/applications',
          meta: { requiresCandidate: true },
        },
        {
          path: 'exam/online',
          name: 'exam-online',
          component: () => import('@/views/exam/OnlineExamView.vue'),
          meta: { requiresCandidate: true },
        },
        {
          path: 'exam/:submissionId',
          name: 'exam-result',
          redirect: '/applications',
          meta: { requiresCandidate: true },
        },
        // 简历投递
        {
          path: 'resume',
          name: 'resume',
          component: () => import('@/views/resume/ResumeUploadView.vue'),
          meta: { requiresCandidate: true },
        },
        {
          path: 'resume/:reviewId',
          name: 'resume-report',
          redirect: '/applications',
          meta: { requiresCandidate: true },
        },
        // AI 面试
        {
          path: 'interview',
          name: 'interview',
          component: () => import('@/views/interview/InterviewSetupView.vue'),
          meta: { requiresCandidate: true },
        },
        {
          path: 'interview/:sessionId',
          name: 'interview-chat',
          component: () => import('@/views/interview/InterviewChatView.vue'),
          meta: { requiresCandidate: true },
        },
        // 招聘方页面（兼容原项目的 teacher/admin 角色）
        {
          path: 'teacher/exam-review',
          name: 'teacher-exam-review',
          component: () => import('@/views/teacher/ExamReviewView.vue'),
          meta: { requiresTeacher: true },
        },
        {
          path: 'teacher/knowledge-pending',
          name: 'teacher-knowledge-pending',
          component: () => import('@/views/teacher/KnowledgePendingView.vue'),
          meta: { requiresTeacher: true },
        },
      ],
    },
    {
      path: '/:pathMatch(.*)*',
      redirect: '/dashboard',
    },
  ],
})

router.beforeEach((to, _from, next) => {
  const auth = useAuthStore()

  if (to.meta.public) {
    if (auth.isLoggedIn && to.name === 'login') return next('/dashboard')
    return next()
  }

  if (!auth.isLoggedIn) return next('/login')

  if (to.meta.requiresCandidate && auth.user?.role !== 'student') return next('/dashboard')
  if (to.meta.requiresRecruiter && !auth.isTeacher) return next('/dashboard')
  if (to.meta.requiresTeacher && !auth.isTeacher) return next('/dashboard')

  next()
})

export default router
