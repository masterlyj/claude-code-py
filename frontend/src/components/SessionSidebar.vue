<script setup lang="ts">
/**
 * 会话侧栏：会话列表 + 新建 / 切换 / 删除。
 *
 * 数据源：useSessionStore 的 sessions 数组 + currentSessionId；切换会话
 * 会触发 chatStore 里 watch(currentSessionId) 自动清理瞬态状态，所以本
 * 组件不需要关心"切换时怎么中止 SSE"这类事。
 *
 * 删除正在流式的会话是允许的：chatStore 里事件写入通过 owningSession()
 * 查找 id 定位——会话不存在时事件被静默丢弃，无副作用。
 */
import { computed } from 'vue'
import { NButton, NIcon, useDialog } from 'naive-ui'
import { useSessionStore } from '@/stores/sessions'

const sessionStore = useSessionStore()
const dialog = useDialog()

const sessions = computed(() => sessionStore.sessions)
const currentId = computed(() => sessionStore.currentSessionId)

function createNew(): void {
  sessionStore.createSession()
}

function select(id: string): void {
  if (id === currentId.value) return
  sessionStore.selectSession(id)
}

function confirmDelete(id: string, title: string): void {
  dialog.warning({
    title: '删除会话',
    content: `确定删除会话「${title}」？`,
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: () => {
      sessionStore.deleteSession(id)
    },
  })
}

function formatCreatedAt(ts: number): string {
  const d = new Date(ts)
  const now = new Date()
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate()
  if (sameDay) {
    return `${d.getHours().toString().padStart(2, '0')}:${d
      .getMinutes()
      .toString()
      .padStart(2, '0')}`
  }
  return `${d.getMonth() + 1}/${d.getDate()}`
}
</script>

<template>
  <div class="session-sidebar">
    <div class="header">
      <div class="brand">claude-code-py</div>
      <n-button size="small" type="primary" @click="createNew">
        + 新会话
      </n-button>
    </div>

    <div v-if="sessions.length === 0" class="empty">
      还没有会话，点击"+ 新会话"开始。
    </div>

    <div v-else class="session-list">
      <div
        v-for="s in sessions"
        :key="s.id"
        class="session-item"
        :class="{ active: s.id === currentId }"
        :title="s.title"
        @click="select(s.id)"
      >
        <div class="session-main">
          <div class="session-title">{{ s.title }}</div>
          <div class="session-meta">
            {{ formatCreatedAt(s.createdAt) }} · {{ s.messages.length }} 条
          </div>
        </div>
        <button
          class="delete-btn"
          title="删除会话"
          @click.stop="confirmDelete(s.id, s.title)"
        >
          <n-icon size="14">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="3 6 5 6 21 6"/>
              <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>
              <path d="M10 11v6M14 11v6"/>
              <path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>
            </svg>
          </n-icon>
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.session-sidebar {
  display: flex;
  flex-direction: column;
  height: 100%;
  padding: 12px;
}
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
  padding-bottom: 12px;
  border-bottom: 1px solid #2a3040;
}
.brand {
  font-size: 13px;
  font-weight: 600;
  color: #e6e8ee;
}
.empty {
  padding: 24px 8px;
  text-align: center;
  color: #6a7280;
  font-size: 12px;
  line-height: 1.6;
}
.session-list {
  flex: 1;
  overflow-y: auto;
  margin: 0 -4px;
}
.session-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 8px;
  margin: 2px 0;
  border-radius: 4px;
  cursor: pointer;
  transition: background 0.1s;
}
.session-item:hover {
  background: #1e2331;
}
.session-item.active {
  background: #263041;
}
.session-main {
  flex: 1;
  min-width: 0;
}
.session-title {
  font-size: 13px;
  color: #d7dae1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.session-meta {
  font-size: 11px;
  color: #6a7280;
  margin-top: 2px;
}
.delete-btn {
  background: transparent;
  border: none;
  color: #6a7280;
  cursor: pointer;
  padding: 4px;
  border-radius: 3px;
  opacity: 0;
  transition: opacity 0.1s, color 0.1s;
  flex-shrink: 0;
}
.session-item:hover .delete-btn {
  opacity: 1;
}
.delete-btn:hover:not(:disabled) {
  color: #e08894;
  background: rgba(224, 136, 148, 0.1);
}
.delete-btn:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}
</style>
