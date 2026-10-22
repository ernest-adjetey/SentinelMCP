"""Single-attempt HTTP adapter with status-first bounded response handling."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import math
import re
import socket
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import cast, final
from urllib.parse import urlsplit, urlunsplit

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
MAX_RESOLVED_ADDRESSES = 32

type IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
type HostResolver = Callable[[str, int], Awaitable[Sequence[str]]]

_HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


class _DuplicateJSONKeyError(ValueError):
    pass


class OutboundTargetRejectedError(ValueError):
    """An outbound target failed the configured network boundary."""


async def _system_resolver(host: str, port: int) -> Sequence[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(
        host,
        port,
        family=socket.AF_UNSPEC,
        type=socket.SOCK_STREAM,
        proto=socket.IPPROTO_TCP,
    )
    return tuple(str(info[4][0]) for info in infos)


def _normalize_hostname(host: str) -> str:
    candidate = host.rstrip(".")
    if not candidate:
        raise ValueError("url host must not be empty")
    try:
        return ipaddress.ip_address(candidate).compressed
    except ValueError:
        pass
    try:
        normalized = candidate.encode("idna").decode("ascii").casefold()
    except UnicodeError as error:
        raise ValueError("url host must be a valid DNS name or IP address") from error
    labels = normalized.split(".")
    if len(normalized) > 253 or any(not _HOST_LABEL.fullmatch(label) for label in labels):
        raise ValueError("url host must be a valid DNS name or IP address")
    return normalized


@dataclass(frozen=True, slots=True)
class HTTPToolTarget:
    """Operational HTTP configuration excluded from tool contract identity."""

    url: str
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES
    _scheme: str = field(init=False, repr=False, compare=False)
    _host: str = field(init=False, repr=False, compare=False)
    _port: int = field(init=False, repr=False, compare=False)
    _explicit_port: bool = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.url, str):
            raise TypeError("url must be a string")
        parsed = urlsplit(self.url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("url must be an absolute HTTP(S) URL")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("url must not contain embedded credentials")
        if parsed.fragment:
            raise ValueError("url must not contain a fragment")
        if parsed.hostname is None:
            raise ValueError("url must contain a host")
        try:
            explicit_port = parsed.port
        except ValueError as error:
            raise ValueError("url must contain a valid port") from error
        object.__setattr__(self, "_scheme", parsed.scheme)
        object.__setattr__(self, "_host", _normalize_hostname(parsed.hostname))
        object.__setattr__(
            self,
            "_port",
            explicit_port
            if explicit_port is not None
            else (443 if parsed.scheme == "https" else 80),
        )
        object.__setattr__(self, "_explicit_port", explicit_port is not None)
        timeout_seconds: object = self.timeout_seconds
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, int | float)
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be finite and positive")
        if (
            isinstance(self.max_response_bytes, bool)
            or not isinstance(self.max_response_bytes, int)
            or not 1 <= self.max_response_bytes <= 10_000_000
        ):
            raise ValueError("max_response_bytes must be between 1 and 10,000,000")

    @property
    def scheme(self) -> str:
        return self._scheme

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port


@dataclass(frozen=True, slots=True)
class _ApprovedHTTPDestination:
    url: str
    host_header: str
    sni_hostname: str


@final
@dataclass(frozen=True, slots=True)
class OutboundHTTPPolicy:
    """Resolve, validate, and pin one outbound HTTP destination per invocation."""

    allowed_hosts: frozenset[str] | None = None
    require_https: bool = True
    allow_non_global_addresses: bool = False
    resolver: HostResolver = field(default=_system_resolver, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.require_https, bool):
            raise TypeError("require_https must be a boolean")
        if not isinstance(self.allow_non_global_addresses, bool):
            raise TypeError("allow_non_global_addresses must be a boolean")
        if not callable(self.resolver):
            raise TypeError("resolver must be callable")
        if self.allowed_hosts is not None:
            if not isinstance(self.allowed_hosts, frozenset):
                raise TypeError("allowed_hosts must be a frozenset or None")
            if any(not isinstance(host, str) for host in self.allowed_hosts):
                raise TypeError("allowed_hosts entries must be strings")
            object.__setattr__(
                self,
                "allowed_hosts",
                frozenset(_normalize_hostname(host) for host in self.allowed_hosts),
            )

    async def authorize(self, target: HTTPToolTarget) -> _ApprovedHTTPDestination:
        """Return a pinned destination or reject without opening a connection."""

        if self.require_https and target.scheme != "https":
            raise OutboundTargetRejectedError("unencrypted outbound HTTP is disabled")
        if self.allowed_hosts is not None and target.host not in self.allowed_hosts:
            raise OutboundTargetRejectedError("target host is not allowlisted")

        try:
            literal = ipaddress.ip_address(target.host)
        except ValueError:
            try:
                raw_addresses = await self.resolver(target.host, target.port)
            except Exception as error:
                raise OutboundTargetRejectedError("target resolution failed") from error
        else:
            raw_addresses = (literal.compressed,)

        if not raw_addresses or len(raw_addresses) > MAX_RESOLVED_ADDRESSES:
            raise OutboundTargetRejectedError("target resolution returned an invalid result")
        if any(not isinstance(value, str) for value in raw_addresses):
            raise OutboundTargetRejectedError("target resolution returned an invalid address")
        try:
            addresses = frozenset(ipaddress.ip_address(value) for value in raw_addresses)
        except ValueError as error:
            raise OutboundTargetRejectedError(
                "target resolution returned an invalid address"
            ) from error
        if not self.allow_non_global_addresses and any(
            not _is_public_address(address) for address in addresses
        ):
            raise OutboundTargetRejectedError("target resolved to a non-public address")

        selected = min(addresses, key=lambda address: (address.version, int(address)))
        pinned_host = f"[{selected.compressed}]" if selected.version == 6 else selected.compressed
        pinned_netloc = f"{pinned_host}:{target.port}" if target._explicit_port else pinned_host
        parsed = urlsplit(target.url)
        pinned_url = urlunsplit((target.scheme, pinned_netloc, parsed.path, parsed.query, ""))
        original_host = f"[{target.host}]" if ":" in target.host else target.host
        host_header = f"{original_host}:{target.port}" if target._explicit_port else original_host
        return _ApprovedHTTPDestination(pinned_url, host_header, target.host)


def _is_public_address(address: IPAddress) -> bool:
    if not address.is_global:
        return False
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None or address.sixtofour is not None:
            return False
        if address.teredo is not None:
            return False
    return True


@final
class HTTPToolAdapter:
    """POST JSON once and normalize transport, status, media, and body failures."""

    __slots__ = ("_client", "_policy", "_target")

    def __init__(
        self,
        client: httpx.AsyncClient,
        target: HTTPToolTarget,
        *,
        policy: OutboundHTTPPolicy | None = None,
    ) -> None:
        self._client = client
        self._target = target
        self._policy = policy or OutboundHTTPPolicy()

    async def execute(self, invocation: AdapterInvocation) -> AdapterResult:
        try:
            destination = await self._policy.authorize(self._target)
        except OutboundTargetRejectedError:
            return AdapterFailure(ExecutionErrorCode.OUTBOUND_TARGET_REJECTED)
        try:
            async with self._client.stream(
                "POST",
                destination.url,
                json=thaw_json(invocation.arguments),
                headers={"host": destination.host_header},
                timeout=self._target.timeout_seconds,
                follow_redirects=False,
                extensions={"sni_hostname": destination.sni_hostname},
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
