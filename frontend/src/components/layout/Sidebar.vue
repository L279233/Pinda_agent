<template>
  <div class="sidebar">
    <div class="logo">
      <span>聘达</span>
    </div>
    <nav class="nav-list">
      <RouterLink to="/dashboard" class="nav-item" :class="{ 'nav-item--active': isActive('/dashboard') }">
        <el-icon><House /></el-icon>
        <span>工作台</span>
      </RouterLink>

      <template v-if="auth.isCandidate">
        <RouterLink to="/positions" class="nav-item" :class="{ 'nav-item--active': isActive('/positions') }">
          <el-icon><Search /></el-icon>
          <span>招聘岗位</span>
        </RouterLink>

        <RouterLink to="/applications" class="nav-item" :class="{ 'nav-item--active': isActive('/applications') }">
          <el-icon><Tickets /></el-icon>
          <span>我的申请</span>
        </RouterLink>

        <RouterLink to="/notifications" class="nav-item" :class="{ 'nav-item--active': isActive('/notifications') }">
          <el-icon><Bell /></el-icon>
          <span>通知中心</span>
        </RouterLink>
      </template>

      <RouterLink to="/chat" class="nav-item" :class="{ 'nav-item--active': isActive('/chat') }">
        <el-icon><ChatDotRound /></el-icon>
        <span>智能问答</span>
      </RouterLink>

      <!-- 招聘方菜单 -->
      <template v-if="auth.isTeacher">
        <div class="nav-divider" />
        <RouterLink to="/recruiter/applications" class="nav-item" :class="{ 'nav-item--active': isActive('/recruiter/applications') }">
          <el-icon><Tickets /></el-icon>
          <span>候选人看板</span>
        </RouterLink>
        <RouterLink to="/recruiter/positions" class="nav-item" :class="{ 'nav-item--active': isActive('/recruiter/positions') }">
          <el-icon><Briefcase /></el-icon>
          <span>岗位管理</span>
        </RouterLink>
        <RouterLink to="/recruiter/knowledge" class="nav-item" :class="{ 'nav-item--active': isActive('/recruiter/knowledge') }">
          <el-icon><UploadFilled /></el-icon>
          <span>知识库文档</span>
        </RouterLink>
        <RouterLink to="/teacher/exam-review" class="nav-item" :class="{ 'nav-item--active': isActive('/teacher/exam-review') }">
          <el-icon><EditPen /></el-icon>
          <span>批改确认</span>
        </RouterLink>
      </template>
    </nav>
  </div>
</template>

<script setup lang="ts">
import { useRoute } from 'vue-router'
import { Bell, Briefcase, House, ChatDotRound, Search, Tickets, EditPen, UploadFilled } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const route = useRoute()

// 仅用于子路由高亮（纯视觉反馈，不参与导航逻辑）
// RouterLink 的 active-class 基于路由记录层级，不覆盖平级子路由（如 /resume/:id）
function isActive(prefix: string) {
  return route.path === prefix || route.path.startsWith(prefix + '/')
}
</script>

<style scoped>
.sidebar {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.logo {
  height: 56px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  font-size: 16px;
  font-weight: 600;
  border-bottom: 1px solid #ffffff1a;
  flex-shrink: 0;
}

.nav-list {
  display: flex;
  flex-direction: column;
  padding: 4px 0;
  flex: 1;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 13px 20px;
  color: #ffffffa6;
  text-decoration: none;
  font-size: 14px;
  cursor: pointer;
  transition: background-color 0.2s, color 0.2s;
  user-select: none;
}

.nav-item:hover {
  background-color: #ffffff14;
  color: #fff;
}

.nav-item--active {
  background-color: #1677ff;
  color: #fff;
}

.nav-item .el-icon {
  font-size: 16px;
  flex-shrink: 0;
}

.nav-divider {
  border: none;
  border-top: 1px solid #ffffff1a;
  margin: 8px 0;
}
</style>
