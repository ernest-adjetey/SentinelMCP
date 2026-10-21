"""End-to-end command tests over the shared demo executor and replay service."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sentinelmcp.cli import main


def test_tools_list_and_describe_emit_exact_versioned_contracts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = tmp_path / "replays"

    assert main(["tools", "list", "--store", str(store)]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["status"] == "ok"
    assert [tool["identity"] for tool in listed["tools"]] == [
        "demo.catalog.lookup",
        "demo.math.add",
        "demo.profile.get",
    ]
    assert all(tool["versions"] == ["1.0.0"] for tool in listed["tools"])

    assert (
        main(
            [
                "tools",
                "describe",
                "demo.math.add@1.0.0",
                "--store",
                str(store),
            ]
        )
        == 0
    )
    described = json.loads(capsys.readouterr().out)
    assert described["tool"]["namespace"] == "demo.math"
    assert described["tool"]["name"] == "add"
    assert "replay-safe" in described["tool"]["capabilities"]


def test_execute_inspect_dry_run_and_replay_share_one_record(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = tmp_path / "replays"
    execute_args = [
        "execute",
        "demo.math.add@1.0.0",
        "--arguments",
        '{"a":2,"b":3}',
        "--record",
        "--store",
        str(store),
    ]

    assert main(execute_args) == 0
    executed = json.loads(capsys.readouterr().out)
    record_id = executed["replay_record_id"]
    assert executed["result"] == {"sum": 5}

    assert main(["inspect", record_id, "--store", str(store)]) == 0
    inspected = json.loads(capsys.readouterr().out)
    assert inspected["record"]["tool_ref"] == {
        "namespace": "demo.math",
        "name": "add",
        "version": "1.0.0",
    }
    assert inspected["record"]["arguments"] == {"a": 2, "b": 3}

    assert main(["replay", record_id, "--dry-run", "--store", str(store)]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["plan"]["can_execute"] is True
    assert plan["plan"]["target_kind"] == "synthetic"

    assert main(["replay", record_id, "--store", str(store)]) == 0
    replayed = json.loads(capsys.readouterr().out)
    assert replayed["result"] == {"sum": 5}
    assert replayed["replay_record_id"] == record_id


def test_cli_returns_stable_errors_without_argument_echo(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = tmp_path / "replays"
    secret_input = '{"a":2,"a":"private-value"}'

    assert (
        main(
            [
                "execute",
                "demo.math.add@1.0.0",
                "--arguments",
                secret_input,
                "--store",
                str(store),
            ]
        )
        == 2
    )
    invalid = capsys.readouterr()
    assert invalid.out == ""
    assert json.loads(invalid.err)["error"]["code"] == "invalid_command"
    assert "private-value" not in invalid.err

    assert (
        main(
            [
                "execute",
                "demo.math.add@2.0.0",
                "--arguments",
                '{"a":2,"b":3}',
                "--store",
                str(store),
            ]
        )
        == 1
    )
    missing = json.loads(capsys.readouterr().out)
    assert missing["error"]["code"] == "tool_not_found"

    assert (
        main(
            [
                "execute",
                "demo.math.add@1.0.0",
                "--arguments",
                "[]",
                "--store",
                str(store),
            ]
        )
        == 2
    )
    non_object = json.loads(capsys.readouterr().err)
    assert non_object["error"]["code"] == "invalid_command"


def test_eval_and_benchmark_commands_return_measured_machine_readable_results(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    store = tmp_path / "replays"

    assert main(["eval", "run", "--store", str(store)]) == 0
    evaluation = json.loads(capsys.readouterr().out)["evaluation"]
    assert evaluation["scenario_count"] == 3
    assert evaluation["successes"] == 3
    assert evaluation["failures"] == 0

    assert (
        main(
            [
                "benchmark",
                "run",
                "--iterations",
                "5",
                "--store",
                str(store),
            ]
        )
        == 0
    )
    benchmark = json.loads(capsys.readouterr().out)["benchmark"]
    assert benchmark["iterations"] == 5
    assert benchmark["requests_per_second"] > 0
    assert benchmark["p50_ms"] >= 0
    assert benchmark["p95_ms"] >= benchmark["p50_ms"]
    assert benchmark["p99_ms"] >= benchmark["p95_ms"]
    assert benchmark["error_rate"] == 0


def test_benchmark_rejects_unbounded_iteration_counts() -> None:
    with pytest.raises(SystemExit):
        main(["benchmark", "run", "--iterations", "10001"])
    with pytest.raises(SystemExit):
        main(["benchmark", "run", "--iterations", "many"])
