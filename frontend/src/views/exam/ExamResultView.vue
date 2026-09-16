<template>
  <div class="submission-status">
    <el-card v-loading="loading">
      <el-result
        :icon="resultIcon"
        :title="title"
        :sub-title="submission?.message || '正在查询提交状态'"
      >
        <template #extra>
          <el-button type="primary" @click="router.push('/dashboard')">返回任务中心</el-button>
        </template>
      </el-result>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { examApi, type CandidateSubmissionStatus } from '@/api/exam'

const route = useRoute()
const router = useRouter()
const loading = ref(true)
const submission = ref<CandidateSubmissionStatus | null>(null)

const resultIcon = computed(() => submission.value?.status === 'failed' ? 'error' : 'success')
const title = computed(() => {
  if (submission.value?.status === 'failed') return '笔试处理失败'
  if (submission.value?.status === 'processing') return '笔试已提交'
  return '笔试已完成'
})

onMounted(async () => {
  try {
    const { data } = await examApi.getSubmissionReview(String(route.params.submissionId))
    submission.value = data
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.submission-status { max-width: 760px; }
</style>
