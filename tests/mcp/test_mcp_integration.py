"""End-to-end MCP integration through the official in-memory SDK transport."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass
from typing import cast

import httpx
import pytest
from mcp.types import TextContent
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from mcp import Client, MCPError
from sentinelmcp.adapters.mcp import (
    StaticMCPPrincipalResolver,
    create_mcp_server,
    encode_tool_ref,
)
from sentinelmcp.application import ToolExecutor
from sentinelmcp.audit import DurableAuditRecorder, InMemoryAuditStore
from sentinelmcp.domain import (
    OperationClass,
    RetryPolicy,
    TimeoutPolicy,
    ToolDefinition,
    ToolName,
    ToolNamespace,
    ToolRef,
    ToolVersion,
)
from sentinelmcp.domain.json_values import FrozenJSONValue, thaw_json
from sentinelmcp.execution import (
    AdapterFailure,
    AdapterInvocation,
    AdapterResult,
    AdapterSuccess,
    CircuitBreakerConfig,
    ExecutionErrorCode,
    HTTPToolAdapter,
    HTTPToolTarget,
    InMemoryCircuitBreaker,
    InMemoryRateLimiter,
    InMemoryRuntimeBindingRegistry,
    OutboundHTTPPolicy,
    ReliabilityControls,
    RuntimeToolBinding,
    TokenBucketConfig,
)
from sentinelmcp.policy import (
    AuthorizationDecision,
    AuthorizationPolicy,
    AuthorizationReason,
    AuthorizationRule,
    PolicyEffect,
    Principal,
    RuleBasedAuthorizationPolicy,
)
from sentinelmcp.registry import InMemoryToolRegistry
from sentinelmcp.telemetry import OpenTelemetryExecutionObservability, PrometheusMetrics

PRINCIPAL = Principal("mcp-test", "https://issuer.test", scopes=frozenset({"tools:read"}))


async def resolve_public_test_host(_host: str, _port: int) -> tuple[str, ...]:
    return ("93.184.216.34",)


class RecordingAdapter:
    def __init__(
        self,
        output: object,
        *,
        failure: AdapterFailure | None = None,
    ) -> None:
        self.output = output
        self.failure = failure
        self.calls: list[AdapterInvocation] = []

    async def execute(self, invocation: AdapterInvocation) -> AdapterResult:
        self.calls.append(invocation)
        if self.failure is not None:
            return self.failure
        return AdapterSuccess(self.output)


@dataclass(frozen=True, slots=True)
class Runtime:
    registry: InMemoryToolRegistry
    executor: ToolExecutor
    adapters: tuple[RecordingAdapter, ...]


class VersionAuthorizationPolicy(AuthorizationPolicy):
    def __init__(self, allowed: ToolRef) -> None:
        self.allowed = allowed

    def authorize(
        self,
        principal: Principal,
        definition: ToolDefinition,
    ) -> AuthorizationDecision:
        del principal
        if definition.ref == self.allowed:
            return AuthorizationDecision(True, AuthorizationReason.ALLOWED)
        return AuthorizationDecision(False, AuthorizationReason.NO_MATCHING_ALLOW)


def definition(
    version: str = "1.0.0",
    *,
    output_schema: dict[str, object] | None = None,
    timeout_policy: TimeoutPolicy | None = None,
    retry_policy: RetryPolicy | None = None,
) -> ToolDefinition:
    return ToolDefinition(
        namespace=ToolNamespace("demo.release"),
        name=ToolName("inspect"),
        version=ToolVersion(version),
        description=f"Inspect exact synthetic release {version}.",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        output_schema=output_schema
        or {
            "type": "object",
            "properties": {"version": {"type": "string"}},
            "required": ["version"],
            "additionalProperties": False,
        },
        operation_class=OperationClass.READ,
        timeout_policy=TimeoutPolicy() if timeout_policy is None else timeout_policy,
        retry_policy=RetryPolicy() if retry_policy is None else retry_policy,
    )


def runtime_for(
    definitions: Iterable[ToolDefinition],
    *,
    policy: AuthorizationPolicy | None = None,
    failures: dict[ToolRef, AdapterFailure] | None = None,
    outputs: dict[ToolRef, object] | None = None,
) -> Runtime:
    registry = InMemoryToolRegistry()
    bindings = InMemoryRuntimeBindingRegistry()
    adapters: list[RecordingAdapter] = []
    values = tuple(definitions)
    for item in values:
        registry.register(item)
        adapter = RecordingAdapter(
            (outputs or {}).get(item.ref, {"version": str(item.version)}),
            failure=(failures or {}).get(item.ref),
        )
        adapters.append(adapter)
        bindings.bind(RuntimeToolBinding(item.ref, adapter))
    authorization = policy or RuleBasedAuthorizationPolicy(
        tuple(
            AuthorizationRule(PolicyEffect.ALLOW, item.identity, item.operation_class)
            for item in values
        )
    )
    return Runtime(
        registry,
        ToolExecutor(registry, bindings, authorization_policy=authorization),
        tuple(adapters),
    )


def test_discovery_is_deterministic_and_preserves_exact_versions() -> None:
    versions = (
        definition("1.1.0"),
        definition("1.0.0+build.7"),
        definition("1.0.0-rc.1"),
        definition("1.0.0"),
    )
    runtime = runtime_for(versions)

    async def exercise() -> None:
        async with Client(
            create_mcp_server(
                runtime.registry,
                runtime.executor,
                principal_resolver=StaticMCPPrincipalResolver(PRINCIPAL),
            )
        ) as client:
            first = await client.list_tools()
            second = await client.list_tools(cache_mode="refresh")
            expected = [
                encode_tool_ref(item.ref)
                for item in sorted(versions, key=lambda item: item.version)
            ]
            assert [tool.name for tool in first.tools] == expected
            assert [tool.name for tool in second.tools] == expected
            assert [tool.title for tool in first.tools] == [
                str(item.ref) for item in sorted(versions, key=lambda item: item.version)
            ]
            assert all(
                tool.input_schema == thaw_json(cast(FrozenJSONValue, item.input_schema))
                for tool, item in zip(
                    first.tools, sorted(versions, key=lambda value: value.version), strict=True
                )
            )

    asyncio.run(exercise())


def test_calls_route_to_each_exact_version_without_latest_fallback() -> None:
    versions = (definition("1.0.0"), definition("1.1.0"))
    runtime = runtime_for(versions)

    async def exercise() -> None:
        async with Client(
            create_mcp_server(
                runtime.registry,
                runtime.executor,
                principal_resolver=StaticMCPPrincipalResolver(PRINCIPAL),
            )
        ) as client:
            await client.list_tools()
            first = await client.call_tool(encode_tool_ref(versions[0].ref), {"value": "a"})
            second = await client.call_tool(encode_tool_ref(versions[1].ref), {"value": "b"})
            assert first.structured_content == {"version": "1.0.0"}
            assert second.structured_content == {"version": "1.1.0"}

    asyncio.run(exercise())
    assert len(runtime.adapters[0].calls) == 1
    assert len(runtime.adapters[1].calls) == 1


def test_default_principal_boundary_denies_discovery_and_invocation() -> None:
    item = definition()
    runtime = runtime_for((item,))
    server = create_mcp_server(runtime.registry, runtime.executor)

    async def exercise() -> None:
        async with Client(server) as client:
            with pytest.raises(MCPError, match="valid SentinelMCP principal"):
                await client.list_tools()
            result = await client.call_tool(encode_tool_ref(item.ref), {"value": "blocked"})
            payload = cast(dict[str, object], result.structured_content)
            assert cast(dict[str, object], payload["error"])["code"] == ("authentication_failed")

    asyncio.run(exercise())
    assert runtime.adapters[0].calls == []


def test_foreign_mcp_name_is_rejected_without_dispatch() -> None:
    item = definition()
    runtime = runtime_for((item,))

    async def exercise() -> None:
        async with Client(
            create_mcp_server(
                runtime.registry,
                runtime.executor,
                principal_resolver=StaticMCPPrincipalResolver(PRINCIPAL),
            )
        ) as client:
            result = await client.call_tool("foreign.tool", {"value": "blocked"})
            payload = cast(dict[str, object], result.structured_content)
            assert cast(dict[str, object], payload["error"])["code"] == "invalid_request"

    asyncio.run(exercise())
    assert runtime.adapters[0].calls == []


def test_discovery_fails_visibly_when_exact_name_exceeds_mcp_limit() -> None:
    item = ToolDefinition(
        namespace=ToolNamespace(f"{'a' * 63}.{'b' * 63}"),
        name=ToolName("run"),
        version=ToolVersion("1.0.0"),
        description="A valid Sentinel contract that exceeds the MCP name limit.",
        input_schema={"type": "object", "additionalProperties": False},
        output_schema={"type": "object", "additionalProperties": False},
        operation_class=OperationClass.READ,
    )
    runtime = runtime_for((item,), outputs={item.ref: {}})

    async def exercise() -> None:
        async with Client(
            create_mcp_server(
                runtime.registry,
                runtime.executor,
                principal_resolver=StaticMCPPrincipalResolver(PRINCIPAL),
            )
        ) as client:
            with pytest.raises(MCPError, match="cannot be represented safely"):
                await client.list_tools()

    asyncio.run(exercise())
    assert runtime.adapters[0].calls == []


def test_mutation_without_durable_service_is_not_dispatched() -> None:
    item = ToolDefinition(
        namespace=ToolNamespace("demo.profile"),
        name=ToolName("update"),
        version=ToolVersion("1.0.0"),
        description="Update a synthetic profile.",
        input_schema={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        },
        operation_class=OperationClass.MUTATION,
    )
    runtime = runtime_for((item,), outputs={item.ref: {"name": "Ada"}})

    async def exercise() -> None:
        async with Client(
            create_mcp_server(
                runtime.registry,
                runtime.executor,
                principal_resolver=StaticMCPPrincipalResolver(PRINCIPAL),
            )
        ) as client:
            result = await client.call_tool(
                encode_tool_ref(item.ref),
                {"action": "preview", "arguments": {"name": "Ada"}},
            )
            payload = cast(dict[str, object], result.structured_content)
            assert cast(dict[str, object], payload["error"])["code"] == ("mutation_not_supported")

    asyncio.run(exercise())
    assert runtime.adapters[0].calls == []


def test_authorization_uses_exact_definition_and_denial_never_dispatches() -> None:
    allowed = definition("1.0.0")
    denied = definition("1.1.0")
    runtime = runtime_for(
        (allowed, denied),
        policy=VersionAuthorizationPolicy(allowed.ref),
    )

    async def exercise() -> None:
        async with Client(
            create_mcp_server(
                runtime.registry,
                runtime.executor,
                principal_resolver=StaticMCPPrincipalResolver(PRINCIPAL),
            )
        ) as client:
            await client.list_tools()
            accepted = await client.call_tool(encode_tool_ref(allowed.ref), {"value": "ok"})
            rejected = await client.call_tool(encode_tool_ref(denied.ref), {"value": "no"})
            assert accepted.is_error is False
            assert rejected.is_error is True
            assert cast(dict[str, object], rejected.structured_content)["error"] == {
                "code": "authorization_failed",
                "message": "The authenticated caller is not authorized for this tool operation.",
            }

    asyncio.run(exercise())
    assert len(runtime.adapters[0].calls) == 1
    assert runtime.adapters[1].calls == []


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        (
            AdapterFailure(ExecutionErrorCode.UPSTREAM_AUTHENTICATION_FAILED, 401),
            "upstream_authentication_failed",
        ),
        (
            AdapterFailure(ExecutionErrorCode.UPSTREAM_MALFORMED_RESPONSE),
            "upstream_malformed_response",
        ),
        (AdapterFailure(ExecutionErrorCode.UPSTREAM_TIMEOUT), "upstream_timeout"),
    ],
)
def test_executor_failures_are_normalized_as_safe_mcp_results(
    failure: AdapterFailure,
    expected_code: str,
) -> None:
    item = definition()
    runtime = runtime_for((item,), failures={item.ref: failure})

    async def exercise() -> None:
        async with Client(
            create_mcp_server(
                runtime.registry,
                runtime.executor,
                principal_resolver=StaticMCPPrincipalResolver(PRINCIPAL),
            )
        ) as client:
            await client.list_tools()
            result = await client.call_tool(encode_tool_ref(item.ref), {"value": "secret"})
            payload = cast(dict[str, object], result.structured_content)
            error = cast(dict[str, object], payload["error"])
            assert result.is_error is True
            assert error["code"] == expected_code
            assert "secret" not in cast(TextContent, result.content[0]).text

    asyncio.run(exercise())


def test_input_and_output_validation_remain_in_shared_executor() -> None:
    item = definition()
    runtime = runtime_for((item,), outputs={item.ref: {"wrong": "shape"}})

    async def exercise() -> None:
        async with Client(
            create_mcp_server(
                runtime.registry,
                runtime.executor,
                principal_resolver=StaticMCPPrincipalResolver(PRINCIPAL),
            )
        ) as client:
            await client.list_tools()
            invalid_input = await client.call_tool(encode_tool_ref(item.ref), {"value": 7})
            invalid_output = await client.call_tool(encode_tool_ref(item.ref), {"value": "valid"})
            input_payload = cast(dict[str, object], invalid_input.structured_content)
            input_error = cast(dict[str, object], input_payload["error"])
            assert input_error["code"] == "input_validation_failed"
            assert input_error["message"] == (
                "Arguments do not satisfy the registered input schema."
            )
            assert isinstance(input_error["issues"], list)
            assert (
                cast(
                    dict[str, object],
                    cast(dict[str, object], invalid_output.structured_content)["error"],
                )["code"]
                == "output_validation_failed"
            )

    asyncio.run(exercise())
    assert len(runtime.adapters[0].calls) == 1


def test_unknown_exact_version_is_not_substituted() -> None:
    item = definition("1.0.0")
    runtime = runtime_for((item,))
    unknown = ToolRef(item.namespace, item.name, ToolVersion("9.9.9"))

    async def exercise() -> None:
        async with Client(
            create_mcp_server(
                runtime.registry,
                runtime.executor,
                principal_resolver=StaticMCPPrincipalResolver(PRINCIPAL),
            )
        ) as client:
            result = await client.call_tool(encode_tool_ref(unknown), {"value": "x"})
            assert result.is_error is True
            assert (
                cast(
                    dict[str, object], cast(dict[str, object], result.structured_content)["error"]
                )["code"]
                == "tool_not_found"
            )

    asyncio.run(exercise())
    assert runtime.adapters[0].calls == []


def test_mcp_call_uses_shared_audit_metrics_and_tracing_lifecycle() -> None:
    item = definition()
    registry = InMemoryToolRegistry()
    registry.register(item)
    adapter = RecordingAdapter({"version": "1.0.0"})
    bindings = InMemoryRuntimeBindingRegistry()
    bindings.bind(RuntimeToolBinding(item.ref, adapter))
    store = InMemoryAuditStore()
    metrics = PrometheusMetrics()
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    executor = ToolExecutor(
        registry,
        bindings,
        authorization_policy=RuleBasedAuthorizationPolicy(
            (AuthorizationRule(PolicyEffect.ALLOW, item.identity, OperationClass.READ),)
        ),
        audit=DurableAuditRecorder(store),
        observability=OpenTelemetryExecutionObservability(
            provider.get_tracer("sentinelmcp.tests.mcp"),
            metrics=metrics,
        ),
    )

    async def exercise() -> None:
        async with Client(
            create_mcp_server(
                registry,
                executor,
                principal_resolver=StaticMCPPrincipalResolver(PRINCIPAL),
            )
        ) as client:
            result = await client.call_tool(encode_tool_ref(item.ref), {"value": "observed"})
            assert result.structured_content == {"version": "1.0.0"}

    asyncio.run(exercise())
    assert len(adapter.calls) == 1
    events = store.list_for_request(adapter.calls[0].context.request_id)
    assert len(events) == 1
    assert events[0].principal_subject == PRINCIPAL.subject
    rendered = metrics.render().decode("utf-8")
    assert 'operation="read"' in rendered
    assert 'outcome="success"' in rendered
    assert "mcp-test" not in rendered
    assert "observed" not in rendered
    assert {span.name for span in exporter.get_finished_spans()} >= {
        "sentinelmcp.execution",
        "sentinelmcp.execution.attempt",
    }


def test_rate_limit_retry_guidance_is_preserved_in_mcp_error() -> None:
    item = definition()
    registry = InMemoryToolRegistry()
    registry.register(item)
    adapter = RecordingAdapter({"version": "1.0.0"})
    bindings = InMemoryRuntimeBindingRegistry()
    bindings.bind(RuntimeToolBinding(item.ref, adapter))
    executor = ToolExecutor(
        registry,
        bindings,
        authorization_policy=RuleBasedAuthorizationPolicy(
            (AuthorizationRule(PolicyEffect.ALLOW, item.identity, OperationClass.READ),)
        ),
        monotonic=lambda: 0.0,
        reliability=ReliabilityControls(
            rate_limiter=InMemoryRateLimiter(TokenBucketConfig(1, 1.0))
        ),
    )

    async def exercise() -> None:
        async with Client(
            create_mcp_server(
                registry,
                executor,
                principal_resolver=StaticMCPPrincipalResolver(PRINCIPAL),
            )
        ) as client:
            first = await client.call_tool(encode_tool_ref(item.ref), {"value": "first"})
            second = await client.call_tool(encode_tool_ref(item.ref), {"value": "second"})
            assert first.is_error is False
            payload = cast(dict[str, object], second.structured_content)
            error = cast(dict[str, object], payload["error"])
            assert error["code"] == "rate_limited"
            assert error["retry_after_ms"] == 1000

    asyncio.run(exercise())
    assert len(adapter.calls) == 1


def test_shared_retry_and_circuit_controls_apply_to_mcp_calls() -> None:
    retrying = definition(
        retry_policy=RetryPolicy(max_attempts=2, initial_delay_ms=0, maximum_delay_ms=0)
    )
    registry = InMemoryToolRegistry()
    registry.register(retrying)
    bindings = InMemoryRuntimeBindingRegistry()

    class SequencedAdapter:
        def __init__(self) -> None:
            self.calls = 0

        async def execute(self, invocation: AdapterInvocation) -> AdapterResult:
            del invocation
            self.calls += 1
            if self.calls == 1:
                return AdapterFailure(ExecutionErrorCode.UPSTREAM_UNAVAILABLE, 503)
            return AdapterSuccess({"version": "retried"})

    adapter = SequencedAdapter()
    bindings.bind(RuntimeToolBinding(retrying.ref, adapter))
    executor = ToolExecutor(
        registry,
        bindings,
        authorization_policy=RuleBasedAuthorizationPolicy(
            (AuthorizationRule(PolicyEffect.ALLOW, retrying.identity, OperationClass.READ),)
        ),
        reliability=ReliabilityControls(random_unit=lambda: 0.0),
    )

    async def retry_call() -> None:
        async with Client(
            create_mcp_server(
                registry,
                executor,
                principal_resolver=StaticMCPPrincipalResolver(PRINCIPAL),
            )
        ) as client:
            result = await client.call_tool(encode_tool_ref(retrying.ref), {"value": "retry"})
            assert result.structured_content == {"version": "retried"}

    asyncio.run(retry_call())
    assert adapter.calls == 2

    failing = definition("2.0.0")
    circuit_runtime = runtime_for(
        (failing,),
        failures={failing.ref: AdapterFailure(ExecutionErrorCode.UPSTREAM_UNAVAILABLE, 503)},
    )
    circuit_bindings = InMemoryRuntimeBindingRegistry()
    circuit_bindings.bind(RuntimeToolBinding(failing.ref, circuit_runtime.adapters[0]))
    circuit_executor = ToolExecutor(
        circuit_runtime.registry,
        circuit_bindings,
        authorization_policy=RuleBasedAuthorizationPolicy(
            (AuthorizationRule(PolicyEffect.ALLOW, failing.identity, OperationClass.READ),)
        ),
        reliability=ReliabilityControls(
            circuit_breaker=InMemoryCircuitBreaker(
                CircuitBreakerConfig(failure_threshold=1, open_seconds=60)
            )
        ),
    )

    async def circuit_calls() -> None:
        async with Client(
            create_mcp_server(
                circuit_runtime.registry,
                circuit_executor,
                principal_resolver=StaticMCPPrincipalResolver(PRINCIPAL),
            )
        ) as client:
            first = await client.call_tool(encode_tool_ref(failing.ref), {"value": "fail"})
            second = await client.call_tool(encode_tool_ref(failing.ref), {"value": "blocked"})
            first_payload = cast(dict[str, object], first.structured_content)
            second_payload = cast(dict[str, object], second.structured_content)
            assert cast(dict[str, object], first_payload["error"])["code"] == (
                "upstream_unavailable"
            )
            assert cast(dict[str, object], second_payload["error"])["code"] == "circuit_open"

    asyncio.run(circuit_calls())
    assert len(circuit_runtime.adapters[0].calls) == 1


def test_shared_deadline_cancels_slow_mcp_read() -> None:
    item = definition(timeout_policy=TimeoutPolicy(timeout_ms=5))
    registry = InMemoryToolRegistry()
    registry.register(item)
    bindings = InMemoryRuntimeBindingRegistry()

    class SlowAdapter:
        def __init__(self) -> None:
            self.calls = 0

        async def execute(self, invocation: AdapterInvocation) -> AdapterResult:
            del invocation
            self.calls += 1
            await asyncio.sleep(1)
            return AdapterSuccess({"version": "too-late"})

    adapter = SlowAdapter()
    bindings.bind(RuntimeToolBinding(item.ref, adapter))
    ticks = iter((0.0, 0.0, 1.0, 1.0))
    executor = ToolExecutor(
        registry,
        bindings,
        authorization_policy=RuleBasedAuthorizationPolicy(
            (AuthorizationRule(PolicyEffect.ALLOW, item.identity, OperationClass.READ),)
        ),
        monotonic=lambda: next(ticks, 1.0),
    )

    async def exercise() -> None:
        async with Client(
            create_mcp_server(
                registry,
                executor,
                principal_resolver=StaticMCPPrincipalResolver(PRINCIPAL),
            )
        ) as client:
            result = await client.call_tool(encode_tool_ref(item.ref), {"value": "slow"})
            payload = cast(dict[str, object], result.structured_content)
            assert cast(dict[str, object], payload["error"])["code"] == "deadline_exceeded"

    asyncio.run(exercise())
    assert adapter.calls == 0


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (
            httpx.Response(
                401,
                headers={"content-type": "text/plain"},
                content=b"private upstream credential detail",
            ),
            "upstream_authentication_failed",
        ),
        (
            httpx.Response(
                200,
                headers={"content-type": "application/json"},
                content=b'{"version":',
            ),
            "upstream_malformed_response",
        ),
    ],
)
def test_actual_http_adapter_failures_remain_safe_through_mcp(
    response: httpx.Response,
    expected: str,
) -> None:
    item = definition()
    registry = InMemoryToolRegistry()
    registry.register(item)
    policy = RuleBasedAuthorizationPolicy(
        (AuthorizationRule(PolicyEffect.ALLOW, item.identity, OperationClass.READ),)
    )

    async def exercise() -> None:
        transport = httpx.MockTransport(lambda _request: response)
        async with httpx.AsyncClient(transport=transport) as http_client:
            bindings = InMemoryRuntimeBindingRegistry()
            bindings.bind(
                RuntimeToolBinding(
                    item.ref,
                    HTTPToolAdapter(
                        http_client,
                        HTTPToolTarget("https://tools.example.test/release"),
                        policy=OutboundHTTPPolicy(resolver=resolve_public_test_host),
                    ),
                )
            )
            executor = ToolExecutor(registry, bindings, authorization_policy=policy)
            async with Client(
                create_mcp_server(
                    registry,
                    executor,
                    principal_resolver=StaticMCPPrincipalResolver(PRINCIPAL),
                )
            ) as client:
                result = await client.call_tool(encode_tool_ref(item.ref), {"value": "private"})
                payload = cast(dict[str, object], result.structured_content)
                error = cast(dict[str, object], payload["error"])
                assert error["code"] == expected
                assert "private upstream" not in cast(TextContent, result.content[0]).text

    asyncio.run(exercise())


def test_client_cancellation_cancels_in_process_adapter_wait() -> None:
    item = definition()
    registry = InMemoryToolRegistry()
    registry.register(item)
    bindings = InMemoryRuntimeBindingRegistry()
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class BlockingAdapter:
        async def execute(self, invocation: AdapterInvocation) -> AdapterResult:
            del invocation
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
            return AdapterSuccess({"version": "unreachable"})

    bindings.bind(RuntimeToolBinding(item.ref, BlockingAdapter()))
    executor = ToolExecutor(
        registry,
        bindings,
        authorization_policy=RuleBasedAuthorizationPolicy(
            (AuthorizationRule(PolicyEffect.ALLOW, item.identity, OperationClass.READ),)
        ),
    )

    async def exercise() -> None:
        async with Client(
            create_mcp_server(
                registry,
                executor,
                principal_resolver=StaticMCPPrincipalResolver(PRINCIPAL),
            )
        ) as client:
            await client.list_tools()
            task = asyncio.create_task(
                client.call_tool(encode_tool_ref(item.ref), {"value": "wait"})
            )
            await asyncio.wait_for(started.wait(), timeout=2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await asyncio.wait_for(cancelled.wait(), timeout=2)

    asyncio.run(exercise())
