<template>
  <section class="page">
    <header class="page-header">
      <div><h1>通知中心</h1><p>查看招聘进度、任务提醒和正式结果通知</p></div>
      <div class="header-actions">
        <el-switch v-model="unreadOnly" active-text="仅看未读" @change="load" />
        <el-button :icon="Refresh" :loading="loading" circle title="刷新" @click="load" />
      </div>
    </header>
    <div v-loading="loading" class="notification-list">
      <button
        v-for="item in items"
        :key="item.notification_id"
        type="button"
        class="notification-row"
        :class="{ unread: !item.read }"
        @click="open(item)"
      >
        <span class="status-dot" />
        <span class="notification-body">
          <strong>{{ item.title }}</strong>
          <span>{{ item.content }}</span>
          <small v-if="item.deadline">截止时间：{{ formatTime(item.deadline) }}</small>
          <small v-else>通知时间：{{ formatTime(item.sent_at) }}</small>
        </span>
        <el-icon><ArrowRight /></el-icon>
      </button>
      <el-empty v-if="!loading && items.length === 0" description="暂无任务提醒" />
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowRight, Refresh } from '@element-plus/icons-vue'
import { notificationsApi, type NotificationItem } from '@/api/notifications'

const router = useRouter()
const loading = ref(false)
const unreadOnly = ref(false)
const items = ref<NotificationItem[]>([])

function formatTime(value?: string | null) {
  return value ? new Date(value).toLocaleString() : '未设置'
}

async function load() {
  loading.value = true
  try {
    const { data } = await notificationsApi.list(unreadOnly.value)
    items.value = data.items
  } finally {
    loading.value = false
  }
}

async function open(item: NotificationItem) {
  if (!item.read) {
    await notificationsApi.markRead(item.notification_id)
    item.read = true
  }
  if (item.application_id) router.push(`/applications/${item.application_id}/tasks`)
}

onMounted(load)
</script>

<style scoped>
.page { max-width: 900px; margin: 0 auto; }
.page-header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 18px; }
.page-header h1 { margin: 0 0 6px; font-size: 22px; }
.page-header p { margin: 0; color: #6b7280; font-size: 14px; }
.header-actions { display: flex; align-items: center; gap: 12px; }
.notification-list { min-height: 180px; background: #fff; border-top: 1px solid #e5e7eb; }
.notification-row { width: 100%; display: grid; grid-template-columns: 10px minmax(0, 1fr) 20px; gap: 12px; align-items: center; padding: 18px; border: 0; border-bottom: 1px solid #e5e7eb; background: #fff; color: #1f2937; text-align: left; cursor: pointer; }
.notification-row:hover { background: #f8fafc; }
.notification-row.unread { background: #f4f9ff; }
.status-dot { width: 7px; height: 7px; border-radius: 50%; background: #cbd5e1; }
.unread .status-dot { background: #1677ff; }
.notification-body { display: flex; flex-direction: column; gap: 5px; min-width: 0; }
.notification-body strong { font-size: 14px; }
.notification-body span { color: #4b5563; font-size: 13px; }
.notification-body small { color: #6b7280; font-size: 12px; }
</style>
