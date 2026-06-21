"""工具系统的抽象基类与上下文类型定义。

本模块定义工具的统一接口（BaseTool）和工具执行时所需的上下文（ToolUseContext）。
所有具体工具均继承 BaseTool，通过 execute() 方法实现各自逻辑。

对外暴露：
  BaseTool       — 所有工具必须实现的抽象基类
  ToolUseContext — 工具执行时的运行时上下文
  ValidationResult — 输入校验结果
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, AsyncIterator

from pydantic import BaseModel

if TYPE_CHECKING:
    from permissions.manager import PermissionManager


# ── 校验结果 ──────────────────────────────────────────────────────────────


class ValidationResult(BaseModel):
    """工具输入校验的结果。

    Attributes:
        ok: 校验是否通过。
        message: 校验失败时的错误描述，通过时为空字符串。
        error_code: 错误码，通过时为 0。
    """

    ok: bool
    message: str = ""
    error_code: int = 0

    @classmethod
    def passed(cls) -> ValidationResult:
        """构建一个校验通过的结果。"""
        return cls(ok=True)

    @classmethod
    def failed(cls, message: str, error_code: int = 1) -> ValidationResult:
        """构建一个校验失败的结果。

        Args:
            message: 失败原因描述。
            error_code: 错误码，默认为 1。

        Returns:
            ok=False 的 ValidationResult 实例。
        """
        return cls(ok=False, message=message, error_code=error_code)


# ── 工具执行上下文 ────────────────────────────────────────────────────────


@dataclass
class ToolUseContext:
    """工具执行时的运行时上下文，由 query 循环注入。

    包含工具执行所需的全局状态：权限管理器、会话配置、
    以及取消信号等。使用 dataclass 而非 Pydantic，
    因为它是内部可变状态容器，不需要序列化或外部校验。

    Args:
        permission_manager: 执行前的权限决策器。
        model: 当前使用的模型 ID。
        tools: 本轮可用的工具列表，工具间互相调用时需要。
        session_id: 当前会话唯一标识。
        is_non_interactive: 是否为非交互模式（如批处理、API 调用）。
        extra: 扩展字段，供各工具存放自定义状态，避免频繁修改本类。
    """

    permission_manager: PermissionManager
    model: str
    tools: list[BaseTool]
    session_id: str
    is_non_interactive: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


# ── 抽象基类 ──────────────────────────────────────────────────────────────


class BaseTool(ABC):
    """所有工具必须实现的抽象基类。

    定义工具的核心契约：名称、描述、输入 schema 和执行方法。
    工具执行结果可以是普通协程（一次性返回）或异步生成器（流式返回），
    调用方通过 execute() 统一调用，无需关心具体返回形式。

    子类必须实现：
      name        — 工具唯一标识
      description — 工具功能描述，会注入给模型
      input_schema — JSON Schema 格式的参数定义
      execute()   — 执行逻辑

    子类可选覆盖：
      validate_input()    — 输入校验，默认直接通过
      is_read_only()      — 是否只读，影响权限决策
      is_enabled()        — 是否在当前上下文中启用
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """工具唯一标识，对应 API tool_use 块中的 name 字段。"""

    @property
    @abstractmethod
    def description(self) -> str:
        """工具功能描述，会注入到模型 prompt 中影响工具选择。"""

    @property
    @abstractmethod
    def input_schema(self) -> dict[str, Any]:
        """工具参数的 JSON Schema 定义，type 必须为 "object"。"""

    @abstractmethod
    def execute(
        self,
        tool_input: dict[str, Any],
        context: ToolUseContext,
    ) -> AsyncIterator[str]:
        """执行工具并以异步生成器形式返回结果。

        子类可以 yield 多个片段（流式）或 yield 一次后 return（一次性）。
        调用方统一通过 async for 消费，不区分两种形式。

        Args:
            tool_input: 模型传入的参数，结构由 input_schema 定义。
            context: 工具执行时的运行时上下文。

        Yields:
            结果文本片段，多次 yield 时调用方会拼接。

        Raises:
            Exception: 执行失败时抛出，调用方捕获后作为 is_error=True 的结果处理。
        """

    async def validate_input(
        self,
        tool_input: dict[str, Any],
    ) -> ValidationResult:
        """校验工具输入的合法性，默认直接通过。

        子类可覆盖此方法添加参数校验逻辑，校验失败时
        query 循环不会执行工具，而是将失败原因返回给模型。

        Args:
            tool_input: 待校验的工具参数。

        Returns:
            ValidationResult，ok=True 表示校验通过。
        """
        return ValidationResult.passed()

    def is_read_only(self, tool_input: dict[str, Any]) -> bool:
        """返回本次调用是否为只读操作，默认为 False（假设有写操作）。

        权限管理器在 auto 模式下会参考此值做决策，
        只读工具通常可以跳过用户确认。

        Args:
            tool_input: 工具参数，部分工具的读写性依赖于参数内容。

        Returns:
            True 表示只读，False 表示可能有写操作。
        """
        return False

    def is_enabled(self, context: ToolUseContext) -> bool:
        """返回此工具在当前上下文中是否启用，默认为 True。

        可用于根据模型能力、权限模式、会话类型等动态禁用工具。

        Args:
            context: 工具执行时的运行时上下文。

        Returns:
            True 表示工具可用，False 时工具注册表会过滤掉该工具。
        """
        return True

    def to_api_schema(self) -> dict[str, Any]:
        """将工具转换为 Anthropic API 所需的 tool schema 格式。

        Returns:
            包含 name、description、input_schema 的字典。
        """
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r})"
