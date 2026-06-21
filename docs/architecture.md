# 项目说明

用 Python 从零实现一个 LLM Agent 系统，深入理解 AI 编程助手的架构原理。

## 项目背景

本项目是一个学习型复现工程，目标是通过动手实现来理解 LLM Agent 系统的核心设计：**如何让语言模型通过工具调用递归地完成复杂任务**。

## 核心概念：Agent 循环

LLM Agent 的本质是一个循环：

```
用户输入 → 调用 LLM → 模型决定用哪个工具 → 执行工具 → 把结果返回给模型 → 继续循环
```

直到模型认为任务完成，不再需要工具，才输出最终答案。

```python
async def query(messages, options):
    while True:
        # 1. 流式调用 LLM
        response = await call_llm(messages, options)

        # 2. 如果模型不需要工具，结束
        if response.stop_reason != "tool_use":
            break

        # 3. 执行模型请求的工具
        tool_results = await execute_tools(response.tool_calls)

        # 4. 把工具结果加入对话，继续下一轮
        messages.append(tool_results)
```

## 架构

```
claude-code-py/
  core/         # 核心循环：query.py（循环）、engine.py（编排，待实现）
  tools/        # 工具系统：base.py（接口）、bash.py、file_read.py 等
  permissions/  # 权限系统：哪些工具可以在什么条件下执行
  api/          # FastAPI 服务：SSE 流式推送给前端（待实现）
  notebooks/    # Jupyter：逐步验证每个模块
  tests/        # pytest：回归测试
  docs/         # 文档：架构说明、工具接口、TS↔Python 对照
```

## 实现进度

| 模块 | 文件 | 状态 |
|------|------|------|
| 核心循环 | `core/query.py` | ✅ 已实现并验证 |
| 工具基类 | `tools/base.py` | ✅ 已实现 |
| Bash 工具 | `tools/bash.py` | ✅ 已实现并验证 |
| 文件读取 | `tools/file_read.py` | ✅ 已实现并验证 |
| 文件编辑 | `tools/file_edit.py` | ✅ 已实现并验证 |
| 文件写入 | `tools/file_write.py` | ✅ 已实现并验证 |
| 工具注册表 | `tools/registry.py` | ✅ 已实现 |
| 权限规则 | `permissions/rules.py` | ✅ 已实现并验证 |
| 权限管理器 | `permissions/manager.py` | ✅ 已实现并验证 |
| 会话编排 | `core/engine.py` | 🔲 待实现 |
| FastAPI 入口 | `api/main.py` | 🔲 待实现 |
| Vue 前端 | `frontend/` | 🔲 待实现 |

## 技术栈

- **Python 3.13** + **uv**（依赖管理）
- **Anthropic Python SDK**（LLM 调用）
- **FastAPI**（API 服务 + SSE 流式响应）
- **Pydantic**（数据模型）
- **JupyterLab**（交互式调试）
- **pytest + pytest-asyncio**（测试）

## 快速开始

```bash
# 克隆后安装依赖
uv sync

# 运行测试（39 个，全绿）
uv run pytest

# 启动 Jupyter 逐步调试
uv run jupyter lab

# 启动 API 服务（待实现后可用）
uv run uvicorn api.main:app --reload
```

## 学习路径

| 步骤 | 文件 | 学到什么 | 状态 |
|------|------|---------|------|
| 1 | `core/query.py` | Agent 循环的核心：如何递归处理工具调用 | ✅ |
| 2 | `tools/base.py` → `tools/bash.py` | 工具的抽象接口与具体实现 | ✅ |
| 3 | `permissions/rules.py` + `manager.py` | 权限规则解析与五步决策流水线 | ✅ |
| 4 | `tools/registry.py` | 工具注册与查找机制 | ✅ |
| 5 | `core/engine.py` | 会话状态管理和权限中间件 | 🔲 |
| 6 | `api/main.py` | 流式事件通过 SSE 推送到前端 | 🔲 |
