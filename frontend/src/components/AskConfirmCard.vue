<script setup lang="ts">
/**
 * Ask 决策的内联确认卡片。
 *
 * 阶段 4 的完整交互：
 *  - 只有"第一个 pending"卡片响应键盘：y/enter 允许、n/escape 拒绝，
 *    多个 Ask 同时挂起时按 y 只会响应第一个而不是全部（避免误伤）
 *  - 该卡片挂载后自动 focus 到"允许"按钮，用户无须鼠标即可决策
 *  - 显示 15 分钟倒计时（与后端 _ASK_TIMEOUT_SECONDS 对齐）；剩余 <60s
 *    时红色高亮，超时按拒绝处理
 *  - 已裁决状态（approved / rejected）显示裁决结果，不响应键盘
 */
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { NButton } from 'naive-ui'
import { useChatStore } from '@/stores/chat'

const props = defineProps<{
  askId: string
  toolName: string
  toolInput: Record<string, unknown>
  reason: string
  state: 'pending' | 'approved' | 'rejected'
}>()

const chatStore = useChatStore()

// 与后端 api/main.py 里 _ASK_TIMEOUT_SECONDS 对齐；超过后端会兜底拒绝，
// 前端提前一秒让用户看到"已超时"而不是继续悬挂
// TODO(契约): 目前倒计时从组件挂载起算——如果 Ask 期间切换会话再切回来，
// 剩余时间会重置为 15:00 而不反映后端真实剩余。要精确需要后端在 ask 事件
// 里带 expires_at 时间戳。当前学习项目场景下 15 分钟窗口本身很宽松，暂受此局限。
const TIMEOUT_SECONDS = 15 * 60

const remainingSeconds = ref(TIMEOUT_SECONDS)
let timerId: ReturnType<typeof setInterval> | null = null

/** 只有 pendingAsks 的第一项才响应键盘/自动 focus，
 *  避免多个 Ask 挂起时同一次按键触发多次响应 */
const isFirstPending = computed(() => {
  const first = chatStore.pendingAsks[0]
  return first?.ask_id === props.askId && props.state === 'pending'
})

const isCritical = computed(() => remainingSeconds.value < 60)

function summarize(input: Record<string, unknown>): string {
  const preferKeys = ['command', 'file_path', 'path', 'url']
  for (const k of preferKeys) {
    const v = input[k]
    if (typeof v === 'string' && v.length > 0) return v
  }
  return JSON.stringify(input, null, 2)
}

function formatRemaining(sec: number): string {
  const m = Math.floor(sec / 60)
  const s = sec % 60
  return `${m}:${s.toString().padStart(2, '0')}`
}

async function respond(approved: boolean): Promise<void> {
  // 立即停止倒计时，避免用户已作出决策但 timer 归零后触发二次 respond，
  // 反而把 approved 状态覆盖成 rejected
  stopTimer()
  await chatStore.respondAsk(props.askId, approved)
}

function stopTimer(): void {
  if (timerId) {
    clearInterval(timerId)
    timerId = null
  }
}

function onKeydown(e: KeyboardEvent): void {
  if (!isFirstPending.value) return
  // 忽略键盘长按 repeat，避免一次按下把连续几个 Ask 全部一键响应掉
  if (e.repeat) return
  // 输入框里按键时不劫持（textarea 里 y/n 只是普通字符）
  const target = e.target as HTMLElement | null
  if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA')) {
    return
  }
  if (e.key === 'y' || e.key === 'Y' || e.key === 'Enter') {
    e.preventDefault()
    void respond(true)
  } else if (e.key === 'n' || e.key === 'N' || e.key === 'Escape') {
    e.preventDefault()
    void respond(false)
  }
}

function focusAllowButton(): void {
  // 用 DOM 查询而不是组件 ref 是因为 Naive UI 的 NButton 不直接暴露 focus()
  // 方法；靠 data-ask-id 属性精确定位到本卡片的允许按钮
  setTimeout(() => {
    const el = document.querySelector<HTMLButtonElement>(
      `[data-ask-id="${props.askId}"] .primary-btn button`,
    )
    el?.focus()
  }, 0)
}

onMounted(() => {
  window.addEventListener('keydown', onKeydown)

  // 只有第一个 pending 卡片自动 focus——排队中的卡片等前面被响应后再切
  if (isFirstPending.value) {
    focusAllowButton()
  }

  // 只对 pending 卡片跑倒计时
  if (props.state === 'pending') {
    timerId = setInterval(() => {
      remainingSeconds.value = Math.max(0, remainingSeconds.value - 1)
      if (remainingSeconds.value === 0) {
        // 客户端本地也标记为拒绝（后端 15 分钟后会兜底拒绝，这里同步一下 UI）
        void respond(false)
      }
    }, 1000)
  }
})

onUnmounted(() => {
  window.removeEventListener('keydown', onKeydown)
  stopTimer()
})

// state 从 pending 转成 approved/rejected 时（比如同一次会话里 pendingAsks
// 顺序前面的卡片被响应），也要停 timer——防止倒计时归零后再触发一次 respond
// 把已裁决状态覆盖回去。
watch(
  () => props.state,
  (s) => {
    if (s !== 'pending') stopTimer()
  },
)

// 当本卡片从"排队"变成"第一个 pending"时（前面卡片刚被响应），
// 把焦点迁移过来，保证键盘可继续操作而不用用户重新点击
watch(isFirstPending, (v) => {
  if (v) focusAllowButton()
})
</script>

<template>
  <div
    class="ask-card"
    :class="[state, { active: isFirstPending, critical: isCritical && state === 'pending' }]"
    :data-ask-id="askId"
  >
    <div class="header">
      <span class="marker">⚠</span>
      <span class="title">需要确认：{{ toolName }}</span>
      <span v-if="state === 'pending'" class="countdown" :class="{ critical: isCritical }">
        {{ formatRemaining(remainingSeconds) }}
      </span>
    </div>
    <div class="reason">{{ reason }}</div>
    <pre class="preview">{{ summarize(toolInput) }}</pre>
    <div v-if="state === 'pending'" class="actions">
      <n-button
        class="primary-btn"
        type="primary"
        size="small"
        @click="respond(true)"
      >
        允许 <span class="hotkey" v-if="isFirstPending">(Y)</span>
      </n-button>
      <n-button size="small" @click="respond(false)">
        拒绝 <span class="hotkey" v-if="isFirstPending">(N)</span>
      </n-button>
      <span v-if="!isFirstPending" class="queued-hint">
        排队中，先处理上方的确认
      </span>
    </div>
    <div v-else class="verdict">
      {{ state === 'approved' ? '✓ 已允许' : '✗ 已拒绝' }}
    </div>
  </div>
</template>

<style scoped>
.ask-card {
  max-width: 780px;
  margin: 10px 0;
  padding: 12px 14px;
  background: #2b241c;
  border: 1px solid #55482f;
  border-radius: 6px;
  font-size: 13px;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.ask-card.active {
  /* 第一个 pending 卡片高亮，明确告诉用户键盘会作用在这一张 */
  border-color: #e0a45c;
  box-shadow: 0 0 0 2px rgba(224, 164, 92, 0.15);
}
.ask-card.critical {
  border-color: #d1717c;
  box-shadow: 0 0 0 2px rgba(209, 113, 124, 0.2);
}
.ask-card.approved {
  background: #1e2b1e;
  border-color: #3d5c3d;
  box-shadow: none;
}
.ask-card.rejected {
  background: #2a1a1e;
  border-color: #5c3d43;
  box-shadow: none;
}
.header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.marker {
  color: #e0a45c;
  font-size: 16px;
}
.title {
  font-weight: 600;
  color: #e6d5b8;
  flex: 1;
}
.countdown {
  font-family: 'SF Mono', Consolas, monospace;
  font-size: 12px;
  color: #a3aab5;
}
.countdown.critical {
  color: #e08894;
  font-weight: 600;
}
.reason {
  color: #b9bec9;
  margin-bottom: 8px;
  line-height: 1.6;
}
.preview {
  margin: 0 0 10px 0;
  padding: 6px 8px;
  background: #0d1117;
  border-radius: 4px;
  font-family: 'SF Mono', Consolas, monospace;
  font-size: 12px;
  color: #c9d1d9;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 240px;
  overflow-y: auto;
}
.actions {
  display: flex;
  gap: 8px;
  align-items: center;
}
.hotkey {
  font-size: 11px;
  opacity: 0.7;
  margin-left: 4px;
}
.queued-hint {
  color: #6a7280;
  font-size: 12px;
  margin-left: 4px;
}
.verdict {
  font-size: 12px;
  color: #8b95a5;
}
</style>
