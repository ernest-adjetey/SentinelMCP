"""End-to-end PostgreSQL idempotency race through the mutation service."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock

import pytest
from sqlalchemy.engine import Engine

from sentinelmcp.application import MutationService, ToolExecutor
from sentinelmcp.domain import (
    OperationClass,
    ToolDefinition,
    ToolName,
    ToolNamespace,
    ToolVersion,
)
from sentinelmcp.execution import (
    AdapterInvocation,
    AdapterResult,
    AdapterSuccess,
    ExecutionErrorCode,
    ExecutionFailure,
    ExecutionRequest,
    ExecutionSuccess,
    InMemoryRuntimeBindingRegistry,
    RuntimeToolBinding,
)
from sentinelmcp.mutation_guard import MutationPreview, PostgresMutationStateStore
from sentinelmcp.policy import (
    AuthorizationRule,
    PolicyEffect,
    Principal,
    RuleBasedAuthorizationPolicy,
)
from sentinelmcp.registry import InMemoryToolRegistry

pytestmark = pytest.mark.postgres
WORKERS = 32
PRINCIPAL = Principal("race-user", "https://issuer.test", scopes=frozenset({"tools:write"}))


class ThreadSafeCountingAdapter:
    def __init__(self) -> None:
        self.calls = 0
        self._lock = Lock()

    async def execute(self, _invocation: AdapterInvocation) -> AdapterResult:
        with self._lock:
            self.calls += 1
        return AdapterSuccess({"updated": True})


def test_32_identical_confirmations_dispatch_adapter_once(postgres_engine: Engine) -> None:
    definition = ToolDefinition(
        namespace=ToolNamespace("demo.race"),
        name=ToolName("update"),
        version=ToolVersion("1.0.0"),
        description="Exercise concurrent mutation execution.",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {"updated": {"type": "boolean"}},
            "required": ["updated"],
            "additionalProperties": False,
        },
        operation_class=OperationClass.MUTATION,
        idempotency_supported=True,
    )
    registry = InMemoryToolRegistry()
    registry.register(definition)
    adapter = ThreadSafeCountingAdapter()
    bindings = InMemoryRuntimeBindingRegistry()
    bindings.bind(RuntimeToolBinding(definition.ref, adapter))
    policy = RuleBasedAuthorizationPolicy(
        (AuthorizationRule(PolicyEffect.ALLOW, definition.identity, OperationClass.MUTATION),)
    )
    executor = ToolExecutor(registry, bindings, authorization_policy=policy)
    token_values = iter(
        f"confirmation-token-{index:02d}-with-enough-bytes" for index in range(WORKERS)
    )
    service = MutationService(
        executor,
        PostgresMutationStateStore(postgres_engine),
        token_factory=lambda: next(token_values),
    )
    request = ExecutionRequest(definition.ref, {"value": "same"})
    previews = tuple(service.preview(request, principal=PRINCIPAL) for _ in range(WORKERS))
    assert all(isinstance(preview, MutationPreview) for preview in previews)
    tokens = tuple(
        preview.confirmation_token for preview in previews if isinstance(preview, MutationPreview)
    )
    barrier = Barrier(WORKERS)

    def confirm(token: str) -> ExecutionSuccess | ExecutionFailure:
        barrier.wait()
        result = asyncio.run(
            service.confirm(
                request,
                principal=PRINCIPAL,
                confirmation_token=token,
                idempotency_key="shared-key",
            )
        )
        assert isinstance(result, ExecutionSuccess | ExecutionFailure)
        return result

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = tuple(pool.map(confirm, tokens))

    assert adapter.calls == 1
    assert all(
        isinstance(result, ExecutionSuccess)
        or result.error.code is ExecutionErrorCode.IDEMPOTENCY_IN_PROGRESS
        for result in results
    )
