"""Small deterministic authorization policy for exact tool operations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, final, runtime_checkable

from sentinelmcp.domain.tools import OperationClass, ToolDefinition, ToolIdentity
from sentinelmcp.policy.authentication import Principal, _validated_claim_values


class PolicyEffect(StrEnum):
    """Explicit policy rule effect."""

    ALLOW = "allow"
    DENY = "deny"


class AuthorizationReason(StrEnum):
    """Stable reasons suitable for bounded security decisions."""

    ALLOWED = "allowed"
    EXPLICIT_DENY = "explicit_deny"
    NO_MATCHING_ALLOW = "no_matching_allow"


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    """One deterministic allow or deny result."""

    allowed: bool
    reason: AuthorizationReason

    def __post_init__(self) -> None:
        if not isinstance(self.allowed, bool):
            raise TypeError("authorization decision allowed flag must be a boolean")
        if not isinstance(self.reason, AuthorizationReason):
            raise TypeError("authorization decision reason must be an AuthorizationReason")


@dataclass(frozen=True, slots=True)
class AuthorizationRule:
    """Exact tool/operation rule with all-of role and scope conditions."""

    effect: PolicyEffect
    identity: ToolIdentity
    operation_class: OperationClass
    required_roles: frozenset[str] = frozenset()
    required_scopes: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not isinstance(self.effect, PolicyEffect):
            raise TypeError("authorization rule effect must be a PolicyEffect")
        if not isinstance(self.identity, ToolIdentity):
            raise TypeError("authorization rule identity must be a ToolIdentity")
        if not isinstance(self.operation_class, OperationClass):
            raise TypeError("authorization rule operation must be an OperationClass")
        object.__setattr__(
            self,
            "required_roles",
            _validated_claim_values(self.required_roles, claim="required_roles"),
        )
        object.__setattr__(
            self,
            "required_scopes",
            _validated_claim_values(self.required_scopes, claim="required_scopes"),
        )

    def target_matches(self, definition: ToolDefinition) -> bool:
        return (
            self.identity == definition.identity
            and self.operation_class is definition.operation_class
        )

    def principal_matches(self, principal: Principal) -> bool:
        return self.required_roles <= principal.roles and self.required_scopes <= principal.scopes


@runtime_checkable
class AuthorizationPolicy(Protocol):
    """Policy boundary used by the application executor."""

    def authorize(
        self,
        principal: Principal,
        definition: ToolDefinition,
    ) -> AuthorizationDecision:
        """Return a default-deny decision for one exact operation."""


@final
class RuleBasedAuthorizationPolicy(AuthorizationPolicy):
    """Evaluate exact rules with explicit-deny precedence and default deny."""

    __slots__ = ("_rules",)

    def __init__(self, rules: tuple[AuthorizationRule, ...]) -> None:
        if any(not isinstance(rule, AuthorizationRule) for rule in rules):
            raise TypeError("authorization policy rules must be AuthorizationRule values")
        self._rules = tuple(rules)

    def authorize(
        self,
        principal: Principal,
        definition: ToolDefinition,
    ) -> AuthorizationDecision:
        matching = tuple(
            rule
            for rule in self._rules
            if rule.target_matches(definition) and rule.principal_matches(principal)
        )
        if any(rule.effect is PolicyEffect.DENY for rule in matching):
            return AuthorizationDecision(False, AuthorizationReason.EXPLICIT_DENY)
        if any(rule.effect is PolicyEffect.ALLOW for rule in matching):
            return AuthorizationDecision(True, AuthorizationReason.ALLOWED)
        return AuthorizationDecision(False, AuthorizationReason.NO_MATCHING_ALLOW)
