"""Architecture smoke tests for declared package boundaries."""

from importlib import import_module

import pytest

COMPONENT_PACKAGES = (
    "sentinelmcp.adapters",
    "sentinelmcp.adapters.mcp",
    "sentinelmcp.adapters.rest",
    "sentinelmcp.application",
    "sentinelmcp.audit",
    "sentinelmcp.cli",
    "sentinelmcp.domain",
    "sentinelmcp.evaluation",
    "sentinelmcp.execution",
    "sentinelmcp.fault_injection",
    "sentinelmcp.mutation_guard",
    "sentinelmcp.policy",
    "sentinelmcp.registry",
    "sentinelmcp.replay",
    "sentinelmcp.telemetry",
)


@pytest.mark.parametrize("package_name", COMPONENT_PACKAGES)
def test_declared_component_package_is_importable(package_name: str) -> None:
    """Every component documented in the architecture must remain importable."""
    module = import_module(package_name)

    assert module.__package__ == package_name
