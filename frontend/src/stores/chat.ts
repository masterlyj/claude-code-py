/**
 * 当前对话的实时 Pinia store：SSE 连接生命周期 + 事件到 timeline 的转换。
 *
 * 职责边界：只管"进行中的对话"这个瞬态状态。持久化的会话历史归
 * useSessionStore 管——本 store 只在关键节点（追加 user 消息、追加助手
 * 输出、追加工具结果）通过它落 storage。
 */

import { defineStore } from 'pinia'
import { ref, computed, watch } from 'vue'
import type { ChatStreamHandle } from '@/api/sse'
import { streamChat, answerAsk, abortRun } from '@/api/sse'
import type { SseEvent } from '@/types/events'
import type {
  AnthropicMessage,
  TurnItem,
} from '@/types/session'
import { useSessionStore } from './sessions'

interface AssistantAggregation {
  /** 当前正在流式累积的助手文本 turn item id，方便持续 append text_delta。 */
  textItemId: string | null
  /** 当前 assistant 消息的所有 content block（文本 + tool_use），一轮结束
   *  时组装成一条 AnthropicMessage 追加到 messages。 */
  contentBlocks: Array<{
    type: 'text' | 'tool_use'
    text?: string
    id?: string
    name?: string
    input?: Record<string, unknown>
  }>
  /** 一批 tool_result 会作为一条 user 消息追加，先在这里缓存。 */
  toolResults: Array<{
    type: 'tool_result'
    tool_use_id: string
    content: string
    is_error?: boolean
  }>
}

function freshAggregation(): AssistantAggregation {
  return { textItemId: null, contentBlocks: [], toolResults: [] }
}

export const useChatStore = defineStore('chat', () => {
  const sessionStore = useSessionStore()

  const isStreaming = ref(false)
  const runId = ref<string | null>(null)
  const errorMessage = ref<string | null>(null)

  let handle: ChatStreamHandle | null = null
  let agg: AssistantAggregation = freshAggregation()

  /** 当前正在流式的运行所归属的 session id。SSE 事件到达时通过它写入
   *  发起时的会话，而不是 sessionStore.currentSession——避免用户在流式
   *  过程中切换会话时，迟到的 SSE 事件被错误写进新会话。 */
  let owningSessionId: string | null = null

  function owningSession() {
    if (!owningSessionId) return null
    return sessionStore.sessions.find((s) => s.id === owningSessionId) ?? null
  }

  /** 当前会话里状态仍为 pending 的 Ask 卡片。顺序即出现顺序，
   *  UI 用它决定给谁自动 focus / 显示"下一个"提示。 */
  const pendingAsks = computed(() => {
    const s = sessionStore.currentSession
    if (!s) return []
    return s.timeline.filter(
      (t) => t.kind === 'ask' && t.ask_state === 'pending',
    )
  })

  const hasPendingAsk = computed(() => pendingAsks.value.length > 0)

  // 有 pending Ask 时也阻止发送——用户应该先处理完权限确认再继续对话
  const canSend = computed(() => !isStreaming.value && !hasPendingAsk.value)

  function appendTimeline(item: TurnItem): void {
    const s = owningSession()
    if (!s) return
    s.timeline.push(item)
  }

  /**
   * 组装并追加当前助手轮次到 messages（Anthropic 契约层）。
   * 一次助手回合结束（收到 message_complete 且未请求工具）或整个循环结束时调用。
   */
  function flushAssistantMessage(): void {
    const s = owningSession()
    if (!s) return
    if (agg.contentBlocks.length === 0) return
    const msg: AnthropicMessage = {
      role: 'assistant',
      content: agg.contentBlocks.map((b) => {
        if (b.type === 'text') {
          return { type: 'text' as const, text: b.text ?? '' }
        }
        return {
          type: 'tool_use' as const,
          id: b.id!,
          name: b.name!,
          input: b.input!,
        }
      }),
    }
    s.messages.push(msg)
    agg = freshAggregation()
  }

  function flushToolResults(): void {
    const s = owningSession()
    if (!s) return
    if (agg.toolResults.length === 0) return
    s.messages.push({ role: 'user', content: agg.toolResults })
    agg.toolResults = []
  }

  function handleEvent(event: SseEvent): void {
    switch (event.type) {
      case 'stream_start':
        runId.value = event.run_id
        break

      case 'text_delta': {
        // 把 delta 追加到助手文本 item（或创建新 item）
        // 用 owningSession() 而不是 sessionStore.currentSession——如果
        // 用户在流式过程中切换了会话，这条 event 也只能写回发起时的会话
        const s = owningSession()
        if (!s) return
        if (agg.textItemId === null) {
          const id = crypto.randomUUID()
          agg.textItemId = id
          agg.contentBlocks.push({ type: 'text', text: '' })
          s.timeline.push({ kind: 'assistant_text', id, text: '' })
        }
        // 追加到 timeline 里对应 item
        const item = s.timeline.find((t) => t.id === agg.textItemId)
        if (item) item.text = (item.text ?? '') + event.text
        // 也追加到聚合器里最新的 text block
        const lastText = [...agg.contentBlocks].reverse().find((b) => b.type === 'text')
        if (lastText) lastText.text = (lastText.text ?? '') + event.text
        break
      }

      case 'tool_use': {
        agg.contentBlocks.push({
          type: 'tool_use',
          id: event.tool_use_id,
          name: event.tool_name,
          input: event.tool_input,
        })
        appendTimeline({
          kind: 'tool_use',
          id: event.tool_use_id,
          tool_use_id: event.tool_use_id,
          tool_name: event.tool_name,
          tool_input: event.tool_input,
        })
        // 收到 tool_use 就意味着当前文本 item 到此为止，下次 delta 要开新 item
        agg.textItemId = null
        break
      }

      case 'tool_result': {
        agg.toolResults.push({
          type: 'tool_result',
          tool_use_id: event.tool_use_id,
          content: event.content,
          is_error: event.is_error,
        })
        appendTimeline({
          kind: 'tool_result',
          id: `res-${event.tool_use_id}`,
          tool_use_id: event.tool_use_id,
          tool_name: event.tool_name,
          content: event.content,
          is_error: event.is_error,
        })
        break
      }

      case 'ask': {
        appendTimeline({
          kind: 'ask',
          id: `ask-${event.ask_id}`,
          ask_id: event.ask_id,
          tool_name: event.tool_name,
          tool_input: event.tool_input,
          reason: event.reason,
          ask_state: 'pending',
        })
        break
      }

      case 'message_complete':
        // 一次助手轮次结束：把 assistant 消息落到 messages，
        // 如果之前累积了 tool_result，也一起 flush（工具轮次之间的边界）
        flushAssistantMessage()
        flushToolResults()
        agg.textItemId = null
        break

      case 'submit_result':
        // 最终统计事件，可用于展示 usage；这里暂时忽略
        break

      case 'error':
        errorMessage.value = event.message
        appendTimeline({
          kind: 'error',
          id: crypto.randomUUID(),
          text: event.message,
        })
        break

      // 无需处理：query_complete 后端不会推、stream_request_start 目前不用
      default:
        break
    }
  }

  async function sendPrompt(prompt: string): Promise<void> {
    if (isStreaming.value) return
    let s = sessionStore.currentSession
    if (!s) {
      sessionStore.createSession()
      s = sessionStore.currentSession!
    }

    // 追加用户消息到 timeline + messages
    const userItemId = crypto.randomUUID()
    s.timeline.push({ kind: 'user', id: userItemId, text: prompt })
    s.messages.push({ role: 'user', content: prompt })
    sessionStore.refreshTitle(s.id)

    errorMessage.value = null
    agg = freshAggregation()
    // 锁定发起时的会话 id；后续所有 SSE 事件都写回这个会话，即使用户
    // 中途切换到别的会话也不会把 delta 泄漏进去
    owningSessionId = s.id

    // 组装请求：把当前 messages 全量带给后端（含刚 push 的这条 user），
    // 但要把最新一条剥出来作为 prompt——api.main 的 ChatRequest 语义：
    // messages = 之前累计历史；prompt = 本次新输入
    const history = s.messages.slice(0, -1)

    isStreaming.value = true
    handle = streamChat(
      {
        prompt,
        messages: history,
      },
      handleEvent,
    )

    try {
      await handle.done
    } catch (err) {
      // AbortError 是用户主动 stop，不当错误提示
      const isAbort =
        err instanceof DOMException && err.name === 'AbortError'
      if (!isAbort) {
        errorMessage.value = err instanceof Error ? err.message : String(err)
      }
    } finally {
      // 保险：兜底 flush 一次，避免网络断连时最后一段 assistant 内容丢失
      flushAssistantMessage()
      flushToolResults()
      isStreaming.value = false
      runId.value = null
      handle = null
      owningSessionId = null
    }
  }

  async function respondAsk(askId: string, approved: boolean): Promise<void> {
    const s = sessionStore.currentSession
    if (!s) return
    const item = s.timeline.find((t) => t.ask_id === askId)
    if (item) {
      item.ask_state = approved ? 'approved' : 'rejected'
    }
    try {
      await answerAsk(askId, approved)
    } catch (err) {
      errorMessage.value = err instanceof Error ? err.message : String(err)
    }
  }

  async function stop(): Promise<void> {
    // 双保险：客户端断连 + 服务端 abort。任一先到都能让流结束。
    if (runId.value) {
      try {
        await abortRun(runId.value)
      } catch {
        // 忽略：abort 端点本身失败不影响客户端断连
      }
    }
    handle?.abort()
  }

  /**
   * 切换/关闭 leaving 会话时的瞬态状态清理：中止进行中的 SSE、把 leaving
   * 会话 timeline 里的 pending Ask 强制置为 rejected（用户切走等价于放弃
   * 权限确认）、清空聚合器与错误。留下的持久化数据（timeline、messages）
   * 不动，因为会话切走后它们还要用来展示。
   *
   * 必须传入 leavingSessionId 而不是访问 sessionStore.currentSession——
   * 因为本函数由 watch(currentSessionId) 触发，触发时 id 已经指向新会话，
   * 从 currentSession 拿会拿到 entering 会话，误标它的 Ask。
   */
  function resetForSessionSwitch(leavingSessionId: string): void {
    if (handle) {
      handle.abort()
      handle = null
    }
    if (runId.value) {
      // fire-and-forget：切会话时不阻塞用户，让后端自己收尾
      void abortRun(runId.value).catch(() => {})
    }
    // 只清 leaving 会话的 pending Ask，避免误伤别的会话
    const leaving = sessionStore.sessions.find((s) => s.id === leavingSessionId)
    if (leaving) {
      for (const item of leaving.timeline) {
        if (item.kind === 'ask' && item.ask_state === 'pending') {
          item.ask_state = 'rejected'
        }
      }
    }
    isStreaming.value = false
    runId.value = null
    errorMessage.value = null
    agg = freshAggregation()
    owningSessionId = null
  }

  // 会话切换时自动清理瞬态状态：sessionStore.currentSessionId 是权威源，
  // chat store 只是它的下游消费者，用 watch 而不是让 sessionStore 反向
  // 调 chatStore 方法，保持依赖方向 chat → sessions 单向。
  //
  // 只在真的从"某个会话"切到"另一个 / 无会话"时清理；从 null 首次挂载
  // 到新建会话不算切换，chatStore 此时本来就是新鲜态。
  watch(
    () => sessionStore.currentSessionId,
    (newId, oldId) => {
      if (oldId && oldId !== newId) {
        resetForSessionSwitch(oldId)
      }
    },
  )

  return {
    isStreaming,
    runId,
    errorMessage,
    canSend,
    pendingAsks,
    hasPendingAsk,
    sendPrompt,
    respondAsk,
    stop,
  }
})
