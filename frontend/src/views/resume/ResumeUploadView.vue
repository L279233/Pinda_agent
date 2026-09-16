<template>
  <div class="resume-upload">
    <el-card>
      <template #header><span>提交简历</span></template>

      <div class="file-picker">
        <el-button type="default" :icon="UploadFilled" @click="triggerFilePicker">
          点击选择 PDF 文件
        </el-button>
        <input
          ref="fileInputRef"
          type="file"
          accept=".pdf"
          style="display: none"
          @change="handleFileChange"
        />
        <span v-if="selectedFile" class="file-name">{{ selectedFile.name }}</span>
        <el-button v-if="selectedFile" text type="danger" size="small" @click="clearFile">
          移除
        </el-button>
      </div>
      <div class="upload-tip">仅支持 PDF 格式，最大 20MB</div>

      <el-button
        type="primary"
        :loading="loading"
        :disabled="!selectedFile"
        style="margin-top: 16px"
        @click="handleUpload"
      >
        提交简历
      </el-button>
      <el-result
        v-if="submitted"
        icon="success"
        title="简历已提交"
        sub-title="请等待 HR 通知"
      />
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { UploadFilled } from '@element-plus/icons-vue'
import { resumeApi } from '@/api/resume'

const route = useRoute()
const loading = ref(false)
const submitted = ref(false)
const selectedFile = ref<File | null>(null)
const fileInputRef = ref<HTMLInputElement | null>(null)

function triggerFilePicker() {
  fileInputRef.value?.click()
}

function handleFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0] ?? null
  if (file && file.size > 20 * 1024 * 1024) {
    ElMessage.error('文件超过 20MB 限制')
    input.value = ''
    return
  }
  selectedFile.value = file
}

function clearFile() {
  selectedFile.value = null
  if (fileInputRef.value) fileInputRef.value.value = ''
}

async function handleUpload() {
  if (!selectedFile.value) return
  const applicationId = String(route.query.application_id || '')
  if (!applicationId) {
    ElMessage.error('缺少招聘申请编号，请从申请任务页进入')
    return
  }
  loading.value = true
  try {
    await resumeApi.upload(selectedFile.value, applicationId)
    submitted.value = true
    clearFile()
    ElMessage.success('简历已提交，请等待 HR 通知')
  } finally {
    loading.value = false
  }
}

</script>

<style scoped>
.resume-upload { max-width: 800px; }
.file-picker { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.file-name { font-size: 13px; color: #595959; max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.upload-tip { font-size: 12px; color: #8c8c8c; margin-top: 8px; }
</style>
