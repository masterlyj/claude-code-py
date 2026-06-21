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
    query.py          # 核心流式循环 + 工具调用递归
    engine.py         # QueryEngine：会话状态 + 权限中间件
  tools/
    base.py           # BaseTool 抽象基类 + ToolUseContext
    bash.py           # BashTool
    file_read.py      # FileReadTool
    file_edit.py      # FileEditTool
    registry.py       # 工具注册表 + 查找
  permissions/
    manager.py        # 规则引擎 + 权限模式
    rules.py          # 规则匹配（支持 Bash(git:*) 通配符）
  api/
    main.py           # FastAPI 入口 + SSE 端点
    schemas.py        # Pydantic 请求/响应模型
  notebooks/
    01_query_loop.ipynb     # 逐步验证核心循环
    02_tool_execution.ipynb # 逐步验证各工具
    03_permissions.ipynb    # 验证规则匹配逻辑
  tests/
    test_query.py
    test_tools.py
    test_permissions.py
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

# 启动 API 服务
uv run uvicorn api.main:app --reload

# 运行测试
uv run pytest

# 启动 Jupyter
uv run jupyter lab
```

## 学习路径

1. **`core/query.py`** — 从这里开始，递归工具循环是核心。
2. **`tools/base.py`** → `tools/bash.py` — 理解工具的定义和执行方式。
3. **`permissions/manager.py`** — 系统如何决定哪些工具可以运行。
4. **`core/engine.py`** — 会话状态与查询循环如何连接。
5. **`api/main.py`** — 流式事件如何通过 SSE 推送到前端。
