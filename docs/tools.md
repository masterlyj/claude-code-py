# 内置工具说明

本文档描述 `tools/` 目录下各内置工具的职责、接口和使用规范。

---

## 工具清单

| 工具类 | 工具名（API name） | 文件 | 职责 |
|--------|-------------------|------|------|
| `BashTool` | `Bash` | `tools/bash.py` | 执行 shell 命令 |
| `FileReadTool` | `Read` | `tools/file_read.py` | 读取文件内容 |
| `FileEditTool` | `Edit` | `tools/file_edit.py` | 精确替换文件片段 |
| `FileWriteTool` | `Write` | `tools/file_write.py` | 创建或覆盖整个文件 |

工具名（`name` 属性）是模型调用时使用的标识符，与 API `tool_use` 块中的 `name` 字段一一对应。

---

## Bash

执行本地 shell 命令，stdout 和 stderr 合并返回。

**输入参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `command` | string | 是 | 要执行的 shell 命令 |
| `timeout` | integer | 否 | 超时秒数，最大 60，默认 30 |

**平台行为**

- Windows：使用 `powershell -Command`
- Linux / macOS：使用 `bash -c`

**实现细节**

优先使用 `asyncio.create_subprocess_exec` 实现逐行流式输出。Windows `SelectorEventLoop`（如 Jupyter）不支持异步子进程时，自动降级为 `subprocess.run` + `run_in_executor`（一次性返回全部输出）。

**对应原版**

`packages/builtin-tools/src/tools/BashTool/BashTool.tsx`（原版含沙箱、子命令级权限，Python 版暂未实现）

---

## Read

读取本地文件内容，返回带行号的文本。

**输入参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file_path` | string | 是 | 文件路径（绝对或相对） |
| `start_line` | integer | 否 | 起始行号（从 1 开始） |
| `end_line` | integer | 否 | 结束行号（含） |

**输出格式**

每行格式为 `行号\t内容`，例如：

```
1	def hello():
2	    print("hello")
```

**限制**

- 文件大小上限：256 KB
- 超过大小限制时报错，提示使用 `start_line` / `end_line` 分段读取

**对应原版**

`packages/builtin-tools/src/tools/FileReadTool/FileReadTool.ts`（原版支持 PDF、图片、Jupyter，Python 版暂只支持文本文件）

---

## Edit

通过精确字符串替换修改文件内容（str_replace 模式）。

**输入参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file_path` | string | 是 | 要编辑的文件路径 |
| `old_string` | string | 是 | 待替换的原始文本（空字符串表示创建/覆盖文件） |
| `new_string` | string | 是 | 替换后的新文本 |
| `replace_all` | boolean | 否 | 是否替换全部匹配，默认 false |

**行为规则**

- `old_string` 必须与文件内容完全匹配（含空格、缩进、换行）
- 默认模式（`replace_all=false`）要求 `old_string` 在文件中**唯一**，多处匹配时报错，防止在错误位置修改
- `old_string` 为空字符串时，将 `new_string` 写入整个文件（等同于 Write 工具）
- `replace_all=true` 时替换所有匹配项

**对应原版**

`packages/builtin-tools/src/tools/FileEditTool/FileEditTool.ts`

---

## Write

创建新文件或覆盖已有文件的全部内容。

**输入参数**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file_path` | string | 是 | 要写入的文件路径 |
| `content` | string | 是 | 要写入的完整内容 |

**行为规则**

- 文件不存在时自动创建，包括中间目录
- 文件已存在时直接覆盖（无确认）
- 输出提示"已创建"或"已覆盖"

**与 Edit 的区别**

| | Write | Edit |
|-|-------|------|
| 适用场景 | 创建新文件、完全重写 | 局部修改已有文件 |
| 操作粒度 | 整个文件 | 指定文本片段 |
| 安全性 | 直接覆盖 | 要求唯一匹配，更安全 |

**对应原版**

`packages/builtin-tools/src/tools/FileWriteTool/FileWriteTool.ts`

---

## 工具注册表

`tools/registry.py` 提供统一的工具管理接口：

```python
from tools.registry import get_tools, find_tool

# 获取全部内置工具
tools = get_tools()  # [BashTool, FileReadTool, FileEditTool, FileWriteTool]

# 按名称查找
tool = find_tool("Bash")          # 返回 BashTool 实例
tool = find_tool("NotExist")      # 返回 None
tool = find_tool("Read", tools)   # 在指定列表中查找
```

---

## 工具封装规范

### API tools 参数（当前实现）

所有工具通过 `to_api_schema()` 方法序列化为 Anthropic API 的 `tools` 参数格式：

```python
{
    "name": "Bash",
    "description": "...",
    "input_schema": {...}
}
```

这是官方推荐的结构化工具调用方式，模型在约束解码下生成，输出稳定可靠。

### Prompt Caching（待实现）

工具 schema 是静态内容，每次请求重复传递会浪费 token。后续在 `core/query.py` 中对 `tools` 参数加 `cache_control`，利用 Anthropic Prompt Caching 降低成本：

```python
# 二期：在 _query_loop 中对工具 schema 加缓存标记
# 将固定的 tools 放在 cache_control breakpoint 之前
```

**注意**：动态变化的工具集会破坏缓存前缀，建议工具列表在会话内保持不变。

---

## 待实现工具（二期）

| 工具 | 对应原版 | 说明 |
|------|---------|------|
| `GlobTool` | `GlobTool/GlobTool.ts` | 文件模式匹配 |
| `GrepTool` | `GrepTool/GrepTool.ts` | 文件内容搜索 |
| `WebFetchTool` | `WebFetchTool/WebFetchTool.ts` | HTTP 请求 |
| `WebSearchTool` | `WebSearchTool/WebSearchTool.ts` | 网络搜索 |
| `TaskCreateTool` | `TaskCreateTool/` | 任务追踪 |
| `AgentTool` | `AgentTool/AgentTool.ts` | 子 Agent |
