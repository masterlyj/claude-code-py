/**
 * SSE 客户端：POST /api/chat 并解析 event stream。
 *
 * 不用浏览器原生 EventSource，因为它只支持 GET 且不能带 body——
 * 而我们的 chat 端点是 POST + JSON body。用 fetch + ReadableStream 手动
 * 拆 SSE 帧（`data: <json>\\n\\n`）就足够，代码量与依赖都比 sse.js 之类
 * 的第三方库更小。
 */

import type { ChatRequest, SseEvent } from '@/types/events'

// SSE 帧之间用一个空行分隔，即两个连续的 \n\n
const FRAME_DELIMITER = '\n\n'

export interface ChatStreamHandle {
  /** 中止本次 SSE 请求，触发 fetch 的 AbortSignal，同时后端 SSE 生成器
   *  会检测到断连并调用 engine.abort()。 */
  abort: () => void
  /** await 它拿到 SSE 完整结束——正常结束 resolve，abort/网络错误 reject。 */
  done: Promise<void>
}

/**
 * 打开一次 chat SSE 流。events 会按到达顺序逐个 yield 给 onEvent 回调。
 *
 * 设计取舍：不返回 AsyncIterator，因为 Vue 组件里更习惯用回调，且这样
 * abort 语义（fetch AbortController）更直接。
 */
export function streamChat(
  req: ChatRequest,
  onEvent: (event: SseEvent) => void,
): ChatStreamHandle {
  const controller = new AbortController()

  const done = (async () => {
    const resp = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req),
      signal: controller.signal,
    })

    if (!resp.ok) {
      // 后端非 SSE 场景（500/400/…）会走 JSON 分支
      const detail = await resp.text().catch(() => '')
      throw new Error(`chat failed: ${resp.status} ${detail}`)
    }
    if (!resp.body) {
      throw new Error('chat response has no body')
    }

    const reader = resp.body.getReader()
    const decoder = new TextDecoder('utf-8')
    let buffer = ''

    try {
      while (true) {
        const { value, done } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        // 按帧切分：每帧末尾是空行（\n\n）
        let frameEnd = buffer.indexOf(FRAME_DELIMITER)
        while (frameEnd !== -1) {
          const frame = buffer.slice(0, frameEnd)
          buffer = buffer.slice(frameEnd + FRAME_DELIMITER.length)
          const event = parseFrame(frame)
          if (event) onEvent(event)
          frameEnd = buffer.indexOf(FRAME_DELIMITER)
        }
      }
    } finally {
      reader.releaseLock()
    }
  })()

  return {
    abort: () => controller.abort(),
    done,
  }
}

/**
 * 解析一帧 SSE：目前只处理 `data: <json>` 行，忽略 event:/id:/retry: 等
 * 因为后端没用到它们。多行 data: 会被拼接（按 SSE 规范），但当前后端
 * 每帧都是单行，所以实际不会走到那条分支。
 */
function parseFrame(frame: string): SseEvent | null {
  const dataLines: string[] = []
  for (const line of frame.split('\n')) {
    if (line.startsWith('data: ')) {
      dataLines.push(line.slice(6))
    } else if (line.startsWith('data:')) {
      // 无空格的 data:xxx 也接受，SSE 规范允许
      dataLines.push(line.slice(5))
    }
    // 其他行（注释 `:` 开头、event:、id:）忽略
  }
  if (dataLines.length === 0) return null

  const payload = dataLines.join('\n')
  try {
    return JSON.parse(payload) as SseEvent
  } catch {
    // 无法解析的帧丢弃比抛异常更稳——个别帧损坏不该整个断流
    return null
  }
}

/**
 * POST 一个 Ask 响应回后端。ask_id 来自前面 AskEvent。
 */
export async function answerAsk(askId: string, approved: boolean): Promise<void> {
  const resp = await fetch(`/api/ask/${encodeURIComponent(askId)}/answer`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ approved }),
  })
  if (!resp.ok) {
    throw new Error(`answer failed: ${resp.status}`)
  }
}

/**
 * 主动中止某次运行。abort 后 SSE 流会自然结束（后端释放挂起 Ask + 触发
 * engine.abort()），也可以让 streamChat 的 handle.abort() 客户端断开。
 * 提供这个端点是为了"用户点停止但网络还没断"的场景。
 */
export async function abortRun(runId: string): Promise<void> {
  const resp = await fetch(`/api/chat/${encodeURIComponent(runId)}/abort`, {
    method: 'POST',
  })
  if (!resp.ok && resp.status !== 404) {
    // 404 说明这次运行已经自然结束了，忽略；其他错误抛出。
    throw new Error(`abort failed: ${resp.status}`)
  }
}
