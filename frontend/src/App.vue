<script setup lang="ts">
import { NConfigProvider, NLayout, NLayoutSider, NLayoutContent, darkTheme } from 'naive-ui'
import { useSessionStore } from '@/stores/sessions'
import { useChatStore } from '@/stores/chat'

// 先建 stores（Pinia 需要 app.use 之后才能用；此文件通过 setup 语法自动
// 保证在 createPinia 挂载后执行）
const sessionStore = useSessionStore()
const chatStore = useChatStore()

// 阶段 3 才会填对话流组件，这里先占位确认脚手架跑通
void sessionStore
void chatStore
</script>

<template>
  <n-config-provider :theme="darkTheme">
    <n-layout has-sider style="height: 100vh">
      <n-layout-sider :width="260" bordered content-style="padding: 16px">
        <div style="opacity: 0.5">会话侧栏（阶段 5 实现）</div>
      </n-layout-sider>
      <n-layout-content content-style="padding: 24px">
        <div style="opacity: 0.5">
          阶段 2 脚手架已就绪。<br />
          下一步：阶段 3 会在这里渲染对话流。
        </div>
      </n-layout-content>
    </n-layout>
  </n-config-provider>
</template>

<style>
html, body, #app {
  margin: 0;
  padding: 0;
  height: 100%;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'PingFang SC', 'Microsoft YaHei', sans-serif;
}
</style>
