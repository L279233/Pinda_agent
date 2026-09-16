<template>
  <section class="page">
    <header class="page-header">
      <div>
        <h1>岗位管理</h1>
        <p>维护岗位说明、筛选参数和候选人笔试</p>
      </div>
      <div class="header-actions">
        <el-button :icon="Refresh" :loading="loading" circle title="刷新" @click="load" />
        <el-button type="primary" :icon="Plus" @click="openCreate">新增岗位</el-button>
      </div>
    </header>

    <el-alert
      v-if="!hasAvailableExam"
      title="当前没有未绑定的试卷，新岗位可以先保存为草稿，配置试卷后再开放。"
      type="info"
      :closable="false"
      show-icon
      class="notice"
    />

    <el-table v-loading="loading" :data="positions" row-key="position_id" empty-text="暂无岗位">
      <el-table-column type="expand">
        <template #default="scope">
          <div class="position-detail">
            <div><strong>岗位说明</strong><p>{{ scope.row.jd_text }}</p></div>
            <div><strong>技能要求</strong><p>{{ skillSummary(scope.row.requirements) }}</p></div>
          </div>
        </template>
      </el-table-column>
      <el-table-column prop="title" label="岗位" min-width="190" />
      <el-table-column prop="code" label="编号" min-width="120" />
      <el-table-column prop="department" label="部门" min-width="130">
        <template #default="scope">{{ scope.row.department || '未设置' }}</template>
      </el-table-column>
      <el-table-column label="状态" width="100">
        <template #default="scope">
          <el-tag :type="statusType(scope.row.status)">{{ statusLabel(scope.row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="绑定试卷" min-width="180">
        <template #default="scope">{{ scope.row.exam_title || '未绑定' }}</template>
      </el-table-column>
      <el-table-column prop="application_count" label="申请数" width="90" />
      <el-table-column label="截止时间" min-width="170">
        <template #default="scope">{{ formatTime(scope.row.apply_deadline) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="90" fixed="right">
        <template #default="scope">
          <el-button type="primary" link @click="openEdit(scope.row)">编辑</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialogVisible" :title="editingId ? '编辑岗位' : '新增岗位'" width="680px" destroy-on-close>
      <el-form label-position="top" class="position-form">
        <div class="form-grid">
          <el-form-item label="岗位编号" required>
            <el-input v-model="form.code" maxlength="64" placeholder="例如 LLM-002" />
          </el-form-item>
          <el-form-item label="岗位名称" required>
            <el-input v-model="form.title" maxlength="128" placeholder="例如大模型应用开发工程师" />
          </el-form-item>
          <el-form-item label="所属部门">
            <el-input v-model="form.department" maxlength="128" placeholder="例如研发中心" />
          </el-form-item>
          <el-form-item label="岗位状态">
            <el-select v-model="form.status" style="width: 100%">
              <el-option label="草稿" value="draft" />
              <el-option label="开放招聘" value="open" :disabled="!form.exam_id" />
              <el-option label="已关闭" value="closed" />
            </el-select>
          </el-form-item>
        </div>
        <el-form-item label="岗位说明" required>
          <el-input v-model="form.jd_text" type="textarea" :rows="5" placeholder="填写岗位职责和工作内容" />
        </el-form-item>
        <el-form-item label="核心技能">
          <el-input v-model="form.skills" type="textarea" :rows="3" placeholder="每行填写一项，例如 Python、RAG、LangGraph" />
        </el-form-item>
        <div class="form-grid form-grid--three">
          <el-form-item label="简历通过分">
            <el-input-number v-model="form.resume_pass_score" :min="0" :max="100" controls-position="right" />
          </el-form-item>
          <el-form-item label="笔试时限（小时）">
            <el-input-number v-model="form.exam_window_hours" :min="1" controls-position="right" />
          </el-form-item>
          <el-form-item label="面试时限（小时）">
            <el-input-number v-model="form.interview_window_hours" :min="1" controls-position="right" />
          </el-form-item>
        </div>
        <div class="form-grid">
          <el-form-item label="岗位试卷">
            <el-select v-model="form.exam_id" clearable placeholder="暂不绑定，保存为草稿" style="width: 100%" @change="onExamChange">
              <el-option
                v-for="exam in exams"
                :key="exam.exam_id"
                :label="examOptionLabel(exam)"
                :value="exam.exam_id"
                :disabled="isExamUnavailable(exam)"
              />
            </el-select>
          </el-form-item>
          <el-form-item label="申请截止时间">
            <el-date-picker
              v-model="form.apply_deadline"
              type="datetime"
              value-format="YYYY-MM-DDTHH:mm:ss"
              placeholder="不设置表示长期有效"
              style="width: 100%"
            />
          </el-form-item>
        </div>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存岗位</el-button>
      </template>
    </el-dialog>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Plus, Refresh } from '@element-plus/icons-vue'
import {
  recruiterApi,
  type PositionPayload,
  type RecruiterExam,
  type RecruiterPosition,
} from '@/api/recruiter'

interface PositionForm extends Omit<PositionPayload, 'requirements'> {
  skills: string
  baseRequirements: Record<string, unknown>
}

function emptyForm(): PositionForm {
  return {
    code: '', title: '', department: '', jd_text: '', skills: '', baseRequirements: {},
    resume_pass_score: 70, exam_window_hours: 72, interview_window_hours: 72,
    exam_id: null, status: 'draft', apply_deadline: null,
  }
}

const positions = ref<RecruiterPosition[]>([])
const exams = ref<RecruiterExam[]>([])
const loading = ref(false)
const saving = ref(false)
const dialogVisible = ref(false)
const editingId = ref('')
const form = reactive<PositionForm>(emptyForm())
const hasAvailableExam = computed(() => exams.value.some(exam => !exam.assigned_position_id))

function resetForm(value: PositionForm) {
  Object.assign(form, emptyForm(), value)
}

function skillsOf(requirements: Record<string, unknown>) {
  const skills = requirements.skills
  return Array.isArray(skills) ? skills.map(String) : []
}

function skillSummary(requirements: Record<string, unknown>) {
  return skillsOf(requirements).join('、') || '未设置'
}

function formatTime(value?: string | null) {
  return value ? new Date(value).toLocaleString() : '长期有效'
}

function statusLabel(value: RecruiterPosition['status']) {
  return { draft: '草稿', open: '招聘中', closed: '已关闭' }[value]
}

function statusType(value: RecruiterPosition['status']) {
  return value === 'open' ? 'success' : value === 'closed' ? 'info' : 'warning'
}

function examOptionLabel(exam: RecruiterExam) {
  return exam.assigned_position_id ? `${exam.title}（已绑定 ${exam.assigned_position_title}）` : exam.title
}

function isExamUnavailable(exam: RecruiterExam) {
  return Boolean(exam.assigned_position_id && exam.assigned_position_id !== editingId.value)
}

function onExamChange(value: string) {
  if (!value && form.status === 'open') form.status = 'draft'
}

function openCreate() {
  editingId.value = ''
  resetForm(emptyForm())
  dialogVisible.value = true
}

function openEdit(position: RecruiterPosition) {
  editingId.value = position.position_id
  resetForm({
    code: position.code,
    title: position.title,
    department: position.department || '',
    jd_text: position.jd_text,
    skills: skillsOf(position.requirements).join('\n'),
    baseRequirements: { ...position.requirements },
    resume_pass_score: position.resume_pass_score,
    exam_window_hours: position.exam_window_hours,
    interview_window_hours: position.interview_window_hours,
    exam_id: position.exam_id || null,
    status: position.status,
    apply_deadline: position.apply_deadline ? position.apply_deadline.slice(0, 19) : null,
  })
  dialogVisible.value = true
}

async function load() {
  loading.value = true
  try {
    const [positionResult, examResult] = await Promise.all([
      recruiterApi.listPositions(), recruiterApi.listExams(),
    ])
    positions.value = positionResult.data.items
    exams.value = examResult.data.items
  } finally {
    loading.value = false
  }
}

async function save() {
  if (!form.code.trim() || !form.title.trim() || !form.jd_text.trim()) {
    ElMessage.warning('请填写岗位编号、名称和岗位说明')
    return
  }
  if (form.status === 'open' && !form.exam_id) {
    ElMessage.warning('开放岗位前必须绑定试卷')
    return
  }
  const skills = form.skills.split(/\r?\n|、|,/).map(item => item.trim()).filter(Boolean)
  const payload: PositionPayload = {
    code: form.code.trim(),
    title: form.title.trim(),
    department: form.department?.trim() || null,
    jd_text: form.jd_text.trim(),
    requirements: { ...form.baseRequirements, skills },
    resume_pass_score: form.resume_pass_score,
    exam_window_hours: form.exam_window_hours,
    interview_window_hours: form.interview_window_hours,
    exam_id: form.exam_id || null,
    status: form.status,
    apply_deadline: form.apply_deadline || null,
  }
  saving.value = true
  try {
    if (editingId.value) await recruiterApi.updatePosition(editingId.value, payload)
    else await recruiterApi.createPosition(payload)
    ElMessage.success(editingId.value ? '岗位已更新' : '岗位已创建')
    dialogVisible.value = false
    await load()
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.page { max-width: 1250px; margin: 0 auto; }
.page-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; margin-bottom: 18px; }
.page-header h1 { margin: 0 0 6px; font-size: 22px; }
.page-header p { margin: 0; color: #6b7280; font-size: 14px; }
.header-actions { display: flex; gap: 10px; }
.notice { margin-bottom: 16px; }
.position-detail { padding: 8px 48px 18px; display: grid; grid-template-columns: 2fr 1fr; gap: 32px; color: #374151; }
.position-detail strong { font-size: 14px; }
.position-detail p { margin: 7px 0 0; line-height: 1.7; white-space: pre-wrap; }
.form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 18px; }
.form-grid--three { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.form-grid--three :deep(.el-input-number) { width: 100%; }
@media (max-width: 760px) {
  .page-header { align-items: stretch; flex-direction: column; }
  .header-actions { justify-content: flex-end; }
  .position-detail, .form-grid, .form-grid--three { grid-template-columns: 1fr; }
  :deep(.el-dialog) { width: calc(100vw - 24px) !important; }
}
</style>
