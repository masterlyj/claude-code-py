<script setup lang="ts">
/**
 * Ask 决策的内联确认卡片（阶段 4 会完善交互，本阶段先做只读展示）。
 */
import { useChatStore } from '@/stores/chat'
import { NButton } from 'naive-ui'

const props = defineProps<{
  askId: string
  toolName: string
  toolInput: Record<string, unknown>
  reason: string
  state: 'pending' | 'approved' | 'rejected'
}>()

const chatStore = useChatStore()

function summarize(input: Record<string, unknown>): string {
  const preferKeys = ['command', 'file_path', 'path']
  for (const k of preferKeys) {
    const v = input[k]
    if (typeof v === 'string' && v.length > 0) return v
  }
  return JSON.stringify(input)
}

async function respond(approved: boolean): Promise<void> {
  await chatStore.respondAsk(props.askId, approved)
}
</script>

<template>
  <div class="ask-card" :class="state">
    <div class="header">
      <span class="marker">⚠</span>
      <span class="title">需要确认：{{ toolName }}</span>
    </div>
    <div class="reason">{{ reason }}</div>
    <pre class="preview">{{ summarize(toolInput) }}</pre>
    <div v-if="state === 'pending'" class="actions">
      <n-button type="primary" size="small" @click="respond(true)">
        允许
      </n-button>
      <n-button size="small" @click="respond(false)">
        拒绝
      </n-button>
    </div>
    <div v-else class="verdict">
      {{ state === 'approved' ? '✓ 已允许' : '✗ 已拒绝' }}
    </div>
  </div>
</template>

<style scoped>
.ask-card {
  max-width: 780px;
  margin: 10px 0;
  padding: 12px 14px;
  background: #2b241c;
  border: 1px solid #55482f;
  border-radius: 6px;
  font-size: 13px;
}
.ask-card.approved {
  background: #1e2b1e;
  border-color: #3d5c3d;
}
.ask-card.rejected {
  background: #2a1a1e;
  border-color: #5c3d43;
}
.header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.marker {
  color: #e0a45c;
  font-size: 16px;
}
.title {
  font-weight: 600;
  color: #e6d5b8;
}
.reason {
  color: #b9bec9;
  margin-bottom: 8px;
  line-height: 1.6;
}
.preview {
  margin: 0 0 10px 0;
  padding: 6px 8px;
  background: #0d1117;
  border-radius: 4px;
  font-family: 'SF Mono', Consolas, monospace;
  font-size: 12px;
  color: #c9d1d9;
  white-space: pre-wrap;
  word-break: break-all;
}
.actions {
  display: flex;
  gap: 8px;
}
.verdict {
  font-size: 12px;
  color: #8b95a5;
}
</style>
