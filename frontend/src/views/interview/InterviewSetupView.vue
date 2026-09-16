<template>
  <div class="interview-setup">
    <el-card>
      <template #header><span>AI 初面</span></template>
      <el-button type="primary" :loading="loading" @click="startInterview">
        开始面试
      </el-button>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { interviewApi } from '@/api/interview'

const route = useRoute()
const router = useRouter()
const loading = ref(false)

async function startInterview() {
  const applicationId = String(route.query.application_id || '')
  if (!applicationId) {
    ElMessage.error('缺少招聘申请编号，请从候选人任务页进入')
    return
  }
  loading.value = true
  try {
    const { data } = await interviewApi.startSession({ application_id: applicationId })
    router.push({ path: `/interview/${data.session_id}`, state: { openingMessage: data.message } })
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '面试启动失败，请稍后重试')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.interview-setup { max-width: 560px; }
</style>
