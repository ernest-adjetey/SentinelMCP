"""Tests for exact operational runtime bindings."""

from __future__ import annotations

import pytest

from sentinelmcp.domain import ToolRef
from sentinelmcp.execution import (
    AdapterInvocation,
    AdapterResult,
    AdapterSuccess,
    BindingNotFoundError,
    InMemoryRuntimeBindingRegistry,
    RuntimeBindingResolver,
    RuntimeToolBinding,
)


class StubAdapter:
    async def execute(self, _invocation: AdapterInvocation) -> AdapterResult:
        return AdapterSuccess({"ok": True})


def test_binding_registry_satisfies_protocol_and_resolves_exact_version() -> None:
    registry = InMemoryRuntimeBindingRegistry()
    reference = ToolRef.parse("demo.math.add@1.0.0")
    binding = RuntimeToolBinding(reference, StubAdapter())

    assert isinstance(registry, RuntimeBindingResolver)
    registry.bind(binding)

    assert registry.resolve(reference) is binding
    assert registry.list_bindings() == (binding,)


def test_binding_is_operational_state_that_can_be_replaced_or_removed() -> None:
    registry = InMemoryRuntimeBindingRegistry()
    reference = ToolRef.parse("demo.math.add@1.0.0")
    first = RuntimeToolBinding(reference, StubAdapter())
    second = RuntimeToolBinding(reference, StubAdapter())

    registry.bind(first)
    registry.bind(second)
    assert registry.resolve(reference) is second

    registry.unbind(reference)
    try:
        registry.resolve(reference)
    except BindingNotFoundError as error:
        assert error.tool_ref == reference
    else:
        raise AssertionError("missing binding did not raise")


def test_binding_listing_is_sorted_by_exact_reference() -> None:
    registry = InMemoryRuntimeBindingRegistry()
    later = RuntimeToolBinding(ToolRef.parse("demo.zeta.get@2.0.0"), StubAdapter())
    earlier = RuntimeToolBinding(ToolRef.parse("demo.alpha.get@1.0.0"), StubAdapter())
    registry.bind(later)
    registry.bind(earlier)

    assert registry.list_bindings() == (earlier, later)


def test_runtime_binding_rejects_untyped_reference_or_adapter() -> None:
    with pytest.raises(TypeError, match="ToolRef"):
        RuntimeToolBinding("demo.math.add@1.0.0", StubAdapter())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="ToolAdapter"):
        RuntimeToolBinding(ToolRef.parse("demo.math.add@1.0.0"), object())  # type: ignore[arg-type]
