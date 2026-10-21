"""Canonical binding digests for confirmation and idempotency."""

from __future__ import annotations

from hashlib import sha256
from typing import Any, cast

import rfc8785

from sentinelmcp.domain.json_values import FrozenJSONMapping, thaw_json
from sentinelmcp.domain.tools import ToolRef
from sentinelmcp.mutation_guard.errors import InvalidMutationRequestError
from sentinelmcp.policy import Principal


def canonicalize_arguments(arguments: FrozenJSONMapping) -> bytes:
    """Return RFC 8785 bytes for validated immutable mutation arguments."""

    try:
        return rfc8785.dumps(cast(Any, thaw_json(arguments)))
    except rfc8785.CanonicalizationError as error:
        raise InvalidMutationRequestError(
            reason="arguments must be representable by RFC 8785 canonical JSON"
        ) from error


def arguments_digest(arguments: FrozenJSONMapping) -> str:
    """Return the SHA-256 digest of canonical mutation arguments."""

    return sha256(canonicalize_arguments(arguments)).hexdigest()


def semantic_request_digest(
    *,
    principal: Principal,
    tool_ref: ToolRef,
    definition_fingerprint: str,
    arguments: FrozenJSONMapping,
) -> str:
    """Bind one idempotency record to actor, exact contract, and arguments."""

    document = {
        "arguments": thaw_json(arguments),
        "definition_fingerprint": definition_fingerprint,
        "operation_class": "mutation",
        "principal": {"issuer": principal.issuer, "subject": principal.subject},
        "tool_ref": str(tool_ref),
    }
    try:
        canonical = rfc8785.dumps(cast(Any, document))
    except rfc8785.CanonicalizationError as error:
        raise InvalidMutationRequestError(
            reason="semantic request must be representable by RFC 8785 canonical JSON"
        ) from error
    return sha256(canonical).hexdigest()


def confirmation_token_digest(token: str) -> bytes:
    """Hash one validated opaque token for lookup without persistence of the secret."""

    try:
        encoded = token.encode("ascii")
    except UnicodeEncodeError as error:
        raise InvalidMutationRequestError(reason="confirmation token must be ASCII") from error
    if not 32 <= len(encoded) <= 256:
        raise InvalidMutationRequestError(reason="confirmation token has an invalid length")
    return sha256(encoded).digest()
