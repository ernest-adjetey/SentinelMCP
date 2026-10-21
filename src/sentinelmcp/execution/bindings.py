"""Mutable operational routing kept separate from immutable tool contracts."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Protocol, final, runtime_checkable

from sentinelmcp.domain.tools import ToolRef
from sentinelmcp.execution.protocol import ToolAdapter


class BindingNotFoundError(LookupError):
    """Raised when an exact tool version has no runtime adapter binding."""

    def __init__(self, tool_ref: ToolRef) -> None:
        self.tool_ref = tool_ref
        super().__init__(f"Runtime binding not found: {tool_ref}")


@dataclass(frozen=True, slots=True)
class RuntimeToolBinding:
    """Operational association between one exact contract and one adapter."""

    tool_ref: ToolRef
    adapter: ToolAdapter

    def __post_init__(self) -> None:
        if not isinstance(self.tool_ref, ToolRef):
            raise TypeError("tool_ref must be a ToolRef")
        if not isinstance(self.adapter, ToolAdapter):
            raise TypeError("adapter must satisfy ToolAdapter")


@runtime_checkable
class RuntimeBindingResolver(Protocol):
    """Resolve the current operational route for one exact tool version."""

    def resolve(self, tool_ref: ToolRef) -> RuntimeToolBinding:
        """Return a binding or raise ``BindingNotFoundError``."""


@final
class InMemoryRuntimeBindingRegistry(RuntimeBindingResolver):
    """Thread-safe process-local runtime binding registry."""

    __slots__ = ("_bindings", "_lock")

    def __init__(self) -> None:
        self._bindings: dict[ToolRef, RuntimeToolBinding] = {}
        self._lock = RLock()

    def bind(self, binding: RuntimeToolBinding) -> None:
        """Atomically add or replace mutable operational routing state."""

        with self._lock:
            self._bindings[binding.tool_ref] = binding

    def resolve(self, tool_ref: ToolRef) -> RuntimeToolBinding:
        with self._lock:
            binding = self._bindings.get(tool_ref)
            if binding is None:
                raise BindingNotFoundError(tool_ref)
            return binding

    def unbind(self, tool_ref: ToolRef) -> None:
        """Remove an operational route without changing the tool contract."""

        with self._lock:
            self._bindings.pop(tool_ref, None)

    def list_bindings(self) -> tuple[RuntimeToolBinding, ...]:
        with self._lock:
            return tuple(self._bindings[tool_ref] for tool_ref in sorted(self._bindings))
