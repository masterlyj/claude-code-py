# claude-code-py

用 Python 重新实现一个 AI 编程助手 CLI 的核心架构，目的是通过逐模块复现来深入理解 LLM Agent 系统的设计原理。

## 学习目标

- 理解 Agent 核心循环：LLM 如何通过工具调用递归驱动任务执行
- 掌握 FastAPI 流式响应（SSE）的后端实现方式
- 理解权限系统、会话管理和上下文处理的设计思路
- 每个模块独立可验证：Jupyter 逐步调试 + pytest 回归测试

## 架构总览

```
用户输入
    ↓
QueryEngine          ← 高层编排，管理会话状态（core/engine.py）
    ↓
query()              ← 核心循环：流式调用 API → 检测 tool_use → 递归（core/query.py）
    ↓
ToolExecutor         ← 查找工具 → 检查权限 → 执行（tools/）
    ↓
FastAPI + SSE        ← 将事件流推送给前端（api/）
    ↓
Vue 前端             ← 实时渲染消息（frontend/ — 后期）
```

## 核心循环（整个系统的灵魂）

```python
async def query(messages, options):
    while True:
        async with client.messages.stream(...) as stream:
            async for event in stream:
                yield event          # 通过 SSE 推送给前端

        if stop_reason != "tool_use":
            break                    # 对话结束，退出循环

        tool_results = await execute_tools(...)
        messages.append({"role": "user", "content": tool_results})
        # 继续循环，把工具结果发回给模型
```

## 目录结构

```
claude-code-py/
  core/
    query.py          # 核心流式循环 + 工具调用递归（已实现）
    engine.py         # QueryEngine：会话状态 + 权限中间件（待实现）
  tools/
    base.py           # BaseTool 抽象基类 + ToolUseContext（已实现）
    bash.py           # BashTool（已实现）
    file_read.py      # FileReadTool（已实现）
    file_edit.py      # FileEditTool（已实现）
    file_write.py     # FileWriteTool（已实现）
    registry.py       # 工具注册表 get_tools() / find_tool()（已实现）
  permissions/
    manager.py        # 五步权限决策流水线（已实现）
    rules.py          # 规则类型 + 解析（支持 Bash(git:*) 通配符）（已实现）
  api/
    main.py           # FastAPI 入口 + SSE 端点（待实现）
  notebooks/
    01_query_loop.ipynb     # 验证核心循环 + 工具调用（已验证）
  tests/
    conftest.py             # pytest fixture（bypass_ctx）
    test_permissions.py     # 权限系统测试（16 个）
    test_tools.py           # 工具测试（15 个）
    test_registry.py        # 注册表测试（8 个）
  docs/
    architecture.md   # 架构说明（公开）
    tools.md          # 工具接口文档（公开）
    mapping.md        # TS ↔ Python 对照表（本地，不提交）
```

## 技术栈

| 组件 | 选型 | 原因 |
|------|------|------|
| 运行时 | Python 3.12+ | 原生异步，LLM SDK 支持好 |
| 依赖管理 | uv | 快速、现代 |
| API 框架 | FastAPI | 异步、支持 SSE、Pydantic 集成 |
| LLM SDK | anthropic | 官方 Python SDK |
| 测试 | pytest + pytest-asyncio | 标准、支持异步 |
| 调试验证 | JupyterLab | 逐步运行验证每个函数 |

## 快速开始

```bash
# 安装依赖
uv sync

# 运行测试
uv run pytest

# 启动 Jupyter 逐步调试
uv run jupyter lab

# 启动 API 服务（api/main.py 待实现后可用）
# uv run uvicorn api.main:app --reload
```

## 学习路径

| 步骤 | 文件 | 学到什么 | 状态 |
|------|------|---------|------|
| 1 | `core/query.py` | Agent 循环的核心：如何递归处理工具调用 | ✅ 已实现 |
| 2 | `tools/base.py` + `tools/bash.py` | 工具的定义方式和执行机制 | ✅ 已实现 |
| 3 | `permissions/manager.py` | 权限系统：规则引擎和模式决策 | ✅ 已实现 |
| 4 | `core/engine.py` | 会话状态管理和权限中间件 | 🔲 待实现 |
| 5 | `api/main.py` | 流式事件通过 SSE 推送到前端 | 🔲 待实现 |
