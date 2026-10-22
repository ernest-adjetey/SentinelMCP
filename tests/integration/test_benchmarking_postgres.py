"""Small benchmark smoke over the real PostgreSQL mutation state store."""

from __future__ import annotations

import asyncio

import pytest

from sentinelmcp.benchmarking import BenchmarkConfig, run_benchmark_suite, summarize


@pytest.mark.postgres
def test_postgres_mutation_benchmark_path(postgres_database_url: str) -> None:
    run = asyncio.run(
        run_benchmark_suite(
            BenchmarkConfig(
                concurrency_levels=(1,),
                postgres_concurrency_levels=(1, 2),
                warmup_requests=1,
                requests_per_trial=2,
                trials=1,
            ),
            postgres_url=postgres_database_url,
        )
    )
    summary = summarize(run)
    groups = summary["groups"]

    assert isinstance(groups, list)
    postgres = [
        group for group in groups if group["workload"] == "postgres_mutation_preview_confirm"
    ]
    assert [group["concurrency"] for group in postgres] == [1, 2]
    assert all(group["error_rate"] == 0 for group in postgres)
    assert run.omitted == {}
