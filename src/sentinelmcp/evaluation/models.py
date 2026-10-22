"""Strict data models for deterministic reliability evaluation."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from sentinelmcp.execution import ExecutionErrorCode
from sentinelmcp.fault_injection import HTTPFaultStep

_SCENARIO_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class OutcomeAttribution(StrEnum):
    """Mutually exclusive ownership of one observed scenario outcome."""

    MODEL_ERROR = "MODEL_ERROR"
    TOOL_ERROR = "TOOL_ERROR"
    INFRASTRUCTURE_ERROR = "INFRASTRUCTURE_ERROR"
    POLICY_REJECTION = "POLICY_REJECTION"
    SUCCESS = "SUCCESS"


class ScenarioKind(StrEnum):
    """Closed evaluation behavior families."""

    HTTP = "http"
    POLICY_REJECTION = "policy_rejection"
    DUPLICATE_REQUEST = "duplicate_request"
    MUTATION_AMBIGUITY = "mutation_ambiguity"


@dataclass(frozen=True, slots=True)
class EvaluationScenario:
    """One validated, declarative fault scenario."""

    scenario_id: str
    description: str
    kind: ScenarioKind
    expected_attribution: OutcomeAttribution
    expected_error: ExecutionErrorCode | None
    expected_dispatches: int
    fault_steps: tuple[HTTPFaultStep, ...] = ()
    retry_recovered: bool = False

    def __post_init__(self) -> None:
        if not _SCENARIO_ID.fullmatch(self.scenario_id):
            raise ValueError("scenario_id must be lowercase kebab-case")
        if not self.description or self.description != self.description.strip():
            raise ValueError("description must be non-empty and trimmed")
        if not isinstance(self.kind, ScenarioKind):
            raise TypeError("kind must be a ScenarioKind")
        if not isinstance(self.expected_attribution, OutcomeAttribution):
            raise TypeError("expected_attribution must be an OutcomeAttribution")
        if self.expected_error is not None and not isinstance(
            self.expected_error, ExecutionErrorCode
        ):
            raise TypeError("expected_error must be an ExecutionErrorCode or None")
        if (
            isinstance(self.expected_dispatches, bool)
            or not isinstance(self.expected_dispatches, int)
            or not 0 <= self.expected_dispatches <= 10
        ):
            raise ValueError("expected_dispatches must be from 0 through 10")
        if self.kind is ScenarioKind.HTTP and not self.fault_steps:
            raise ValueError("HTTP scenarios require at least one fault step")
        if self.kind is not ScenarioKind.HTTP and self.fault_steps:
            raise ValueError("only HTTP scenarios may contain fault steps")
        if self.retry_recovered and self.expected_error is not None:
            raise ValueError("a recovered retry scenario cannot expect an error")


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    """Machine-readable observed and expected values for one scenario."""

    scenario_id: str
    passed: bool
    expected_attribution: OutcomeAttribution
    observed_attribution: OutcomeAttribution
    expected_error: ExecutionErrorCode | None
    observed_error: ExecutionErrorCode | None
    expected_dispatches: int
    observed_dispatches: int
    duration_ms: float
    labels: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not _SCENARIO_ID.fullmatch(self.scenario_id):
            raise ValueError("scenario_id must be lowercase kebab-case")
        if not isinstance(self.passed, bool):
            raise TypeError("passed must be a boolean")
        if (
            isinstance(self.observed_dispatches, bool)
            or not isinstance(self.observed_dispatches, int)
            or self.observed_dispatches < 0
        ):
            raise ValueError("observed_dispatches must be non-negative")
        if not math.isfinite(self.duration_ms) or self.duration_ms < 0:
            raise ValueError("duration_ms must be finite and non-negative")

    def to_document(self) -> dict[str, object]:
        return {
            "scenario_id": self.scenario_id,
            "passed": self.passed,
            "expected_attribution": self.expected_attribution.value,
            "observed_attribution": self.observed_attribution.value,
            "expected_error": (None if self.expected_error is None else self.expected_error.value),
            "observed_error": (None if self.observed_error is None else self.observed_error.value),
            "expected_dispatches": self.expected_dispatches,
            "observed_dispatches": self.observed_dispatches,
            "duration_ms": round(self.duration_ms, 3),
            "labels": list(self.labels),
        }


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    """One measured run of a versioned deterministic scenario suite."""

    suite_id: str
    suite_version: str
    generated_at: datetime
    elapsed_ms: float
    results: tuple[ScenarioResult, ...]

    @property
    def passed(self) -> int:
        return sum(result.passed for result in self.results)

    def to_document(self) -> dict[str, object]:
        counts = {
            attribution.value: sum(
                result.observed_attribution is attribution for result in self.results
            )
            for attribution in OutcomeAttribution
        }
        return {
            "schema_version": "1.0",
            "suite_id": self.suite_id,
            "suite_version": self.suite_version,
            "generated_at": self.generated_at.isoformat().replace("+00:00", "Z"),
            "elapsed_ms": round(self.elapsed_ms, 3),
            "scenario_count": len(self.results),
            "passed": self.passed,
            "failed": len(self.results) - self.passed,
            "attribution_counts": counts,
            "results": [result.to_document() for result in self.results],
        }
