<script setup lang="ts">
import { computed, ref } from 'vue'

const props = defineProps<{
  toolName: string
  content: string
  isError: boolean
}>()

const PREVIEW_LINES = 6
const expanded = ref(false)

const lines = computed(() => props.content.split('\n'))
const isTruncatable = computed(() => lines.value.length > PREVIEW_LINES)

const displayed = computed(() => {
  if (expanded.value || !isTruncatable.value) return props.content
  return lines.value.slice(0, PREVIEW_LINES).join('\n')
})

const hiddenLineCount = computed(() =>
  isTruncatable.value ? lines.value.length - PREVIEW_LINES : 0,
)
</script>

<template>
  <div class="tool-result-card" :class="{ error: isError }">
    <div class="header">
      <span class="label">{{ isError ? '[错误]' : '[结果]' }} {{ toolName }}</span>
      <button
        v-if="isTruncatable"
        class="toggle"
        @click="expanded = !expanded"
      >
        {{ expanded ? '收起' : `展开（还有 ${hiddenLineCount} 行）` }}
      </button>
    </div>
    <pre class="content">{{ displayed }}</pre>
  </div>
</template>

<style scoped>
.tool-result-card {
  max-width: 780px;
  margin: 4px 0 12px 0;
  padding: 8px 12px;
  background: #0f1420;
  border-left: 3px solid #4a556a;
  border-radius: 4px;
  font-size: 13px;
}
.tool-result-card.error {
  border-left-color: #d1717c;
  background: #221217;
}
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
}
.label {
  color: #8b95a5;
  font-size: 12px;
  font-weight: 500;
}
.tool-result-card.error .label {
  color: #e08894;
}
.toggle {
  background: transparent;
  border: none;
  color: #79b8ff;
  cursor: pointer;
  font-size: 12px;
  padding: 0 4px;
}
.toggle:hover {
  text-decoration: underline;
}
.content {
  margin: 0;
  padding: 0;
  color: #c9d1d9;
  font-family: 'SF Mono', Consolas, monospace;
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
  overflow-x: auto;
}
</style>
