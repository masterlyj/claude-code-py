/**
 * 消息历史的自检修复：保证每个 tool_use 都有配对的 tool_result。
 *
 * 为什么前端要管这件事：本地 messages 是权威版本。api/main.py 每次请求都用
 * 前端带过来的历史直接覆盖后端 QueryEngine 的 messages，所以后端补得再全，
 * 只要前端手里的历史不合法，下一次提交就会被 Anthropic API 400 拒绝。
 *
 * 中断路径（用户点 stop、SSE 断连、切会话时丢弃聚合器）都可能把历史停在
 * "assistant 已经带 tool_use，但 tool_result 只收了一部分甚至一条都没有"的
 * 状态——后端是串行逐个执行工具、逐个推 tool_result 的，中途断线必然留下
 * 残缺批次。这类历史一旦落进 localStorage，该会话此后每次提交都会失败，
 * 用户没有自救手段，所以必须在提交前兜住。
 *
 * 补齐策略与后端 max_turns 的合成逻辑一致：给缺失的 tool_use 补一条
 * is_error=True 的 tool_result，内容说明结果未送达。措辞刻意不写"未执行"——
 * 断线可能发生在工具已经跑完之后，前端无法区分，只能让模型去核实实际状态。
 */

import type {
  AnthropicContentBlock,
  AnthropicMessage,
  AnthropicToolResultBlock,
} from '@/types/session'

/** 合成 tool_result 的内容：说清"结果没收到"而不是猜"没执行"——断线可能发生在
 *  工具已经跑完之后，前端无从判断，所以让模型自己去看实际状态。 */
const INTERRUPTED_REASON = '工具调用被中断，执行结果未知'

/**
 * 就地修复 messages 里残缺的 tool_use / tool_result 配对。
 *
 * 对每条带 tool_use 的 assistant 消息，检查紧跟其后的 user 消息是否覆盖了
 * 全部 tool_use_id：缺的补进那条 user 消息；下一条不是 tool_result 载体
 * （比如已经是用户的新输入）时，另插一条 user 消息在 assistant 之后——
 * Anthropic 接受连续同角色消息并会合并，所以不会造成非法序列。
 *
 * 就地修改传入数组（而不是返回新数组），是为了让 Pinia 的 deep watch 能
 * 捕获到修复并把干净的历史写回 localStorage。
 *
 * Args:
 *   messages: 会话累计的 Anthropic 格式消息历史，函数直接在其上增补。
 */
export function repairToolPairing(messages: AnthropicMessage[]): void {
  for (let i = 0; i < messages.length; i++) {
    const msg = messages[i]
    if (msg.role !== 'assistant' || !Array.isArray(msg.content)) continue

    const toolUseIds: string[] = []
    for (const block of msg.content) {
      if (block.type === 'tool_use') toolUseIds.push(block.id)
    }
    if (toolUseIds.length === 0) continue

    const next = messages[i + 1]
    // tool_result 只能放在 user 消息里；string 内容的消息是新输入，不是载体
    const carryContent =
      next && next.role === 'user' && Array.isArray(next.content)
        ? (next.content as AnthropicContentBlock[])
        : null

    const answered = new Set<string>()
    if (carryContent) {
      for (const block of carryContent) {
        if (block.type === 'tool_result') answered.add(block.tool_use_id)
      }
    }

    const missing = toolUseIds.filter((id) => !answered.has(id))
    if (missing.length === 0) continue

    const synthetic: AnthropicToolResultBlock[] = missing.map((toolUseId) => ({
      type: 'tool_result',
      tool_use_id: toolUseId,
      content: INTERRUPTED_REASON,
      is_error: true,
    }))

    if (carryContent) {
      carryContent.push(...synthetic)
    } else {
      messages.splice(i + 1, 0, { role: 'user', content: synthetic })
    }
  }
}
