"""Bounded recursive redaction before structured data leaves the process."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

_REDACTED = "[REDACTED]"
_TRUNCATED = "[TRUNCATED]"
_DEFAULT_SENSITIVE_KEYS = frozenset(
    {
        "apikey",
        "authorization",
        "bearertoken",
        "confirmationtoken",
        "cookie",
        "idempotencykey",
        "password",
        "refreshtoken",
        "secret",
        "setcookie",
        "token",
    }
)


@dataclass(frozen=True, slots=True)
class RedactionPolicy:
    """Allow bounded JSON-like diagnostics while replacing known secret fields."""

    sensitive_keys: frozenset[str] = _DEFAULT_SENSITIVE_KEYS
    max_depth: int = 8
    max_items: int = 128
    max_string_length: int = 512

    def __post_init__(self) -> None:
        if not isinstance(self.sensitive_keys, frozenset) or not all(
            isinstance(key, str) and key for key in self.sensitive_keys
        ):
            raise TypeError("sensitive_keys must be a frozenset of non-empty strings")
        for field, value, maximum in (
            ("max_depth", self.max_depth, 64),
            ("max_items", self.max_items, 10_000),
            ("max_string_length", self.max_string_length, 16_384),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
                raise ValueError(f"{field} must be from 1 through {maximum}")
        object.__setattr__(
            self,
            "sensitive_keys",
            frozenset(_normalize_key(key) for key in self.sensitive_keys),
        )

    def redact(self, value: object) -> object:
        """Return a bounded JSON-compatible value without mutating the caller input."""

        return self._redact(value, depth=0)

    def _redact(self, value: object, *, depth: int) -> object:
        if depth >= self.max_depth:
            return _TRUNCATED
        if value is None or isinstance(value, bool | int):
            return value
        if isinstance(value, float):
            return value if math.isfinite(value) else _TRUNCATED
        if isinstance(value, str):
            if len(value) <= self.max_string_length:
                return value
            return value[: self.max_string_length] + _TRUNCATED
        if isinstance(value, Mapping):
            redacted: dict[str, object] = {}
            for index, (key, item) in enumerate(value.items()):
                if index >= self.max_items:
                    redacted["_truncated"] = True
                    break
                if not isinstance(key, str):
                    continue
                redacted[key] = (
                    _REDACTED
                    if _normalize_key(key) in self.sensitive_keys
                    else self._redact(item, depth=depth + 1)
                )
            return redacted
        if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray | memoryview):
            items = [self._redact(item, depth=depth + 1) for item in value[: self.max_items]]
            if len(value) > self.max_items:
                items.append(_TRUNCATED)
            return items
        return _TRUNCATED


def _normalize_key(key: str) -> str:
    return "".join(character for character in key.lower() if character.isalnum())
