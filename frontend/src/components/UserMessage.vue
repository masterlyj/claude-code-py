<script setup lang="ts">
import { computed } from 'vue'
import { NCard } from 'naive-ui'

const props = defineProps<{
  text: string
}>()

// 用户消息不做 markdown 渲染——用户输入的是纯文本，把 `#` 之类当标题
// 处理反而奇怪；用 pre-wrap 保留换行/空格即可。
const displayText = computed(() => props.text)
</script>

<template>
  <div class="user-message-row">
    <n-card size="small" class="user-message-bubble" :bordered="false">
      <div class="user-message-text">{{ displayText }}</div>
    </n-card>
  </div>
</template>

<style scoped>
.user-message-row {
  display: flex;
  justify-content: flex-end;
  margin: 8px 0;
}
.user-message-bubble {
  max-width: 720px;
  background: #2b3346;
}
.user-message-text {
  white-space: pre-wrap;
  word-break: break-word;
  color: #e6e8ee;
  font-size: 14px;
  line-height: 1.6;
}
</style>
