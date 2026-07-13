<script setup lang="ts">
/**
 * 会话设置面板：权限模式下拉 + 规则列表（allow / deny / ask）编辑。
 *
 * 这些设置每次 sendPrompt 时被打包进 /api/chat 请求，后端 stateless，
 * 前端是唯一持久化点（走 useSettingsStore → localStorage）。
 *
 * 规则语法与 permissions/rules.py 的 parse_rule 对齐：
 *   Bash                — 工具级
 *   Bash(git status)    — 精确匹配子命令
 *   Bash(git:*)         — 前缀匹配 argv[0] == "git"
 */
import { ref } from 'vue'
import { NButton, NInput, NSelect, NTag, NCollapse, NCollapseItem } from 'naive-ui'
import { useSettingsStore } from '@/stores/settings'
import type { PermissionMode } from '@/types/events'

const settings = useSettingsStore()

const modeOptions: Array<{ label: string; value: PermissionMode }> = [
  { label: 'default（默认询问）', value: 'default' },
  { label: 'acceptEdits（自动允许只读）', value: 'acceptEdits' },
  { label: 'bypassPermissions（全部放行）', value: 'bypassPermissions' },
  { label: 'plan（先规划）', value: 'plan' },
  { label: 'dontAsk（拒绝所有需要询问的）', value: 'dontAsk' },
]

const newAllow = ref('')
const newDeny = ref('')
const newAsk = ref('')

function submit(kind: 'allow' | 'deny' | 'ask'): void {
  const field = kind === 'allow' ? newAllow : kind === 'deny' ? newDeny : newAsk
  settings.addRule(kind, field.value)
  field.value = ''
}
</script>

<template>
  <n-collapse class="settings-panel">
    <n-collapse-item title="权限设置" name="perms">
      <div class="section">
        <div class="label">权限模式</div>
        <n-select
          v-model:value="settings.permissionMode"
          :options="modeOptions"
          size="small"
        />
      </div>

      <div class="section">
        <div class="label">Allow 规则</div>
        <div class="rule-list">
          <n-tag
            v-for="r in settings.allowRules"
            :key="'a-' + r"
            closable
            size="small"
            type="success"
            @close="settings.removeRule('allow', r)"
          >
            {{ r }}
          </n-tag>
        </div>
        <div class="rule-input">
          <n-input
            v-model:value="newAllow"
            size="small"
            placeholder="如 Bash(git:*)"
            @keyup.enter="submit('allow')"
          />
          <n-button size="small" @click="submit('allow')">加</n-button>
        </div>
      </div>

      <div class="section">
        <div class="label">Deny 规则</div>
        <div class="rule-list">
          <n-tag
            v-for="r in settings.denyRules"
            :key="'d-' + r"
            closable
            size="small"
            type="error"
            @close="settings.removeRule('deny', r)"
          >
            {{ r }}
          </n-tag>
        </div>
        <div class="rule-input">
          <n-input
            v-model:value="newDeny"
            size="small"
            placeholder="如 Bash(rm:*)"
            @keyup.enter="submit('deny')"
          />
          <n-button size="small" @click="submit('deny')">加</n-button>
        </div>
      </div>

      <div class="section">
        <div class="label">Ask 规则</div>
        <div class="rule-list">
          <n-tag
            v-for="r in settings.askRules"
            :key="'q-' + r"
            closable
            size="small"
            type="warning"
            @close="settings.removeRule('ask', r)"
          >
            {{ r }}
          </n-tag>
        </div>
        <div class="rule-input">
          <n-input
            v-model:value="newAsk"
            size="small"
            placeholder="如 Bash(npm publish:*)"
            @keyup.enter="submit('ask')"
          />
          <n-button size="small" @click="submit('ask')">加</n-button>
        </div>
      </div>
    </n-collapse-item>
  </n-collapse>
</template>

<style scoped>
.settings-panel {
  padding: 0 8px;
}
.settings-panel :deep(.n-collapse-item__header-main) {
  font-size: 12px;
  color: #b9bec9;
}
.section {
  margin-bottom: 12px;
}
.section:last-child {
  margin-bottom: 0;
}
.label {
  font-size: 11px;
  color: #6a7280;
  margin-bottom: 6px;
}
.rule-list {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-bottom: 6px;
  min-height: 4px;
}
.rule-input {
  display: flex;
  gap: 4px;
}
</style>
