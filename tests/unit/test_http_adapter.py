"""Deterministic HTTP adapter tests using HTTPX MockTransport."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest

from sentinelmcp.domain import ToolRef
from sentinelmcp.domain.json_values import FrozenJSONMapping
from sentinelmcp.execution import (
    AdapterFailure,
    AdapterInvocation,
    AdapterResult,
    AdapterSuccess,
    ExecutionContext,
    ExecutionErrorCode,
    ExecutionId,
    ExecutionRequest,
    HTTPToolAdapter,
    HTTPToolTarget,
    RequestId,
)


def make_invocation() -> AdapterInvocation:
    reference = ToolRef.parse("demo.http.lookup@1.0.0")
    arguments = ExecutionRequest(reference, {"id": "abc123"}).arguments
    assert isinstance(arguments, FrozenJSONMapping)
    return AdapterInvocation(
        ExecutionContext(
            request_id=RequestId(UUID("00000000-0000-4000-8000-000000000001")),
            execution_id=ExecutionId(UUID("00000000-0000-4000-8000-000000000002")),
            tool_ref=reference,
            definition_fingerprint="a" * 64,
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            started_monotonic=1.0,
        ),
        arguments,
    )


def invoke(
    handler: httpx.MockTransport,
    *,
    maximum_bytes: int = 1_048_576,
) -> AdapterResult:
    async def run() -> AdapterResult:
        async with httpx.AsyncClient(transport=handler) as client:
            adapter = HTTPToolAdapter(
                client,
                HTTPToolTarget(
                    "https://tools.example.test/lookup",
                    max_response_bytes=maximum_bytes,
                ),
            )
            return await adapter.execute(make_invocation())

    return asyncio.run(run())


def response_transport(
    status: int,
    content: bytes = b"",
    *,
    content_type: str | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.MockTransport:
    response_headers = dict(headers or {})
    if content_type is not None:
        response_headers["content-type"] = content_type

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers=response_headers, content=content)

    return httpx.MockTransport(handler)


def test_valid_json_response_is_decoded_without_response_json() -> None:
    seen_body = b""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_body
        seen_body = request.content
        return httpx.Response(
            200,
            headers={"content-type": "application/json; charset=utf-8"},
            content=b'{"asset_id":"abc123","nested":{"ok":true}}',
        )

    result = invoke(httpx.MockTransport(handler))

    assert isinstance(result, AdapterSuccess)
    assert isinstance(result.output, FrozenJSONMapping)
    nested = result.output["nested"]
    assert isinstance(nested, FrozenJSONMapping)
    assert nested["ok"] is True
    assert b"abc123" in seen_body


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (302, ExecutionErrorCode.UPSTREAM_STATUS_ERROR),
        (400, ExecutionErrorCode.UPSTREAM_BAD_REQUEST),
        (401, ExecutionErrorCode.UPSTREAM_AUTHENTICATION_FAILED),
        (403, ExecutionErrorCode.UPSTREAM_AUTHORIZATION_FAILED),
        (404, ExecutionErrorCode.UPSTREAM_NOT_FOUND),
        (409, ExecutionErrorCode.UPSTREAM_CONFLICT),
        (418, ExecutionErrorCode.UPSTREAM_STATUS_ERROR),
        (429, ExecutionErrorCode.UPSTREAM_RATE_LIMITED),
        (500, ExecutionErrorCode.UPSTREAM_SERVER_ERROR),
        (502, ExecutionErrorCode.UPSTREAM_UNAVAILABLE),
        (503, ExecutionErrorCode.UPSTREAM_UNAVAILABLE),
        (504, ExecutionErrorCode.UPSTREAM_TIMEOUT),
    ],
)
def test_status_errors_are_classified_before_body_decoding(
    status: int,
    expected: ExecutionErrorCode,
) -> None:
    result = invoke(response_transport(status, b"not-json", content_type="text/plain"))

    assert isinstance(result, AdapterFailure)
    assert result.code is expected
    assert result.upstream_status == status


def test_plain_text_401_remains_authentication_failure_regression() -> None:
    result = invoke(
        response_transport(
            401,
            b"invalid upstream credentials: do not expose this",
            content_type="text/plain",
        )
    )

    assert isinstance(result, AdapterFailure)
    assert result.code is ExecutionErrorCode.UPSTREAM_AUTHENTICATION_FAILED
    assert result.upstream_status == 401


@pytest.mark.parametrize(
    "transport",
    [
        response_transport(200, b'{"ok":', content_type="application/json"),
        response_transport(200, b"", content_type="application/json"),
        response_transport(200, b'{"x":1,"x":2}', content_type="application/json"),
        response_transport(200, b"\xff", content_type="application/json"),
    ],
)
def test_malformed_success_body_is_normalized(transport: httpx.MockTransport) -> None:
    result = invoke(transport)

    assert isinstance(result, AdapterFailure)
    assert result.code is ExecutionErrorCode.UPSTREAM_MALFORMED_RESPONSE
    assert result.upstream_status == 200


@pytest.mark.parametrize(
    "content_type",
    [None, "text/plain", "application/json; charset=latin-1"],
)
def test_success_requires_supported_json_content_type(content_type: str | None) -> None:
    result = invoke(response_transport(200, b'{"ok":true}', content_type=content_type))

    assert isinstance(result, AdapterFailure)
    assert result.code is ExecutionErrorCode.UPSTREAM_INVALID_CONTENT_TYPE


def test_structured_json_suffix_and_utf8_alias_are_supported() -> None:
    result = invoke(
        response_transport(
            200,
            b'{"ok":true}',
            content_type='application/vnd.demo+json; charset="utf8"',
        )
    )

    assert isinstance(result, AdapterSuccess)
    assert isinstance(result.output, FrozenJSONMapping)
    assert result.output["ok"] is True


def test_no_content_is_malformed_success_not_content_type_failure() -> None:
    result = invoke(response_transport(204))

    assert isinstance(result, AdapterFailure)
    assert result.code is ExecutionErrorCode.UPSTREAM_MALFORMED_RESPONSE
    assert result.upstream_status == 204


def test_deep_json_body_becomes_bounded_output_validation_failure() -> None:
    body = ("[" * 130 + "0" + "]" * 130).encode()
    result = invoke(response_transport(200, body, content_type="application/json; profile"))

    assert isinstance(result, AdapterFailure)
    assert result.code is ExecutionErrorCode.OUTPUT_VALIDATION_FAILED


def test_declared_and_streamed_oversized_bodies_are_rejected() -> None:
    declared = invoke(
        response_transport(
            200,
            b"{}",
            content_type="application/json",
            headers={"content-length": "100"},
        ),
        maximum_bytes=8,
    )
    streamed = invoke(
        response_transport(
            200,
            b'{"long":true}',
            content_type="application/json",
            headers={"content-length": "invalid"},
        ),
        maximum_bytes=8,
    )

    assert isinstance(declared, AdapterFailure)
    assert isinstance(streamed, AdapterFailure)
    assert declared.code is ExecutionErrorCode.UPSTREAM_RESPONSE_TOO_LARGE
    assert streamed.code is ExecutionErrorCode.UPSTREAM_RESPONSE_TOO_LARGE


def test_timeout_and_connection_failure_are_distinct_and_never_retried() -> None:
    timeout_calls = 0
    connection_calls = 0

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        nonlocal timeout_calls
        timeout_calls += 1
        raise httpx.ReadTimeout("private timeout detail", request=request)

    def connection_handler(request: httpx.Request) -> httpx.Response:
        nonlocal connection_calls
        connection_calls += 1
        raise httpx.ConnectError("private connection detail", request=request)

    timeout = invoke(httpx.MockTransport(timeout_handler))
    connection = invoke(httpx.MockTransport(connection_handler))

    assert isinstance(timeout, AdapterFailure)
    assert isinstance(connection, AdapterFailure)
    assert timeout.code is ExecutionErrorCode.UPSTREAM_TIMEOUT
    assert connection.code is ExecutionErrorCode.UPSTREAM_UNAVAILABLE
    assert timeout_calls == 1
    assert connection_calls == 1


def test_http_target_rejects_unsafe_or_unbounded_configuration() -> None:
    with pytest.raises(ValueError, match="absolute HTTP"):
        HTTPToolTarget("relative/path")
    with pytest.raises(ValueError, match="credentials"):
        HTTPToolTarget("https://user:secret@tools.example.test/path")
    with pytest.raises(ValueError, match="fragment"):
        HTTPToolTarget("https://tools.example.test/path#fragment")
    with pytest.raises(ValueError, match="timeout"):
        HTTPToolTarget("https://tools.example.test/path", timeout_seconds=0)
    with pytest.raises(ValueError, match="max_response_bytes"):
        HTTPToolTarget("https://tools.example.test/path", max_response_bytes=0)
