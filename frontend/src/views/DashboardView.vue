<template>
  <section class="dashboard">
    <header class="welcome">
      <div>
        <h1>您好，{{ auth.user?.username ?? auth.user?.userId }}</h1>
        <p>从岗位申请开始，按任务开放情况完成招聘流程。</p>
      </div>
      <el-button :icon="Refresh" :loading="loading" circle title="刷新" @click="loadApplications" />
    </header>

    <div class="quick-actions" :class="{ 'quick-actions--single': !auth.isCandidate }">
      <button v-if="auth.isCandidate" class="quick-action" type="button" @click="router.push('/positions')">
        <el-icon><Search /></el-icon>
        <span><strong>查看招聘岗位</strong><small>浏览当前开放职位</small></span>
        <el-icon><ArrowRight /></el-icon>
      </button>
      <button v-if="auth.isCandidate" class="quick-action" type="button" @click="router.push('/applications')">
        <el-icon><Tickets /></el-icon>
        <span><strong>进入我的申请</strong><small>查看待办和截止时间</small></span>
        <el-icon><ArrowRight /></el-icon>
      </button>
      <button class="quick-action" type="button" @click="router.push('/chat')">
        <el-icon><ChatDotRound /></el-icon>
        <span><strong>进入智能问答</strong><small>自动识别需求并匹配专业 Agent</small></span>
        <el-icon><ArrowRight /></el-icon>
      </button>
    </div>

    <div v-if="auth.isCandidate" class="section-heading">
      <h2>近期申请</h2>
      <el-button link type="primary" @click="router.push('/applications')">查看全部</el-button>
    </div>
    <el-table v-if="auth.isCandidate" v-loading="loading" :data="applications.slice(0, 5)" row-key="application_id" empty-text="暂无申请，可先查看招聘岗位">
      <el-table-column prop="position_title" label="申请岗位" min-width="240" />
      <el-table-column label="当前待办" min-width="220">
        <template #default="scope">{{ taskSummary(scope.row) }}</template>
      </el-table-column>
      <el-table-column label="提示" min-width="240">
        <template #default="scope">{{ scope.row.notice }}</template>
      </el-table-column>
      <el-table-column label="操作" width="120">
        <template #default="scope">
          <el-button type="primary" link @click="router.push(`/applications/${scope.row.application_id}/tasks`)">进入任务</el-button>
        </template>
      </el-table-column>
    </el-table>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowRight, ChatDotRound, Refresh, Search, Tickets } from '@element-plus/icons-vue'
import { applicationsApi, type CandidateApplication } from '@/api/applications'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const auth = useAuthStore()
const loading = ref(false)
const applications = ref<CandidateApplication[]>([])

function taskSummary(application: CandidateApplication) {
  const labels = { resume: '简历投递', exam: '在线笔试', interview: 'AI 面试' }
  const pending = application.tasks.filter(task => task.available && !task.completed)
  if (pending.length) return pending.map(task => labels[task.type]).join('、')
  return application.tasks.length ? '暂无待完成任务' : '等待后续安排'
}

async function loadApplications() {
  if (auth.user?.role !== 'student') return
  loading.value = true
  try {
    const { data } = await applicationsApi.listMine()
    applications.value = data.items
  } finally {
    loading.value = false
  }
}

onMounted(loadApplications)
</script>

<style scoped>
.dashboard { max-width: 1180px; margin: 0 auto; }
.welcome { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 22px; }
.welcome h1 { margin: 0 0 6px; font-size: 22px; }
.welcome p { margin: 0; color: #6b7280; font-size: 14px; }
.quick-actions { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin-bottom: 28px; }
.quick-actions--single { grid-template-columns: minmax(280px, 380px); }
.quick-action { display: grid; grid-template-columns: 36px minmax(0, 1fr) 20px; align-items: center; gap: 10px; min-height: 82px; padding: 14px 16px; border: 1px solid #dfe4ea; border-radius: 6px; background: #fff; color: #1f2937; text-align: left; cursor: pointer; }
.quick-action:hover { border-color: #1677ff; background: #f7fbff; }
.quick-action > .el-icon:first-child { color: #1677ff; font-size: 22px; }
.quick-action span { display: flex; flex-direction: column; min-width: 0; }
.quick-action strong { font-size: 15px; font-weight: 600; }
.quick-action small { margin-top: 5px; color: #6b7280; font-size: 12px; }
.section-heading { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; }
.section-heading h2 { margin: 0; font-size: 17px; }
@media (max-width: 800px) { .quick-actions { grid-template-columns: 1fr; } }
</style>
