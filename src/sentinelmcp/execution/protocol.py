"""Transport-neutral execution ports."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from sentinelmcp.execution.models import AdapterInvocation, AdapterResult


@runtime_checkable
class ToolAdapter(Protocol):
    """Dispatch one immutable invocation through a configured runtime transport."""

    async def execute(self, invocation: AdapterInvocation) -> AdapterResult:
        """Return a normalized result without retrying."""
