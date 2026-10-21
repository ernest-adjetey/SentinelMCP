"""Replay target policies evaluated immediately before dispatch."""

from typing import Protocol, final, runtime_checkable

from sentinelmcp.domain import OperationClass, ToolDefinition
from sentinelmcp.execution import RuntimeToolBinding, SyntheticToolAdapter

REPLAY_SAFE_CAPABILITY = "replay-safe"


@runtime_checkable
class ReplayTargetPolicy(Protocol):
    def allows(self, definition: ToolDefinition, binding: RuntimeToolBinding) -> bool:
        """Return whether the exact current binding is safe for replay."""


@final
class SyntheticReplayTargetPolicy(ReplayTargetPolicy):
    """Allow only explicitly marked read tools backed by in-process synthetic adapters."""

    def allows(self, definition: ToolDefinition, binding: RuntimeToolBinding) -> bool:
        return (
            definition.operation_class is OperationClass.READ
            and REPLAY_SAFE_CAPABILITY in definition.capabilities
            and isinstance(binding.adapter, SyntheticToolAdapter)
        )
