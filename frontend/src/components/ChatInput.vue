<script setup lang="ts">
import { ref } from 'vue'
import { NButton, NInput } from 'naive-ui'
import { useChatStore } from '@/stores/chat'

const chatStore = useChatStore()
const input = ref('')

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
  <div class="chat-input-bar">
    <n-input
      v-model:value="input"
      type="textarea"
      placeholder="输入消息，Enter 发送，Shift+Enter 换行"
      :autosize="{ minRows: 1, maxRows: 8 }"
      :disabled="chatStore.isStreaming"
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
        :disabled="!input.trim()"
        @click="send"
      >
        发送
      </n-button>
    </div>
  </div>
</template>

<style scoped>
.chat-input-bar {
  display: flex;
  gap: 8px;
  padding: 12px 24px;
  border-top: 1px solid #2a3040;
  background: #1a1e28;
}
.actions {
  display: flex;
  align-items: flex-end;
}
</style>
