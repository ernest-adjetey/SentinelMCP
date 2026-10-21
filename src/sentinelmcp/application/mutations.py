"""Durable preview, confirmation, and idempotent mutation orchestration."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import cast, final

from sentinelmcp.application.executor import PreparedExecution, ToolExecutor
from sentinelmcp.domain import OperationClass
from sentinelmcp.domain.json_values import FrozenJSONMapping
from sentinelmcp.execution import (
    ExecutionErrorCode,
    ExecutionFailure,
    ExecutionRequest,
    ExecutionResult,
    ExecutionSuccess,
)
from sentinelmcp.mutation_guard.canonical import (
    arguments_digest,
    confirmation_token_digest,
    semantic_request_digest,
)
from sentinelmcp.mutation_guard.errors import (
    ConfirmationRejectedError,
    IdempotencyConflictError,
    InvalidMutationRequestError,
    MutationStateIntegrityError,
    MutationStateUnavailableError,
)
from sentinelmcp.mutation_guard.models import (
    ChallengeId,
    IdempotencyClaimRequest,
    IdempotencyClaimStatus,
    IdempotencyKey,
    IdempotencyRecordId,
    IdempotencyRecordState,
    MutationBinding,
    MutationChallengeRecord,
    MutationPreview,
)
from sentinelmcp.mutation_guard.protocol import MutationStateStore
from sentinelmcp.mutation_guard.serialization import (
    deserialize_execution_result,
    serialize_execution_result,
)
from sentinelmcp.policy import Principal

MIN_CHALLENGE_TTL = timedelta(seconds=30)
MAX_CHALLENGE_TTL = timedelta(minutes=30)
DEFAULT_CHALLENGE_TTL = timedelta(minutes=5)
_MUTATION_WARNING = "This confirmed operation may change external state."

type MutationPreviewResult = MutationPreview | ExecutionFailure


@final
class MutationService:
    """Run mutation safety controls around the shared execution lifecycle."""

    __slots__ = (
        "_challenge_id_factory",
        "_challenge_ttl",
        "_executor",
        "_record_id_factory",
        "_store",
        "_token_factory",
        "_wall_clock",
    )

    def __init__(
        self,
        executor: ToolExecutor,
        store: MutationStateStore,
        *,
        challenge_ttl: timedelta = DEFAULT_CHALLENGE_TTL,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        token_factory: Callable[[], str] = lambda: secrets.token_urlsafe(32),
        challenge_id_factory: Callable[[], ChallengeId] = ChallengeId.generate,
        record_id_factory: Callable[[], IdempotencyRecordId] = IdempotencyRecordId.generate,
    ) -> None:
        if not MIN_CHALLENGE_TTL <= challenge_ttl <= MAX_CHALLENGE_TTL:
            raise ValueError("challenge TTL must be from 30 seconds through 30 minutes")
        self._executor = executor
        self._store = store
        self._challenge_ttl = challenge_ttl
        self._wall_clock = wall_clock
        self._token_factory = token_factory
        self._challenge_id_factory = challenge_id_factory
        self._record_id_factory = record_id_factory

    def preview(
        self,
        request: ExecutionRequest,
        *,
        principal: Principal,
    ) -> MutationPreviewResult:
        """Persist and return a bound challenge without resolving or invoking an adapter."""

        prepared = self._executor.prepare(request, principal=principal)
        if isinstance(prepared, ExecutionFailure):
            return prepared
        if prepared.definition.operation_class is not OperationClass.MUTATION:
            return self._executor._failure(
                prepared.context,
                ExecutionErrorCode.INVALID_REQUEST,
            )
        arguments = cast(FrozenJSONMapping, prepared.request.arguments)
        now = self._wall_clock()
        expires_at = now + self._challenge_ttl
        challenge_id = self._challenge_id_factory()
        token = self._token_factory()
        try:
            binding = _binding(prepared, principal, arguments)
            record = MutationChallengeRecord(
                challenge_id=challenge_id,
                token_digest=confirmation_token_digest(token),
                binding=binding,
                expires_at=expires_at,
            )
            self._store.issue_challenge(record)
        except InvalidMutationRequestError:
            return self._executor._failure(prepared.context, ExecutionErrorCode.INVALID_REQUEST)
        except MutationStateUnavailableError:
            return self._executor._failure(
                prepared.context,
                ExecutionErrorCode.MUTATION_STATE_UNAVAILABLE,
            )
        except Exception:
            return self._executor._failure(
                prepared.context,
                ExecutionErrorCode.INTERNAL_EXECUTION_ERROR,
            )
        return MutationPreview(
            challenge_id=challenge_id,
            request_id=prepared.context.request_id,
            confirmation_token=token,
            tool_ref=prepared.request.tool_ref,
            definition_fingerprint=binding.definition_fingerprint,
            arguments=arguments,
            warnings=(_MUTATION_WARNING,),
            expires_at=expires_at,
        )

    async def confirm(
        self,
        request: ExecutionRequest,
        *,
        principal: Principal,
        confirmation_token: str,
        idempotency_key: str,
    ) -> ExecutionResult:
        """Atomically authorize one mutation record, dispatch once, and persist its outcome."""

        prepared = self._executor.prepare(request, principal=principal)
        if isinstance(prepared, ExecutionFailure):
            return prepared
        if prepared.definition.operation_class is not OperationClass.MUTATION:
            return self._executor._failure(
                prepared.context,
                ExecutionErrorCode.INVALID_REQUEST,
            )
        binding = self._executor.resolve_binding(prepared)
        if isinstance(binding, ExecutionFailure):
            return binding

        arguments = cast(FrozenJSONMapping, prepared.request.arguments)
        now = self._wall_clock()
        try:
            mutation_binding = _binding(prepared, principal, arguments)
            claim_request = IdempotencyClaimRequest(
                record_id=self._record_id_factory(),
                confirmation_token_digest=confirmation_token_digest(confirmation_token),
                binding=mutation_binding,
                idempotency_key=IdempotencyKey(idempotency_key),
                request_digest=semantic_request_digest(
                    principal=principal,
                    tool_ref=prepared.request.tool_ref,
                    definition_fingerprint=mutation_binding.definition_fingerprint,
                    arguments=arguments,
                ),
                request_id=prepared.context.request_id,
                execution_id=prepared.context.execution_id,
                started_at=prepared.context.started_at,
                now=now,
            )
            claim = self._store.claim_execution(claim_request)
        except (InvalidMutationRequestError, ValueError):
            return self._executor._failure(prepared.context, ExecutionErrorCode.INVALID_REQUEST)
        except ConfirmationRejectedError:
            return self._executor._failure(
                prepared.context,
                ExecutionErrorCode.MUTATION_CONFIRMATION_INVALID,
            )
        except IdempotencyConflictError:
            return self._executor._failure(
                prepared.context,
                ExecutionErrorCode.IDEMPOTENCY_CONFLICT,
            )
        except MutationStateUnavailableError:
            return self._executor._failure(
                prepared.context,
                ExecutionErrorCode.MUTATION_STATE_UNAVAILABLE,
            )
        except Exception:
            return self._executor._failure(
                prepared.context,
                ExecutionErrorCode.INTERNAL_EXECUTION_ERROR,
            )

        if claim.status is IdempotencyClaimStatus.IN_PROGRESS:
            return self._executor._failure(
                prepared.context,
                ExecutionErrorCode.IDEMPOTENCY_IN_PROGRESS,
            )
        if claim.status is IdempotencyClaimStatus.REUSED:
            return self._reused_outcome(prepared, claim.outcome)

        result = await self._executor.dispatch(prepared, binding)
        state = (
            IdempotencyRecordState.SUCCEEDED
            if isinstance(result, ExecutionSuccess)
            else IdempotencyRecordState.OUTCOME_UNKNOWN
        )
        try:
            outcome = serialize_execution_result(result)
        except ValueError:
            result = self._executor._failure(
                prepared.context,
                ExecutionErrorCode.MUTATION_OUTCOME_UNKNOWN,
            )
            state = IdempotencyRecordState.OUTCOME_UNKNOWN
            outcome = serialize_execution_result(result)
        try:
            self._store.complete_execution(
                claim.record_id,
                state=state,
                outcome=outcome,
                completed_at=self._wall_clock(),
            )
        except (MutationStateUnavailableError, MutationStateIntegrityError):
            return self._executor._failure(
                prepared.context,
                ExecutionErrorCode.MUTATION_OUTCOME_UNKNOWN,
            )
        return result

    def _reused_outcome(
        self,
        prepared: PreparedExecution,
        outcome: bytes | None,
    ) -> ExecutionResult:
        if outcome is None:
            return self._executor._failure(
                prepared.context,
                ExecutionErrorCode.INTERNAL_EXECUTION_ERROR,
            )
        try:
            result = deserialize_execution_result(outcome)
        except MutationStateIntegrityError:
            return self._executor._failure(
                prepared.context,
                ExecutionErrorCode.INTERNAL_EXECUTION_ERROR,
            )
        if (
            result.context.tool_ref != prepared.context.tool_ref
            or result.context.definition_fingerprint != prepared.context.definition_fingerprint
        ):
            return self._executor._failure(
                prepared.context,
                ExecutionErrorCode.INTERNAL_EXECUTION_ERROR,
            )
        return result


def _binding(
    prepared: PreparedExecution,
    principal: Principal,
    arguments: FrozenJSONMapping,
) -> MutationBinding:
    fingerprint = prepared.context.definition_fingerprint
    if fingerprint is None:
        raise AssertionError("prepared execution must contain a definition fingerprint")
    return MutationBinding(
        principal_issuer=principal.issuer,
        principal_subject=principal.subject,
        tool_ref=prepared.request.tool_ref,
        definition_fingerprint=fingerprint,
        arguments_digest=arguments_digest(arguments),
    )
