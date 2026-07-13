/**
 * 会话级设置：权限模式 + 规则集。持久化到 localStorage，跨刷新保留。
 *
 * 这些设置在每次 sendPrompt 时被打包进 /api/chat 请求；后端 stateless，
 * 每次请求都据此重新构造 PermissionContext。
 */
import { defineStore } from 'pinia'
import { ref, watch } from 'vue'
import type { PermissionMode } from '@/types/events'

const STORAGE_KEY = 'claude-code-py:settings'

interface StoredSettings {
  permissionMode: PermissionMode
  allowRules: string[]
  denyRules: string[]
  askRules: string[]
}

const VALID_MODES: readonly PermissionMode[] = [
  'default',
  'acceptEdits',
  'bypassPermissions',
  'plan',
  'auto',
  'dontAsk',
] as const

function coerceMode(v: unknown): PermissionMode {
  return typeof v === 'string' && (VALID_MODES as readonly string[]).includes(v)
    ? (v as PermissionMode)
    : 'default'
}

function coerceRuleArray(v: unknown): string[] {
  // 只保留字符串项，防止 storage 被手工改坏后 [1, null, {}] 混过校验
  // 被 JSON.stringify 塞进请求体触发后端 400
  if (!Array.isArray(v)) return []
  return v.filter((x): x is string => typeof x === 'string')
}

function loadFromStorage(): StoredSettings {
  const defaults: StoredSettings = {
    permissionMode: 'default',
    allowRules: [],
    denyRules: [],
    askRules: [],
  }
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return defaults
    const parsed = JSON.parse(raw)
    return {
      permissionMode: coerceMode(parsed.permissionMode),
      allowRules: coerceRuleArray(parsed.allowRules),
      denyRules: coerceRuleArray(parsed.denyRules),
      askRules: coerceRuleArray(parsed.askRules),
    }
  } catch {
    return defaults
  }
}

function saveToStorage(s: StoredSettings): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(s))
  } catch {
    // quota exceeded 静默失败：settings 体积极小，触发这个说明存储整体
    // 用满，届时上层已经该处理了
  }
}

export const useSettingsStore = defineStore('settings', () => {
  const initial = loadFromStorage()
  const permissionMode = ref<PermissionMode>(initial.permissionMode)
  const allowRules = ref<string[]>(initial.allowRules)
  const denyRules = ref<string[]>(initial.denyRules)
  const askRules = ref<string[]>(initial.askRules)

  watch(
    [permissionMode, allowRules, denyRules, askRules],
    () => {
      saveToStorage({
        permissionMode: permissionMode.value,
        allowRules: allowRules.value,
        denyRules: denyRules.value,
        askRules: askRules.value,
      })
    },
    { deep: true },
  )

  function addRule(kind: 'allow' | 'deny' | 'ask', rule: string): void {
    const trimmed = rule.trim()
    if (!trimmed) return
    const target =
      kind === 'allow' ? allowRules : kind === 'deny' ? denyRules : askRules
    if (target.value.includes(trimmed)) return  // 去重
    target.value.push(trimmed)
  }

  function removeRule(kind: 'allow' | 'deny' | 'ask', rule: string): void {
    const target =
      kind === 'allow' ? allowRules : kind === 'deny' ? denyRules : askRules
    const idx = target.value.indexOf(rule)
    if (idx >= 0) target.value.splice(idx, 1)
  }

  return {
    permissionMode,
    allowRules,
    denyRules,
    askRules,
    addRule,
    removeRule,
  }
})
