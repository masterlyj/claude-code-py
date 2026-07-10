<script setup lang="ts">
import { computed } from 'vue'
import { NIcon } from 'naive-ui'

const props = defineProps<{
  toolName: string
  toolInput: Record<string, unknown>
}>()

// 一行摘要：把 tool_input 里最"有信息量"的字段挑出来展示，
// 比如 Bash 的 command / Read 的 file_path。找不到时 fallback 到整个 input。
const summary = computed(() => {
  const preferKeys = ['command', 'file_path', 'path', 'query', 'url']
  for (const k of preferKeys) {
    const v = props.toolInput[k]
    if (typeof v === 'string' && v.length > 0) return v
  }
  return JSON.stringify(props.toolInput)
})

const fullInput = computed(() => JSON.stringify(props.toolInput, null, 2))
</script>

<template>
  <details class="tool-use-card">
    <summary class="tool-use-summary">
      <n-icon size="14" class="tool-icon">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>
        </svg>
      </n-icon>
      <span class="tool-name">{{ toolName }}</span>
      <span class="tool-summary-text">{{ summary }}</span>
    </summary>
    <pre class="tool-input-full">{{ fullInput }}</pre>
  </details>
</template>

<style scoped>
.tool-use-card {
  max-width: 780px;
  margin: 6px 0;
  padding: 6px 12px;
  background: #1c2130;
  border: 1px solid #2a3040;
  border-radius: 6px;
  font-size: 13px;
  color: #b9bec9;
}
.tool-use-summary {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  user-select: none;
  list-style: none;
}
.tool-use-summary::-webkit-details-marker {
  display: none;
}
.tool-icon {
  color: #79b8ff;
  flex-shrink: 0;
}
.tool-name {
  font-weight: 600;
  color: #79b8ff;
}
.tool-summary-text {
  font-family: 'SF Mono', Consolas, monospace;
  color: #a3aab5;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  flex: 1;
  min-width: 0;
}
.tool-input-full {
  margin: 8px 0 0 0;
  padding: 8px;
  background: #0d1117;
  border-radius: 4px;
  overflow-x: auto;
  font-family: 'SF Mono', Consolas, monospace;
  font-size: 12px;
  color: #c9d1d9;
}
</style>
