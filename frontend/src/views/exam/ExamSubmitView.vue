<template>
  <div class="exam-submit">
    <el-card>
      <template #header><span>提交招聘笔试</span></template>

      <el-form label-width="100px" style="max-width: 560px">
        <el-form-item label="答题文件">
          <el-upload
            ref="uploadRef"
            :auto-upload="false"
            :limit="1"
            accept=".docx"
            :on-change="handleFileChange"
            :on-remove="clearFile"
          >
            <el-button :icon="Upload">选择文件</el-button>
            <template #tip>
              <div class="upload-tip">仅支持 .docx 格式，最大 20MB</div>
            </template>
          </el-upload>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="loading" :disabled="!file" @click="handleSubmit">
            提交笔试
          </el-button>
        </el-form-item>
      </el-form>

      <el-result
        v-if="submitted"
        icon="success"
        title="笔试已提交"
        sub-title="请等待 HR 通知"
      />
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, type UploadFile, type UploadInstance } from 'element-plus'
import { Upload } from '@element-plus/icons-vue'
import { examApi } from '@/api/exam'

const route = useRoute()
const uploadRef = ref<UploadInstance>()
const loading = ref(false)
const submitted = ref(false)
const file = ref<File | null>(null)

function handleFileChange(uploadFile: UploadFile) {
  if (uploadFile.raw && uploadFile.raw.size > 20 * 1024 * 1024) {
    ElMessage.error('文件超过 20MB 限制')
    uploadRef.value?.clearFiles()
    file.value = null
    return
  }
  file.value = uploadFile.raw ?? null
}

function clearFile() {
  file.value = null
}

async function handleSubmit() {
  if (!file.value) return
  const applicationId = String(route.query.application_id || '')
  if (!applicationId) {
    ElMessage.error('缺少招聘申请编号，请从候选人任务页进入')
    return
  }
  loading.value = true
  try {
    await examApi.submit(applicationId, file.value)
    submitted.value = true
    file.value = null
    uploadRef.value?.clearFiles()
    ElMessage.success('笔试已提交，请等待 HR 通知')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.exam-submit { max-width: 800px; }
.upload-tip { font-size: 12px; color: #8c8c8c; margin-top: 4px; }
</style>
