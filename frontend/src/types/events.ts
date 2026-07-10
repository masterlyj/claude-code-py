/**
 * 与后端事件流对齐的 TypeScript 类型。
 *
 * 手工维护，不用 codegen——8 个事件类型总共几十行，一次性拷贝
 * 比引入代码生成工具链更划算，且改动是显式的、可 review 的。
 *
 * 数据结构来源：
 *   core.models 里 StreamEvent 的各子类型 → 前 7 项
 *   api.main 里 API 层扩展的三个事件 → 后 3 项（stream_start / ask / error）
 *
 * 前端按 `type` 字段做 discriminated union 分发；新增事件类型时同步这里。
 */

// ── core.models 的透传事件 ────────────────────────────────────────────────

export interface StreamRequestStartEvent {
  type: 'stream_request_start'
}

export interface TextDeltaEvent {
  type: 'text_delta'
  text: string
}

export interface ToolUseEvent {
  type: 'tool_use'
  tool_use_id: string
  tool_name: string
  tool_input: Record<string, unknown>
}

export interface ToolResultEvent {
  type: 'tool_result'
  tool_use_id: string
  tool_name: string
  content: string
  is_error: boolean
}

export interface MessageCompleteEvent {
  type: 'message_complete'
  stop_reason: string
  usage: Record<string, number>
}

export interface QueryCompleteEvent {
  // 后端 engine 层其实吞掉了这个事件不透传给 SSE，这里列出只是完整性
  type: 'query_complete'
  final_messages: unknown[]
  total_turns: number
  stopped_reason: 'end_turn' | 'max_turns' | 'aborted'
}

export interface SubmitResultEvent {
  type: 'submit_result'
  subtype: 'success' | 'max_turns' | 'aborted'
  num_turns: number
  total_usage: Record<string, number>
  permission_denials: Array<{
    tool_name: string
    tool_input: Record<string, unknown>
    behavior: string
    reason: string
  }>
}

// ── API 层扩展的事件 ─────────────────────────────────────────────────────

export interface StreamStartEvent {
  type: 'stream_start'
  run_id: string
}

export interface AskEvent {
  type: 'ask'
  ask_id: string
  tool_name: string
  tool_input: Record<string, unknown>
  reason: string
}

export interface ErrorEvent {
  type: 'error'
  message: string
}

// ── discriminated union & guards ──────────────────────────────────────────

export type SseEvent =
  | StreamRequestStartEvent
  | TextDeltaEvent
  | ToolUseEvent
  | ToolResultEvent
  | MessageCompleteEvent
  | QueryCompleteEvent
  | SubmitResultEvent
  | StreamStartEvent
  | AskEvent
  | ErrorEvent

// ── 请求体类型 ───────────────────────────────────────────────────────────

export type PermissionMode =
  | 'default'
  | 'acceptEdits'
  | 'bypassPermissions'
  | 'plan'
  | 'auto'
  | 'dontAsk'

export interface RuleSet {
  allow?: string[]
  deny?: string[]
  ask?: string[]
}

export interface ChatRequest {
  prompt: string
  messages?: unknown[]
  system_prompt?: string
  model?: string
  max_turns?: number
  permission_mode?: PermissionMode
  rules?: RuleSet
}
