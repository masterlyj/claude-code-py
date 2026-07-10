/**
 * 会话列表 Pinia store：持久化在 localStorage，跨刷新可恢复。
 *
 * 设计取舍：整份会话列表一起存 `sessions` 键；不做分片存储、不做 IndexedDB。
 * 学习项目单用户，会话量级不会大到 localStorage 顶不住（~5MB 上限）。
 */

import { defineStore } from 'pinia'
import { ref, computed, watch } from 'vue'
import type { Session } from '@/types/session'

const STORAGE_KEY = 'claude-code-py:sessions'

function loadFromStorage(): Session[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed
  } catch {
    // 存储损坏时静默丢弃，比崩掉整个 UI 更好
    return []
  }
}

function saveToStorage(sessions: Session[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(sessions))
  } catch {
    // quota exceeded 等场景暂不处理，让写入静默失败——真实使用中触发这个
    // 上限说明积累了大量长会话，届时再考虑压缩或分片
  }
}

export const useSessionStore = defineStore('sessions', () => {
  const sessions = ref<Session[]>(loadFromStorage())
  const currentSessionId = ref<string | null>(sessions.value[0]?.id ?? null)

  const currentSession = computed(() => {
    if (!currentSessionId.value) return null
    return sessions.value.find((s) => s.id === currentSessionId.value) ?? null
  })

  // 任何 sessions/timeline 修改都自动落 localStorage。用 deep watch 确保
  // 深层修改（比如 push 一个 turn item）也能触发保存。
  watch(sessions, (v) => saveToStorage(v), { deep: true })

  function createSession(): Session {
    const id = crypto.randomUUID()
    const s: Session = {
      id,
      title: '新会话',
      createdAt: Date.now(),
      messages: [],
      timeline: [],
    }
    sessions.value.unshift(s)
    currentSessionId.value = id
    return s
  }

  function selectSession(id: string): void {
    currentSessionId.value = id
  }

  function deleteSession(id: string): void {
    const idx = sessions.value.findIndex((s) => s.id === id)
    if (idx === -1) return
    sessions.value.splice(idx, 1)
    if (currentSessionId.value === id) {
      currentSessionId.value = sessions.value[0]?.id ?? null
    }
  }

  /**
   * 从当前会话第一条 user 消息推断标题（前 30 字符）。
   * 每次追加 user 消息时应调用一次，之后就冻结。
   */
  function refreshTitle(sessionId: string): void {
    const s = sessions.value.find((x) => x.id === sessionId)
    if (!s) return
    if (s.title !== '新会话') return  // 已有标题不覆盖
    const firstUser = s.timeline.find((t) => t.kind === 'user')
    if (firstUser?.text) {
      s.title = firstUser.text.slice(0, 30) || '新会话'
    }
  }

  return {
    sessions,
    currentSessionId,
    currentSession,
    createSession,
    selectSession,
    deleteSession,
    refreshTitle,
  }
})
