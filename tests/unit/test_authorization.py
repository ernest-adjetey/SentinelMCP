"""Default-deny exact-operation authorization tests."""

from typing import cast

import pytest

from sentinelmcp.domain import (
    OperationClass,
    ToolDefinition,
    ToolIdentity,
    ToolName,
    ToolNamespace,
    ToolVersion,
)
from sentinelmcp.policy import (
    AuthorizationDecision,
    AuthorizationReason,
    AuthorizationRule,
    PolicyEffect,
    Principal,
    RuleBasedAuthorizationPolicy,
)


def definition(operation: OperationClass = OperationClass.READ) -> ToolDefinition:
    return ToolDefinition(
        namespace=ToolNamespace("demo.catalog"),
        name=ToolName("lookup"),
        version=ToolVersion("1.0.0"),
        description="Exercise authorization policy.",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        operation_class=operation,
    )


def principal(*, roles: frozenset[str], scopes: frozenset[str]) -> Principal:
    return Principal("user-1", "https://issuer.test", roles, scopes)


def test_matching_allow_requires_all_declared_roles_and_scopes() -> None:
    tool = definition()
    policy = RuleBasedAuthorizationPolicy(
        (
            AuthorizationRule(
                PolicyEffect.ALLOW,
                tool.identity,
                OperationClass.READ,
                required_roles=frozenset({"developer"}),
                required_scopes=frozenset({"tools:execute", "catalog:read"}),
            ),
        )
    )

    allowed = policy.authorize(
        principal(
            roles=frozenset({"developer"}),
            scopes=frozenset({"tools:execute", "catalog:read"}),
        ),
        tool,
    )
    missing_scope = policy.authorize(
        principal(roles=frozenset({"developer"}), scopes=frozenset({"tools:execute"})),
        tool,
    )

    assert allowed.allowed is True
    assert allowed.reason is AuthorizationReason.ALLOWED
    assert missing_scope.allowed is False
    assert missing_scope.reason is AuthorizationReason.NO_MATCHING_ALLOW


def test_explicit_deny_precedes_matching_allow() -> None:
    tool = definition()
    caller = principal(
        roles=frozenset({"developer", "suspended"}),
        scopes=frozenset({"tools:execute"}),
    )
    policy = RuleBasedAuthorizationPolicy(
        (
            AuthorizationRule(
                PolicyEffect.ALLOW,
                tool.identity,
                OperationClass.READ,
                required_roles=frozenset({"developer"}),
            ),
            AuthorizationRule(
                PolicyEffect.DENY,
                tool.identity,
                OperationClass.READ,
                required_roles=frozenset({"suspended"}),
            ),
        )
    )

    decision = policy.authorize(caller, tool)

    assert decision.allowed is False
    assert decision.reason is AuthorizationReason.EXPLICIT_DENY


def test_policy_defaults_to_deny_and_operation_classes_do_not_imply_each_other() -> None:
    read = definition(OperationClass.READ)
    mutation = definition(OperationClass.MUTATION)
    caller = principal(roles=frozenset(), scopes=frozenset({"catalog:read"}))
    policy = RuleBasedAuthorizationPolicy(
        (
            AuthorizationRule(
                PolicyEffect.ALLOW,
                read.identity,
                OperationClass.READ,
                required_scopes=frozenset({"catalog:read"}),
            ),
        )
    )

    assert policy.authorize(caller, read).allowed is True
    assert policy.authorize(caller, mutation).allowed is False
    assert RuleBasedAuthorizationPolicy(()).authorize(caller, read).allowed is False


def test_decision_and_rule_configuration_reject_invalid_runtime_types() -> None:
    tool = definition()
    with pytest.raises(TypeError, match="boolean"):
        AuthorizationDecision(cast(bool, 1), AuthorizationReason.ALLOWED)
    with pytest.raises(TypeError, match="reason"):
        AuthorizationDecision(True, cast(AuthorizationReason, "allowed"))
    with pytest.raises(TypeError, match="effect"):
        AuthorizationRule(
            cast(PolicyEffect, "allow"),
            tool.identity,
            OperationClass.READ,
        )
    with pytest.raises(TypeError, match="identity"):
        AuthorizationRule(
            PolicyEffect.ALLOW,
            cast(ToolIdentity, object()),
            OperationClass.READ,
        )
    with pytest.raises(TypeError, match="operation"):
        AuthorizationRule(
            PolicyEffect.ALLOW,
            tool.identity,
            cast(OperationClass, "read"),
        )


def test_policy_rejects_invalid_rules_and_claim_requirements() -> None:
    tool = definition()
    with pytest.raises(ValueError, match="required_roles"):
        AuthorizationRule(
            PolicyEffect.ALLOW,
            tool.identity,
            OperationClass.READ,
            required_roles=cast(frozenset[str], "developer"),
        )
    with pytest.raises(TypeError, match="rules"):
        RuleBasedAuthorizationPolicy(cast(tuple[AuthorizationRule, ...], (object(),)))
