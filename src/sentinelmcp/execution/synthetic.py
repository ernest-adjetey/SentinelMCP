"""Deterministic in-process adapter for public synthetic tools."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import final

from sentinelmcp.domain.json_values import FrozenJSONMapping, JSONValueError
from sentinelmcp.execution.models import (
    AdapterFailure,
    AdapterInvocation,
    AdapterResult,
    AdapterSuccess,
    ExecutionErrorCode,
)

type SyntheticHandler = Callable[[FrozenJSONMapping], Awaitable[object]]


@final
class SyntheticToolAdapter:
    """Invoke one async in-process handler without bypassing executor validation."""

    __slots__ = ("_handler",)

    def __init__(self, handler: SyntheticHandler) -> None:
        if not callable(handler):
            raise TypeError("handler must be callable")
        self._handler = handler

    async def execute(self, invocation: AdapterInvocation) -> AdapterResult:
        try:
            output = await self._handler(invocation.arguments)
            return AdapterSuccess(output)
        except JSONValueError:
            return AdapterFailure(ExecutionErrorCode.OUTPUT_VALIDATION_FAILED)
        except Exception:
            return AdapterFailure(ExecutionErrorCode.ADAPTER_FAILURE)
