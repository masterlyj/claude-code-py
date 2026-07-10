<script setup lang="ts">
import { onMounted } from 'vue'
import {
  NConfigProvider,
  NLayout,
  NLayoutContent,
  NLayoutSider,
  NMessageProvider,
  darkTheme,
} from 'naive-ui'
import MessageStream from '@/components/MessageStream.vue'
import ChatInput from '@/components/ChatInput.vue'
import { useSessionStore } from '@/stores/sessions'

const sessionStore = useSessionStore()

// 首次打开时如果没有任何会话，建一个空的作为初始工作面。
// 阶段 5 会实现完整侧栏（列表 + 新建 + 删除），本阶段先保证主界面可用。
onMounted(() => {
  if (sessionStore.sessions.length === 0) {
    sessionStore.createSession()
  }
})
</script>

<template>
  <n-config-provider :theme="darkTheme">
    <n-message-provider>
      <n-layout has-sider style="height: 100vh; background: #12151d">
        <n-layout-sider
          :width="240"
          bordered
          content-style="padding: 16px; color: #b9bec9;"
        >
          <div class="sider-title">claude-code-py</div>
          <div class="sider-placeholder">
            会话列表<br />（阶段 5）
          </div>
        </n-layout-sider>
        <n-layout-content
          content-style="display: flex; flex-direction: column; height: 100vh;"
        >
          <MessageStream />
          <ChatInput />
        </n-layout-content>
      </n-layout>
    </n-message-provider>
  </n-config-provider>
</template>

<style>
html, body, #app {
  margin: 0;
  padding: 0;
  height: 100%;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'PingFang SC', 'Microsoft YaHei', sans-serif;
  background: #12151d;
}
</style>

<style scoped>
.sider-title {
  font-size: 14px;
  font-weight: 600;
  color: #e6e8ee;
  margin-bottom: 12px;
}
.sider-placeholder {
  font-size: 12px;
  color: #6a7280;
  line-height: 1.6;
}
</style>
