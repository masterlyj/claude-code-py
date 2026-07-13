<script setup lang="ts">
/**
 * 顶部 usage 面板：展示当前会话累计 tokens + 权限模式简报。
 *
 * 数据来源：sessionStore.currentSession.lastUsage（后端在每次 submit
 * 结束时通过 SubmitResultEvent 推送的 total_usage 快照）。session 还没
 * 跑过一轮时不显示，避免 "0 tokens" 制造无信息噪音。
 */
import { computed } from 'vue'
import { useSessionStore } from '@/stores/sessions'
import { useSettingsStore } from '@/stores/settings'
import { useChatStore } from '@/stores/chat'

const sessionStore = useSessionStore()
const settingsStore = useSettingsStore()
const chatStore = useChatStore()

const usage = computed(() => sessionStore.currentSession?.lastUsage)

function formatTokens(n: number | undefined): string {
  if (!n) return '0'
  if (n >= 10_000) return `${(n / 1000).toFixed(1)}k`
  return n.toString()
}
</script>

<template>
  <div class="usage-panel">
    <div class="left">
      <span class="mode-badge" :class="settingsStore.permissionMode">
        {{ settingsStore.permissionMode }}
      </span>
      <span v-if="chatStore.isStreaming" class="streaming-badge">
        <span class="dot" />
        streaming
      </span>
    </div>
    <div v-if="usage" class="right">
      <span class="metric">
        <span class="label">in</span>
        <span class="value">{{ formatTokens(usage.input_tokens) }}</span>
      </span>
      <span class="metric">
        <span class="label">out</span>
        <span class="value">{{ formatTokens(usage.output_tokens) }}</span>
      </span>
      <span
        v-if="usage.cache_read_input_tokens"
        class="metric cache"
        title="cache read tokens"
      >
        <span class="label">cache</span>
        <span class="value">{{ formatTokens(usage.cache_read_input_tokens) }}</span>
      </span>
    </div>
  </div>
</template>

<style scoped>
.usage-panel {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 24px;
  background: #171a24;
  border-bottom: 1px solid #2a3040;
  font-size: 11px;
  color: #8b95a5;
}
.left, .right {
  display: flex;
  align-items: center;
  gap: 12px;
}
.mode-badge {
  padding: 2px 8px;
  border-radius: 10px;
  background: #263041;
  color: #b9bec9;
  font-family: 'SF Mono', Consolas, monospace;
  font-size: 10px;
}
.mode-badge.bypassPermissions {
  background: #4a2b2b;
  color: #e08894;
}
.mode-badge.acceptEdits {
  background: #2b3a2b;
  color: #7dbf7d;
}
.streaming-badge {
  display: flex;
  align-items: center;
  gap: 6px;
  color: #e0a45c;
}
.streaming-badge .dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #e0a45c;
  animation: pulse 1s infinite;
}
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
}
.metric {
  display: inline-flex;
  gap: 4px;
  font-family: 'SF Mono', Consolas, monospace;
}
.metric .label {
  color: #6a7280;
}
.metric .value {
  color: #b9bec9;
}
.metric.cache .value {
  color: #7dbf7d;
}
</style>
