<template>
  <section class="page">
    <header class="page-header">
      <div>
        <el-button :icon="ArrowLeft" link @click="router.push('/applications')">返回我的申请</el-button>
        <h1>{{ exam?.position_title || '在线笔试' }}</h1>
        <p>请完成以下题目并提交，结果将由招聘方审核</p>
      </div>
      <el-button :icon="Refresh" :loading="loading" circle title="重新加载" @click="load" />
    </header>

    <div v-loading="loading" class="exam-body" v-if="exam && !submitted">
      <div v-for="question in exam.questions" :key="question.question_id" class="question-row">
        <div class="question-title"><strong>{{ question.question_no }}. {{ question.content }}</strong><span>{{ question.score }} 分</span></div>
        <el-radio-group v-if="['single_choice', 'judge'].includes(question.question_type)" v-model="answers[question.question_id]">
          <el-radio v-for="option in optionsFor(question)" :key="option" :label="option">{{ option }}</el-radio>
        </el-radio-group>
        <el-checkbox-group v-else-if="question.question_type === 'multi_choice'" v-model="multiAnswers[question.question_id]">
          <el-checkbox v-for="option in ['A', 'B', 'C', 'D']" :key="option" :label="option">{{ option }}</el-checkbox>
        </el-checkbox-group>
        <el-input v-else v-model="answers[question.question_id]" type="textarea" :rows="5" placeholder="请输入答案" resize="vertical" />
      </div>
      <div class="submit-bar"><span>提交后无法修改答案，请确认填写完整</span><el-button type="primary" :loading="submitting" @click="submit">提交笔试</el-button></div>
    </div>
    <el-result v-else-if="submitted" icon="success" title="笔试已提交" sub-title="请等待 HR 通知">
      <template #extra><el-button type="primary" @click="router.push('/applications')">返回我的申请</el-button></template>
    </el-result>
    <el-empty v-else-if="!loading" description="笔试任务不存在或尚未开放" />
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowLeft, Refresh } from '@element-plus/icons-vue'
import { examApi, type OnlineExam, type OnlineExamQuestion } from '@/api/exam'

const route = useRoute(); const router = useRouter(); const exam = ref<OnlineExam | null>(null)
const loading = ref(false); const submitting = ref(false); const submitted = ref(false)
const answers = reactive<Record<string, string>>({}); const multiAnswers = reactive<Record<string, string[]>>({})
function optionsFor(question: OnlineExamQuestion) { return question.question_type === 'judge' ? ['正确', '错误'] : ['A', 'B', 'C', 'D'] }
async function load() { loading.value = true; try { const { data } = await examApi.getOnline(String(route.query.application_id || '')); exam.value = data; data.questions.forEach(q => { answers[q.question_id] = ''; multiAnswers[q.question_id] = [] }) } catch { ElMessage.error('在线笔试加载失败') } finally { loading.value = false } }
async function submit() {
  if (!exam.value) return
  const payload = exam.value.questions.map(q => ({ question_id: q.question_id, answer: q.question_type === 'multi_choice' ? [...(multiAnswers[q.question_id] || [])].sort().join('') : (answers[q.question_id] || '') }))
  if (payload.some(item => !item.answer.trim())) { ElMessage.warning('请先完成所有题目'); return }
  try { await ElMessageBox.confirm('提交后无法修改答案，确认提交吗？', '确认提交', { type: 'warning' }) } catch { return }
  submitting.value = true
  try {
    await examApi.submitOnline(exam.value.application_id, payload)
    submitted.value = true
    ElMessage.success('笔试已提交，请等待 HR 通知')
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '笔试提交失败，请稍后重试')
  } finally { submitting.value = false }
}
onMounted(() => { if (!route.query.application_id) { router.replace('/applications') } else load() })
</script>

<style scoped>
.page { max-width: 980px; margin: 0 auto; }.page-header { display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:18px; }.page-header h1 { margin:8px 0 6px; font-size:22px; }.page-header p { margin:0; color:#6b7280; font-size:14px; }.exam-body { background:#fff; border-top:1px solid #e5e7eb; }.question-row { padding:20px; border-bottom:1px solid #e5e7eb; }.question-title { display:flex; justify-content:space-between; gap:14px; margin-bottom:14px; line-height:1.6; }.question-title strong { min-width:0; overflow-wrap:anywhere; font-size:15px; white-space:pre-line; }.question-title span { flex-shrink:0; color:#6b7280; font-size:13px; }.submit-bar { display:flex; align-items:center; justify-content:space-between; gap:14px; padding:18px 20px; color:#6b7280; font-size:13px; }
@media (max-width: 640px) { .question-row { padding:16px 12px; }.question-title { flex-direction:column; gap:4px; }.submit-bar { align-items:stretch; flex-direction:column; }.submit-bar .el-button { width:100%; } }
</style>
