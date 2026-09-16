<template>
  <section class="page">
    <header class="page-header">
      <div>
        <h1>我的申请</h1>
        <p>查看已开放任务和提交状态</p>
      </div>
      <el-button type="primary" :icon="Plus" @click="router.push('/positions')">查看岗位</el-button>
    </header>

    <el-table v-loading="loading" :data="applications" row-key="application_id" empty-text="暂无招聘申请">
      <el-table-column prop="position_title" label="申请岗位" min-width="240" />
      <el-table-column label="简历" width="120">
        <template #default="scope"><TaskStatus :task="findTask(scope.row, 'resume')" /></template>
      </el-table-column>
      <el-table-column label="笔试" width="120">
        <template #default="scope"><TaskStatus :task="findTask(scope.row, 'exam')" /></template>
      </el-table-column>
      <el-table-column label="AI 面试" width="120">
        <template #default="scope"><TaskStatus :task="findTask(scope.row, 'interview')" /></template>
      </el-table-column>
      <el-table-column label="提示" min-width="220">
        <template #default="scope">{{ scope.row.notice }}</template>
      </el-table-column>
      <el-table-column label="操作" width="120" fixed="right">
        <template #default="scope">
          <el-button type="primary" link @click="router.push(`/applications/${scope.row.application_id}/tasks`)">
            进入任务
          </el-button>
        </template>
      </el-table-column>
    </el-table>
  </section>
</template>

<script setup lang="ts">
import { defineComponent, h, onMounted, ref, type PropType } from 'vue'
import { useRouter } from 'vue-router'
import { ElTag } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import { applicationsApi, type CandidateApplication, type CandidateTask } from '@/api/applications'

const router = useRouter()
const applications = ref<CandidateApplication[]>([])
const loading = ref(false)

const TaskStatus = defineComponent({
  props: { task: Object as PropType<CandidateTask | undefined> },
  setup(props) {
    return () => {
      if (!props.task) return h(ElTag, { type: 'info', effect: 'plain' }, () => '未开放')
      if (props.task.completed) return h(ElTag, { type: 'success', effect: 'plain' }, () => '已完成')
      if (props.task.available) return h(ElTag, { type: 'warning', effect: 'plain' }, () => '待完成')
      return h(ElTag, { type: 'info', effect: 'plain' }, () => '不可操作')
    }
  },
})

function findTask(application: CandidateApplication, type: CandidateTask['type']) {
  return application.tasks.find(task => task.type === type)
}

async function loadApplications() {
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
.page { max-width: 1180px; margin: 0 auto; }
.page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px; }
.page-header h1 { margin: 0 0 6px; font-size: 22px; }
.page-header p { margin: 0; color: #6b7280; font-size: 14px; }
</style>
