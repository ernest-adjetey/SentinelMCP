"""Single-attempt HTTP adapter with status-first bounded response handling."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import cast, final
from urllib.parse import urlsplit

import httpx

from sentinelmcp.domain.json_values import JSONValueError, thaw_json
from sentinelmcp.execution.models import (
    AdapterFailure,
    AdapterInvocation,
    AdapterResult,
    AdapterSuccess,
    ExecutionErrorCode,
)

DEFAULT_MAX_RESPONSE_BYTES = 1_048_576
DEFAULT_TIMEOUT_SECONDS = 10.0


class _DuplicateJSONKeyError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class HTTPToolTarget:
    """Operational HTTP configuration excluded from tool contract identity."""

    url: str
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES

    def __post_init__(self) -> None:
        parsed = urlsplit(self.url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("url must be an absolute HTTP(S) URL")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("url must not contain embedded credentials")
        if parsed.fragment:
            raise ValueError("url must not contain a fragment")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be finite and positive")
        if (
            isinstance(self.max_response_bytes, bool)
            or not 1 <= self.max_response_bytes <= 10_000_000
        ):
            raise ValueError("max_response_bytes must be between 1 and 10,000,000")


@final
class HTTPToolAdapter:
    """POST JSON once and normalize transport, status, media, and body failures."""

    __slots__ = ("_client", "_target")

    def __init__(self, client: httpx.AsyncClient, target: HTTPToolTarget) -> None:
        self._client = client
        self._target = target

    async def execute(self, invocation: AdapterInvocation) -> AdapterResult:
        try:
            async with self._client.stream(
                "POST",
                self._target.url,
                json=thaw_json(invocation.arguments),
                timeout=self._target.timeout_seconds,
                follow_redirects=False,
            ) as response:
                status = response.status_code
                if not 200 <= status < 300:
                    return AdapterFailure(_classify_status(status), upstream_status=status)
                if status == 204:
                    return AdapterFailure(
                        ExecutionErrorCode.UPSTREAM_MALFORMED_RESPONSE,
                        upstream_status=status,
                    )
                if not _is_supported_json_content_type(response.headers.get("content-type")):
                    return AdapterFailure(
                        ExecutionErrorCode.UPSTREAM_INVALID_CONTENT_TYPE,
                        upstream_status=status,
                    )
                if _declared_body_is_too_large(
                    response.headers.get("content-length"), self._target.max_response_bytes
                ):
                    return AdapterFailure(
                        ExecutionErrorCode.UPSTREAM_RESPONSE_TOO_LARGE,
                        upstream_status=status,
                    )
                body = await _read_bounded_body(response, self._target.max_response_bytes)
        except httpx.TimeoutException:
            return AdapterFailure(ExecutionErrorCode.UPSTREAM_TIMEOUT)
        except httpx.RequestError:
            return AdapterFailure(ExecutionErrorCode.UPSTREAM_UNAVAILABLE)

        if body is None:
            return AdapterFailure(
                ExecutionErrorCode.UPSTREAM_RESPONSE_TOO_LARGE,
                upstream_status=status,
            )
        try:
            output = _decode_json(body)
        except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateJSONKeyError):
            return AdapterFailure(
                ExecutionErrorCode.UPSTREAM_MALFORMED_RESPONSE,
                upstream_status=status,
            )
        try:
            return AdapterSuccess(output)
        except JSONValueError:
            return AdapterFailure(
                ExecutionErrorCode.OUTPUT_VALIDATION_FAILED,
                upstream_status=status,
            )


async def _read_bounded_body(response: httpx.Response, maximum_bytes: int) -> bytes | None:
    body = bytearray()
    async for chunk in response.aiter_bytes():
        if len(body) + len(chunk) > maximum_bytes:
            return None
        body.extend(chunk)
    return bytes(body)


def _declared_body_is_too_large(raw_length: str | None, maximum_bytes: int) -> bool:
    if raw_length is None:
        return False
    try:
        length = int(raw_length)
    except ValueError:
        return False
    return length > maximum_bytes


def _is_supported_json_content_type(raw_content_type: str | None) -> bool:
    if raw_content_type is None:
        return False
    parts = [part.strip() for part in raw_content_type.split(";")]
    media_type = parts[0].casefold()
    if media_type != "application/json" and not (
        media_type.startswith("application/") and media_type.endswith("+json")
    ):
        return False
    for parameter in parts[1:]:
        name, separator, value = parameter.partition("=")
        if separator and name.strip().casefold() == "charset":
            charset = value.strip().strip('"').casefold()
            if charset not in {"utf-8", "utf8"}:
                return False
    return True


def _decode_json(body: bytes) -> object:
    text = body.decode("utf-8")
    return cast(object, json.loads(text, object_pairs_hook=_unique_object))


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJSONKeyError(key)
        result[key] = value
    return result


def _classify_status(status: int) -> ExecutionErrorCode:
    statuses = {
        400: ExecutionErrorCode.UPSTREAM_BAD_REQUEST,
        401: ExecutionErrorCode.UPSTREAM_AUTHENTICATION_FAILED,
        403: ExecutionErrorCode.UPSTREAM_AUTHORIZATION_FAILED,
        404: ExecutionErrorCode.UPSTREAM_NOT_FOUND,
        409: ExecutionErrorCode.UPSTREAM_CONFLICT,
        429: ExecutionErrorCode.UPSTREAM_RATE_LIMITED,
        500: ExecutionErrorCode.UPSTREAM_SERVER_ERROR,
        502: ExecutionErrorCode.UPSTREAM_UNAVAILABLE,
        503: ExecutionErrorCode.UPSTREAM_UNAVAILABLE,
        504: ExecutionErrorCode.UPSTREAM_TIMEOUT,
    }
    return statuses.get(status, ExecutionErrorCode.UPSTREAM_STATUS_ERROR)
