<script setup lang="ts">
import { computed, ref } from 'vue'
import { NButton, NInput } from 'naive-ui'
import { useChatStore } from '@/stores/chat'

const chatStore = useChatStore()
const input = ref('')

const pendingHint = computed(() => {
  const n = chatStore.pendingAsks.length
  if (n === 0) return null
  if (n === 1) return '有 1 条权限请求待处理，按 Y 允许 / N 拒绝'
  return `有 ${n} 条权限请求待处理，按 Y/N 依次响应`
})

// 输入框在流式或有待确认时都要禁用——待确认时用户应该先响应 Ask
const disableInput = computed(
  () => chatStore.isStreaming || chatStore.hasPendingAsk,
)

async function send(): Promise<void> {
  const text = input.value.trim()
  if (!text || !chatStore.canSend) return
  input.value = ''
  await chatStore.sendPrompt(text)
}

async function stop(): Promise<void> {
  await chatStore.stop()
}

// Enter 发送，Shift+Enter 换行——对话场景的常见约定
function onKeydown(e: KeyboardEvent): void {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault()
    void send()
  }
}
</script>

<template>
  <div class="chat-input-container">
    <div v-if="pendingHint" class="pending-hint">
      <span class="marker">⚠</span>
      {{ pendingHint }}
    </div>
    <div class="chat-input-bar">
      <n-input
        v-model:value="input"
        type="textarea"
        :placeholder="
          disableInput
            ? '等待中，先处理上方的请求或停止当前运行…'
            : '输入消息，Enter 发送，Shift+Enter 换行'
        "
        :autosize="{ minRows: 1, maxRows: 8 }"
        :disabled="disableInput"
        @keydown="onKeydown"
      />
      <div class="actions">
        <n-button
          v-if="chatStore.isStreaming"
          type="warning"
          @click="stop"
        >
          停止
        </n-button>
        <n-button
          v-else
          type="primary"
          :disabled="!input.trim() || !chatStore.canSend"
          @click="send"
        >
          发送
        </n-button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chat-input-container {
  display: flex;
  flex-direction: column;
  border-top: 1px solid #2a3040;
  background: #1a1e28;
}
.pending-hint {
  padding: 8px 24px;
  background: #2b241c;
  border-bottom: 1px solid #55482f;
  color: #e6d5b8;
  font-size: 12px;
  display: flex;
  align-items: center;
  gap: 6px;
}
.pending-hint .marker {
  color: #e0a45c;
}
.chat-input-bar {
  display: flex;
  gap: 8px;
  padding: 12px 24px;
}
.actions {
  display: flex;
  align-items: flex-end;
}
</style>
