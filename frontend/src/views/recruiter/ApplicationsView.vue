<template>
  <section class="page">
    <header class="page-header">
      <div><h1>候选人看板</h1><p>按岗位和申请状态筛选候选人</p></div>
      <el-button :icon="Refresh" :loading="loading" circle title="刷新" @click="load" />
    </header>
    <div class="filters">
      <el-input v-model="positionId" placeholder="岗位 ID（可选）" clearable />
      <el-select v-model="status" placeholder="申请状态" clearable>
        <el-option v-for="item in statuses" :key="item.value" :label="item.label" :value="item.value" />
      </el-select>
      <el-button type="primary" :icon="Search" @click="load">筛选</el-button>
    </div>
    <el-table v-loading="loading" :data="items" row-key="application_id" empty-text="暂无候选人申请">
      <el-table-column prop="candidate_name" label="候选人" min-width="150" />
      <el-table-column prop="position_title" label="岗位" min-width="220" />
      <el-table-column label="简历" width="110"><template #default="s"><el-tag :type="decisionType(s.row.resume_decision)">{{ decisionLabel(s.row.resume_decision) }}</el-tag></template></el-table-column>
      <el-table-column label="笔试" width="100"><template #default="s"><el-tag effect="plain">{{ statusLabel(s.row.exam_status) }}</el-tag></template></el-table-column>
      <el-table-column label="面试" width="100"><template #default="s"><el-tag effect="plain">{{ statusLabel(s.row.interview_status) }}</el-tag></template></el-table-column>
      <el-table-column label="综合分数" width="190"><template #default="s">{{ scoreSummary(s.row) }}</template></el-table-column>
      <el-table-column label="最终决策" width="120"><template #default="s">{{ decisionLabel(s.row.final_decision) }}</template></el-table-column>
      <el-table-column label="操作" width="100" fixed="right"><template #default="s"><el-button type="primary" link @click="openDetail(s.row.application_id)">查看详情</el-button></template></el-table-column>
    </el-table>
    <el-pagination v-if="total > pageSize" v-model:current-page="page" :page-size="pageSize" :total="total" layout="prev, pager, next" @current-change="load" />
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Refresh, Search } from '@element-plus/icons-vue'
import { recruiterApi, type RecruiterApplicationItem } from '@/api/recruiter'

const router = useRouter(); const items = ref<RecruiterApplicationItem[]>([]); const loading = ref(false)
const positionId = ref(''); const status = ref(''); const page = ref(1); const pageSize = 20; const total = ref(0)
const statuses = [{ value: 'draft', label: '草稿' }, { value: 'resume_processing', label: '简历处理中' }, { value: 'screening', label: '筛选中' }, { value: 'completed', label: '已完成' }, { value: 'rejected', label: '已淘汰' }]
const statusMap: Record<string, string> = { locked: '未开放', available: '待完成', in_progress: '进行中', pending_review: '待复核', completed: '已完成', expired: '已过期', draft: '草稿', resume_processing: '处理中', screening: '筛选中', rejected: '已淘汰' }
const decisionMap: Record<string, string> = { pending: '待决策', passed: '通过', pass: '通过', rejected: '淘汰', manual_review: '待复核', advance: '推进', hired: '录用', withdrawn: '撤回' }
function statusLabel(value?: string | null) { return value ? (statusMap[value] || value) : '暂无' }
function decisionLabel(value?: string | null) { return value ? (decisionMap[value] || value) : '待决策' }
function decisionType(value?: string | null) { return value === 'passed' || value === 'pass' ? 'success' : value === 'rejected' ? 'danger' : 'warning' }
function scoreSummary(row: RecruiterApplicationItem) { return [row.resume_score, row.exam_score, row.interview_score].map(v => v == null ? '-' : v).join(' / ') }
function openDetail(id: string) { router.push(`/recruiter/applications/${id}`) }
async function load() { loading.value = true; try { const { data } = await recruiterApi.list({ position_id: positionId.value || undefined, status: status.value || undefined, page: page.value, page_size: pageSize }); items.value = data.items; total.value = data.total } catch (e) { ElMessage.error('候选人列表加载失败') } finally { loading.value = false } }
onMounted(load)
</script>

<style scoped>
.page { max-width: 1250px; margin: 0 auto; }.page-header { display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:18px; }.page-header h1 { margin:0 0 6px; font-size:22px; }.page-header p { margin:0; color:#6b7280; font-size:14px; }.filters { display:flex; gap:10px; margin-bottom:16px; max-width:620px; }.filters .el-input { width:220px; }.filters .el-select { width:160px; }.el-pagination { margin-top:16px; justify-content:flex-end; }
</style>
