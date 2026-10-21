"""FastAPI contract tests for the Phase 3 execution gateway."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI

from sentinelmcp.adapters.rest import create_execution_app
from sentinelmcp.adapters.rest.demo import app as demo_app
from sentinelmcp.application import create_demo_runtime
from sentinelmcp.domain import ToolRef
from sentinelmcp.execution import (
    ExecutionContext,
    ExecutionError,
    ExecutionErrorCode,
    ExecutionFailure,
    ExecutionId,
    RequestId,
)
from sentinelmcp.policy import Authenticator, Principal

TEST_PRINCIPAL = Principal(
    subject="gateway-test",
    issuer="https://issuer.test",
    roles=frozenset({"developer"}),
    scopes=frozenset({"tools:execute"}),
)


class StaticAuthenticator(Authenticator):
    def authenticate(self, token: str) -> Principal:
        if token != "valid-test-token":
            raise AssertionError("gateway passed an unexpected test token")
        return TEST_PRINCIPAL


TEST_AUTHENTICATOR = StaticAuthenticator()


def payload(
    *,
    namespace: str = "demo.math",
    name: str = "add",
    version: str = "1.0.0",
    arguments: dict[str, Any] | None = None,
) -> dict[str, object]:
    return {
        "tool": {"namespace": namespace, "name": name, "version": version},
        "arguments": {"a": 2, "b": 3} if arguments is None else arguments,
    }


def post(
    app: FastAPI,
    body: dict[str, object],
    *,
    authorization: str = "Bearer valid-test-token",
) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://sentinelmcp.test",
        ) as client:
            return await client.post(
                "/v1/execute",
                json=body,
                headers={"Authorization": authorization},
            )

    return asyncio.run(send())


def test_gateway_executes_registered_synthetic_tool_end_to_end() -> None:
    runtime = create_demo_runtime()

    response = post(create_execution_app(runtime.executor, TEST_AUTHENTICATOR), payload())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "success"
    assert body["tool"] == "demo.math.add@1.0.0"
    assert body["result"] == {"sum": 5}
    assert UUID(body["request_id"]).version == 4
    assert UUID(body["execution_id"]).version == 4
    assert body["request_id"] != body["execution_id"]
    assert body["duration_ms"] >= 0


def test_gateway_maps_unknown_tool_invalid_input_and_missing_binding() -> None:
    runtime = create_demo_runtime()
    app = create_execution_app(runtime.executor, TEST_AUTHENTICATOR)

    unknown = post(app, payload(version="2.0.0"))
    invalid = post(app, payload(arguments={"a": "two", "b": 3}))

    runtime = create_demo_runtime()
    reference = ToolRef.parse("demo.math.add@1.0.0")
    runtime.bindings.unbind(reference)
    missing = post(create_execution_app(runtime.executor, TEST_AUTHENTICATOR), payload())

    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "tool_not_found"
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "input_validation_failed"
    assert invalid.json()["error"]["issues"][0]["instance_path"] == ["a"]
    assert missing.status_code == 503
    assert missing.json()["error"]["code"] == "binding_not_found"


def test_gateway_returns_stable_envelope_for_invalid_reference_or_body() -> None:
    runtime = create_demo_runtime()
    app = create_execution_app(runtime.executor, TEST_AUTHENTICATOR)

    invalid_reference = post(app, payload(namespace="Demo"))
    invalid_body = post(app, {"tool": "not-an-object"})

    for response in (invalid_reference, invalid_body):
        assert response.status_code == 422
        body = response.json()
        assert body["status"] == "error"
        assert body["error"]["code"] == "invalid_request"
        assert "traceback" not in response.text.casefold()
        assert UUID(body["request_id"]).version == 4


def test_gateway_does_not_leak_unexpected_internal_exception() -> None:
    class ExplodingExecutor:
        async def execute(self, _request: object, *, principal: Principal | None = None) -> object:
            del principal
            raise RuntimeError("secret internal stack detail")

    app = create_execution_app(ExplodingExecutor(), TEST_AUTHENTICATOR)  # type: ignore[arg-type]
    response = post(app, payload())

    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "internal_execution_error"
    assert "secret" not in response.text
    assert "traceback" not in response.text.casefold()


def test_gateway_generated_request_ids_are_not_caller_controlled() -> None:
    runtime = create_demo_runtime()
    attempted_id = str(RequestId.generate())
    body = payload()
    body["request_id"] = attempted_id

    response = post(create_execution_app(runtime.executor, TEST_AUTHENTICATOR), body)

    assert response.status_code == 422
    assert response.json()["request_id"] != attempted_id


def test_demo_gateway_module_exposes_runnable_fastapi_app() -> None:
    response = post(demo_app, payload(arguments={"a": 1, "b": 4}))

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_failed"


@pytest.mark.parametrize(
    ("code", "expected_status", "upstream_status", "retry_after_ms"),
    [
        (ExecutionErrorCode.AUTHENTICATION_FAILED, 401, None, None),
        (ExecutionErrorCode.AUTHORIZATION_FAILED, 403, None, None),
        (ExecutionErrorCode.MUTATION_NOT_SUPPORTED, 409, None, None),
        (ExecutionErrorCode.RATE_LIMITED, 429, None, 500),
        (ExecutionErrorCode.CIRCUIT_OPEN, 503, None, 1_000),
        (ExecutionErrorCode.DEADLINE_EXCEEDED, 504, None, None),
        (ExecutionErrorCode.UPSTREAM_TIMEOUT, 504, None, None),
        (ExecutionErrorCode.ADAPTER_FAILURE, 502, None, None),
        (ExecutionErrorCode.UPSTREAM_AUTHENTICATION_FAILED, 502, 401, None),
        (ExecutionErrorCode.INTERNAL_EXECUTION_ERROR, 500, None, None),
    ],
)
def test_gateway_maps_internal_categories_without_changing_error_code(
    code: ExecutionErrorCode,
    expected_status: int,
    upstream_status: int | None,
    retry_after_ms: int | None,
) -> None:
    context = ExecutionContext(
        request_id=RequestId.generate(),
        execution_id=ExecutionId.generate(),
        tool_ref=ToolRef.parse("demo.math.add@1.0.0"),
        definition_fingerprint="a" * 64,
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        started_monotonic=1.0,
    )
    failure = ExecutionFailure(
        context,
        ExecutionError(
            code,
            "Safe deterministic failure.",
            upstream_status=upstream_status,
            retry_after_ms=retry_after_ms,
        ),
        1.0,
    )

    class FixedFailureExecutor:
        async def execute(
            self,
            _request: object,
            *,
            principal: Principal | None = None,
        ) -> ExecutionFailure:
            del principal
            return failure

    response = post(
        create_execution_app(FixedFailureExecutor(), TEST_AUTHENTICATOR),  # type: ignore[arg-type]
        payload(),
    )

    assert response.status_code == expected_status
    assert response.json()["error"]["code"] == code.value
    if upstream_status is not None:
        assert response.json()["error"]["upstream_status"] == upstream_status
    if retry_after_ms is not None:
        assert response.json()["error"]["retry_after_ms"] == retry_after_ms
