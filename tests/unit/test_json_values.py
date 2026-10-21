"""Tests for bounded, deeply immutable JSON values."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

import pytest

from sentinelmcp.domain.json_values import (
    FrozenJSONMapping,
    JSONValueError,
    freeze_json,
    freeze_json_mapping,
    thaw_json,
)


def test_freeze_json_copies_sorts_and_hashes_nested_values() -> None:
    source: dict[str, object] = {"z": [1, True, None], "a": {"value": 1.5}}

    frozen = freeze_json_mapping(source)

    assert isinstance(frozen, FrozenJSONMapping)
    assert list(frozen) == ["a", "z"]
    assert len(frozen) == 2
    assert frozen == freeze_json_mapping({"a": {"value": 1.5}, "z": [1, True, None]})
    assert hash(frozen) == hash(freeze_json_mapping(source))
    assert "FrozenJSONMapping" in repr(frozen)


def test_thaw_json_returns_detached_mutable_collections() -> None:
    frozen = freeze_json_mapping({"items": [{"name": "one"}]})

    thawed = thaw_json(frozen)

    assert thawed == {"items": [{"name": "one"}]}
    assert isinstance(thawed, dict)
    items = thawed["items"]
    assert isinstance(items, list)
    items.append("two")
    assert thaw_json(frozen) == {"items": [{"name": "one"}]}


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_freeze_json_rejects_non_finite_numbers(value: float) -> None:
    with pytest.raises(JSONValueError, match="non-finite"):
        freeze_json(value)


def test_freeze_json_rejects_excessive_nesting() -> None:
    with pytest.raises(JSONValueError, match="maximum depth"):
        freeze_json([[[0]]], max_depth=1)


def test_freeze_json_stops_at_node_limit_during_copy() -> None:
    with pytest.raises(JSONValueError, match="node count") as captured:
        freeze_json([1, 2], max_nodes=2)

    assert captured.value.path == "$[1]"


def test_freeze_json_rejects_non_string_object_keys() -> None:
    invalid = cast(Mapping[str, object], {1: "value"})

    with pytest.raises(JSONValueError, match="keys must be strings"):
        freeze_json_mapping(invalid)


def test_freeze_json_rejects_cyclic_lists() -> None:
    cyclic: list[object] = []
    cyclic.append(cyclic)

    with pytest.raises(JSONValueError, match="cyclic"):
        freeze_json(cyclic)


def test_freeze_json_allows_reused_noncyclic_containers() -> None:
    reused = ["value"]

    assert thaw_json(freeze_json([reused, reused])) == [["value"], ["value"]]
