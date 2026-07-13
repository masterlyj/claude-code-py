<script setup lang="ts">
/**
 * 全局错误提示：watch chatStore.errorMessage，非空时用 naive-ui useMessage
 * 弹一次 toast，然后清空——避免同一条错误反复弹出。
 *
 * 单独抽这个组件是因为 useMessage 只能在 <n-message-provider> 内使用，
 * 独立组件比塞进 App.vue 更清晰。
 */
import { watch } from 'vue'
import { useMessage } from 'naive-ui'
import { useChatStore } from '@/stores/chat'

const chatStore = useChatStore()
const message = useMessage()

watch(
  () => chatStore.errorMessage,
  (msg) => {
    if (!msg) return
    message.error(msg, { duration: 8000, closable: true })
    // 展示后立刻清空，避免同一条错误因 watch 触发次数或组件重挂载再弹一次
    chatStore.errorMessage = null
  },
)
</script>

<template><!-- 纯 side-effect 组件，无 UI --></template>
