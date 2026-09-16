<template>
  <section class="page">
    <header class="page-header">
      <div>
        <el-button :icon="ArrowLeft" link @click="router.push('/applications')">返回我的申请</el-button>
        <h1>{{ application?.position_title || '申请任务' }}</h1>
        <p>{{ application?.notice || '正在加载任务信息' }}</p>
      </div>
      <el-button :icon="Refresh" :loading="loading" circle title="刷新" @click="loadTasks" />
    </header>

    <div v-loading="loading" class="task-list">
      <div v-for="task in orderedTasks" :key="task.type" class="task-row">
        <div class="task-icon"><el-icon><component :is="taskMeta[task.type].icon" /></el-icon></div>
        <div class="task-main">
          <div class="task-title-line">
            <h2>{{ taskMeta[task.type].label }}</h2>
            <el-tag :type="statusMeta(task).type" effect="plain">{{ statusMeta(task).label }}</el-tag>
          </div>
          <p>{{ taskMeta[task.type].description }}</p>
          <span v-if="task.deadline" class="deadline">截止时间：{{ formatTime(task.deadline) }}</span>
        </div>
        <el-button
          type="primary"
          :disabled="!task.available || task.completed"
          @click="openTask(task)"
        >{{ task.completed ? '已完成' : '进入任务' }}</el-button>
      </div>

      <el-empty v-if="!loading && orderedTasks.length === 0" description="暂无可显示任务" />
    </div>

    <div v-if="application" class="support-row">
      <span>对岗位或招聘流程有疑问？</span>
      <el-button type="primary" link @click="openQa">进入智能问答</el-button>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, ChatDotRound, Document, Postcard, Refresh } from '@element-plus/icons-vue'
import { applicationsApi, type CandidateApplication, type CandidateTask } from '@/api/applications'

const route = useRoute()
const router = useRouter()
const loading = ref(false)
const application = ref<CandidateApplication | null>(null)

const taskMeta = {
  resume: { label: '简历投递', description: '上传 PDF 简历，提交后等待 HR 通知。', icon: Postcard },
  exam: { label: '在线笔试', description: '在开放时间内在线完成题目并提交。', icon: Document },
  interview: { label: 'AI 面试', description: '在开放时间内完成在线初面。', icon: ChatDotRound },
}

const orderedTasks = computed(() => {
  const order = { resume: 0, exam: 1, interview: 2 }
  return [...(application.value?.tasks || [])].sort((a, b) => order[a.type] - order[b.type])
})

function statusMeta(task: CandidateTask) {
  if (task.completed) return { label: '已完成', type: 'success' as const }
  if (task.available) return { label: '待完成', type: 'warning' as const }
  return { label: '当前不可操作', type: 'info' as const }
}

function formatTime(value: string) {
  return new Date(value).toLocaleString()
}

function openTask(task: CandidateTask) {
  if (!application.value || !task.available || task.completed) return
  const paths = { resume: '/resume', exam: '/exam/online', interview: '/interview' }
  router.push({ path: paths[task.type], query: { application_id: application.value.application_id } })
}

function openQa() {
  if (!application.value) return
  router.push({ path: '/chat', query: {
    application_id: application.value.application_id,
    position_id: application.value.position_id,
  } })
}

async function loadTasks() {
  loading.value = true
  try {
    const { data } = await applicationsApi.getTasks(String(route.params.applicationId))
    application.value = data
  } finally {
    loading.value = false
  }
}

onMounted(loadTasks)
</script>

<style scoped>
.page { max-width: 960px; margin: 0 auto; }
.page-header { display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: 18px; }
.page-header h1 { margin: 10px 0 6px; font-size: 22px; }
.page-header p { margin: 0; color: #6b7280; font-size: 14px; }
.task-list { min-height: 180px; border-top: 1px solid #e5e7eb; background: #fff; }
.task-row { display: grid; grid-template-columns: 44px minmax(0, 1fr) auto; gap: 16px; align-items: center; padding: 20px; border-bottom: 1px solid #e5e7eb; }
.task-icon { display: grid; place-items: center; width: 40px; height: 40px; color: #1677ff; background: #eef6ff; border-radius: 6px; font-size: 20px; }
.task-main { min-width: 0; }
.task-title-line { display: flex; align-items: center; gap: 10px; }
.task-title-line h2 { margin: 0; font-size: 16px; }
.task-main p { margin: 6px 0; color: #4b5563; font-size: 13px; }
.deadline { color: #6b7280; font-size: 12px; }
.support-row { display: flex; align-items: center; justify-content: flex-end; gap: 6px; margin-top: 14px; color: #6b7280; font-size: 13px; }
@media (max-width: 640px) {
  .task-row { grid-template-columns: 40px minmax(0, 1fr); }
  .task-row > .el-button { grid-column: 1 / -1; width: 100%; }
}
</style>
