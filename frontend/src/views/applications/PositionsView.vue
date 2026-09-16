<template>
  <section class="page">
    <header class="page-header">
      <div>
        <h1>招聘岗位</h1>
        <p>选择岗位并创建招聘申请</p>
      </div>
      <el-button :icon="Refresh" :loading="loading" circle title="刷新" @click="loadPositions" />
    </header>

    <el-table v-loading="loading" :data="positions" row-key="position_id" empty-text="暂无开放岗位">
      <el-table-column type="expand">
        <template #default="scope">
          <div class="jd-content">
            <h3>岗位说明</h3>
            <p>{{ scope.row.jd_text }}</p>
          </div>
        </template>
      </el-table-column>
      <el-table-column prop="title" label="岗位" min-width="220" />
      <el-table-column prop="department" label="部门" min-width="140">
        <template #default="scope">{{ scope.row.department || '未设置' }}</template>
      </el-table-column>
      <el-table-column prop="code" label="岗位编号" min-width="130" />
      <el-table-column label="申请截止" min-width="180">
        <template #default="scope">{{ formatTime(scope.row.apply_deadline) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="150" fixed="right">
        <template #default="scope">
          <el-button
            v-if="scope.row.application_id"
            type="primary"
            link
            @click="openTasks(scope.row.application_id)"
          >查看任务</el-button>
          <el-button
            v-else
            type="primary"
            :loading="applyingId === scope.row.position_id"
            @click="apply(scope.row.position_id)"
          >申请岗位</el-button>
        </template>
      </el-table-column>
    </el-table>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { applicationsApi, type PositionItem } from '@/api/applications'

const router = useRouter()
const positions = ref<PositionItem[]>([])
const loading = ref(false)
const applyingId = ref('')

function formatTime(value?: string | null) {
  return value ? new Date(value).toLocaleString() : '长期有效'
}

function openTasks(applicationId: string) {
  router.push(`/applications/${applicationId}/tasks`)
}

async function loadPositions() {
  loading.value = true
  try {
    const { data } = await applicationsApi.listPositions()
    positions.value = data.items
  } finally {
    loading.value = false
  }
}

async function apply(positionId: string) {
  applyingId.value = positionId
  try {
    const { data } = await applicationsApi.create(positionId)
    ElMessage.success('申请已创建，请提交简历')
    openTasks(data.application_id)
  } finally {
    applyingId.value = ''
  }
}

onMounted(loadPositions)
</script>

<style scoped>
.page { max-width: 1180px; margin: 0 auto; }
.page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px; }
.page-header h1 { margin: 0 0 6px; font-size: 22px; }
.page-header p { margin: 0; color: #6b7280; font-size: 14px; }
.jd-content { padding: 4px 48px 18px; max-width: 860px; }
.jd-content h3 { margin: 0 0 8px; font-size: 15px; }
.jd-content p { margin: 0; color: #4b5563; line-height: 1.75; white-space: pre-wrap; }
</style>
