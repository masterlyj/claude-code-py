<script setup lang="ts">
/**
 * 对话流容器：把 currentSession.timeline 渲染成一串消息组件。
 *
 * 关键行为：
 *   - 新消息追加时自动滚到底部（但用户主动向上滚动过就暂停自动滚动，
 *     避免抢用户视线；再次到达底部时恢复）
 *   - 用 TransitionGroup 让新增消息淡入
 */
import { computed, nextTick, ref, watch } from 'vue'
import { useSessionStore } from '@/stores/sessions'
import { useChatStore } from '@/stores/chat'
import UserMessage from './UserMessage.vue'
import AssistantMessage from './AssistantMessage.vue'
import ToolUseCard from './ToolUseCard.vue'
import ToolResultCard from './ToolResultCard.vue'
import AskConfirmCard from './AskConfirmCard.vue'

const sessionStore = useSessionStore()
const chatStore = useChatStore()
const container = ref<HTMLElement | null>(null)
const followBottom = ref(true)

const timeline = computed(() => sessionStore.currentSession?.timeline ?? [])

const EXAMPLE_PROMPTS = [
  '列出当前目录下的所有 .py 文件',
  '读取 README.md 前 20 行',
  '解释一下 core/query.py 里的 Agent 循环',
]

function useExample(prompt: string): void {
  if (chatStore.canSend) {
    void chatStore.sendPrompt(prompt)
  }
}

// 是否处于"接近底部"的状态。用 32px 阈值给用户一点缓冲。
function isNearBottom(el: HTMLElement): boolean {
  return el.scrollHeight - el.scrollTop - el.clientHeight < 32
}

function onScroll(): void {
  const el = container.value
  if (!el) return
  followBottom.value = isNearBottom(el)
}

async function scrollToBottom(): Promise<void> {
  await nextTick()
  const el = container.value
  if (!el) return
  el.scrollTop = el.scrollHeight
}

// 每当 timeline 变化，如果之前在底部就继续跟随；否则不打扰用户
watch(
  () => timeline.value.length,
  () => {
    if (followBottom.value) void scrollToBottom()
  },
)

// 切会话时重置为跟随
watch(
  () => sessionStore.currentSessionId,
  () => {
    followBottom.value = true
    void scrollToBottom()
  },
)
</script>

<template>
  <div class="message-stream" ref="container" @scroll="onScroll">
    <div v-if="timeline.length === 0" class="empty">
      <div class="empty-title">在下方输入框开始一个新的对话</div>
      <div class="empty-subtitle">或试试这些示例：</div>
      <div class="examples">
        <button
          v-for="p in EXAMPLE_PROMPTS"
          :key="p"
          class="example-btn"
          @click="useExample(p)"
        >
          {{ p }}
        </button>
      </div>
    </div>
    <template v-for="item in timeline" :key="item.id">
      <UserMessage
        v-if="item.kind === 'user'"
        :text="item.text ?? ''"
      />
      <AssistantMessage
        v-else-if="item.kind === 'assistant_text'"
        :text="item.text ?? ''"
      />
      <ToolUseCard
        v-else-if="item.kind === 'tool_use'"
        :tool-name="item.tool_name ?? ''"
        :tool-input="item.tool_input ?? {}"
      />
      <ToolResultCard
        v-else-if="item.kind === 'tool_result'"
        :tool-name="item.tool_name ?? ''"
        :content="item.content ?? ''"
        :is-error="item.is_error ?? false"
      />
      <AskConfirmCard
        v-else-if="item.kind === 'ask'"
        :ask-id="item.ask_id ?? ''"
        :tool-name="item.tool_name ?? ''"
        :tool-input="item.tool_input ?? {}"
        :reason="item.reason ?? ''"
        :state="item.ask_state ?? 'pending'"
      />
      <div
        v-else-if="item.kind === 'error'"
        class="error-line"
      >
        [错误] {{ item.text }}
      </div>
    </template>
  </div>
</template>

<style scoped>
.message-stream {
  flex: 1;
  overflow-y: auto;
  padding: 16px 24px;
  scroll-behavior: smooth;
}
.empty {
  padding: 48px 24px;
  text-align: center;
  color: #7a8296;
  font-size: 14px;
}
.empty-title {
  color: #b9bec9;
  margin-bottom: 4px;
}
.empty-subtitle {
  font-size: 12px;
  color: #6a7280;
  margin-bottom: 12px;
}
.examples {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
}
.example-btn {
  padding: 8px 16px;
  background: #1c2130;
  border: 1px solid #2a3040;
  border-radius: 20px;
  color: #b9bec9;
  cursor: pointer;
  font-size: 12px;
  transition: background 0.1s, border-color 0.1s;
}
.example-btn:hover {
  background: #263041;
  border-color: #3a4560;
}
.error-line {
  max-width: 780px;
  margin: 8px 0;
  padding: 8px 12px;
  color: #e08894;
  background: #221217;
  border-left: 3px solid #d1717c;
  border-radius: 4px;
  font-size: 13px;
}
</style>
