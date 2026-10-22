"""Minimal FastAPI boundary for the transport-independent tool executor."""

from __future__ import annotations

from typing import Any, final

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from sentinelmcp.application import MutationService, ToolExecutor
from sentinelmcp.domain import (
    InvalidToolReferenceError,
    ToolName,
    ToolNamespace,
    ToolRef,
    ToolVersion,
)
from sentinelmcp.domain.json_values import thaw_json
from sentinelmcp.execution import (
    ExecutionErrorCode,
    ExecutionFailure,
    ExecutionRequest,
    ExecutionSuccess,
    InvalidExecutionRequestError,
    RequestId,
)
from sentinelmcp.mutation_guard import MutationPreview
from sentinelmcp.policy import AuthenticationError, Authenticator, Principal
from sentinelmcp.telemetry import GatewayObservability, GatewayRoute, PrometheusMetrics

DEFAULT_MAX_REQUEST_BYTES = 1_048_576


class ToolReferencePayload(BaseModel):
    """Exact tool identity accepted by the REST boundary."""

    model_config = ConfigDict(extra="forbid", strict=True)

    namespace: str
    name: str
    version: str


class ExecutePayload(BaseModel):
    """Public Phase 3 execution envelope."""

    model_config = ConfigDict(extra="forbid", strict=True)

    tool: ToolReferencePayload
    arguments: dict[str, Any]


class ConfirmMutationPayload(ExecutePayload):
    """A previewed mutation plus single-use confirmation and idempotency key."""

    confirmation_token: str
    idempotency_key: str


@final
class RequestBodyLimitMiddleware:
    """Buffer at most one bounded HTTP request body before framework decoding."""

    __slots__ = ("_app", "_maximum_bytes")

    def __init__(self, app: ASGIApp, maximum_bytes: int) -> None:
        self._app = app
        self._maximum_bytes = maximum_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        try:
            declared_length = _content_length(scope)
        except ValueError:
            await _request_body_error(
                400, "Request Content-Length is invalid.", scope, receive, send
            )
            return
        if declared_length is not None and declared_length > self._maximum_bytes:
            await _request_body_error(
                413,
                "Request body exceeds the configured limit.",
                scope,
                receive,
                send,
            )
            return

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                continue
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > self._maximum_bytes:
                await _request_body_error(
                    413,
                    "Request body exceeds the configured limit.",
                    scope,
                    receive,
                    send,
                )
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break

        replayed = False

        async def replay_receive() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self._app(scope, replay_receive, send)


def _content_length(scope: Scope) -> int | None:
    values = [value for name, value in scope["headers"] if name.lower() == b"content-length"]
    if not values:
        return None
    if len(values) != 1:
        raise ValueError("multiple Content-Length headers")
    try:
        length = int(values[0].decode("ascii"))
    except (UnicodeDecodeError, ValueError) as error:
        raise ValueError("invalid Content-Length") from error
    if length < 0:
        raise ValueError("invalid Content-Length")
    return length


async def _request_body_error(
    status_code: int,
    message: str,
    scope: Scope,
    receive: Receive,
    send: Send,
) -> None:
    response = _gateway_error_response(
        status_code=status_code,
        code=ExecutionErrorCode.INVALID_REQUEST,
        message=message,
    )
    await response(scope, receive, send)


def create_execution_app(
    executor: ToolExecutor,
    authenticator: Authenticator,
    mutation_service: MutationService | None = None,
    *,
    gateway_observability: GatewayObservability | None = None,
    metrics: PrometheusMetrics | None = None,
    max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES,
) -> FastAPI:
    """Create a gateway around an already-composed core executor."""

    if (
        isinstance(max_request_bytes, bool)
        or not isinstance(max_request_bytes, int)
        or not 1 <= max_request_bytes <= 10_000_000
    ):
        raise ValueError("max_request_bytes must be between 1 and 10,000,000")
    app = FastAPI(title="SentinelMCP", version="0.1.0")
    app.add_middleware(RequestBodyLimitMiddleware, maximum_bytes=max_request_bytes)
    gateway = GatewayObservability() if gateway_observability is None else gateway_observability

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(
        _request: Request, _error: RequestValidationError
    ) -> JSONResponse:
        return _gateway_error_response(
            status_code=422,
            code=ExecutionErrorCode.INVALID_REQUEST,
            message="Request body does not match the execution envelope.",
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(_request: Request, _error: Exception) -> JSONResponse:
        return _gateway_error_response(
            status_code=500,
            code=ExecutionErrorCode.INTERNAL_EXECUTION_ERROR,
            message="SentinelMCP could not process the gateway request.",
        )

    @app.post("/v1/execute", response_model=None)
    async def execute_tool(payload: ExecutePayload, request: Request) -> JSONResponse:
        with gateway.observe(GatewayRoute.EXECUTE) as observation:
            with gateway.observe_authentication():
                principal = _authenticate_request(request, authenticator)
            if isinstance(principal, JSONResponse):
                observation.finish(ExecutionErrorCode.AUTHENTICATION_FAILED)
                return principal

            try:
                execution_request = _execution_request(payload)
            except (InvalidToolReferenceError, InvalidExecutionRequestError):
                observation.finish(ExecutionErrorCode.INVALID_REQUEST)
                return _gateway_error_response(
                    status_code=422,
                    code=ExecutionErrorCode.INVALID_REQUEST,
                    message="Tool reference or arguments are invalid.",
                )

            result = await executor.execute(execution_request, principal=principal)
            if isinstance(result, ExecutionSuccess):
                observation.finish(None)
                return JSONResponse(status_code=200, content=_success_content(result))
            observation.finish(result.error.code)
            return JSONResponse(
                status_code=_http_status_for_failure(result),
                content=_failure_content(result),
            )

    @app.post("/v1/mutations/preview", response_model=None)
    async def preview_mutation(payload: ExecutePayload, request: Request) -> JSONResponse:
        with gateway.observe(GatewayRoute.MUTATION_PREVIEW) as observation:
            with gateway.observe_authentication():
                principal = _authenticate_request(request, authenticator)
            if isinstance(principal, JSONResponse):
                observation.finish(ExecutionErrorCode.AUTHENTICATION_FAILED)
                return principal
            if mutation_service is None:
                observation.finish(ExecutionErrorCode.MUTATION_NOT_SUPPORTED)
                return _gateway_error_response(
                    status_code=503,
                    code=ExecutionErrorCode.MUTATION_NOT_SUPPORTED,
                    message="Durable mutation confirmation is not configured.",
                )
            try:
                execution_request = _execution_request(payload)
            except (InvalidToolReferenceError, InvalidExecutionRequestError):
                observation.finish(ExecutionErrorCode.INVALID_REQUEST)
                return _gateway_error_response(
                    status_code=422,
                    code=ExecutionErrorCode.INVALID_REQUEST,
                    message="Tool reference or arguments are invalid.",
                )
            result = mutation_service.preview(execution_request, principal=principal)
            if isinstance(result, MutationPreview):
                observation.finish(None)
                return JSONResponse(status_code=201, content=_preview_content(result))
            observation.finish(result.error.code)
            return JSONResponse(
                status_code=_http_status_for_failure(result),
                content=_failure_content(result),
            )

    @app.post("/v1/mutations/confirm", response_model=None)
    async def confirm_mutation(
        payload: ConfirmMutationPayload,
        request: Request,
    ) -> JSONResponse:
        with gateway.observe(GatewayRoute.MUTATION_CONFIRM) as observation:
            with gateway.observe_authentication():
                principal = _authenticate_request(request, authenticator)
            if isinstance(principal, JSONResponse):
                observation.finish(ExecutionErrorCode.AUTHENTICATION_FAILED)
                return principal
            if mutation_service is None:
                observation.finish(ExecutionErrorCode.MUTATION_NOT_SUPPORTED)
                return _gateway_error_response(
                    status_code=503,
                    code=ExecutionErrorCode.MUTATION_NOT_SUPPORTED,
                    message="Durable mutation confirmation is not configured.",
                )
            try:
                execution_request = _execution_request(payload)
            except (InvalidToolReferenceError, InvalidExecutionRequestError):
                observation.finish(ExecutionErrorCode.INVALID_REQUEST)
                return _gateway_error_response(
                    status_code=422,
                    code=ExecutionErrorCode.INVALID_REQUEST,
                    message="Tool reference or arguments are invalid.",
                )
            result = await mutation_service.confirm(
                execution_request,
                principal=principal,
                confirmation_token=payload.confirmation_token,
                idempotency_key=payload.idempotency_key,
            )
            if isinstance(result, ExecutionSuccess):
                observation.finish(None)
                return JSONResponse(status_code=200, content=_success_content(result))
            observation.finish(result.error.code)
            return JSONResponse(
                status_code=_http_status_for_failure(result),
                content=_failure_content(result),
            )

    if metrics is not None:

        @app.get("/metrics", include_in_schema=False, response_model=None)
        async def prometheus_metrics() -> Response:
            return Response(
                content=metrics.render(),
                media_type="text/plain; version=0.0.4; charset=utf-8",
            )

    return app


def _success_content(result: ExecutionSuccess) -> dict[str, object]:
    return {
        "request_id": str(result.context.request_id),
        "execution_id": str(result.context.execution_id),
        "tool": str(result.context.tool_ref),
        "status": "success",
        "result": thaw_json(result.result),
        "duration_ms": round(result.duration_ms, 3),
    }


def _preview_content(preview: MutationPreview) -> dict[str, object]:
    return {
        "request_id": str(preview.request_id),
        "tool": str(preview.tool_ref),
        "status": "confirmation_required",
        "preview": {
            "arguments": thaw_json(preview.arguments),
            "challenge_id": str(preview.challenge_id),
            "confirmation_token": preview.confirmation_token,
            "definition_fingerprint": preview.definition_fingerprint,
            "expires_at": preview.expires_at.isoformat(),
            "warnings": list(preview.warnings),
        },
    }


def _failure_content(result: ExecutionFailure) -> dict[str, object]:
    error: dict[str, object] = {
        "code": result.error.code.value,
        "message": result.error.message,
    }
    if result.error.upstream_status is not None:
        error["upstream_status"] = result.error.upstream_status
    if result.error.retry_after_ms is not None:
        error["retry_after_ms"] = result.error.retry_after_ms
    if result.error.issues:
        error["issues"] = [
            {
                "instance_path": list(issue.instance_path),
                "schema_path": list(issue.schema_path),
                "message": issue.message,
                "validator": issue.validator,
            }
            for issue in result.error.issues
        ]
    return {
        "request_id": str(result.context.request_id),
        "execution_id": str(result.context.execution_id),
        "tool": str(result.context.tool_ref),
        "status": "error",
        "error": error,
        "duration_ms": round(result.duration_ms, 3),
    }


def _gateway_error_response(
    *,
    status_code: int,
    code: ExecutionErrorCode,
    message: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        headers=headers,
        content={
            "request_id": str(RequestId.generate()),
            "tool": None,
            "status": "error",
            "error": {"code": code.value, "message": message},
            "duration_ms": 0.0,
        },
    )


def _http_status_for_failure(result: ExecutionFailure) -> int:
    code = result.error.code
    if code is ExecutionErrorCode.AUTHENTICATION_FAILED:
        return 401
    if code is ExecutionErrorCode.AUTHORIZATION_FAILED:
        return 403
    if code is ExecutionErrorCode.TOOL_NOT_FOUND:
        return 404
    if code in {ExecutionErrorCode.INVALID_REQUEST, ExecutionErrorCode.INPUT_VALIDATION_FAILED}:
        return 422
    if code is ExecutionErrorCode.RATE_LIMITED:
        return 429
    if code is ExecutionErrorCode.MUTATION_NOT_SUPPORTED:
        return 409
    if code in {
        ExecutionErrorCode.MUTATION_CONFIRMATION_INVALID,
        ExecutionErrorCode.IDEMPOTENCY_CONFLICT,
        ExecutionErrorCode.IDEMPOTENCY_IN_PROGRESS,
    }:
        return 409
    if code in {
        ExecutionErrorCode.BINDING_NOT_FOUND,
        ExecutionErrorCode.CIRCUIT_OPEN,
        ExecutionErrorCode.AUDIT_UNAVAILABLE,
        ExecutionErrorCode.MUTATION_STATE_UNAVAILABLE,
        ExecutionErrorCode.UPSTREAM_RATE_LIMITED,
        ExecutionErrorCode.UPSTREAM_UNAVAILABLE,
    }:
        return 503
    if code in {ExecutionErrorCode.DEADLINE_EXCEEDED, ExecutionErrorCode.UPSTREAM_TIMEOUT}:
        return 504
    if code is ExecutionErrorCode.INTERNAL_EXECUTION_ERROR:
        return 500
    return 502


def _authenticate_request(
    request: Request,
    authenticator: Authenticator,
) -> Principal | JSONResponse:
    token = _bearer_token(request.headers.get("authorization"))
    if token is not None:
        try:
            return authenticator.authenticate(token)
        except AuthenticationError:
            pass
    return _gateway_error_response(
        status_code=401,
        code=ExecutionErrorCode.AUTHENTICATION_FAILED,
        message="A valid bearer token is required.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _execution_request(payload: ExecutePayload) -> ExecutionRequest:
    return ExecutionRequest(
        tool_ref=ToolRef(
            namespace=ToolNamespace(payload.tool.namespace),
            name=ToolName(payload.tool.name),
            version=ToolVersion(payload.tool.version),
        ),
        arguments=payload.arguments,
    )


def _bearer_token(authorization: str | None) -> str | None:
    if authorization is None:
        return None
    scheme, separator, token = authorization.partition(" ")
    if not separator or scheme.casefold() != "bearer" or not token or token != token.strip():
        return None
    return token
