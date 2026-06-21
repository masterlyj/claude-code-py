# 测试说明

本文档说明测试的组织方式、运行方法和新增规范。

## 运行测试

```bash
# 运行全部测试
uv run pytest

# 运行单个文件
uv run pytest tests/test_tools.py

# 运行单个用例
uv run pytest tests/test_tools.py::test_file_read_line_range

# 显示详细输出
uv run pytest -v

# 显示标准输出（调试时有用）
uv run pytest -s
```

当前测试数量：**39 个，全部通过**。

---

## 测试文件职责

| 文件 | 测试对象 | 用例数 |
|------|---------|-------|
| `tests/conftest.py` | 公共 fixture | — |
| `tests/test_permissions.py` | `permissions/rules.py` + `permissions/manager.py` | 16 |
| `tests/test_tools.py` | `tools/bash.py` + `tools/file_read.py` + `tools/file_edit.py` + `tools/file_write.py` | 15 |
| `tests/test_registry.py` | `tools/registry.py` | 8 |

### conftest.py

提供全项目共享的 pytest fixture：

- `bypass_ctx` — 返回 `PermissionMode.BYPASS` 的 `ToolUseContext`，工具测试直接使用，不需要每个测试自己构建上下文

### test_permissions.py

覆盖权限系统的两层：

- **规则解析**：`parse_rule` / `rule_to_string` 的各种格式（纯工具名、带内容、通配符、转义括号）
- **权限决策**：`PermissionManager.check()` 的六种场景（bypass、deny 规则、allow 规则、default 无规则、dont_ask、plan 模式）
- 额外覆盖：deny 优先于 allow、`update_context` 实时生效

### test_tools.py

每个工具覆盖 schema 正确性 + 执行边界场景：

- **schema 测试**：验证工具名、必填字段（不涉及 I/O，极快）
- **FileReadTool**：正常读取带行号、行范围切片、文件不存在、范围越界
- **FileWriteTool**：创建新文件、覆盖已有文件
- **FileEditTool**：正常替换、old_string 不存在、多处匹配报错、replace_all、old_string 为空创建文件

所有涉及磁盘操作的测试使用 pytest 内置的 `tmp_path` fixture，测试结束自动清理。

### test_registry.py

覆盖 `get_tools()` 和 `find_tool()` 的行为：

- 返回工具数量和名称集合
- 按名称查找（找到 / 找不到 / 自定义列表 / 大小写敏感 / 第一个匹配）

---

## 异步测试说明

项目配置了 `asyncio_mode = "auto"`（见 `pyproject.toml`），所有 `async def test_xxx` 函数自动被 pytest-asyncio 处理，**不需要** `@pytest.mark.asyncio` 装饰器。

```python
# 正确写法
async def test_file_read_existing_file(tmp_path, bypass_ctx):
    ...

# 不需要这样写
@pytest.mark.asyncio
async def test_file_read_existing_file(tmp_path, bypass_ctx):
    ...
```

收集异步生成器输出的通用模式：

```python
async def collect(gen) -> str:
    chunks = []
    async for chunk in gen:
        chunks.append(chunk)
    return "".join(chunks)

# 使用
output = await collect(tool.execute(tool_input, ctx))
assert "已创建" in output
```

---

## 新增测试规范

### 命名

- 文件：`tests/test_<模块名>.py`
- 用例：`test_<行为描述>`，描述的是**期望行为**，不是实现细节

```python
# 好
def test_file_read_not_found():
def test_manager_deny_rule_blocks():

# 不好
def test_read_raises_exception():
def test_check_returns_deny_decision():
```

### 结构

每个用例只测一件事，用 `assert` 明确期望值：

```python
async def test_file_write_creates_new_file(tmp_path, bypass_ctx):
    """新文件写入后应存在且内容正确。"""
    tool = FileWriteTool()
    path = tmp_path / "new.txt"

    output = await collect(tool.execute({"file_path": str(path), "content": "hello"}, bypass_ctx))

    assert path.exists()
    assert path.read_text() == "hello"
    assert "已创建" in output
```

### 避免的做法

- 不在测试里写业务逻辑（用 `bypass_ctx` 而不是自己构建权限上下文）
- 不 mock 被测模块本身（只 mock 外部依赖，如网络、文件系统用 `tmp_path` 代替）
- 不在测试文件里留临时文件（用 `tmp_path`）
