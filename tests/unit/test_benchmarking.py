"""Tests for reproducible benchmark measurement and artifact generation."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from sentinelmcp.benchmarking import (
    BenchmarkConfig,
    RequestMeasurement,
    ResourceMeasurement,
    run_benchmark_suite,
    summarize,
    write_benchmark_artifacts,
)
from sentinelmcp.benchmarking.runner import _percentile, _public_profile_path


def test_small_suite_measures_every_non_postgres_path_and_writes_artifacts(
    tmp_path: Path,
) -> None:
    config = BenchmarkConfig(
        concurrency_levels=(1, 2),
        postgres_concurrency_levels=(1,),
        warmup_requests=0,
        requests_per_trial=2,
        trials=1,
    )
    run = asyncio.run(run_benchmark_suite(config))
    summary = summarize(run)
    output = tmp_path / "result"
    written = write_benchmark_artifacts(run, output, command="benchmark test command")

    assert len(run.requests) == 24
    assert len(run.resources) == 12
    assert run.omitted == {"postgres_mutation": "SENTINELMCP_TEST_DATABASE_URL was not provided"}
    assert len(run.profile) == 25
    assert written == summary
    assert {path.name for path in output.iterdir()} == {
        "manifest.json",
        "profile.json",
        "requests.jsonl",
        "resources.jsonl",
        "summary.json",
        "summary.md",
    }
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["configuration"]["concurrency_levels"] == [1, 2]
    assert manifest["git_commit"]
    assert "adjet" not in (output / "profile.json").read_text(encoding="utf-8").lower()
    groups = summary["groups"]
    assert isinstance(groups, list)
    assert len(groups) == 12
    failures = [group for group in groups if group["workload"] == "executor_failure"]
    assert all(group["error_rate"] == 1.0 for group in failures)
    assert all(group["requests"] == 2 for group in groups)
    if sys.platform == "win32":
        assert all(item.working_set_bytes is not None for item in run.resources)

    with pytest.raises(ValueError, match="already exists"):
        write_benchmark_artifacts(run, output, command="benchmark test command")


@pytest.mark.parametrize(
    "config",
    [
        BenchmarkConfig(),
        BenchmarkConfig(concurrency_levels=(1, 2)),
    ],
)
def test_config_accepts_bounded_ascending_levels(config: BenchmarkConfig) -> None:
    assert config.trials >= 1


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (lambda: BenchmarkConfig(concurrency_levels=()), "concurrency_levels"),
        (lambda: BenchmarkConfig(concurrency_levels=(2, 1)), "concurrency_levels"),
        (
            lambda: BenchmarkConfig(postgres_concurrency_levels=(0,)),
            "postgres_concurrency_levels",
        ),
        (lambda: BenchmarkConfig(warmup_requests=-1), "warmup_requests"),
        (lambda: BenchmarkConfig(requests_per_trial=0), "requests_per_trial"),
        (lambda: BenchmarkConfig(trials=21), "trials"),
    ],
)
def test_config_rejects_unbounded_values(
    factory: Callable[[], BenchmarkConfig], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        factory()


def test_measurement_documents_round_values_and_summary_helpers_are_bounded() -> None:
    request = RequestMeasurement("path", 1, 1, 0, 1.23456789, None)
    resource = ResourceMeasurement("path", 1, 1, 1.234567891, 0.5, None, None, 8.7654321)

    assert request.document()["latency_ms"] == 1.234568
    assert resource.document()["elapsed_seconds"] == 1.234567891
    assert resource.document()["requests_per_second"] == 8.765432
    assert _percentile([3.0, 1.0, 2.0], 0.5) == 2.0
    with pytest.raises(ValueError, match="at least one"):
        _percentile([], 0.5)
    assert _public_profile_path(str(Path.cwd() / "src" / "module.py")) == "src/module.py"
