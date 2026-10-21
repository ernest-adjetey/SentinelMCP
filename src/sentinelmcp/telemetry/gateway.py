"""Bounded gateway and authentication tracing built only on OpenTelemetry API types."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager, suppress
from types import TracebackType
from typing import final

from opentelemetry.trace import Span, Status, StatusCode, Tracer

from sentinelmcp.execution import ExecutionErrorCode
from sentinelmcp.telemetry.metrics import GatewayRoute, PrometheusMetrics


@final
class GatewayObservation:
    __slots__ = ("_metrics", "_route", "_span")

    def __init__(
        self,
        *,
        route: GatewayRoute,
        span: Span | None,
        metrics: PrometheusMetrics | None,
    ) -> None:
        self._route = route
        self._span = span
        self._metrics = metrics

    def finish(self, outcome: ExecutionErrorCode | None) -> None:
        if self._span is not None:
            with suppress(Exception):
                normalized = "success" if outcome is None else outcome.value
                self._span.set_attribute("sentinelmcp.gateway.outcome", normalized)
                self._span.set_status(
                    Status(StatusCode.OK if outcome is None else StatusCode.ERROR)
                )
        if self._metrics is not None:
            with suppress(Exception):
                self._metrics.record_gateway(self._route, outcome)


@final
class GatewayObservability:
    """Trace finite REST routes and authentication without request payload attributes."""

    __slots__ = ("_metrics", "_tracer")

    def __init__(
        self,
        *,
        tracer: Tracer | None = None,
        metrics: PrometheusMetrics | None = None,
    ) -> None:
        self._tracer = tracer
        self._metrics = metrics

    @contextmanager
    def observe(self, route: GatewayRoute) -> Iterator[GatewayObservation]:
        if not isinstance(route, GatewayRoute):
            raise TypeError("route must be a GatewayRoute")
        if self._tracer is None:
            yield GatewayObservation(route=route, span=None, metrics=self._metrics)
            return
        try:
            manager = self._tracer.start_as_current_span(
                "sentinelmcp.gateway",
                attributes={"sentinelmcp.gateway.route": route.value},
                record_exception=False,
                set_status_on_exception=False,
            )
            span = manager.__enter__()
        except Exception:
            yield GatewayObservation(route=route, span=None, metrics=self._metrics)
            return
        observation = GatewayObservation(route=route, span=span, metrics=self._metrics)
        try:
            yield observation
        except BaseException as error:
            observation.finish(ExecutionErrorCode.INTERNAL_EXECUTION_ERROR)
            _safe_exit(manager, type(error), error, error.__traceback__)
            raise
        else:
            _safe_exit(manager, None, None, None)

    @contextmanager
    def observe_authentication(self) -> Iterator[None]:
        if self._tracer is None:
            yield
            return
        try:
            manager = self._tracer.start_as_current_span(
                "sentinelmcp.authentication",
                attributes={"sentinelmcp.stage": "authentication"},
                record_exception=False,
                set_status_on_exception=False,
            )
            manager.__enter__()
        except Exception:
            yield
            return
        try:
            yield
        except BaseException as error:
            _safe_exit(manager, type(error), error, error.__traceback__)
            raise
        else:
            _safe_exit(manager, None, None, None)


def _safe_exit(
    manager: AbstractContextManager[Span],
    exception_type: type[BaseException] | None,
    exception: BaseException | None,
    traceback: TracebackType | None,
) -> None:
    with suppress(Exception):
        manager.__exit__(exception_type, exception, traceback)
