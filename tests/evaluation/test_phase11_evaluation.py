"""End-to-end tests for the deterministic Phase 11 evaluation suite."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast

import pytest

from sentinelmcp.evaluation import (
    EvaluationReport,
    EvaluationScenario,
    OutcomeAttribution,
    ScenarioKind,
    ScenarioResult,
    attribute_error,
    load_phase11_catalog,
    parse_scenario_catalog,
    run_phase11_evaluation,
)
from sentinelmcp.execution import ExecutionErrorCode
from sentinelmcp.fault_injection import HTTPFaultKind, HTTPFaultStep


def test_packaged_catalog_covers_the_required_fault_matrix() -> None:
    catalog = load_phase11_catalog()
    scenario_ids = {scenario.scenario_id for scenario in catalog.scenarios}

    assert catalog.suite_id == "phase11-fault-injection"
    assert catalog.suite_version == "1.0.0"
    assert len(scenario_ids) == 23
    assert {
        "http-400",
        "http-401",
        "http-403",
        "http-404",
        "http-409",
        "http-429",
        "http-500",
        "http-502",
        "http-503",
        "http-504",
        "malformed-json",
        "wrong-content-type",
        "invalid-utf8",
        "oversized-response",
        "timeout",
        "slow-response",
        "connection-failure",
        "connection-drop",
        "intermittent-outage",
        "output-schema-violation",
        "policy-rejection",
        "duplicate-request",
        "mutation-ambiguity",
    } == scenario_ids


def test_complete_phase11_suite_matches_every_declared_expectation() -> None:
    report = asyncio.run(run_phase11_evaluation())
    document = report.to_document()

    assert report.passed == 23
    assert document["failed"] == 0
    assert document["scenario_count"] == 23
    assert document["attribution_counts"] == {
        "MODEL_ERROR": 0,
        "TOOL_ERROR": 8,
        "INFRASTRUCTURE_ERROR": 11,
        "POLICY_REJECTION": 1,
        "SUCCESS": 3,
    }
    results = {result.scenario_id: result for result in report.results}
    assert results["policy-rejection"].observed_dispatches == 0
    assert results["intermittent-outage"].labels == ("retry_recovered",)
    assert results["duplicate-request"].labels == ("duplicate_reused",)
    assert results["mutation-ambiguity"].labels == ("outcome_ambiguous",)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (None, OutcomeAttribution.SUCCESS),
        (ExecutionErrorCode.AUTHORIZATION_FAILED, OutcomeAttribution.POLICY_REJECTION),
        (ExecutionErrorCode.UPSTREAM_BAD_REQUEST, OutcomeAttribution.TOOL_ERROR),
        (ExecutionErrorCode.UPSTREAM_UNAVAILABLE, OutcomeAttribution.INFRASTRUCTURE_ERROR),
    ],
)
def test_error_attribution_is_stable(
    error: ExecutionErrorCode | None,
    expected: OutcomeAttribution,
) -> None:
    assert attribute_error(error) is expected


def test_scenario_model_rejects_incoherent_definitions() -> None:
    step = HTTPFaultStep(HTTPFaultKind.SUCCESS)
    with pytest.raises(ValueError, match="kebab-case"):
        EvaluationScenario(
            "Not Valid",
            "Description.",
            ScenarioKind.HTTP,
            OutcomeAttribution.SUCCESS,
            None,
            1,
            (step,),
        )

    valid = EvaluationScenario(
        "valid",
        "Description.",
        ScenarioKind.HTTP,
        OutcomeAttribution.SUCCESS,
        None,
        1,
        (step,),
    )
    with pytest.raises(ValueError, match="trimmed"):
        replace(valid, description=" not trimmed")
    with pytest.raises(TypeError, match="ScenarioKind"):
        replace(valid, kind=cast(ScenarioKind, "http"))
    with pytest.raises(TypeError, match="OutcomeAttribution"):
        replace(valid, expected_attribution=cast(OutcomeAttribution, "SUCCESS"))
    with pytest.raises(TypeError, match="ExecutionErrorCode"):
        replace(valid, expected_error=cast(ExecutionErrorCode, "bad"))
    with pytest.raises(ValueError, match="0 through 10"):
        replace(valid, expected_dispatches=11)
    with pytest.raises(ValueError, match="require at least one"):
        EvaluationScenario(
            "missing-fault",
            "Description.",
            ScenarioKind.HTTP,
            OutcomeAttribution.SUCCESS,
            None,
            1,
        )
    with pytest.raises(ValueError, match="only HTTP"):
        EvaluationScenario(
            "extra-fault",
            "Description.",
            ScenarioKind.POLICY_REJECTION,
            OutcomeAttribution.POLICY_REJECTION,
            ExecutionErrorCode.AUTHORIZATION_FAILED,
            0,
            (step,),
        )
    with pytest.raises(ValueError, match="cannot expect an error"):
        EvaluationScenario(
            "bad-recovery",
            "Description.",
            ScenarioKind.HTTP,
            OutcomeAttribution.TOOL_ERROR,
            ExecutionErrorCode.UPSTREAM_UNAVAILABLE,
            1,
            (step,),
            True,
        )


def test_result_model_rejects_invalid_measurements_and_serializes_errors() -> None:
    valid = ScenarioResult(
        "measured",
        False,
        OutcomeAttribution.TOOL_ERROR,
        OutcomeAttribution.TOOL_ERROR,
        ExecutionErrorCode.UPSTREAM_BAD_REQUEST,
        ExecutionErrorCode.UPSTREAM_BAD_REQUEST,
        1,
        1,
        1.2345,
    )
    document = valid.to_document()
    report = EvaluationReport(
        "suite",
        "1.0.0",
        datetime(2026, 10, 21, tzinfo=UTC),
        2.3456,
        (valid,),
    ).to_document()

    assert document["expected_error"] == "upstream_bad_request"
    assert document["duration_ms"] == 1.234
    assert report["generated_at"] == "2026-10-21T00:00:00Z"
    assert report["elapsed_ms"] == 2.346
    with pytest.raises(ValueError, match="kebab-case"):
        replace(valid, scenario_id="INVALID")
    with pytest.raises(TypeError, match="boolean"):
        replace(valid, passed=cast(bool, 1))
    with pytest.raises(ValueError, match="non-negative"):
        replace(valid, observed_dispatches=-1)
    with pytest.raises(ValueError, match="finite"):
        replace(valid, duration_ms=float("inf"))


def _catalog_document() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "suite_id": "test-suite",
        "suite_version": "1.0.0",
        "scenarios": [
            {
                "id": "success",
                "description": "Successful dependency response.",
                "kind": "http",
                "expected_attribution": "SUCCESS",
                "expected_error": None,
                "expected_dispatches": 1,
                "faults": [{"kind": "success"}],
            }
        ],
    }


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda value: value.update(schema_version="2.0"), "schema_version"),
        (lambda value: value.update(scenarios=[]), "non-empty array"),
        (
            lambda value: cast(list[object], value["scenarios"]).append(
                cast(list[object], value["scenarios"])[0]
            ),
            "unique",
        ),
        (
            lambda value: cast(dict[str, object], cast(list[object], value["scenarios"])[0]).update(
                unknown=True
            ),
            "unknown fields",
        ),
        (
            lambda value: cast(dict[str, object], cast(list[object], value["scenarios"])[0]).pop(
                "id"
            ),
            "missing fields",
        ),
        (
            lambda value: cast(dict[str, object], cast(list[object], value["scenarios"])[0]).update(
                expected_dispatches=True
            ),
            "must be an integer",
        ),
        (
            lambda value: cast(dict[str, object], cast(list[object], value["scenarios"])[0]).update(
                faults="bad"
            ),
            "must be an array",
        ),
        (
            lambda value: cast(dict[str, object], cast(list[object], value["scenarios"])[0]).update(
                retry_recovered="bad"
            ),
            "must be a boolean",
        ),
    ],
)
def test_catalog_rejects_invalid_root_and_scenario_documents(
    mutate: Callable[[dict[str, object]], object],
    message: str,
) -> None:
    document = _catalog_document()
    mutate(document)
    with pytest.raises(ValueError, match=message):
        parse_scenario_catalog(document)


@pytest.mark.parametrize(
    ("fault", "message"),
    [
        ({"kind": "success", "unknown": True}, "unknown fields"),
        ({}, "missing kind"),
        ({"kind": "status", "status": "500"}, "status must be an integer"),
        ({"kind": "success", "delay_ms": True}, "delay_ms must be an integer"),
        ({"kind": "success", "response_bytes": True}, "response_bytes must be an integer"),
    ],
)
def test_catalog_rejects_invalid_fault_documents(
    fault: dict[str, object],
    message: str,
) -> None:
    document = _catalog_document()
    scenario = cast(dict[str, object], cast(list[object], document["scenarios"])[0])
    scenario["faults"] = [fault]
    with pytest.raises(ValueError, match=message):
        parse_scenario_catalog(document)


def test_catalog_rejects_non_objects_empty_strings_wrong_keys_and_duplicate_json() -> None:
    from sentinelmcp.evaluation.catalog import _unique_object

    with pytest.raises(ValueError, match="must be an object"):
        parse_scenario_catalog([])
    document = _catalog_document()
    document["suite_id"] = ""
    with pytest.raises(ValueError, match="non-empty string"):
        parse_scenario_catalog(document)
    document = _catalog_document()
    document["unexpected"] = True
    with pytest.raises(ValueError, match="fields do not match"):
        parse_scenario_catalog(document)
    with pytest.raises(ValueError):
        _unique_object([("key", 1), ("key", 2)])
