"""PostgreSQL confirmation and idempotency state with atomic arbitration."""

from __future__ import annotations

from datetime import datetime
from typing import final
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Index,
    LargeBinary,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    Uuid,
    delete,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Engine, RowMapping
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.sql import Select, func
from sqlalchemy.sql.elements import ColumnElement

from sentinelmcp.mutation_guard.errors import (
    ConfirmationRejectedError,
    ConfirmationRejectionReason,
    IdempotencyConflictError,
    MutationStateIntegrityError,
    MutationStateUnavailableError,
)
from sentinelmcp.mutation_guard.models import (
    IdempotencyClaim,
    IdempotencyClaimRequest,
    IdempotencyClaimStatus,
    IdempotencyRecordId,
    IdempotencyRecordState,
    MutationBinding,
    MutationChallengeRecord,
)
from sentinelmcp.mutation_guard.protocol import MutationStateStore

CHALLENGE_TOKEN_UNIQUE_CONSTRAINT = "uq_mutation_challenges_token_digest"
IDEMPOTENCY_SCOPE_UNIQUE_CONSTRAINT = "uq_mutation_idempotency_scope"

mutation_metadata = MetaData()
mutation_challenges = Table(
    "mutation_challenges",
    mutation_metadata,
    Column("challenge_id", Uuid(as_uuid=True), primary_key=True),
    Column("token_digest", LargeBinary(length=32), nullable=False),
    Column("principal_issuer", String(2048), nullable=False),
    Column("principal_subject", String(255), nullable=False),
    Column("namespace", String(191), nullable=False),
    Column("name", String(63), nullable=False),
    Column("version", String(128), nullable=False),
    Column("definition_fingerprint", String(64), nullable=False),
    Column("arguments_digest", String(64), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("consumed_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("token_digest", name=CHALLENGE_TOKEN_UNIQUE_CONSTRAINT),
)
Index("ix_mutation_challenges_expires_at", mutation_challenges.c.expires_at)

mutation_idempotency = Table(
    "mutation_idempotency",
    mutation_metadata,
    Column("record_id", Uuid(as_uuid=True), primary_key=True),
    Column("principal_issuer", String(2048), nullable=False),
    Column("principal_subject", String(255), nullable=False),
    Column("namespace", String(191), nullable=False),
    Column("name", String(63), nullable=False),
    Column("version", String(128), nullable=False),
    Column("idempotency_key", String(255), nullable=False),
    Column("request_digest", String(64), nullable=False),
    Column("state", String(32), nullable=False),
    Column("request_id", Uuid(as_uuid=True), nullable=False),
    Column("execution_id", Uuid(as_uuid=True), nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("outcome", LargeBinary(), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
    UniqueConstraint(
        "principal_issuer",
        "principal_subject",
        "namespace",
        "name",
        "version",
        "idempotency_key",
        name=IDEMPOTENCY_SCOPE_UNIQUE_CONSTRAINT,
    ),
    CheckConstraint(
        "state IN ('pending', 'failed', 'succeeded', 'outcome_unknown')",
        name="ck_mutation_idempotency_state",
    ),
    CheckConstraint(
        "(state = 'pending' AND outcome IS NULL) OR (state <> 'pending' AND outcome IS NOT NULL)",
        name="ck_mutation_idempotency_outcome",
    ),
)
Index("ix_mutation_idempotency_state", mutation_idempotency.c.state)


@final
class PostgresMutationStateStore(MutationStateStore):
    """Durable mutation state using PostgreSQL uniqueness and row updates."""

    __slots__ = ("_engine",)

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def issue_challenge(self, record: MutationChallengeRecord) -> None:
        binding = record.binding
        statement = insert(mutation_challenges).values(
            challenge_id=record.challenge_id.value,
            token_digest=record.token_digest,
            principal_issuer=binding.principal_issuer,
            principal_subject=binding.principal_subject,
            namespace=str(binding.tool_ref.namespace),
            name=str(binding.tool_ref.name),
            version=str(binding.tool_ref.version),
            definition_fingerprint=binding.definition_fingerprint,
            arguments_digest=binding.arguments_digest,
            expires_at=record.expires_at,
        )
        try:
            with self._engine.begin() as connection:
                connection.execute(statement)
        except SQLAlchemyError:
            raise MutationStateUnavailableError(operation="challenge issuance") from None

    def claim_execution(self, request: IdempotencyClaimRequest) -> IdempotencyClaim:
        binding = request.binding
        insert_statement = (
            insert(mutation_idempotency)
            .values(
                record_id=request.record_id.value,
                principal_issuer=binding.principal_issuer,
                principal_subject=binding.principal_subject,
                namespace=str(binding.tool_ref.namespace),
                name=str(binding.tool_ref.name),
                version=str(binding.tool_ref.version),
                idempotency_key=str(request.idempotency_key),
                request_digest=request.request_digest,
                state=IdempotencyRecordState.PENDING.value,
                request_id=request.request_id.value,
                execution_id=request.execution_id.value,
                started_at=request.started_at,
            )
            .on_conflict_do_nothing(constraint=IDEMPOTENCY_SCOPE_UNIQUE_CONSTRAINT)
            .returning(mutation_idempotency.c.record_id)
        )
        try:
            with self._engine.begin() as connection:
                inserted = connection.execute(insert_statement).one_or_none()
                if inserted is None:
                    row = connection.execute(_idempotency_scope_query(request)).mappings().one()
                    return _existing_claim(row, request)

                consumed = connection.execute(
                    update(mutation_challenges)
                    .where(
                        mutation_challenges.c.token_digest == request.confirmation_token_digest,
                        mutation_challenges.c.consumed_at.is_(None),
                        mutation_challenges.c.expires_at > request.now,
                        *_binding_predicates(mutation_challenges, binding),
                    )
                    .values(consumed_at=request.now)
                    .returning(mutation_challenges.c.challenge_id)
                ).scalar_one_or_none()
                if consumed is None:
                    challenge_row = (
                        connection.execute(
                            select(mutation_challenges).where(
                                mutation_challenges.c.token_digest
                                == request.confirmation_token_digest
                            )
                        )
                        .mappings()
                        .one_or_none()
                    )
                    _raise_challenge_rejection(challenge_row, binding, request.now)
                return IdempotencyClaim(
                    status=IdempotencyClaimStatus.NEW,
                    record_id=request.record_id,
                    state=IdempotencyRecordState.PENDING,
                )
        except (ConfirmationRejectedError, IdempotencyConflictError, MutationStateIntegrityError):
            raise
        except SQLAlchemyError:
            raise MutationStateUnavailableError(operation="execution claim") from None

    def complete_execution(
        self,
        record_id: IdempotencyRecordId,
        *,
        state: IdempotencyRecordState,
        outcome: bytes,
        completed_at: datetime,
    ) -> None:
        if state is IdempotencyRecordState.PENDING:
            raise ValueError("completion state must be terminal")
        statement = (
            update(mutation_idempotency)
            .where(
                mutation_idempotency.c.record_id == record_id.value,
                mutation_idempotency.c.state == IdempotencyRecordState.PENDING.value,
            )
            .values(state=state.value, outcome=outcome, updated_at=completed_at)
            .returning(mutation_idempotency.c.record_id)
        )
        try:
            with self._engine.begin() as connection:
                updated = connection.execute(statement).scalar_one_or_none()
            if updated is None:
                raise MutationStateIntegrityError(
                    reason="pending idempotency record was not available for completion"
                )
        except MutationStateIntegrityError:
            raise
        except SQLAlchemyError:
            raise MutationStateUnavailableError(operation="outcome persistence") from None


def clear_mutation_state(engine: Engine) -> None:
    """Delete durable mutation rows for isolated tests only."""

    with engine.begin() as connection:
        connection.execute(delete(mutation_idempotency))
        connection.execute(delete(mutation_challenges))


def _idempotency_scope_query(request: IdempotencyClaimRequest) -> Select[tuple[object, ...]]:
    binding = request.binding
    return select(mutation_idempotency).where(
        mutation_idempotency.c.principal_issuer == binding.principal_issuer,
        mutation_idempotency.c.principal_subject == binding.principal_subject,
        mutation_idempotency.c.namespace == str(binding.tool_ref.namespace),
        mutation_idempotency.c.name == str(binding.tool_ref.name),
        mutation_idempotency.c.version == str(binding.tool_ref.version),
        mutation_idempotency.c.idempotency_key == str(request.idempotency_key),
    )


def _existing_claim(row: RowMapping, request: IdempotencyClaimRequest) -> IdempotencyClaim:
    if _row_string(row, "request_digest") != request.request_digest:
        raise IdempotencyConflictError
    try:
        record_id = IdempotencyRecordId(_row_uuid(row, "record_id"))
        state = IdempotencyRecordState(_row_string(row, "state"))
    except ValueError as error:
        raise MutationStateIntegrityError(
            reason="persisted idempotency state is invalid"
        ) from error
    if state is IdempotencyRecordState.PENDING:
        if row.get("outcome") is not None:
            raise MutationStateIntegrityError(reason="pending idempotency record has an outcome")
        return IdempotencyClaim(
            status=IdempotencyClaimStatus.IN_PROGRESS,
            record_id=record_id,
            state=state,
        )
    outcome = _row_bytes(row, "outcome")
    return IdempotencyClaim(
        status=IdempotencyClaimStatus.REUSED,
        record_id=record_id,
        state=state,
        outcome=outcome,
    )


def _raise_challenge_rejection(
    row: RowMapping | None,
    binding: MutationBinding,
    now: datetime,
) -> None:
    if row is None:
        raise ConfirmationRejectedError(ConfirmationRejectionReason.NOT_FOUND)
    consumed_at = row.get("consumed_at")
    if consumed_at is not None:
        if not isinstance(consumed_at, datetime):
            raise MutationStateIntegrityError(reason="persisted consumed time is invalid")
        raise ConfirmationRejectedError(ConfirmationRejectionReason.CONSUMED)
    expires_at = row.get("expires_at")
    if not isinstance(expires_at, datetime):
        raise MutationStateIntegrityError(reason="persisted expiry is invalid")
    if expires_at <= now:
        raise ConfirmationRejectedError(ConfirmationRejectionReason.EXPIRED)
    if not _row_binding_matches(row, binding):
        raise ConfirmationRejectedError(ConfirmationRejectionReason.BINDING_MISMATCH)
    raise MutationStateIntegrityError(reason="active matching challenge could not be consumed")


def _binding_predicates(
    table: Table,
    binding: MutationBinding,
) -> tuple[ColumnElement[bool], ...]:
    return (
        table.c.principal_issuer == binding.principal_issuer,
        table.c.principal_subject == binding.principal_subject,
        table.c.namespace == str(binding.tool_ref.namespace),
        table.c.name == str(binding.tool_ref.name),
        table.c.version == str(binding.tool_ref.version),
        table.c.definition_fingerprint == binding.definition_fingerprint,
        table.c.arguments_digest == binding.arguments_digest,
    )


def _row_binding_matches(row: RowMapping, binding: MutationBinding) -> bool:
    return (
        _row_string(row, "principal_issuer") == binding.principal_issuer
        and _row_string(row, "principal_subject") == binding.principal_subject
        and _row_string(row, "namespace") == str(binding.tool_ref.namespace)
        and _row_string(row, "name") == str(binding.tool_ref.name)
        and _row_string(row, "version") == str(binding.tool_ref.version)
        and _row_string(row, "definition_fingerprint") == binding.definition_fingerprint
        and _row_string(row, "arguments_digest") == binding.arguments_digest
    )


def _row_string(row: RowMapping, field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str):
        raise MutationStateIntegrityError(reason=f"persisted {field} is not a string")
    return value


def _row_uuid(row: RowMapping, field: str) -> UUID:
    value = row.get(field)
    if not isinstance(value, UUID):
        raise MutationStateIntegrityError(reason=f"persisted {field} is not a UUID")
    return value


def _row_bytes(row: RowMapping, field: str) -> bytes:
    value = row.get(field)
    if not isinstance(value, bytes | bytearray | memoryview):
        raise MutationStateIntegrityError(reason=f"persisted {field} is not binary")
    return bytes(value)
