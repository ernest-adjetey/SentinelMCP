"""JSON-emitting operator CLI over the transport-independent application services."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sentinelmcp.application import DemoRuntime, create_demo_runtime
from sentinelmcp.audit import DurableAuditRecorder, InMemoryAuditStore
from sentinelmcp.benchmarking import (
    BenchmarkConfig,
    run_benchmark_suite,
    write_benchmark_artifacts,
)
from sentinelmcp.domain import SentinelMCPError, ToolRef
from sentinelmcp.domain.json_values import thaw_json
from sentinelmcp.evaluation import run_phase11_evaluation
from sentinelmcp.execution import (
    ExecutionError,
    ExecutionFailure,
    ExecutionRequest,
    ExecutionResult,
    ExecutionSuccess,
)
from sentinelmcp.policy import Principal
from sentinelmcp.registry import canonicalize_tool_definition
from sentinelmcp.replay import (
    FileReplayStore,
    ReplayError,
    ReplayPlan,
    ReplayRecordId,
    ReplayService,
    replay_record_to_document,
)

DEFAULT_REPLAY_DIRECTORY = Path(".sentinelmcp") / "replays"
DEFAULT_PRINCIPAL_ISSUER = "urn:sentinelmcp:cli"
DEFAULT_PRINCIPAL_SUBJECT = "local-operator"
MAX_BENCHMARK_ITERATIONS = 10_000


@dataclass(frozen=True, slots=True)
class _CliRuntime:
    demo: DemoRuntime
    replay: ReplayService


def main(argv: Sequence[str] | None = None) -> int:
    """Run one CLI command and return a process exit code."""

    parser = _parser()
    arguments = parser.parse_args(argv)
    try:
        document, exit_code = asyncio.run(_dispatch(arguments))
    except (ReplayError, SentinelMCPError, ValueError) as error:
        _emit(_known_error(error), stream=sys.stderr)
        return 2
    except Exception:
        _emit(
            {
                "status": "error",
                "error": {"code": "internal_error", "message": "Command failed."},
            },
            stream=sys.stderr,
        )
        return 1
    _emit(document, stream=sys.stdout)
    return exit_code


async def _dispatch(arguments: argparse.Namespace) -> tuple[dict[str, object], int]:
    runtime = _runtime(Path(arguments.store))
    principal = Principal(arguments.principal_subject, arguments.principal_issuer)

    if arguments.command == "tools":
        if arguments.tools_command == "list":
            tools = [
                {
                    "identity": str(identity),
                    "versions": [
                        str(version) for version in runtime.demo.registry.list_versions(identity)
                    ],
                }
                for identity in runtime.demo.registry.list_tools()
            ]
            return {"status": "ok", "tools": tools}, 0
        definition = runtime.demo.registry.get(ToolRef.parse(arguments.tool_ref))
        document = json.loads(canonicalize_tool_definition(definition))
        return {"status": "ok", "tool": document}, 0

    if arguments.command == "execute":
        request = ExecutionRequest(
            ToolRef.parse(arguments.tool_ref),
            _json_object(arguments.arguments),
        )
        execution_result = await runtime.demo.executor.execute(request, principal=principal)
        document = _execution_document(execution_result)
        if arguments.record and isinstance(execution_result, ExecutionSuccess):
            record = runtime.replay.capture(
                request,
                principal=principal,
                result=execution_result,
            )
            document["replay_record_id"] = str(record.record_id)
        return document, 0 if isinstance(execution_result, ExecutionSuccess) else 1

    if arguments.command == "inspect":
        record = runtime.replay.inspect(ReplayRecordId.parse(arguments.record_id))
        return {"status": "ok", "record": replay_record_to_document(record)}, 0

    if arguments.command == "replay":
        replay_result = await runtime.replay.replay(
            ReplayRecordId.parse(arguments.record_id),
            principal=principal,
            dry_run=arguments.dry_run,
        )
        if isinstance(replay_result, ReplayPlan):
            return {"status": "ok", "plan": _plan_document(replay_result)}, 0
        document = _execution_document(replay_result)
        document["replay_record_id"] = arguments.record_id
        return document, 0 if isinstance(replay_result, ExecutionSuccess) else 1

    if arguments.command == "eval":
        report = await run_phase11_evaluation(principal=principal)
        evaluation = report.to_document()
        evaluation_document: dict[str, object] = {
            "status": "ok" if report.passed == len(report.results) else "error",
            "evaluation": evaluation,
        }
        if arguments.output is not None:
            output_path = Path(arguments.output)
            output_path.write_text(
                json.dumps(evaluation, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            evaluation_document["artifact"] = str(output_path)
        return evaluation_document, 0 if report.passed == len(report.results) else 1

    if arguments.command == "benchmark":
        if arguments.benchmark_command == "suite":
            config = BenchmarkConfig(
                concurrency_levels=arguments.concurrency,
                postgres_concurrency_levels=arguments.postgres_concurrency,
                warmup_requests=arguments.warmup,
                requests_per_trial=arguments.requests,
                trials=arguments.trials,
            )
            run = await run_benchmark_suite(
                config,
                postgres_url=os.environ.get("SENTINELMCP_TEST_DATABASE_URL"),
            )
            output = Path(arguments.output)
            command = (
                "sentinel benchmark suite "
                f"--output {output} --trials {config.trials} "
                f"--requests {config.requests_per_trial} --warmup {config.warmup_requests} "
                f"--concurrency {','.join(map(str, config.concurrency_levels))} "
                "--postgres-concurrency "
                f"{','.join(map(str, config.postgres_concurrency_levels))}"
            )
            summary = write_benchmark_artifacts(run, output, command=command)
            return {
                "status": "ok",
                "artifact": str(output),
                "summary": summary,
            }, 0
        return await _run_benchmark(runtime.demo, principal, arguments.iterations), 0

    raise ValueError("unknown command")


def _runtime(store_path: Path) -> _CliRuntime:
    demo = create_demo_runtime()
    recorder = DurableAuditRecorder(InMemoryAuditStore())
    replay = ReplayService(
        demo.registry,
        demo.bindings,
        demo.executor,
        FileReplayStore(store_path),
        recorder,
    )
    return _CliRuntime(demo, replay)


async def _run_benchmark(
    runtime: DemoRuntime,
    principal: Principal,
    iterations: int,
) -> dict[str, object]:
    request = ExecutionRequest(ToolRef.parse("demo.math.add@1.0.0"), {"a": 2, "b": 3})
    for _ in range(min(3, iterations)):
        await runtime.executor.execute(request, principal=principal)

    durations_ms: list[float] = []
    errors = 0
    started = time.perf_counter_ns()
    for _ in range(iterations):
        iteration_started = time.perf_counter_ns()
        result = await runtime.executor.execute(request, principal=principal)
        durations_ms.append((time.perf_counter_ns() - iteration_started) / 1_000_000)
        errors += isinstance(result, ExecutionFailure)
    elapsed_seconds = (time.perf_counter_ns() - started) / 1_000_000_000
    return {
        "status": "ok",
        "benchmark": {
            "path": "core_executor_synthetic",
            "iterations": iterations,
            "requests_per_second": round(iterations / elapsed_seconds, 3),
            "p50_ms": round(_percentile(durations_ms, 0.50), 3),
            "p95_ms": round(_percentile(durations_ms, 0.95), 3),
            "p99_ms": round(_percentile(durations_ms, 0.99), 3),
            "error_rate": round(errors / iterations, 6),
        },
    }


def _execution_document(result: ExecutionResult) -> dict[str, object]:
    document: dict[str, object] = {
        "status": "success" if isinstance(result, ExecutionSuccess) else "error",
        "tool": str(result.context.tool_ref),
        "request_id": str(result.context.request_id),
        "execution_id": str(result.context.execution_id),
        "definition_fingerprint": result.context.definition_fingerprint,
        "duration_ms": result.duration_ms,
    }
    if isinstance(result, ExecutionSuccess):
        document["result"] = thaw_json(result.result)
    else:
        document["error"] = _execution_error_document(result.error)
    return document


def _execution_error_document(error: ExecutionError) -> dict[str, object]:
    document: dict[str, object] = {"code": error.code.value, "message": error.message}
    if error.upstream_status is not None:
        document["upstream_status"] = error.upstream_status
    if error.retry_after_ms is not None:
        document["retry_after_ms"] = error.retry_after_ms
    return document


def _plan_document(plan: ReplayPlan) -> dict[str, object]:
    return {
        "record_id": str(plan.record_id),
        "tool": str(plan.tool_ref),
        "definition_fingerprint": plan.definition_fingerprint,
        "can_execute": plan.can_execute,
        "error_code": None if plan.error_code is None else plan.error_code.value,
        "target_kind": plan.target_kind,
    }


def _json_object(value: str) -> dict[str, object]:
    try:
        document = json.loads(value, object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, _DuplicateJSONKeyError):
        raise ValueError("arguments must be strict JSON") from None
    if not isinstance(document, dict):
        raise ValueError("arguments must be a JSON object")
    return document


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJSONKeyError(key)
        result[key] = value
    return result


class _DuplicateJSONKeyError(ValueError):
    pass


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(quantile * len(ordered)) - 1)
    return ordered[index]


def _known_error(error: Exception) -> dict[str, object]:
    code = getattr(error, "code", "invalid_command")
    message = (
        str(error) if isinstance(error, ReplayError | SentinelMCPError) else "Invalid command."
    )
    return {"status": "error", "error": {"code": code, "message": message}}


def _emit(document: dict[str, object], *, stream: Any) -> None:
    print(
        json.dumps(document, ensure_ascii=True, separators=(",", ":"), sort_keys=True),
        file=stream,
    )


def _bounded_iterations(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("iterations must be an integer") from None
    if not 1 <= parsed <= MAX_BENCHMARK_ITERATIONS:
        raise argparse.ArgumentTypeError(
            f"iterations must be from 1 through {MAX_BENCHMARK_ITERATIONS}"
        )
    return parsed


def _bounded_number(value: str, *, name: str, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{name} must be an integer") from None
    if not minimum <= parsed <= maximum:
        raise argparse.ArgumentTypeError(f"{name} must be from {minimum} through {maximum}")
    return parsed


def _concurrency_levels(value: str) -> tuple[int, ...]:
    try:
        levels = tuple(int(part) for part in value.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError("concurrency must be comma-separated integers") from None
    if (
        not levels
        or tuple(sorted(set(levels))) != levels
        or not all(1 <= level <= 500 for level in levels)
    ):
        raise argparse.ArgumentTypeError(
            "concurrency must contain unique ascending values from 1 through 500"
        )
    return levels


def _add_identity_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--principal-issuer", default=DEFAULT_PRINCIPAL_ISSUER)
    parser.add_argument("--principal-subject", default=DEFAULT_PRINCIPAL_SUBJECT)


def _add_store_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--store",
        default=os.environ.get("SENTINELMCP_REPLAY_DIR", str(DEFAULT_REPLAY_DIRECTORY)),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sentinel")
    subparsers = parser.add_subparsers(dest="command", required=True)

    tools = subparsers.add_parser("tools")
    tools_subparsers = tools.add_subparsers(dest="tools_command", required=True)
    tools_list = tools_subparsers.add_parser("list")
    tools_describe = tools_subparsers.add_parser("describe")
    tools_describe.add_argument("tool_ref")
    for command in (tools_list, tools_describe):
        _add_identity_arguments(command)
        _add_store_argument(command)

    execute = subparsers.add_parser("execute")
    execute.add_argument("tool_ref")
    execute.add_argument("--arguments", required=True)
    execute.add_argument("--record", action="store_true")
    _add_identity_arguments(execute)
    _add_store_argument(execute)

    inspect = subparsers.add_parser("inspect")
    inspect.add_argument("record_id")
    _add_identity_arguments(inspect)
    _add_store_argument(inspect)

    replay = subparsers.add_parser("replay")
    replay.add_argument("record_id")
    replay.add_argument("--dry-run", action="store_true")
    _add_identity_arguments(replay)
    _add_store_argument(replay)

    evaluation = subparsers.add_parser("eval")
    evaluation_subparsers = evaluation.add_subparsers(dest="eval_command", required=True)
    evaluation_run = evaluation_subparsers.add_parser("run")
    evaluation_run.add_argument("--output")
    _add_identity_arguments(evaluation_run)
    _add_store_argument(evaluation_run)

    benchmark = subparsers.add_parser("benchmark")
    benchmark_subparsers = benchmark.add_subparsers(dest="benchmark_command", required=True)
    benchmark_run = benchmark_subparsers.add_parser("run")
    benchmark_run.add_argument("--iterations", type=_bounded_iterations, default=100)
    _add_identity_arguments(benchmark_run)
    _add_store_argument(benchmark_run)
    benchmark_suite = benchmark_subparsers.add_parser("suite")
    benchmark_suite.add_argument("--output", required=True)
    benchmark_suite.add_argument(
        "--trials",
        type=lambda value: _bounded_number(value, name="trials", minimum=1, maximum=20),
        default=5,
    )
    benchmark_suite.add_argument(
        "--requests",
        type=lambda value: _bounded_number(value, name="requests", minimum=1, maximum=100_000),
        default=200,
    )
    benchmark_suite.add_argument(
        "--warmup",
        type=lambda value: _bounded_number(value, name="warmup", minimum=0, maximum=10_000),
        default=20,
    )
    benchmark_suite.add_argument(
        "--concurrency",
        type=_concurrency_levels,
        default=(1, 10, 50, 100),
    )
    benchmark_suite.add_argument(
        "--postgres-concurrency",
        type=_concurrency_levels,
        default=(1, 10),
    )
    _add_identity_arguments(benchmark_suite)
    _add_store_argument(benchmark_suite)
    return parser
