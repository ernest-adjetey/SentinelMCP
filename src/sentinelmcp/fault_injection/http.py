"""Deterministic HTTPX transport faults for evaluation and tests."""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from enum import StrEnum
from typing import final

import httpx


class HTTPFaultKind(StrEnum):
    """Supported single-attempt dependency behaviors."""

    STATUS = "status"
    MALFORMED_JSON = "malformed_json"
    WRONG_CONTENT_TYPE = "wrong_content_type"
    INVALID_UTF8 = "invalid_utf8"
    OVERSIZED_RESPONSE = "oversized_response"
    TIMEOUT = "timeout"
    SLOW_RESPONSE = "slow_response"
    CONNECTION_FAILURE = "connection_failure"
    CONNECTION_DROP = "connection_drop"
    OUTPUT_SCHEMA_VIOLATION = "output_schema_violation"
    SUCCESS = "success"


@dataclass(frozen=True, slots=True)
class HTTPFaultStep:
    """One bounded deterministic transport response or exception."""

    kind: HTTPFaultKind
    status: int | None = None
    delay_seconds: float = 0.0
    response_bytes: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.kind, HTTPFaultKind):
            raise TypeError("kind must be an HTTPFaultKind")
        if self.kind is HTTPFaultKind.STATUS:
            if self.status is None or not 100 <= self.status <= 599:
                raise ValueError("status fault requires an HTTP status from 100 through 599")
        elif self.status is not None:
            raise ValueError("status is only valid for a status fault")
        if (
            isinstance(self.delay_seconds, bool)
            or not isinstance(self.delay_seconds, int | float)
            or not math.isfinite(self.delay_seconds)
            or not 0 <= self.delay_seconds <= 1
        ):
            raise ValueError("delay_seconds must be finite and from 0 through 1")
        if (
            isinstance(self.response_bytes, bool)
            or not isinstance(self.response_bytes, int)
            or not 0 <= self.response_bytes <= 10_000_001
        ):
            raise ValueError("response_bytes must be from 0 through 10,000,001")


@final
class DeterministicHTTPTransport(httpx.AsyncBaseTransport):
    """Replay a finite fault plan and retain only bounded request metadata."""

    __slots__ = ("_index", "_steps", "request_methods", "request_urls")

    def __init__(self, steps: tuple[HTTPFaultStep, ...]) -> None:
        if not steps:
            raise ValueError("fault plan must contain at least one step")
        self._steps = steps
        self._index = 0
        self.request_methods: list[str] = []
        self.request_urls: list[str] = []

    @property
    def request_count(self) -> int:
        return self._index

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.request_methods.append(request.method)
        self.request_urls.append(str(request.url))
        step = self._steps[min(self._index, len(self._steps) - 1)]
        self._index += 1

        if step.delay_seconds:
            await asyncio.sleep(step.delay_seconds)
        if step.kind is HTTPFaultKind.STATUS:
            assert step.status is not None
            return httpx.Response(step.status, content=b"private upstream failure body")
        if step.kind is HTTPFaultKind.MALFORMED_JSON:
            return _json_response(b'{"ok":')
        if step.kind is HTTPFaultKind.WRONG_CONTENT_TYPE:
            return httpx.Response(200, headers={"content-type": "text/plain"}, content=b"ok")
        if step.kind is HTTPFaultKind.INVALID_UTF8:
            return _json_response(b"\xff")
        if step.kind is HTTPFaultKind.OVERSIZED_RESPONSE:
            body = b'{"payload":"' + b"x" * step.response_bytes + b'"}'
            return _json_response(body)
        if step.kind is HTTPFaultKind.TIMEOUT:
            raise httpx.ReadTimeout("injected timeout", request=request)
        if step.kind is HTTPFaultKind.CONNECTION_FAILURE:
            raise httpx.ConnectError("injected connection failure", request=request)
        if step.kind is HTTPFaultKind.CONNECTION_DROP:
            raise httpx.ReadError("injected connection drop", request=request)
        if step.kind is HTTPFaultKind.OUTPUT_SCHEMA_VIOLATION:
            return _json_response(b'{"ok":"not-a-boolean"}')
        return _json_response(b'{"ok":true}')


def _json_response(body: bytes) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "application/json"}, content=body)
