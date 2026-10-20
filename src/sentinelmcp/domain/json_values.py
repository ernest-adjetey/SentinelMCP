"""Deeply immutable JSON-compatible values."""

from __future__ import annotations

import math
from collections.abc import Iterator, Mapping
from types import MappingProxyType
from typing import cast, final

type JSONScalar = bool | int | float | str | None


@final
class FrozenJSONMapping(Mapping[str, "FrozenJSONValue"]):
    """A recursively immutable JSON object with deterministic key iteration."""

    __slots__ = ("_data", "_hash")

    def __init__(self, values: Mapping[str, FrozenJSONValue]) -> None:
        ordered = dict(sorted(values.items()))
        self._data: Mapping[str, FrozenJSONValue] = MappingProxyType(ordered)
        self._hash = hash(tuple(ordered.items()))

    def __getitem__(self, key: str) -> FrozenJSONValue:
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __hash__(self) -> int:
        return self._hash

    def __repr__(self) -> str:
        return f"FrozenJSONMapping({dict(self._data)!r})"


type FrozenJSONValue = JSONScalar | FrozenJSONMapping | tuple[FrozenJSONValue, ...]


class JSONValueError(ValueError):
    """Raised when a value cannot be represented as bounded JSON data."""

    def __init__(self, *, path: str, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"{path}: {reason}")


def freeze_json(value: object, *, max_depth: int = 128) -> FrozenJSONValue:
    """Validate, copy, and recursively freeze a JSON-compatible value."""

    return _freeze_json(value, path="$", depth=0, max_depth=max_depth, active_ids=set())


def freeze_json_mapping(value: Mapping[str, object], *, max_depth: int = 128) -> FrozenJSONMapping:
    """Freeze a JSON object and preserve its mapping type in the return contract."""

    return _freeze_mapping(
        cast(Mapping[object, object], value),
        path="$",
        depth=0,
        max_depth=max_depth,
        active_ids=set(),
    )


def thaw_json(value: FrozenJSONValue) -> object:
    """Return a detached mutable JSON representation for third-party libraries."""

    if isinstance(value, FrozenJSONMapping):
        return {key: thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw_json(item) for item in value]
    return value


def _freeze_json(
    value: object,
    *,
    path: str,
    depth: int,
    max_depth: int,
    active_ids: set[int],
) -> FrozenJSONValue:
    if depth > max_depth:
        raise JSONValueError(path=path, reason=f"nesting exceeds maximum depth {max_depth}")
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise JSONValueError(path=path, reason="non-finite numbers are not valid JSON")
        return value
    if isinstance(value, Mapping):
        return _freeze_mapping(
            value,
            path=path,
            depth=depth,
            max_depth=max_depth,
            active_ids=active_ids,
        )
    if isinstance(value, list | tuple):
        marker = id(value)
        _enter_container(marker, path=path, active_ids=active_ids)
        try:
            return tuple(
                _freeze_json(
                    item,
                    path=f"{path}[{index}]",
                    depth=depth + 1,
                    max_depth=max_depth,
                    active_ids=active_ids,
                )
                for index, item in enumerate(value)
            )
        finally:
            active_ids.remove(marker)
    raise JSONValueError(path=path, reason=f"unsupported JSON value type {type(value).__name__}")


def _freeze_mapping(
    value: Mapping[object, object],
    *,
    path: str,
    depth: int,
    max_depth: int,
    active_ids: set[int],
) -> FrozenJSONMapping:
    marker = id(value)
    _enter_container(marker, path=path, active_ids=active_ids)
    try:
        frozen: dict[str, FrozenJSONValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise JSONValueError(path=path, reason="JSON object keys must be strings")
            frozen[key] = _freeze_json(
                item,
                path=f"{path}.{key}",
                depth=depth + 1,
                max_depth=max_depth,
                active_ids=active_ids,
            )
        return FrozenJSONMapping(frozen)
    finally:
        active_ids.remove(marker)


def _enter_container(marker: int, *, path: str, active_ids: set[int]) -> None:
    if marker in active_ids:
        raise JSONValueError(path=path, reason="cyclic containers are not valid JSON")
    active_ids.add(marker)
