"""Load the packaged Phase 11 scenario catalog without executable configuration."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources import files
from typing import cast

from sentinelmcp.evaluation.models import (
    EvaluationScenario,
    OutcomeAttribution,
    ScenarioKind,
)
from sentinelmcp.execution import ExecutionErrorCode
from sentinelmcp.fault_injection import HTTPFaultKind, HTTPFaultStep


class _DuplicateKeyError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ScenarioCatalog:
    suite_id: str
    suite_version: str
    scenarios: tuple[EvaluationScenario, ...]


def load_phase11_catalog() -> ScenarioCatalog:
    """Load and validate the built-in catalog."""

    raw = (
        files("sentinelmcp.evaluation")
        .joinpath("scenarios", "phase11.json")
        .read_text(encoding="utf-8")
    )
    document = json.loads(raw, object_pairs_hook=_unique_object)
    return parse_scenario_catalog(document)


def parse_scenario_catalog(document: object) -> ScenarioCatalog:
    """Validate an already-decoded catalog document."""

    root = _object(document, "catalog")
    _exact_keys(root, {"schema_version", "suite_id", "suite_version", "scenarios"}, "catalog")
    if root["schema_version"] != "1.0":
        raise ValueError("unsupported evaluation catalog schema_version")
    suite_id = _string(root["suite_id"], "suite_id")
    suite_version = _string(root["suite_version"], "suite_version")
    raw_scenarios = root["scenarios"]
    if not isinstance(raw_scenarios, list) or not raw_scenarios:
        raise ValueError("scenarios must be a non-empty array")
    scenarios = tuple(_parse_scenario(item) for item in raw_scenarios)
    ids = [scenario.scenario_id for scenario in scenarios]
    if len(ids) != len(set(ids)):
        raise ValueError("scenario IDs must be unique")
    return ScenarioCatalog(suite_id, suite_version, scenarios)


def _parse_scenario(value: object) -> EvaluationScenario:
    item = _object(value, "scenario")
    allowed = {
        "id",
        "description",
        "kind",
        "expected_attribution",
        "expected_error",
        "expected_dispatches",
        "faults",
        "retry_recovered",
    }
    if unknown := set(item) - allowed:
        raise ValueError(f"scenario has unknown fields: {sorted(unknown)}")
    required = allowed - {"faults", "retry_recovered"}
    if missing := required - set(item):
        raise ValueError(f"scenario is missing fields: {sorted(missing)}")
    raw_error = item["expected_error"]
    expected_error = None if raw_error is None else ExecutionErrorCode(_string(raw_error, "error"))
    raw_dispatches = item["expected_dispatches"]
    if isinstance(raw_dispatches, bool) or not isinstance(raw_dispatches, int):
        raise ValueError("expected_dispatches must be an integer")
    raw_faults = item.get("faults", [])
    if not isinstance(raw_faults, list):
        raise ValueError("faults must be an array")
    retry_recovered = item.get("retry_recovered", False)
    if not isinstance(retry_recovered, bool):
        raise ValueError("retry_recovered must be a boolean")
    return EvaluationScenario(
        scenario_id=_string(item["id"], "id"),
        description=_string(item["description"], "description"),
        kind=ScenarioKind(_string(item["kind"], "kind")),
        expected_attribution=OutcomeAttribution(
            _string(item["expected_attribution"], "expected_attribution")
        ),
        expected_error=expected_error,
        expected_dispatches=raw_dispatches,
        fault_steps=tuple(_parse_fault(fault) for fault in raw_faults),
        retry_recovered=retry_recovered,
    )


def _parse_fault(value: object) -> HTTPFaultStep:
    item = _object(value, "fault")
    allowed = {"kind", "status", "delay_ms", "response_bytes"}
    if unknown := set(item) - allowed:
        raise ValueError(f"fault has unknown fields: {sorted(unknown)}")
    if "kind" not in item:
        raise ValueError("fault is missing kind")
    status = item.get("status")
    delay_ms = item.get("delay_ms", 0)
    response_bytes = item.get("response_bytes", 0)
    if status is not None and (isinstance(status, bool) or not isinstance(status, int)):
        raise ValueError("fault status must be an integer")
    if isinstance(delay_ms, bool) or not isinstance(delay_ms, int):
        raise ValueError("fault delay_ms must be an integer")
    if isinstance(response_bytes, bool) or not isinstance(response_bytes, int):
        raise ValueError("fault response_bytes must be an integer")
    return HTTPFaultStep(
        HTTPFaultKind(_string(item["kind"], "fault kind")),
        status=status,
        delay_seconds=delay_ms / 1_000,
        response_bytes=response_bytes,
    )


def _object(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{field} must be an object")
    return cast(dict[str, object], value)


def _string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _exact_keys(value: dict[str, object], expected: set[str], field: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{field} fields do not match the catalog schema")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(key)
        result[key] = value
    return result
