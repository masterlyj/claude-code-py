/**
 * 会话数据模型：一次对话的持久化结构。
 *
 * 前端保存的 messages 就是 Anthropic API 的消息格式（role: user/assistant，
 * content 可以是 string 或 content block 数组）。这样每次 POST /api/chat
 * 时可以原样带回后端，后端 stateless 无需存储。
 */

export interface AnthropicTextBlock {
  type: 'text'
  text: string
}

export interface AnthropicToolUseBlock {
  type: 'tool_use'
  id: string
  name: string
  input: Record<string, unknown>
}

export interface AnthropicToolResultBlock {
  type: 'tool_result'
  tool_use_id: string
  content: string
  is_error?: boolean
}

export type AnthropicContentBlock =
  | AnthropicTextBlock
  | AnthropicToolUseBlock
  | AnthropicToolResultBlock

export interface AnthropicMessage {
  role: 'user' | 'assistant'
  content: string | AnthropicContentBlock[]
}

/**
 * 前端展示用的"轮次"结构：把 API 消息按人类可读顺序拆成 turn，
 * 一 turn = 一段用户输入或一段助手输出（含内嵌的工具调用/结果）。
 *
 * 与 AnthropicMessage 的关系：turn 是 UI 层视图，messages 是后端契约层，
 * 提交时以 messages 为准，UI 渲染时按 turn 分组更自然。
 */
export interface TurnItem {
  kind: 'user' | 'assistant_text' | 'tool_use' | 'tool_result' | 'ask' | 'error'
  // 通用字段
  id: string
  // user/assistant_text
  text?: string
  // tool_use / tool_result
  tool_use_id?: string
  tool_name?: string
  tool_input?: Record<string, unknown>
  // tool_result
  content?: string
  is_error?: boolean
  // ask
  ask_id?: string
  reason?: string
  ask_state?: 'pending' | 'approved' | 'rejected'
}

export interface Session {
  id: string
  title: string
  createdAt: number
  messages: AnthropicMessage[]
  // 展示层视图：跨会话切换时直接用，不用从 messages 反推
  timeline: TurnItem[]
}
