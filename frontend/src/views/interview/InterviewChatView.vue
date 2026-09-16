<template>
  <div class="interview-chat">
    <el-card v-if="isFinished">
      <el-result icon="success" title="AI 初面已完成" sub-title="请等待 HR 通知">
        <template #extra>
          <el-button type="primary" @click="router.push('/dashboard')">返回任务中心</el-button>
        </template>
      </el-result>
    </el-card>

    <template v-else>
      <el-card class="stage-card"><StageProgressBar :current="currentStage" /></el-card>
      <el-card class="chat-card">
        <div ref="messagesEl" class="chat-messages">
          <ChatBubble v-for="(msg, index) in messages" :key="index" :role="msg.role">
            <MarkdownRenderer :content="msg.content" />
          </ChatBubble>
          <ChatBubble v-if="loading && !streamingReply" role="assistant">
            <span class="muted">思考中...</span>
          </ChatBubble>
        </div>

        <div class="chat-input-area">
          <el-input
            v-model="inputText"
            type="textarea"
            :rows="3"
            placeholder="输入你的回答"
            resize="none"
            :disabled="loading"
            @keydown.ctrl.enter="sendMessage"
          />
          <div class="input-actions">
            <span class="muted">第 {{ totalTurns }} 轮</span>
            <div class="buttons">
              <el-button type="danger" plain :disabled="loading" @click="endInterview">结束面试</el-button>
              <el-button type="primary" :loading="loading" :disabled="!inputText.trim()" @click="sendMessage">
                发送
              </el-button>
            </div>
          </div>
        </div>
      </el-card>
    </template>
  </div>
</template>

<script setup lang="ts">
import { nextTick, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import ChatBubble from '@/components/chat/ChatBubble.vue'
import MarkdownRenderer from '@/components/chat/MarkdownRenderer.vue'
import StageProgressBar from '@/components/interview/StageProgressBar.vue'
import { interviewApi } from '@/api/interview'

interface Message { role: 'user' | 'assistant'; content: string }

const route = useRoute()
const router = useRouter()
const sessionId = String(route.params.sessionId)
const messages = ref<Message[]>([])
const inputText = ref('')
const loading = ref(false)
const streamingReply = ref(false)
const currentStage = ref('warmup')
const totalTurns = ref(0)
const isFinished = ref(false)
const messagesEl = ref<HTMLElement>()

async function scrollToBottom() {
  await nextTick()
  if (messagesEl.value) messagesEl.value.scrollTop = messagesEl.value.scrollHeight
}

async function sendMessage() {
  const answer = inputText.value.trim()
  if (!answer || loading.value) return
  messages.value.push({ role: 'user', content: answer })
  inputText.value = ''
  loading.value = true
  await scrollToBottom()

  let replyIndex = -1
  return new Promise<void>((resolve) => {
    interviewApi.chatStream(sessionId, answer, {
      onToken: async (chunk) => {
        if (replyIndex === -1) {
          messages.value.push({ role: 'assistant', content: '' })
          replyIndex = messages.value.length - 1
          streamingReply.value = true
        }
        messages.value[replyIndex].content += chunk
        await scrollToBottom()
      },
      onDone: async (result) => {
        streamingReply.value = false
        currentStage.value = result.current_stage
        totalTurns.value = result.total_turns
        if (replyIndex === -1 && result.reply) {
          messages.value.push({ role: 'assistant', content: result.reply })
        } else if (replyIndex >= 0 && !messages.value[replyIndex].content && result.reply) {
          messages.value[replyIndex].content = result.reply
        }
        isFinished.value = result.is_finished
        loading.value = false
        await scrollToBottom()
        resolve()
      },
      onError: (error) => {
        streamingReply.value = false
        if (replyIndex >= 0 && !messages.value[replyIndex].content) messages.value.splice(replyIndex, 1)
        ElMessage.error(error.message || '发送失败，请重试')
        loading.value = false
        resolve()
      },
    })
  })
}

async function endInterview() {
  inputText.value = '结束面试'
  await sendMessage()
}

onMounted(async () => {
  const opening = (history.state as { openingMessage?: string })?.openingMessage
  if (opening) messages.value.push({ role: 'assistant', content: opening })
  try {
    const { data } = await interviewApi.getReport(sessionId)
    isFinished.value = data.status === 'submitted'
    if (isFinished.value) currentStage.value = 'finished'
  } catch {
    // 新建会话首次进入时由 openingMessage 初始化。
  }
})
</script>

<style scoped>
.interview-chat { max-width: 900px; }
.stage-card { margin-bottom: 16px; }
.chat-messages { height: calc(100vh - 400px); min-height: 320px; overflow-y: auto; padding: 8px 0; }
.chat-input-area { border-top: 1px solid #f0f0f0; padding-top: 12px; margin-top: 12px; }
.input-actions { display: flex; justify-content: space-between; align-items: center; margin-top: 10px; }
.buttons { display: flex; gap: 8px; }
.muted { color: #8c8c8c; font-size: 12px; }
</style>
