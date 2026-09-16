<template>
  <section class="page">
    <header class="page-header">
      <div>
        <h1>知识库文档</h1>
        <p>上传岗位 JD、招聘制度、公司介绍、常见问题和题库资料</p>
      </div>
      <div class="header-actions">
        <el-button :icon="Refresh" :loading="loading" circle title="刷新" @click="loadDocuments" />
        <el-button type="primary" :icon="Upload" @click="openUpload">上传文档</el-button>
      </div>
    </header>

    <el-table v-loading="loading" :data="documents" row-key="document_id" empty-text="暂无知识库文档">
      <el-table-column prop="filename" label="文件" min-width="220" show-overflow-tooltip />
      <el-table-column label="资料类型" min-width="130">
        <template #default="scope">{{ typeLabel(scope.row.document_type) }}</template>
      </el-table-column>
      <el-table-column label="所属岗位" min-width="180">
        <template #default="scope">{{ scope.row.position_title || '公共知识库' }}</template>
      </el-table-column>
      <el-table-column label="处理状态" width="110">
        <template #default="scope">
          <el-tag :type="statusType(scope.row.status)">{{ statusLabel(scope.row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="分块数" width="90">
        <template #default="scope">{{ scope.row.chunk_count ?? '-' }}</template>
      </el-table-column>
      <el-table-column label="上下文增强" width="110">
        <template #default="scope">{{ scope.row.use_context ? '已启用' : '未启用' }}</template>
      </el-table-column>
      <el-table-column label="上传时间" min-width="170">
        <template #default="scope">{{ formatTime(scope.row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="说明" min-width="180">
        <template #default="scope">
          <span v-if="scope.row.status === 'processing'" class="muted">正在切分并写入 Milvus</span>
          <span v-else-if="scope.row.status === 'failed'" class="error" :title="scope.row.error_msg || ''">
            {{ scope.row.error_msg || '处理失败' }}
          </span>
          <span v-else class="muted">可供招聘答疑检索</span>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialogVisible" title="上传知识库文档" width="560px" destroy-on-close @closed="resetUpload">
      <el-form label-position="top">
        <div class="form-grid">
          <el-form-item label="资料类型" required>
            <el-select v-model="uploadForm.document_type" style="width: 100%" @change="onTypeChange">
              <el-option v-for="item in documentTypes" :key="item.value" :label="item.label" :value="item.value" />
            </el-select>
          </el-form-item>
          <el-form-item :label="uploadForm.document_type === 'position_jd' ? '所属岗位（必选）' : '所属岗位（可选）'">
            <el-select v-model="uploadForm.position_id" clearable placeholder="不选则进入公共知识库" style="width: 100%">
              <el-option v-for="position in positions" :key="position.position_id" :label="position.title" :value="position.position_id" />
            </el-select>
          </el-form-item>
        </div>
        <el-form-item label="文档文件" required>
          <el-upload
            ref="uploadRef"
            drag
            action="#"
            accept=".pdf,.md,.markdown"
            :auto-upload="false"
            :limit="1"
            :on-change="onFileChange"
            :on-remove="onFileRemove"
          >
            <el-icon class="upload-icon"><UploadFilled /></el-icon>
            <div>将文件拖到此处，或点击选择</div>
            <template #tip><div class="upload-tip">支持 PDF、Markdown，单个文件不超过 20MB</div></template>
          </el-upload>
        </el-form-item>
        <el-form-item>
          <div class="context-row">
            <div><strong>Contextual RAG</strong><span>调用大模型生成分块上下文，处理时间更长</span></div>
            <el-switch v-model="uploadForm.use_context" />
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="uploading" @click="submitUpload">开始上传</el-button>
      </template>
    </el-dialog>
  </section>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage, type UploadFile, type UploadInstance } from 'element-plus'
import { Refresh, Upload, UploadFilled } from '@element-plus/icons-vue'
import {
  recruiterApi,
  type KnowledgeDocument,
  type RecruiterPosition,
} from '@/api/recruiter'

type DocumentType = KnowledgeDocument['document_type']

const documentTypes: { value: DocumentType; label: string }[] = [
  { value: 'position_jd', label: '岗位 JD' },
  { value: 'policy', label: '招聘制度' },
  { value: 'company', label: '公司介绍' },
  { value: 'faq', label: '常见问题' },
  { value: 'question_bank', label: '招聘题库' },
]

const documents = ref<KnowledgeDocument[]>([])
const positions = ref<RecruiterPosition[]>([])
const loading = ref(false)
const uploading = ref(false)
const dialogVisible = ref(false)
const selectedFile = ref<File | null>(null)
const uploadRef = ref<UploadInstance>()
const uploadForm = reactive<{ document_type: DocumentType; position_id: string; use_context: boolean }>({
  document_type: 'policy', position_id: '', use_context: false,
})
let pollTimer: number | undefined

function typeLabel(type: DocumentType) {
  return documentTypes.find(item => item.value === type)?.label || type
}

function statusLabel(value: KnowledgeDocument['status']) {
  return { processing: '处理中', ready: '已完成', failed: '失败' }[value]
}

function statusType(value: KnowledgeDocument['status']) {
  return value === 'ready' ? 'success' : value === 'failed' ? 'danger' : 'warning'
}

function formatTime(value: string) {
  return new Date(value).toLocaleString()
}

function openUpload() {
  dialogVisible.value = true
}

function resetUpload() {
  selectedFile.value = null
  uploadForm.document_type = 'policy'
  uploadForm.position_id = ''
  uploadForm.use_context = false
  uploadRef.value?.clearFiles()
}

function onTypeChange() {
  if (uploadForm.document_type !== 'position_jd') uploadForm.position_id = ''
}

function onFileChange(file: UploadFile) {
  selectedFile.value = file.raw || null
}

function onFileRemove() {
  selectedFile.value = null
}

async function loadDocuments(showLoading = true) {
  if (showLoading) loading.value = true
  try {
    const { data } = await recruiterApi.listKnowledgeDocuments()
    documents.value = data.items
  } finally {
    if (showLoading) loading.value = false
  }
}

async function loadPositions() {
  const { data } = await recruiterApi.listPositions()
  positions.value = data.items
}

async function submitUpload() {
  if (!selectedFile.value) {
    ElMessage.warning('请选择要上传的文档')
    return
  }
  if (uploadForm.document_type === 'position_jd' && !uploadForm.position_id) {
    ElMessage.warning('岗位 JD 必须选择所属岗位')
    return
  }
  uploading.value = true
  try {
    await recruiterApi.uploadKnowledgeDocument({
      file: selectedFile.value,
      document_type: uploadForm.document_type,
      position_id: uploadForm.position_id || undefined,
      use_context: uploadForm.use_context,
    })
    ElMessage.success('文档已接收，后台正在构建知识库')
    dialogVisible.value = false
    await loadDocuments()
  } finally {
    uploading.value = false
  }
}

onMounted(async () => {
  await Promise.all([loadDocuments(), loadPositions()])
  pollTimer = window.setInterval(() => {
    if (documents.value.some(item => item.status === 'processing')) loadDocuments(false)
  }, 5000)
})

onUnmounted(() => {
  if (pollTimer) window.clearInterval(pollTimer)
})
</script>

<style scoped>
.page { max-width: 1250px; margin: 0 auto; }
.page-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; margin-bottom: 18px; }
.page-header h1 { margin: 0 0 6px; font-size: 22px; }
.page-header p { margin: 0; color: #6b7280; font-size: 14px; }
.header-actions { display: flex; gap: 10px; }
.form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 16px; }
.upload-icon { font-size: 34px; color: #1677ff; margin-bottom: 8px; }
.upload-tip { color: #6b7280; font-size: 12px; }
.context-row { width: 100%; display: flex; justify-content: space-between; align-items: center; gap: 16px; }
.context-row div { display: flex; flex-direction: column; gap: 4px; }
.context-row strong { font-size: 14px; }
.context-row span, .muted { color: #6b7280; font-size: 13px; }
.error { color: #dc2626; display: inline-block; max-width: 240px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; vertical-align: bottom; }
:deep(.el-upload), :deep(.el-upload-dragger) { width: 100%; }
@media (max-width: 680px) {
  .page-header { align-items: stretch; flex-direction: column; }
  .header-actions { justify-content: flex-end; }
  .form-grid { grid-template-columns: 1fr; }
  :deep(.el-dialog) { width: calc(100vw - 24px) !important; }
}
</style>
