"""Deterministic public demo tools composed through the real Phase 3 lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from sentinelmcp.application.executor import ToolExecutor
from sentinelmcp.domain import (
    OperationClass,
    ToolDefinition,
    ToolName,
    ToolNamespace,
    ToolVersion,
)
from sentinelmcp.domain.json_values import FrozenJSONMapping
from sentinelmcp.execution import (
    InMemoryRuntimeBindingRegistry,
    RuntimeToolBinding,
    SyntheticToolAdapter,
)
from sentinelmcp.policy import AuthorizationRule, PolicyEffect, RuleBasedAuthorizationPolicy
from sentinelmcp.registry import InMemoryToolRegistry


@dataclass(frozen=True, slots=True)
class DemoRuntime:
    """Composed in-memory services used by docs, tests, and the demo gateway."""

    registry: InMemoryToolRegistry
    bindings: InMemoryRuntimeBindingRegistry
    executor: ToolExecutor


def create_demo_runtime() -> DemoRuntime:
    registry = InMemoryToolRegistry()
    bindings = InMemoryRuntimeBindingRegistry()

    tools = (
        (_math_add_definition(), SyntheticToolAdapter(_math_add)),
        (_catalog_lookup_definition(), SyntheticToolAdapter(_catalog_lookup)),
        (_profile_get_definition(), SyntheticToolAdapter(_profile_get)),
    )
    for definition, adapter in tools:
        registry.register(definition)
        bindings.bind(RuntimeToolBinding(definition.ref, adapter))

    policy = RuleBasedAuthorizationPolicy(
        tuple(
            AuthorizationRule(PolicyEffect.ALLOW, definition.identity, definition.operation_class)
            for definition, _adapter in tools
        )
    )

    return DemoRuntime(
        registry=registry,
        bindings=bindings,
        executor=ToolExecutor(registry, bindings, authorization_policy=policy),
    )


def _math_add_definition() -> ToolDefinition:
    return ToolDefinition(
        namespace=ToolNamespace("demo.math"),
        name=ToolName("add"),
        version=ToolVersion("1.0.0"),
        description="Add two numbers in a deterministic synthetic tool.",
        input_schema={
            "type": "object",
            "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
            "required": ["a", "b"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {"sum": {"type": "number"}},
            "required": ["sum"],
            "additionalProperties": False,
        },
        operation_class=OperationClass.READ,
        capabilities=frozenset({"demo", "math", "replay-safe"}),
    )


def _catalog_lookup_definition() -> ToolDefinition:
    return ToolDefinition(
        namespace=ToolNamespace("demo.catalog"),
        name=ToolName("lookup"),
        version=ToolVersion("1.0.0"),
        description="Return a nested synthetic catalog record.",
        input_schema={
            "type": "object",
            "properties": {"id": {"type": "string", "minLength": 1}},
            "required": ["id"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "asset_id": {"type": "string"},
                "name": {"type": "string"},
                "details": {
                    "type": "object",
                    "properties": {
                        "category": {"type": "string"},
                        "tags": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["category", "tags"],
                    "additionalProperties": False,
                },
            },
            "required": ["asset_id", "name", "details"],
            "additionalProperties": False,
        },
        operation_class=OperationClass.READ,
        capabilities=frozenset({"catalog", "demo", "replay-safe"}),
    )


def _profile_get_definition() -> ToolDefinition:
    return ToolDefinition(
        namespace=ToolNamespace("demo.profile"),
        name=ToolName("get"),
        version=ToolVersion("1.0.0"),
        description="Return a deterministic synthetic profile.",
        input_schema={
            "type": "object",
            "properties": {"username": {"type": "string", "minLength": 1}},
            "required": ["username"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "username": {"type": "string"},
                "display_name": {"type": "string"},
                "preferences": {
                    "type": "object",
                    "properties": {"theme": {"enum": ["light", "dark"]}},
                    "required": ["theme"],
                    "additionalProperties": False,
                },
            },
            "required": ["username", "display_name", "preferences"],
            "additionalProperties": False,
        },
        operation_class=OperationClass.READ,
        capabilities=frozenset({"demo", "profile", "replay-safe"}),
    )


async def _math_add(arguments: FrozenJSONMapping) -> object:
    left = cast(int | float, arguments["a"])
    right = cast(int | float, arguments["b"])
    return {"sum": left + right}


async def _catalog_lookup(arguments: FrozenJSONMapping) -> object:
    asset_id = cast(str, arguments["id"])
    return {
        "asset_id": asset_id,
        "name": f"Synthetic asset {asset_id}",
        "details": {"category": "demo", "tags": ["synthetic", "public"]},
    }


async def _profile_get(arguments: FrozenJSONMapping) -> object:
    username = cast(str, arguments["username"])
    return {
        "username": username,
        "display_name": username.replace("-", " ").title(),
        "preferences": {"theme": "light"},
    }
