<script setup lang="ts">
import { onMounted } from 'vue'
import {
  NConfigProvider,
  NDialogProvider,
  NLayout,
  NLayoutContent,
  NLayoutSider,
  NMessageProvider,
  darkTheme,
} from 'naive-ui'
import MessageStream from '@/components/MessageStream.vue'
import ChatInput from '@/components/ChatInput.vue'
import SessionSidebar from '@/components/SessionSidebar.vue'
import { useSessionStore } from '@/stores/sessions'

const sessionStore = useSessionStore()

// 首次打开时如果没有任何会话，建一个空的作为初始工作面
onMounted(() => {
  if (sessionStore.sessions.length === 0) {
    sessionStore.createSession()
  }
})
</script>

<template>
  <n-config-provider :theme="darkTheme">
    <n-message-provider>
      <n-dialog-provider>
        <n-layout has-sider style="height: 100vh; background: #12151d">
          <n-layout-sider
            :width="240"
            bordered
            content-style="padding: 0;"
          >
            <SessionSidebar />
          </n-layout-sider>
          <n-layout-content
            content-style="display: flex; flex-direction: column; height: 100vh;"
          >
            <MessageStream />
            <ChatInput />
          </n-layout-content>
        </n-layout>
      </n-dialog-provider>
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
