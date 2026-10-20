"""Immutable versioned tool contracts."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from functools import total_ordering
from typing import Any

import semver

from sentinelmcp.domain.errors import (
    InvalidToolDefinitionError,
    InvalidToolReferenceError,
)
from sentinelmcp.domain.json_values import JSONValueError, freeze_json_mapping

_SEGMENT_PATTERN = re.compile(r"^[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_MAX_NAMESPACE_LENGTH = 191
_MAX_NAMESPACE_SEGMENTS = 16
_MAX_VERSION_LENGTH = 128
_MAX_DESCRIPTION_LENGTH = 1_000
_MAX_CAPABILITIES = 64


def _validate_segment(value: object, *, component: str) -> str:
    if not isinstance(value, str):
        raise InvalidToolReferenceError(
            component=component,
            value=value,
            reason="must be a string",
        )
    if not _SEGMENT_PATTERN.fullmatch(value):
        raise InvalidToolReferenceError(
            component=component,
            value=value,
            reason=(
                "must be 1-63 lowercase ASCII letters, digits, or hyphens; "
                "it must start with a letter and end with a letter or digit"
            ),
        )
    return value


@dataclass(frozen=True, slots=True, order=True)
class ToolNamespace:
    """A dot-separated sequence of strict lowercase identifier segments."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise InvalidToolReferenceError(
                component="namespace",
                value=self.value,
                reason="must be a string",
            )
        if len(self.value) > _MAX_NAMESPACE_LENGTH:
            raise InvalidToolReferenceError(
                component="namespace",
                value=self.value,
                reason=f"must not exceed {_MAX_NAMESPACE_LENGTH} characters",
            )
        segments = self.value.split(".")
        if len(segments) > _MAX_NAMESPACE_SEGMENTS:
            raise InvalidToolReferenceError(
                component="namespace",
                value=self.value,
                reason=f"must not exceed {_MAX_NAMESPACE_SEGMENTS} segments",
            )
        for segment in segments:
            _validate_segment(segment, component="namespace segment")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True, order=True)
class ToolName:
    """A strict lowercase tool name segment."""

    value: str

    def __post_init__(self) -> None:
        _validate_segment(self.value, component="name")

    def __str__(self) -> str:
        return self.value


@total_ordering
@dataclass(frozen=True, slots=True)
class ToolVersion:
    """A validated SemVer 2.0.0 version, including prerelease and build metadata."""

    value: str
    _parsed: semver.Version = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise InvalidToolReferenceError(
                component="version",
                value=self.value,
                reason="must be a string",
            )
        if not self.value or len(self.value) > _MAX_VERSION_LENGTH:
            raise InvalidToolReferenceError(
                component="version",
                value=self.value,
                reason=f"must contain 1-{_MAX_VERSION_LENGTH} characters",
            )
        try:
            parsed = semver.Version.parse(self.value)
        except ValueError as error:
            raise InvalidToolReferenceError(
                component="version",
                value=self.value,
                reason="must be a complete Semantic Versioning 2.0.0 value",
            ) from error
        object.__setattr__(self, "_parsed", parsed)

    def __str__(self) -> str:
        return self.value

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, ToolVersion):
            return NotImplemented
        precedence = self._parsed.compare(other._parsed)
        if precedence != 0:
            return precedence < 0
        return self.value < other.value


@dataclass(frozen=True, slots=True, order=True)
class ToolIdentity:
    """The namespace and name shared by all immutable versions of one tool."""

    namespace: ToolNamespace
    name: ToolName

    def __post_init__(self) -> None:
        if not isinstance(self.namespace, ToolNamespace):
            raise InvalidToolReferenceError(
                component="namespace",
                value=self.namespace,
                reason="must be a ToolNamespace",
            )
        if not isinstance(self.name, ToolName):
            raise InvalidToolReferenceError(
                component="name",
                value=self.name,
                reason="must be a ToolName",
            )

    def __str__(self) -> str:
        return f"{self.namespace}.{self.name}"


@dataclass(frozen=True, slots=True, order=True)
class ToolRef:
    """An exact immutable tool version in ``namespace.name@version`` form."""

    namespace: ToolNamespace
    name: ToolName
    version: ToolVersion

    def __post_init__(self) -> None:
        if not isinstance(self.namespace, ToolNamespace):
            raise InvalidToolReferenceError(
                component="namespace",
                value=self.namespace,
                reason="must be a ToolNamespace",
            )
        if not isinstance(self.name, ToolName):
            raise InvalidToolReferenceError(
                component="name",
                value=self.name,
                reason="must be a ToolName",
            )
        if not isinstance(self.version, ToolVersion):
            raise InvalidToolReferenceError(
                component="version",
                value=self.version,
                reason="must be a ToolVersion",
            )

    @classmethod
    def parse(cls, value: str) -> ToolRef:
        """Parse the canonical external representation without implicit normalization."""

        if not isinstance(value, str) or value.count("@") != 1:
            raise InvalidToolReferenceError(
                component="tool reference",
                value=value,
                reason="must have the form namespace.name@version",
            )
        qualified_name, version_value = value.split("@")
        namespace_value, separator, name_value = qualified_name.rpartition(".")
        if not separator or not namespace_value or not name_value:
            raise InvalidToolReferenceError(
                component="tool reference",
                value=value,
                reason="must have the form namespace.name@version",
            )
        return cls(
            namespace=ToolNamespace(namespace_value),
            name=ToolName(name_value),
            version=ToolVersion(version_value),
        )

    @property
    def identity(self) -> ToolIdentity:
        return ToolIdentity(namespace=self.namespace, name=self.name)

    def __str__(self) -> str:
        return f"{self.namespace}.{self.name}@{self.version}"


class OperationClass(StrEnum):
    """The only operation classes understood by the safety model."""

    READ = "read"
    MUTATION = "mutation"


@dataclass(frozen=True, slots=True)
class TimeoutPolicy:
    """Declared upper bound for one future end-to-end execution budget."""

    timeout_ms: int = 30_000

    def __post_init__(self) -> None:
        _validate_int_range(
            "timeout_policy.timeout_ms", self.timeout_ms, minimum=1, maximum=300_000
        )


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Declarative retry limits; retry eligibility remains an execution concern."""

    max_attempts: int = 1
    initial_delay_ms: int = 100
    maximum_delay_ms: int = 1_000

    def __post_init__(self) -> None:
        _validate_int_range("retry_policy.max_attempts", self.max_attempts, minimum=1, maximum=10)
        _validate_int_range(
            "retry_policy.initial_delay_ms",
            self.initial_delay_ms,
            minimum=0,
            maximum=60_000,
        )
        _validate_int_range(
            "retry_policy.maximum_delay_ms",
            self.maximum_delay_ms,
            minimum=0,
            maximum=300_000,
        )
        if self.initial_delay_ms > self.maximum_delay_ms:
            raise InvalidToolDefinitionError(
                field="retry_policy",
                reason="initial delay must not exceed maximum delay",
            )


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """A deeply immutable declaration for one exact tool version."""

    namespace: ToolNamespace
    name: ToolName
    version: ToolVersion
    description: str
    input_schema: Mapping[str, object]
    output_schema: Mapping[str, object]
    operation_class: OperationClass
    timeout_policy: TimeoutPolicy = field(default_factory=TimeoutPolicy)
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    idempotency_supported: bool = False
    capabilities: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        self._validate_types()
        if not self.description or self.description != self.description.strip():
            raise InvalidToolDefinitionError(
                field="description",
                reason="must be non-empty and have no leading or trailing whitespace",
            )
        if len(self.description) > _MAX_DESCRIPTION_LENGTH:
            raise InvalidToolDefinitionError(
                field="description",
                reason=f"must not exceed {_MAX_DESCRIPTION_LENGTH} characters",
            )
        try:
            frozen_input = freeze_json_mapping(self.input_schema)
            frozen_output = freeze_json_mapping(self.output_schema)
        except JSONValueError as error:
            raise InvalidToolDefinitionError(
                field="schema",
                reason=str(error),
            ) from error
        object.__setattr__(self, "input_schema", frozen_input)
        object.__setattr__(self, "output_schema", frozen_output)
        object.__setattr__(self, "capabilities", self._freeze_capabilities())
        if (
            self.operation_class is OperationClass.MUTATION
            and self.retry_policy.max_attempts > 1
            and not self.idempotency_supported
        ):
            raise InvalidToolDefinitionError(
                field="retry_policy",
                reason="mutation retries require declared idempotency support",
            )

    @property
    def ref(self) -> ToolRef:
        return ToolRef(namespace=self.namespace, name=self.name, version=self.version)

    @property
    def identity(self) -> ToolIdentity:
        return ToolIdentity(namespace=self.namespace, name=self.name)

    def _validate_types(self) -> None:
        expected_types: tuple[tuple[str, object, type[Any]], ...] = (
            ("namespace", self.namespace, ToolNamespace),
            ("name", self.name, ToolName),
            ("version", self.version, ToolVersion),
            ("operation_class", self.operation_class, OperationClass),
            ("timeout_policy", self.timeout_policy, TimeoutPolicy),
            ("retry_policy", self.retry_policy, RetryPolicy),
        )
        for field_name, value, expected_type in expected_types:
            if not isinstance(value, expected_type):
                raise InvalidToolDefinitionError(
                    field=field_name,
                    reason=f"must be a {expected_type.__name__}",
                )
        if not isinstance(self.description, str):
            raise InvalidToolDefinitionError(field="description", reason="must be a string")
        if not isinstance(self.input_schema, Mapping):
            raise InvalidToolDefinitionError(field="input_schema", reason="must be a mapping")
        if not isinstance(self.output_schema, Mapping):
            raise InvalidToolDefinitionError(field="output_schema", reason="must be a mapping")
        if not isinstance(self.idempotency_supported, bool):
            raise InvalidToolDefinitionError(
                field="idempotency_supported",
                reason="must be a boolean",
            )

    def _freeze_capabilities(self) -> frozenset[str]:
        raw_capabilities: object = self.capabilities
        if isinstance(raw_capabilities, str):
            raise InvalidToolDefinitionError(
                field="capabilities",
                reason="must be a collection of capability names",
            )
        try:
            capabilities = frozenset(self.capabilities)
        except TypeError as error:
            raise InvalidToolDefinitionError(
                field="capabilities",
                reason="must be an iterable of strings",
            ) from error
        if len(capabilities) > _MAX_CAPABILITIES:
            raise InvalidToolDefinitionError(
                field="capabilities",
                reason=f"must not contain more than {_MAX_CAPABILITIES} values",
            )
        for capability in capabilities:
            _validate_segment(capability, component="capability")
        return capabilities


def _validate_int_range(field_name: str, value: object, *, minimum: int, maximum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidToolDefinitionError(field=field_name, reason="must be an integer")
    if not minimum <= value <= maximum:
        raise InvalidToolDefinitionError(
            field=field_name,
            reason=f"must be between {minimum} and {maximum}",
        )
