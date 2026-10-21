"""Construct payload-free audit events from trusted execution values."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol, final, runtime_checkable

from sentinelmcp.audit.models import (
    AuditEvent,
    AuditEventId,
    AuditEventKind,
    AuditOutcome,
)
from sentinelmcp.audit.protocol import AuditStore
from sentinelmcp.domain import OperationClass
from sentinelmcp.execution import (
    ExecutionContext,
    ExecutionErrorCode,
    ExecutionFailure,
    ExecutionResult,
)
from sentinelmcp.policy import Principal


@runtime_checkable
class AuditRecorder(Protocol):
    def record_preview(
        self,
        *,
        principal: Principal,
        context: ExecutionContext,
    ) -> AuditEvent | None:
        """Persist one successfully created mutation preview fact."""

    def record_intent(
        self,
        *,
        kind: AuditEventKind,
        principal: Principal,
        context: ExecutionContext,
        operation_class: OperationClass,
    ) -> AuditEvent | None:
        """Persist a pre-dispatch audit fact."""

    def record_result(
        self,
        *,
        kind: AuditEventKind,
        principal: Principal,
        result: ExecutionResult,
        operation_class: OperationClass | None,
        attempt_count: int,
    ) -> AuditEvent | None:
        """Persist one normalized terminal outcome."""


@final
class NoOpAuditRecorder(AuditRecorder):
    def record_preview(
        self,
        *,
        principal: Principal,
        context: ExecutionContext,
    ) -> None:
        del principal, context

    def record_intent(
        self,
        *,
        kind: AuditEventKind,
        principal: Principal,
        context: ExecutionContext,
        operation_class: OperationClass,
    ) -> None:
        del kind, principal, context, operation_class

    def record_result(
        self,
        *,
        kind: AuditEventKind,
        principal: Principal,
        result: ExecutionResult,
        operation_class: OperationClass | None,
        attempt_count: int,
    ) -> None:
        del kind, principal, result, operation_class, attempt_count


@final
class DurableAuditRecorder(AuditRecorder):
    __slots__ = ("_event_id_factory", "_store", "_wall_clock")

    def __init__(
        self,
        store: AuditStore,
        *,
        event_id_factory: Callable[[], AuditEventId] = AuditEventId.generate,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not isinstance(store, AuditStore):
            raise TypeError("store must implement AuditStore")
        self._store = store
        self._event_id_factory = event_id_factory
        self._wall_clock = wall_clock

    def record_preview(
        self,
        *,
        principal: Principal,
        context: ExecutionContext,
    ) -> AuditEvent:
        event = self._event(
            kind=AuditEventKind.MUTATION_PREVIEWED,
            principal=principal,
            context=context,
            operation_class=OperationClass.MUTATION,
            outcome=AuditOutcome.SUCCEEDED,
            duration_ms=0.0,
            attempt_count=0,
        )
        self._store.append(event)
        return event

    def record_intent(
        self,
        *,
        kind: AuditEventKind,
        principal: Principal,
        context: ExecutionContext,
        operation_class: OperationClass,
    ) -> AuditEvent:
        event = self._event(
            kind=kind,
            principal=principal,
            context=context,
            operation_class=operation_class,
            outcome=AuditOutcome.ATTEMPTED,
        )
        self._store.append(event)
        return event

    def record_result(
        self,
        *,
        kind: AuditEventKind,
        principal: Principal,
        result: ExecutionResult,
        operation_class: OperationClass | None,
        attempt_count: int,
    ) -> AuditEvent:
        error_code = result.error.code if isinstance(result, ExecutionFailure) else None
        event = self._event(
            kind=kind,
            principal=principal,
            context=result.context,
            operation_class=operation_class,
            outcome=(
                AuditOutcome.FAILED
                if isinstance(result, ExecutionFailure)
                else AuditOutcome.SUCCEEDED
            ),
            error_code=error_code,
            duration_ms=result.duration_ms,
            attempt_count=attempt_count,
        )
        self._store.append(event)
        return event

    def _event(
        self,
        *,
        kind: AuditEventKind,
        principal: Principal,
        context: ExecutionContext,
        operation_class: OperationClass | None,
        outcome: AuditOutcome,
        error_code: ExecutionErrorCode | None = None,
        duration_ms: float | None = None,
        attempt_count: int | None = None,
    ) -> AuditEvent:
        return AuditEvent(
            event_id=self._event_id_factory(),
            recorded_at=self._wall_clock(),
            kind=kind,
            outcome=outcome,
            principal_issuer=principal.issuer,
            principal_subject=principal.subject,
            tool_ref=context.tool_ref,
            request_id=context.request_id,
            execution_id=context.execution_id,
            operation_class=operation_class,
            definition_fingerprint=context.definition_fingerprint,
            error_code=error_code,
            duration_ms=duration_ms,
            attempt_count=attempt_count,
        )
