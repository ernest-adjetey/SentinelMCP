"""Reproducible local benchmarks over real SentinelMCP composition paths."""

from __future__ import annotations

import asyncio
import cProfile
import json
import math
import os
import platform
import pstats
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable, Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from pathlib import Path
from statistics import median
from typing import cast, final

import httpx
import jwt
from opentelemetry import trace

from mcp import Client
from sentinelmcp.adapters.mcp import (
    StaticMCPPrincipalResolver,
    create_mcp_server,
    encode_tool_ref,
)
from sentinelmcp.adapters.rest import create_execution_app
from sentinelmcp.application import MutationService, ToolExecutor
from sentinelmcp.audit import DurableAuditRecorder, InMemoryAuditStore
from sentinelmcp.domain import (
    OperationClass,
    ToolDefinition,
    ToolName,
    ToolNamespace,
    ToolVersion,
)
from sentinelmcp.domain.json_values import FrozenJSONMapping
from sentinelmcp.execution import (
    AdapterFailure,
    AdapterInvocation,
    AdapterResult,
    AdapterSuccess,
    ExecutionContext,
    ExecutionErrorCode,
    ExecutionFailure,
    ExecutionId,
    ExecutionRequest,
    InMemoryRuntimeBindingRegistry,
    RequestId,
    RuntimeToolBinding,
)
from sentinelmcp.mutation_guard import (
    MutationPreview,
    PostgresMutationStateStore,
    clear_mutation_state,
)
from sentinelmcp.policy import (
    AuthenticationError,
    AuthenticationFailureReason,
    Authenticator,
    AuthorizationRule,
    JWTAuthenticator,
    JWTVerificationKey,
    PolicyEffect,
    Principal,
    RuleBasedAuthorizationPolicy,
)
from sentinelmcp.registry import InMemoryToolRegistry, create_postgres_engine
from sentinelmcp.registry.fingerprint import fingerprint_tool_definition
from sentinelmcp.telemetry import OpenTelemetryExecutionObservability, PrometheusMetrics

type Invocation = Callable[[int], Awaitable[str | None]]

_PRINCIPAL = Principal("benchmark-runner", "urn:sentinelmcp:benchmark")
_REQUEST = {"value": "benchmark"}
_JWT_SECRET = b"sentinelmcp-local-benchmark-key-32"


@dataclass(frozen=True, slots=True)
class BenchmarkConfig:
    """Bounded workload and repetition controls."""

    concurrency_levels: tuple[int, ...] = (1, 10, 50, 100)
    postgres_concurrency_levels: tuple[int, ...] = (1, 10)
    warmup_requests: int = 20
    requests_per_trial: int = 200
    trials: int = 5

    def __post_init__(self) -> None:
        for name, levels in (
            ("concurrency_levels", self.concurrency_levels),
            ("postgres_concurrency_levels", self.postgres_concurrency_levels),
        ):
            if (
                not levels
                or tuple(sorted(set(levels))) != levels
                or not all(1 <= level <= 500 for level in levels)
            ):
                raise ValueError(f"{name} must contain unique ascending values from 1 through 500")
        if not 0 <= self.warmup_requests <= 10_000:
            raise ValueError("warmup_requests must be from 0 through 10,000")
        if not 1 <= self.requests_per_trial <= 100_000:
            raise ValueError("requests_per_trial must be from 1 through 100,000")
        if not 1 <= self.trials <= 20:
            raise ValueError("trials must be from 1 through 20")


@dataclass(frozen=True, slots=True)
class RequestMeasurement:
    workload: str
    concurrency: int
    trial: int
    sequence: int
    latency_ms: float
    error_code: str | None

    def document(self) -> dict[str, object]:
        return {
            "workload": self.workload,
            "concurrency": self.concurrency,
            "trial": self.trial,
            "sequence": self.sequence,
            "latency_ms": round(self.latency_ms, 6),
            "error_code": self.error_code,
        }


@dataclass(frozen=True, slots=True)
class ResourceMeasurement:
    workload: str
    concurrency: int
    trial: int
    elapsed_seconds: float
    process_cpu_seconds: float
    working_set_bytes: int | None
    peak_working_set_bytes: int | None
    requests_per_second: float

    def document(self) -> dict[str, object]:
        return {
            "workload": self.workload,
            "concurrency": self.concurrency,
            "trial": self.trial,
            "elapsed_seconds": round(self.elapsed_seconds, 9),
            "process_cpu_seconds": round(self.process_cpu_seconds, 9),
            "working_set_bytes": self.working_set_bytes,
            "peak_working_set_bytes": self.peak_working_set_bytes,
            "requests_per_second": round(self.requests_per_second, 6),
        }


@dataclass(frozen=True, slots=True)
class BenchmarkRun:
    generated_at: datetime
    config: BenchmarkConfig
    requests: tuple[RequestMeasurement, ...]
    resources: tuple[ResourceMeasurement, ...]
    omitted: dict[str, str]
    profile: tuple[dict[str, object], ...]


@dataclass(frozen=True, slots=True)
class _Workload:
    name: str
    invoke: Invocation
    concurrency_levels: tuple[int, ...]


@final
class _JWTBenchmarkAuthenticator(Authenticator):
    __slots__ = ("_delegate",)

    def __init__(self) -> None:
        self._delegate = JWTAuthenticator(
            issuer=_PRINCIPAL.issuer,
            audience="sentinelmcp-benchmark",
            keys=(JWTVerificationKey("benchmark", "HS256", _JWT_SECRET),),
            allowed_algorithms=frozenset({"HS256"}),
        )

    def authenticate(self, token: str) -> Principal:
        try:
            return self._delegate.authenticate(token)
        except AuthenticationError:
            raise
        except Exception as error:  # pragma: no cover - protocol guard
            raise AuthenticationError(AuthenticationFailureReason.INVALID) from error


async def run_benchmark_suite(
    config: BenchmarkConfig,
    *,
    postgres_url: str | None = None,
) -> BenchmarkRun:
    """Profile first, then measure each workload after excluded warmup calls."""

    requests: list[RequestMeasurement] = []
    resources: list[ResourceMeasurement] = []
    omitted: dict[str, str] = {}
    async with AsyncExitStack() as stack:
        workloads = await _workloads(stack, config, postgres_url, omitted)
        profile = await _profile_executor(workloads)
        sequence = 0
        for workload in workloads:
            await _warmup(workload, config.warmup_requests)
            for concurrency in workload.concurrency_levels:
                count = max(config.requests_per_trial, concurrency)
                for trial in range(1, config.trials + 1):
                    measured, resource = await _measure_trial(
                        workload,
                        concurrency=concurrency,
                        trial=trial,
                        count=count,
                        first_sequence=sequence,
                    )
                    sequence += count
                    requests.extend(measured)
                    resources.append(resource)
    return BenchmarkRun(
        generated_at=datetime.now(UTC),
        config=config,
        requests=tuple(requests),
        resources=tuple(resources),
        omitted=omitted,
        profile=profile,
    )


def write_benchmark_artifacts(
    run: BenchmarkRun,
    output_directory: Path,
    *,
    command: str,
) -> dict[str, object]:
    """Write one immutable artifact directory and return its computed summary."""

    if output_directory.exists():
        raise ValueError("benchmark output directory already exists")
    output_directory.mkdir(parents=True)
    manifest = {
        "schema_version": "1.0",
        "generated_at": run.generated_at.isoformat().replace("+00:00", "Z"),
        "git_commit": _git_commit(),
        "command": command,
        "environment": _environment(),
        "configuration": {
            "concurrency_levels": list(run.config.concurrency_levels),
            "postgres_concurrency_levels": list(run.config.postgres_concurrency_levels),
            "warmup_requests": run.config.warmup_requests,
            "requests_per_trial": run.config.requests_per_trial,
            "trials": run.config.trials,
        },
        "omitted": run.omitted,
        "memory_metric": (
            "Windows process working set and process-lifetime peak working set sampled after each "
            "trial; null on unsupported platforms"
        ),
        "cpu_metric": "Process CPU time from time.process_time_ns",
    }
    summary = summarize(run)
    _write_json(output_directory / "manifest.json", manifest)
    _write_json(output_directory / "summary.json", summary)
    _write_json(output_directory / "profile.json", {"functions": list(run.profile)})
    _write_jsonl(output_directory / "requests.jsonl", [item.document() for item in run.requests])
    _write_jsonl(output_directory / "resources.jsonl", [item.document() for item in run.resources])
    (output_directory / "summary.md").write_text(_summary_markdown(summary), encoding="utf-8")
    return summary


def summarize(run: BenchmarkRun) -> dict[str, object]:
    """Aggregate raw samples without averaging worker percentiles."""

    groups: list[dict[str, object]] = []
    keys = sorted({(item.workload, item.concurrency) for item in run.requests})
    for workload, concurrency in keys:
        samples = [
            item
            for item in run.requests
            if item.workload == workload and item.concurrency == concurrency
        ]
        trial_resources = [
            item
            for item in run.resources
            if item.workload == workload and item.concurrency == concurrency
        ]
        latencies = [item.latency_ms for item in samples]
        errors = sum(item.error_code is not None for item in samples)
        groups.append(
            {
                "workload": workload,
                "concurrency": concurrency,
                "requests": len(samples),
                "trials": len(trial_resources),
                "median_requests_per_second": round(
                    median(item.requests_per_second for item in trial_resources), 3
                ),
                "p50_ms": round(_percentile(latencies, 0.50), 3),
                "p95_ms": round(_percentile(latencies, 0.95), 3),
                "p99_ms": round(_percentile(latencies, 0.99), 3),
                "error_count": errors,
                "error_rate": round(errors / len(samples), 6),
                "median_process_cpu_seconds": round(
                    median(item.process_cpu_seconds for item in trial_resources), 6
                ),
                "median_working_set_bytes": _median_optional(
                    [item.working_set_bytes for item in trial_resources]
                ),
                "maximum_peak_working_set_bytes": _maximum_optional(
                    [item.peak_working_set_bytes for item in trial_resources]
                ),
            }
        )
    return {"schema_version": "1.0", "groups": groups, "omitted": run.omitted}


async def _workloads(
    stack: AsyncExitStack,
    config: BenchmarkConfig,
    postgres_url: str | None,
    omitted: dict[str, str],
) -> tuple[_Workload, ...]:
    definition = _definition(OperationClass.READ)
    success_adapter = _SuccessAdapter()
    invocation = _invocation(definition)

    async def direct_adapter(_sequence: int) -> str | None:
        result = await success_adapter.execute(invocation)
        return None if isinstance(result, AdapterSuccess) else result.code.value

    registry, executor = _executor_stack(definition, success_adapter)

    async def executor_core(_sequence: int) -> str | None:
        result = await executor.execute(
            ExecutionRequest(definition.ref, _REQUEST), principal=_PRINCIPAL
        )
        return result.error.code.value if isinstance(result, ExecutionFailure) else None

    token = _jwt_token()
    rest_client = await stack.enter_async_context(
        httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=create_execution_app(executor, _JWTBenchmarkAuthenticator())
            ),
            base_url="http://sentinelmcp.benchmark",
            headers={"Authorization": f"Bearer {token}"},
        )
    )
    rest_payload = {
        "tool": {"namespace": "benchmark", "name": "invoke", "version": "1.0.0"},
        "arguments": _REQUEST,
    }

    async def rest_jwt(_sequence: int) -> str | None:
        response = await rest_client.post("/v1/execute", json=rest_payload)
        return None if response.status_code == 200 else f"http_{response.status_code}"

    mcp_client = await stack.enter_async_context(
        Client(
            create_mcp_server(
                registry=registry,
                executor=executor,
                principal_resolver=StaticMCPPrincipalResolver(_PRINCIPAL),
            )
        )
    )
    mcp_name = encode_tool_ref(definition.ref)

    async def mcp_in_memory(_sequence: int) -> str | None:
        result = await mcp_client.call_tool(mcp_name, _REQUEST)
        return "mcp_error" if result.is_error else None

    observed_executor = _executor(
        definition,
        _SuccessAdapter(),
        audit=DurableAuditRecorder(InMemoryAuditStore()),
        observed=True,
    )

    async def observed(_sequence: int) -> str | None:
        result = await observed_executor.execute(
            ExecutionRequest(definition.ref, _REQUEST), principal=_PRINCIPAL
        )
        return result.error.code.value if isinstance(result, ExecutionFailure) else None

    failure_executor = _executor(definition, _FailureAdapter())

    async def failure(_sequence: int) -> str | None:
        result = await failure_executor.execute(
            ExecutionRequest(definition.ref, _REQUEST), principal=_PRINCIPAL
        )
        return result.error.code.value if isinstance(result, ExecutionFailure) else None

    workloads = [
        _Workload("direct_adapter", direct_adapter, config.concurrency_levels),
        _Workload("executor_core", executor_core, config.concurrency_levels),
        _Workload("rest_jwt_authenticated", rest_jwt, config.concurrency_levels),
        _Workload("mcp_in_memory", mcp_in_memory, config.concurrency_levels),
        _Workload("executor_audit_telemetry", observed, config.concurrency_levels),
        _Workload("executor_failure", failure, config.concurrency_levels),
    ]
    if postgres_url is None:
        omitted["postgres_mutation"] = "SENTINELMCP_TEST_DATABASE_URL was not provided"
    else:
        workloads.append(await _postgres_workload(stack, config, postgres_url))
    return tuple(workloads)


async def _postgres_workload(
    stack: AsyncExitStack,
    config: BenchmarkConfig,
    postgres_url: str,
) -> _Workload:
    engine = create_postgres_engine(postgres_url)
    clear_mutation_state(engine)
    stack.callback(engine.dispose)
    stack.callback(clear_mutation_state, engine)
    definition = _definition(OperationClass.MUTATION)
    executor = _executor(definition, _SuccessAdapter())
    service = MutationService(
        executor, PostgresMutationStateStore(engine), challenge_ttl=timedelta(minutes=5)
    )
    counter = 0

    async def postgres_mutation(_sequence: int) -> str | None:
        nonlocal counter
        counter += 1
        request = ExecutionRequest(definition.ref, _REQUEST)
        preview = service.preview(request, principal=_PRINCIPAL)
        if not isinstance(preview, MutationPreview):
            return preview.error.code.value
        result = await service.confirm(
            request,
            principal=_PRINCIPAL,
            confirmation_token=preview.confirmation_token,
            idempotency_key=f"benchmark-{counter}",
        )
        return result.error.code.value if isinstance(result, ExecutionFailure) else None

    return _Workload(
        "postgres_mutation_preview_confirm",
        postgres_mutation,
        config.postgres_concurrency_levels,
    )


async def _profile_executor(workloads: Sequence[_Workload]) -> tuple[dict[str, object], ...]:
    workload = next(item for item in workloads if item.name == "executor_core")
    profiler = cProfile.Profile()
    profiler.enable()
    for sequence in range(200):
        await workload.invoke(sequence)
    profiler.disable()
    stats = pstats.Stats(profiler)
    raw_stats = cast(
        dict[tuple[str, int, str], tuple[int, int, float, float, object]],
        stats.stats,  # type: ignore[attr-defined]
    )
    entries = sorted(raw_stats.items(), key=lambda item: item[1][3], reverse=True)[:25]
    return tuple(
        {
            "file": _public_profile_path(file),
            "line": line,
            "function": function,
            "primitive_calls": values[0],
            "total_calls": values[1],
            "total_seconds": round(values[2], 9),
            "cumulative_seconds": round(values[3], 9),
        }
        for (file, line, function), values in entries
    )


async def _warmup(workload: _Workload, count: int) -> None:
    for sequence in range(count):
        await workload.invoke(-sequence - 1)


async def _measure_trial(
    workload: _Workload,
    *,
    concurrency: int,
    trial: int,
    count: int,
    first_sequence: int,
) -> tuple[list[RequestMeasurement], ResourceMeasurement]:
    semaphore = asyncio.Semaphore(concurrency)
    start = asyncio.Event()

    async def invoke(offset: int) -> RequestMeasurement:
        await start.wait()
        request_started = time.perf_counter_ns()
        async with semaphore:
            error = await workload.invoke(first_sequence + offset)
        latency_ms = (time.perf_counter_ns() - request_started) / 1_000_000
        return RequestMeasurement(
            workload.name,
            concurrency,
            trial,
            first_sequence + offset,
            latency_ms,
            error,
        )

    tasks = [asyncio.create_task(invoke(offset)) for offset in range(count)]
    cpu_started = time.process_time_ns()
    wall_started = time.perf_counter_ns()
    start.set()
    measured = list(await asyncio.gather(*tasks))
    elapsed_seconds = (time.perf_counter_ns() - wall_started) / 1_000_000_000
    cpu_seconds = (time.process_time_ns() - cpu_started) / 1_000_000_000
    working_set, peak_working_set = _process_memory_bytes()
    return measured, ResourceMeasurement(
        workload.name,
        concurrency,
        trial,
        elapsed_seconds,
        cpu_seconds,
        working_set,
        peak_working_set,
        count / elapsed_seconds,
    )


def _executor(
    definition: ToolDefinition,
    adapter: _SuccessAdapter | _FailureAdapter,
    *,
    audit: DurableAuditRecorder | None = None,
    observed: bool = False,
) -> ToolExecutor:
    return _executor_stack(
        definition,
        adapter,
        audit=audit,
        observed=observed,
    )[1]


def _executor_stack(
    definition: ToolDefinition,
    adapter: _SuccessAdapter | _FailureAdapter,
    *,
    audit: DurableAuditRecorder | None = None,
    observed: bool = False,
) -> tuple[InMemoryToolRegistry, ToolExecutor]:
    registry = InMemoryToolRegistry()
    registry.register(definition)
    bindings = InMemoryRuntimeBindingRegistry()
    bindings.bind(RuntimeToolBinding(definition.ref, adapter))
    policy = RuleBasedAuthorizationPolicy(
        (AuthorizationRule(PolicyEffect.ALLOW, definition.identity, definition.operation_class),)
    )
    observability = None
    if observed:
        observability = OpenTelemetryExecutionObservability(
            trace.get_tracer("sentinelmcp.benchmark"), metrics=PrometheusMetrics()
        )
    return (
        registry,
        ToolExecutor(
            registry,
            bindings,
            authorization_policy=policy,
            audit=audit,
            observability=observability,
        ),
    )


class _SuccessAdapter:
    async def execute(self, invocation: AdapterInvocation) -> AdapterResult:
        del invocation
        return AdapterSuccess({"ok": True})


class _FailureAdapter:
    async def execute(self, invocation: AdapterInvocation) -> AdapterResult:
        del invocation
        return AdapterFailure(ExecutionErrorCode.UPSTREAM_UNAVAILABLE)


def _definition(operation: OperationClass) -> ToolDefinition:
    schema = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
        "additionalProperties": False,
    }
    return ToolDefinition(
        namespace=ToolNamespace("benchmark"),
        name=ToolName("invoke"),
        version=ToolVersion("1.0.0"),
        description="Execute one deterministic benchmark operation.",
        input_schema=schema,
        output_schema={
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        },
        operation_class=operation,
        idempotency_supported=operation is OperationClass.MUTATION,
        capabilities=frozenset({"benchmark", "synthetic"}),
    )


def _invocation(definition: ToolDefinition) -> AdapterInvocation:
    request = ExecutionRequest(definition.ref, _REQUEST)
    return AdapterInvocation(
        ExecutionContext(
            RequestId.generate(),
            ExecutionId.generate(),
            definition.ref,
            fingerprint_tool_definition(definition),
            datetime.now(UTC),
            time.monotonic(),
        ),
        cast(FrozenJSONMapping, request.arguments),
    )


def _jwt_token() -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "iss": _PRINCIPAL.issuer,
            "sub": _PRINCIPAL.subject,
            "aud": "sentinelmcp-benchmark",
            "iat": now,
            "exp": now + timedelta(hours=1),
        },
        _JWT_SECRET,
        algorithm="HS256",
        headers={"kid": "benchmark"},
    )


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        raise ValueError("percentile requires at least one value")
    ordered = sorted(values)
    return ordered[max(0, math.ceil(quantile * len(ordered)) - 1)]


def _median_optional(values: list[int | None]) -> int | None:
    measured = [value for value in values if value is not None]
    return None if not measured else round(median(measured))


def _maximum_optional(values: list[int | None]) -> int | None:
    measured = [value for value in values if value is not None]
    return None if not measured else max(measured)


def _is_windows() -> bool:
    return sys.platform == "win32"


def _process_memory_bytes() -> tuple[int | None, int | None]:
    if not _is_windows():
        return None, None
    import ctypes
    from ctypes import wintypes

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    # Linux typeshed omits this Windows-only loader even when analyzing portable source.
    win_dll = cast(type[ctypes.CDLL], vars(ctypes)["WinDLL"])
    kernel32 = win_dll("kernel32", use_last_error=True)
    psapi = win_dll("psapi", use_last_error=True)
    get_current_process = kernel32.GetCurrentProcess
    get_current_process.argtypes = []
    get_current_process.restype = wintypes.HANDLE
    get_process_memory_info = psapi.GetProcessMemoryInfo
    get_process_memory_info.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ProcessMemoryCounters),
        wintypes.DWORD,
    ]
    get_process_memory_info.restype = wintypes.BOOL

    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    process = get_current_process()
    succeeded = get_process_memory_info(
        process,
        ctypes.byref(counters),
        counters.cb,
    )
    if not succeeded:
        return None, None
    return int(counters.WorkingSetSize), int(counters.PeakWorkingSetSize)


def _public_profile_path(raw_path: str) -> str:
    path = Path(raw_path)
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        parts = path.parts
        for marker in ("site-packages", "Lib"):
            if marker in parts:
                return "/".join(parts[parts.index(marker) :])
        return path.name


def _environment() -> dict[str, object]:
    packages = {name: version(name) for name in ("fastapi", "httpx", "mcp", "sqlalchemy")}
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "python": sys.version,
        "cpu_count": os.cpu_count(),
        "packages": packages,
    }


def _git_commit() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _write_json(path: Path, document: object) -> None:
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, documents: Sequence[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(document, sort_keys=True) + "\n" for document in documents),
        encoding="utf-8",
    )


def _summary_markdown(summary: dict[str, object]) -> str:
    groups = summary["groups"]
    assert isinstance(groups, list)
    lines = [
        "# Phase 12 Benchmark Summary",
        "",
        "| Workload | Concurrency | Requests | RPS | p50 ms | p95 ms | p99 ms | Error rate |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for raw_group in groups:
        assert isinstance(raw_group, dict)
        lines.append(
            "| {workload} | {concurrency} | {requests} | {median_requests_per_second} | "
            "{p50_ms} | {p95_ms} | {p99_ms} | {error_rate} |".format(**raw_group)
        )
    return "\n".join(lines) + "\n"
