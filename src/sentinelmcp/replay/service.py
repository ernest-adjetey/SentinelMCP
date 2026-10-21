"""Capture, inspect, and safely replay exact historical requests."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import final

from sentinelmcp.application import PreparedExecution, ToolExecutor
from sentinelmcp.audit import AuditEventKind, AuditRecorder
from sentinelmcp.domain import ToolDefinition, ToolNotFoundError, ToolRef
from sentinelmcp.execution import (
    BindingNotFoundError,
    ExecutionContext,
    ExecutionFailure,
    ExecutionRequest,
    ExecutionResult,
    ExecutionSuccess,
    RuntimeBindingResolver,
)
from sentinelmcp.policy import Principal
from sentinelmcp.registry import ToolRegistry, fingerprint_tool_definition
from sentinelmcp.replay.errors import (
    ReplayAuditUnavailableError,
    ReplayContractDriftError,
    ReplayUnsafeError,
)
from sentinelmcp.replay.models import ReplayPlan, ReplayRecord, ReplayRecordId
from sentinelmcp.replay.policy import ReplayTargetPolicy, SyntheticReplayTargetPolicy
from sentinelmcp.replay.protocol import ReplayStore

type ReplayExecution = ReplayPlan | ExecutionResult


@final
class ReplayService:
    """Apply current contract, target, authorization, and audit controls to replay."""

    __slots__ = (
        "_audit",
        "_bindings",
        "_executor",
        "_record_id_factory",
        "_registry",
        "_store",
        "_target_policy",
        "_wall_clock",
    )

    def __init__(
        self,
        registry: ToolRegistry,
        bindings: RuntimeBindingResolver,
        executor: ToolExecutor,
        store: ReplayStore,
        audit: AuditRecorder,
        *,
        target_policy: ReplayTargetPolicy | None = None,
        record_id_factory: Callable[[], ReplayRecordId] = ReplayRecordId.generate,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not isinstance(registry, ToolRegistry):
            raise TypeError("registry must implement ToolRegistry")
        if not isinstance(bindings, RuntimeBindingResolver):
            raise TypeError("bindings must implement RuntimeBindingResolver")
        if not isinstance(executor, ToolExecutor):
            raise TypeError("executor must be a ToolExecutor")
        if not isinstance(store, ReplayStore):
            raise TypeError("store must implement ReplayStore")
        if not isinstance(audit, AuditRecorder):
            raise TypeError("audit must implement AuditRecorder")
        configured_policy = (
            SyntheticReplayTargetPolicy() if target_policy is None else target_policy
        )
        if not isinstance(configured_policy, ReplayTargetPolicy):
            raise TypeError("target_policy must implement ReplayTargetPolicy")
        self._registry = registry
        self._bindings = bindings
        self._executor = executor
        self._store = store
        self._audit = audit
        self._target_policy = configured_policy
        self._record_id_factory = record_id_factory
        self._wall_clock = wall_clock

    def capture(
        self,
        request: ExecutionRequest,
        *,
        principal: Principal,
        result: ExecutionResult,
    ) -> ReplayRecord:
        if not isinstance(request, ExecutionRequest):
            raise TypeError("request must be an ExecutionRequest")
        if not isinstance(principal, Principal):
            raise TypeError("principal must be a Principal")
        if not isinstance(result, ExecutionSuccess):
            raise ReplayUnsafeError(reason="only successful read executions can be captured")
        definition, fingerprint = self._resolve_safe_target(request.tool_ref)
        if result.context.tool_ref != request.tool_ref:
            raise ReplayContractDriftError()
        if result.context.definition_fingerprint != fingerprint:
            raise ReplayContractDriftError()
        record = ReplayRecord(
            record_id=self._record_id_factory(),
            captured_at=self._wall_clock(),
            principal_issuer=principal.issuer,
            principal_subject=principal.subject,
            tool_ref=request.tool_ref,
            definition_fingerprint=fingerprint,
            request_id=result.context.request_id,
            execution_id=result.context.execution_id,
            operation_class=definition.operation_class,
            arguments=request.arguments,
        )
        self._store.append(record)
        return record

    def inspect(self, record_id: ReplayRecordId) -> ReplayRecord:
        return self._store.get(record_id)

    def list_records(self) -> tuple[ReplayRecord, ...]:
        return self._store.list_records()

    async def replay(
        self,
        record_id: ReplayRecordId,
        *,
        principal: Principal,
        dry_run: bool = False,
    ) -> ReplayExecution:
        if not isinstance(principal, Principal):
            raise TypeError("principal must be a Principal")
        if not isinstance(dry_run, bool):
            raise TypeError("dry_run must be a boolean")
        record = self._store.get(record_id)
        _definition, fingerprint = self._resolve_safe_target(record.tool_ref)
        if fingerprint != record.definition_fingerprint:
            raise ReplayContractDriftError()
        context = ExecutionContext(
            request_id=record.request_id,
            execution_id=record.execution_id,
            tool_ref=record.tool_ref,
            definition_fingerprint=record.definition_fingerprint,
            started_at=record.captured_at,
            started_monotonic=0.0,
        )
        try:
            self._audit.record_intent(
                kind=AuditEventKind.REPLAY_ATTEMPTED,
                principal=principal,
                context=context,
                operation_class=record.operation_class,
            )
        except Exception:
            raise ReplayAuditUnavailableError() from None

        request = ExecutionRequest(record.tool_ref, record.arguments)
        if dry_run:
            prepared = self._executor.prepare(request, principal=principal)
            error_code = prepared.error.code if isinstance(prepared, ExecutionFailure) else None
            return ReplayPlan(
                record_id=record.record_id,
                tool_ref=record.tool_ref,
                definition_fingerprint=record.definition_fingerprint,
                can_execute=isinstance(prepared, PreparedExecution),
                error_code=error_code,
            )
        return await self._executor.execute(
            request,
            principal=principal,
            binding_guard=self._target_policy.allows,
        )

    def _resolve_safe_target(self, tool_ref: ToolRef) -> tuple[ToolDefinition, str]:
        try:
            definition = self._registry.get(tool_ref)
            binding = self._bindings.resolve(definition.ref)
        except (ToolNotFoundError, BindingNotFoundError):
            raise ReplayUnsafeError(reason="the exact replay target is unavailable") from None
        if not self._target_policy.allows(definition, binding):
            raise ReplayUnsafeError(reason="the exact binding is not approved for replay")
        return definition, fingerprint_tool_definition(definition)
