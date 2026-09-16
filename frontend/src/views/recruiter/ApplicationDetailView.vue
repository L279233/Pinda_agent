<template>
  <section class="page">
    <el-button :icon="ArrowLeft" link @click="router.push('/recruiter/applications')">返回候选人看板</el-button>
    <div v-loading="loading" class="content" v-if="detail">
      <header class="detail-header"><div><h1>{{ detail.position.title }}</h1><p>候选人：{{ detail.candidate.name || detail.candidate.candidate_id }}</p></div><el-tag type="warning">{{ decisionLabel(detail.final_decision) }}</el-tag></header>
      <div class="score-grid"><div v-for="item in scoreItems" :key="item.label" class="score-item"><span>{{ item.label }}</span><strong>{{ item.value ?? '暂无' }}</strong></div></div>
      <el-collapse v-model="activeReports">
        <el-collapse-item title="简历评审" name="resume">
          <div v-if="detail.resume" class="report-section">
            <div class="report-heading">
              <p><b>综合结论：</b>{{ resumeSummary.overall_comment || resumeSummary.fit_assessment || '暂无结论' }}</p>
              <el-button v-if="needsResumeReview" type="primary" @click="openResumeReview">复核简历</el-button>
            </div>
            <p><b>建议：</b>{{ decisionText(resumeSummary.decision) }}</p>
            <h3>维度评分</h3>
            <el-table :data="resumeDimensions" size="small">
              <el-table-column prop="dimension" label="评估维度" min-width="170" />
              <el-table-column prop="score" label="分数" width="80" />
              <el-table-column label="说明" min-width="320"><template #default="s">{{ listText(s.row.issues) }}</template></el-table-column>
            </el-table>
            <h3>风险与亮点</h3>
            <p><b>亮点：</b>{{ listText(resumeSummary.highlights) }}</p>
            <p><b>风险：</b>{{ listText(resumeSummary.risk_flags) }}</p>
          </div>
          <el-empty v-else description="暂无简历评审" :image-size="60" />
        </el-collapse-item>
        <el-collapse-item title="笔试结果与人工复核" name="exam">
          <div v-if="detail.exam" class="report-section">
            <div class="report-heading">
              <p><b>处理状态：</b>{{ statusText(detail.exam.status) }}</p>
              <el-button v-if="detail.exam.status === 'pending_review'" type="primary" @click="openExamReview">复核笔试</el-button>
            </div>
            <p><b>薄弱点总结：</b>{{ detail.exam.weak_points_summary || listText(detail.exam.weak_points) }}</p>
            <el-table :data="detail.exam.reviews || []" size="small">
              <el-table-column type="index" label="题号" width="70" />
              <el-table-column prop="question_type" label="题型" width="110" />
              <el-table-column prop="student_answer" label="候选人答案" min-width="220" show-overflow-tooltip />
              <el-table-column label="得分" width="90"><template #default="s">{{ s.row.final_score ?? s.row.teacher_score ?? s.row.ai_score ?? '-' }}</template></el-table-column>
              <el-table-column label="评语" min-width="240"><template #default="s">{{ s.row.teacher_comment || s.row.ai_feedback || '暂无' }}</template></el-table-column>
            </el-table>
          </div>
          <el-empty v-else description="暂无笔试结果" :image-size="60" />
        </el-collapse-item>
        <el-collapse-item title="AI 面试报告" name="interview">
          <div v-if="detail.interview" class="report-section">
            <p><b>面试状态：</b>{{ statusText(detail.interview.status) }}</p>
            <p><b>综合评价：</b>{{ interviewReport.overall_comment || '暂无评价' }}</p>
            <h3>能力维度</h3>
            <el-table :data="interviewReport.dimensions || []" size="small">
              <el-table-column prop="dimension" label="维度" min-width="160" />
              <el-table-column prop="score" label="分数" width="80" />
              <el-table-column prop="comment" label="评价" min-width="320" />
            </el-table>
            <p><b>优势：</b>{{ listText(interviewReport.strengths) }}</p>
            <p><b>改进建议：</b>{{ listText(interviewReport.improvements) }}</p>
          </div>
          <el-empty v-else description="暂无面试报告" :image-size="60" />
        </el-collapse-item>
        <el-collapse-item title="决策审计记录" name="audit">
          <el-table :data="detail.decision_history || []" size="small">
            <el-table-column label="时间" min-width="180"><template #default="s">{{ formatTime(s.row.created_at) }}</template></el-table-column>
            <el-table-column label="原决定" width="110"><template #default="s">{{ decisionLabel(s.row.previous_decision) }}</template></el-table-column>
            <el-table-column label="新决定" width="110"><template #default="s">{{ decisionLabel(s.row.new_decision) }}</template></el-table-column>
            <el-table-column prop="comment" label="备注" min-width="220" />
          </el-table>
        </el-collapse-item>
      </el-collapse>
      <el-alert v-if="!canDecide" class="decision-hint" type="info" :closable="false" show-icon title="简历通过且笔试、AI 面试全部完成后，才能提交最终决策。" />
      <div class="decision-bar"><el-select v-model="decision" :disabled="!canDecide" placeholder="选择最终决策"><el-option v-for="item in decisionOptions" :key="item.value" :label="item.label" :value="item.value" /></el-select><el-input v-model="comment" :disabled="!canDecide" placeholder="备注（可选）" maxlength="2000" show-word-limit /><el-button type="primary" :loading="saving" :disabled="!canDecide" @click="saveDecision">提交决策</el-button></div>
    </div>
    <el-empty v-else-if="!loading" description="申请不存在或无权访问" />

    <el-dialog v-model="resumeReviewVisible" title="简历人工复核" width="480px">
      <el-form label-position="top">
        <el-form-item label="复核结论">
          <el-radio-group v-model="resumeReviewDecision">
            <el-radio value="passed">通过简历</el-radio>
            <el-radio value="rejected">淘汰简历</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="复核备注">
          <el-input v-model="resumeReviewComment" type="textarea" :rows="4" maxlength="2000" show-word-limit placeholder="填写判断依据（可选）" />
        </el-form-item>
      </el-form>
      <el-alert type="info" :closable="false" show-icon title="通过后将开放笔试和 AI 面试；淘汰后不能继续后续任务。" />
      <template #footer>
        <el-button :disabled="resumeSaving" @click="resumeReviewVisible = false">取消</el-button>
        <el-button type="primary" :loading="resumeSaving" @click="submitResumeReview">确认复核</el-button>
      </template>
    </el-dialog>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowLeft } from '@element-plus/icons-vue'
import { recruiterApi } from '@/api/recruiter'
const route = useRoute(); const router = useRouter(); const loading = ref(false); const saving = ref(false); const resumeSaving = ref(false); const detail = ref<any>(null); const decision = ref('pending'); const comment = ref('')
const activeReports = ref(['resume'])
const resumeReviewVisible = ref(false)
const resumeReviewDecision = ref<'passed' | 'rejected'>('passed')
const resumeReviewComment = ref('')
const decisionOptions = [{ value:'pending', label:'待决策' }, { value:'advance', label:'推进' }, { value:'rejected', label:'淘汰' }, { value:'hired', label:'录用' }, { value:'withdrawn', label:'撤回' }]
const scoreItems = computed(() => detail.value ? [{ label:'简历分数', value: detail.value.application.resume_score }, { label:'笔试分数', value: detail.value.application.exam_score }, { label:'面试分数', value: detail.value.application.interview_score }] : [])
const canDecide = computed(() => detail.value?.application?.resume_decision === 'passed' && detail.value?.application?.exam_status === 'completed' && detail.value?.application?.interview_status === 'completed')
const resumeSummary = computed(() => objectValue(detail.value?.resume?.summary))
const needsResumeReview = computed(() => detail.value?.application?.resume_decision === 'manual_review' || resumeSummary.value?.decision === 'manual_review')
const resumeDimensions = computed(() => objectValue(detail.value?.resume?.scores).dimension_scores || [])
const interviewReport = computed(() => objectValue(detail.value?.interview?.report))
function decisionLabel(value?: string | null) { return value ? (decisionOptions.find(item => item.value === value)?.label || value) : '待决策' }
function objectValue(value: any) { if (!value) return {}; if (typeof value !== 'string') return value; try { return JSON.parse(value) } catch { return {} } }
function listText(value: unknown) {
  if (!Array.isArray(value) || !value.length) return '暂无'
  return value.map(item => {
    if (typeof item === 'string' || typeof item === 'number') return String(item)
    if (item && typeof item === 'object') {
      const record = item as Record<string, unknown>
      return String(record.description ?? record.comment ?? record.name ?? record.title ?? '结构化明细')
    }
    return '暂无'
  }).join('；')
}
function statusText(value?: string) { return ({ processing:'处理中', pending_review:'待人工复核', completed:'已完成', published:'已完成', done:'已完成', in_progress:'进行中', submitted:'已提交', failed:'失败' } as Record<string,string>)[value || ''] || value || '暂无' }
function decisionText(value?: string) { return ({ passed:'通过', rejected:'淘汰', manual_review:'人工复核' } as Record<string,string>)[value || ''] || value || '暂无' }
function formatTime(value?: string) { return value ? new Date(value).toLocaleString() : '-' }
function openExamReview() { router.push({ path: '/teacher/exam-review', query: { submission_id: detail.value.exam.id } }) }
function openResumeReview() {
  resumeReviewDecision.value = 'passed'
  resumeReviewComment.value = ''
  resumeReviewVisible.value = true
}
async function load() { loading.value = true; try { const { data } = await recruiterApi.detail(String(route.params.applicationId)); detail.value = data; decision.value = data.final_decision || 'pending' } catch { ElMessage.error('详情加载失败') } finally { loading.value = false } }
async function saveDecision() {
  if (!canDecide.value) { ElMessage.warning('请等待简历、笔试和 AI 面试全部完成'); return }
  saving.value = true
  try {
    await recruiterApi.decide(String(route.params.applicationId), decision.value, comment.value || undefined)
    ElMessage.success('最终决策已保存')
    await load()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '最终决策保存失败')
  } finally { saving.value = false }
}
async function submitResumeReview() {
  resumeSaving.value = true
  try {
    await recruiterApi.resumeDecision(String(route.params.applicationId), resumeReviewDecision.value, resumeReviewComment.value || undefined)
    ElMessage.success(resumeReviewDecision.value === 'passed' ? '简历已通过，笔试和 AI 面试已开放' : '简历已淘汰')
    resumeReviewVisible.value = false
    await load()
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || '简历复核失败')
  } finally { resumeSaving.value = false }
}
onMounted(load)
</script>

<style scoped>
.page { max-width:1100px; margin:0 auto; }.content { margin-top:14px; }.detail-header { display:flex; justify-content:space-between; align-items:flex-start; padding:18px 0; }.detail-header h1 { margin:0 0 8px; font-size:22px; }.detail-header p { margin:0; color:#6b7280; }.score-grid { display:grid; grid-template-columns:repeat(3,1fr); gap:12px; margin-bottom:18px; }.score-item { border:1px solid #e5e7eb; background:#fff; padding:16px; display:flex; justify-content:space-between; align-items:center; }.score-item span { color:#6b7280; font-size:13px; }.score-item strong { font-size:20px; color:#1677ff; }.report-section { padding:4px 2px 14px; }.report-section h3 { margin:18px 0 10px; font-size:15px; }.report-section p { margin:8px 0; line-height:1.7; }.decision-bar { display:flex; gap:10px; margin-top:18px; align-items:center; }.decision-bar .el-select { width:150px; }.decision-bar .el-input { flex:1; } @media (max-width:700px) { .score-grid { grid-template-columns:1fr; }.decision-bar { flex-wrap:wrap; }.decision-bar .el-input { min-width:100%; } }
.report-heading { display:flex; align-items:center; justify-content:space-between; gap:12px; }
.report-heading p { margin-top:0; }
.decision-hint { margin-top:18px; }
</style>
