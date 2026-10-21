"""JSON structured logging with mandatory redaction and bounded fields."""

from __future__ import annotations

import json
import logging
import re
from typing import final

from sentinelmcp.domain import OperationClass
from sentinelmcp.execution import ExecutionFailure, ExecutionResult
from sentinelmcp.telemetry.redaction import RedactionPolicy

_EVENT_NAME = re.compile(r"^[a-z][a-z0-9_.]{0,63}$")


@final
class StructuredEventLogger:
    __slots__ = ("_logger", "_redaction")

    def __init__(
        self,
        logger: logging.Logger,
        *,
        redaction: RedactionPolicy | None = None,
    ) -> None:
        if not isinstance(logger, logging.Logger):
            raise TypeError("logger must be a logging.Logger")
        self._logger = logger
        self._redaction = RedactionPolicy() if redaction is None else redaction

    def emit(self, event: str, fields: dict[str, object]) -> None:
        if not isinstance(event, str) or _EVENT_NAME.fullmatch(event) is None:
            raise ValueError("event must be a bounded dotted identifier")
        safe_fields = self._redaction.redact(fields)
        document = {"event": event, "fields": safe_fields}
        self._logger.info(
            json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        )

    def record_execution(
        self,
        result: ExecutionResult,
        operation_class: OperationClass | None,
        *,
        attempt_count: int,
    ) -> None:
        fields: dict[str, object] = {
            "attempt_count": attempt_count,
            "duration_ms": result.duration_ms,
            "execution_id": str(result.context.execution_id),
            "operation_class": ("unknown" if operation_class is None else operation_class.value),
            "outcome": (
                result.error.code.value if isinstance(result, ExecutionFailure) else "success"
            ),
            "request_id": str(result.context.request_id),
            "tool_ref": str(result.context.tool_ref),
        }
        self.emit("execution.completed", fields)
