<script setup lang="ts">
import { computed, ref } from 'vue'
import { renderMarkdown, whenHighlighterReady } from '@/lib/markdown'

const props = defineProps<{
  text: string
}>()

// 触发再渲染的哑变量：shiki 就绪后 +1，让 computed 重新计算，
// 消除"首次渲染代码块无高亮"的短暂空窗
const shikiTick = ref(0)
whenHighlighterReady().then(() => {
  shikiTick.value++
})

// computed 会自动依赖 props.text 和 shikiTick，两者任一变化都会重算
const html = computed(() => {
  void shikiTick.value
  return renderMarkdown(props.text)
})
</script>

<template>
  <div class="assistant-message" v-html="html" />
</template>

<style scoped>
.assistant-message {
  max-width: 780px;
  margin: 8px 0;
  padding: 4px 12px;
  color: #d7dae1;
  font-size: 14px;
  line-height: 1.7;
}
/* markdown-it 生成的元素样式（无 scoped 需要用 :deep） */
.assistant-message :deep(p) {
  margin: 8px 0;
}
.assistant-message :deep(pre) {
  margin: 12px 0;
  padding: 12px;
  background: #0d1117;
  border-radius: 6px;
  overflow-x: auto;
  font-size: 13px;
  line-height: 1.5;
}
.assistant-message :deep(code) {
  font-family: 'SF Mono', Consolas, monospace;
}
.assistant-message :deep(:not(pre) > code) {
  padding: 2px 6px;
  background: #2a2f3a;
  border-radius: 3px;
  font-size: 90%;
}
.assistant-message :deep(a) {
  color: #79b8ff;
}
.assistant-message :deep(ul),
.assistant-message :deep(ol) {
  padding-left: 24px;
}
.assistant-message :deep(blockquote) {
  margin: 8px 0;
  padding-left: 12px;
  border-left: 3px solid #444c58;
  color: #a3aab5;
}
</style>
