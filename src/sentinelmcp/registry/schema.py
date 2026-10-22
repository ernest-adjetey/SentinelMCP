"""Draft 2020-12 schema registration and runtime contract validation."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from typing import cast, final
from urllib.parse import unquote

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from referencing.exceptions import Unresolvable

from sentinelmcp.domain.errors import (
    ArgumentValidationError,
    InvalidSchemaError,
    OutputValidationError,
    PathComponent,
    SchemaKind,
    ValidationIssue,
)
from sentinelmcp.domain.json_values import FrozenJSONValue, JSONValueError, freeze_json, thaw_json
from sentinelmcp.domain.tools import ToolDefinition, ToolRef

DRAFT_2020_12_URI = "https://json-schema.org/draft/2020-12/schema"
_ACCEPTED_DIALECT_URIS = frozenset({DRAFT_2020_12_URI, f"{DRAFT_2020_12_URI}#"})
_ANCHOR_PATTERN = re.compile(r"^[A-Za-z_][-A-Za-z0-9._]*$")
_REFERENCE_KEYWORDS = frozenset({"$ref", "$dynamicRef"})

MAX_SCHEMA_DEPTH = 64
MAX_SCHEMA_NODES = 10_000
MAX_SCHEMA_BYTES = 1_000_000
MAX_INSTANCE_DEPTH = 128
MAX_INSTANCE_NODES = 100_000
MAX_VALIDATION_ISSUES = 32


@final
@dataclass(frozen=True, slots=True)
class ToolContractValidator:
    """Compiled input and output validators for one exact immutable tool version."""

    tool_ref: ToolRef
    _input_validator: Draft202012Validator = field(repr=False, compare=False)
    _output_validator: Draft202012Validator = field(repr=False, compare=False)

    @classmethod
    def compile(cls, definition: ToolDefinition) -> ToolContractValidator:
        """Validate both schemas and compile a reusable contract validator."""

        input_schema = _prepare_schema(definition.input_schema, schema_kind="input")
        output_schema = _prepare_schema(definition.output_schema, schema_kind="output")
        return cls(
            tool_ref=definition.ref,
            _input_validator=Draft202012Validator(input_schema),
            _output_validator=Draft202012Validator(output_schema),
        )

    def validate_arguments(self, arguments: object) -> None:
        """Raise a structured error when arguments violate the input contract."""

        issues = _runtime_validation_issues(self._input_validator, arguments)
        if issues:
            raise ArgumentValidationError(tool_ref=self.tool_ref, issues=issues)

    def validate_output(self, output: object) -> None:
        """Raise a structured error when a result violates the output contract."""

        issues = _runtime_validation_issues(self._output_validator, output)
        if issues:
            raise OutputValidationError(tool_ref=self.tool_ref, issues=issues)


@lru_cache(maxsize=1_024)
def validate_tool_definition_schemas(definition: ToolDefinition) -> ToolContractValidator:
    """Validate and compile an immutable definition's Draft 2020-12 schemas."""

    return ToolContractValidator.compile(definition)


def _prepare_schema(schema_value: object, *, schema_kind: SchemaKind) -> dict[str, object]:
    schema = _thaw_schema(schema_value)
    _inspect_schema(schema, schema_kind=schema_kind)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        issue = _issue_from_schema_error(error)
        raise InvalidSchemaError(
            schema_kind=schema_kind,
            reason="schema does not satisfy the Draft 2020-12 metaschema",
            issues=(issue,),
        ) from error
    _verify_local_references(schema, schema_kind=schema_kind)
    return schema


def _thaw_schema(schema_value: object) -> dict[str, object]:
    schema = thaw_json(cast(FrozenJSONValue, schema_value))
    if not isinstance(schema, dict):
        raise AssertionError("tool definition schemas must be JSON objects")
    return cast(dict[str, object], schema)


def _inspect_schema(schema: dict[str, object], *, schema_kind: SchemaKind) -> None:
    try:
        encoded = json.dumps(
            schema,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise InvalidSchemaError(
            schema_kind=schema_kind,
            reason="schema must be finite UTF-8 JSON data",
        ) from error
    if len(encoded) > MAX_SCHEMA_BYTES:
        raise InvalidSchemaError(
            schema_kind=schema_kind,
            reason=f"schema exceeds the {MAX_SCHEMA_BYTES}-byte limit",
        )

    nodes = 0
    stack: list[tuple[object, tuple[PathComponent, ...], int]] = [(schema, (), 0)]
    while stack:
        value, path, depth = stack.pop()
        nodes += 1
        if nodes > MAX_SCHEMA_NODES:
            raise InvalidSchemaError(
                schema_kind=schema_kind,
                reason=f"schema exceeds the {MAX_SCHEMA_NODES}-node limit",
            )
        if depth > MAX_SCHEMA_DEPTH:
            raise InvalidSchemaError(
                schema_kind=schema_kind,
                reason=f"schema exceeds the {MAX_SCHEMA_DEPTH}-level depth limit",
            )
        if isinstance(value, dict):
            _inspect_schema_object(value, path=path, schema_kind=schema_kind)
            stack.extend(
                (child, (*path, key), depth + 1) for key, child in reversed(tuple(value.items()))
            )
        elif isinstance(value, list):
            stack.extend(
                (child, (*path, index), depth + 1)
                for index, child in reversed(tuple(enumerate(value)))
            )


def _inspect_schema_object(
    value: dict[str, object],
    *,
    path: tuple[PathComponent, ...],
    schema_kind: SchemaKind,
) -> None:
    if "$schema" in value:
        dialect = value["$schema"]
        if path:
            _raise_policy_error(
                schema_kind,
                path=(*path, "$schema"),
                message="nested schema dialect declarations are not supported",
                validator="$schema",
            )
        if dialect not in _ACCEPTED_DIALECT_URIS:
            _raise_policy_error(
                schema_kind,
                path=("$schema",),
                message="schema dialect must be JSON Schema Draft 2020-12",
                validator="$schema",
            )
    if path and "$id" in value:
        _raise_policy_error(
            schema_kind,
            path=(*path, "$id"),
            message="nested schema resources are not supported",
            validator="$id",
        )
    for keyword in _REFERENCE_KEYWORDS:
        if keyword not in value:
            continue
        reference = value[keyword]
        if not isinstance(reference, str) or not reference.startswith("#"):
            _raise_policy_error(
                schema_kind,
                path=(*path, keyword),
                message="only document-local references beginning with '#' are allowed",
                validator=keyword,
            )


def _verify_local_references(schema: dict[str, object], *, schema_kind: SchemaKind) -> None:
    anchors: dict[str, tuple[PathComponent, ...]] = {}
    references: list[tuple[str, tuple[PathComponent, ...], tuple[PathComponent, ...], str]] = []
    stack: list[tuple[object, tuple[PathComponent, ...]]] = [(schema, ())]
    while stack:
        value, path = stack.pop()
        if isinstance(value, dict):
            for keyword in ("$anchor", "$dynamicAnchor"):
                anchor = value.get(keyword)
                if isinstance(anchor, str):
                    if not _ANCHOR_PATTERN.fullmatch(anchor):
                        _raise_policy_error(
                            schema_kind,
                            path=(*path, keyword),
                            message="anchor does not use the supported anchor syntax",
                            validator=keyword,
                        )
                    if anchor in anchors:
                        _raise_policy_error(
                            schema_kind,
                            path=(*path, keyword),
                            message="anchor names must be unique within a schema document",
                            validator=keyword,
                        )
                    anchors[anchor] = path
            for keyword in _REFERENCE_KEYWORDS:
                reference = value.get(keyword)
                if isinstance(reference, str):
                    references.append((reference, path, (*path, keyword), keyword))
            stack.extend((child, (*path, key)) for key, child in value.items())
        elif isinstance(value, list):
            stack.extend((child, (*path, index)) for index, child in enumerate(value))

    reference_edges: dict[tuple[PathComponent, ...], set[tuple[PathComponent, ...]]] = {}
    reference_paths: dict[tuple[PathComponent, ...], tuple[PathComponent, ...]] = {}
    for reference, source_path, keyword_path, keyword in references:
        try:
            target_path = _resolve_local_reference(schema, reference, anchors)
        except (KeyError, IndexError, ValueError) as error:
            _raise_policy_error(
                schema_kind,
                path=keyword_path,
                message="local reference does not resolve within this schema document",
                validator=keyword,
                cause=error,
            )
        target = _resolve_path(schema, target_path)
        if not isinstance(target, dict | bool):
            _raise_policy_error(
                schema_kind,
                path=keyword_path,
                message="local reference target must be a schema object or boolean",
                validator=keyword,
            )
        reference_edges.setdefault(source_path, set()).add(target_path)
        reference_paths[source_path] = keyword_path
    _reject_reference_cycles(
        reference_edges,
        reference_paths=reference_paths,
        schema_kind=schema_kind,
    )


def _resolve_local_reference(
    schema: dict[str, object],
    reference: str,
    anchors: Mapping[str, tuple[PathComponent, ...]],
) -> tuple[PathComponent, ...]:
    fragment = unquote(reference[1:])
    if not fragment:
        return ()
    if not fragment.startswith("/"):
        anchor_path = anchors[fragment]
        _resolve_path(schema, anchor_path)
        return anchor_path
    tokens = tuple(_decode_pointer_token(token) for token in fragment[1:].split("/"))
    return _resolve_pointer_path(schema, tokens)


def _decode_pointer_token(token: str) -> str:
    index = 0
    while index < len(token):
        if token[index] == "~":
            if index + 1 >= len(token) or token[index + 1] not in {"0", "1"}:
                raise ValueError("invalid JSON Pointer escape")
            index += 1
        index += 1
    return token.replace("~1", "/").replace("~0", "~")


def _resolve_path(root: object, path: Iterable[PathComponent]) -> object:
    current = root
    for component in path:
        if isinstance(current, dict) and isinstance(component, str):
            current = current[component]
        elif isinstance(current, list) and isinstance(component, str):
            if component == "-" or (component.startswith("0") and component != "0"):
                raise ValueError("invalid array index")
            current = current[int(component)]
        elif isinstance(current, list) and isinstance(component, int):
            current = current[component]
        else:
            raise KeyError(component)
    return current


def _resolve_pointer_path(root: object, tokens: Iterable[str]) -> tuple[PathComponent, ...]:
    current = root
    resolved: list[PathComponent] = []
    for token in tokens:
        if isinstance(current, dict):
            current = current[token]
            resolved.append(token)
        elif isinstance(current, list):
            if token == "-" or (token.startswith("0") and token != "0"):
                raise ValueError("invalid array index")
            index = int(token)
            current = current[index]
            resolved.append(index)
        else:
            raise KeyError(token)
    return tuple(resolved)


def _reject_reference_cycles(
    edges: Mapping[tuple[PathComponent, ...], set[tuple[PathComponent, ...]]],
    *,
    reference_paths: Mapping[tuple[PathComponent, ...], tuple[PathComponent, ...]],
    schema_kind: SchemaKind,
) -> None:
    visited: set[tuple[PathComponent, ...]] = set()
    active: set[tuple[PathComponent, ...]] = set()

    def visit(node: tuple[PathComponent, ...]) -> None:
        if node in active:
            _raise_policy_error(
                schema_kind,
                path=reference_paths[node],
                message="local reference graph contains a direct cycle",
                validator="$ref",
            )
        if node in visited:
            return
        active.add(node)
        for target in edges.get(node, set()):
            visit(target)
        active.remove(node)
        visited.add(node)

    for source in edges:
        visit(source)


def _runtime_validation_issues(
    validator: Draft202012Validator,
    instance: object,
) -> tuple[ValidationIssue, ...]:
    try:
        frozen = freeze_json(
            instance,
            max_depth=MAX_INSTANCE_DEPTH,
            max_nodes=MAX_INSTANCE_NODES,
        )
    except JSONValueError as json_error:
        validator_keyword = "maxNodes" if "node count" in json_error.reason else "json"
        message = (
            f"Value exceeds the {MAX_INSTANCE_NODES}-node limit."
            if validator_keyword == "maxNodes"
            else "Value must be bounded JSON data."
        )
        return (
            ValidationIssue(
                instance_path=(),
                schema_path=(),
                message=message,
                validator=validator_keyword,
            ),
        )

    normalized = thaw_json(frozen)
    errors: list[ValidationError] = []
    omitted = False
    try:
        for error in validator.iter_errors(normalized):
            if len(errors) == MAX_VALIDATION_ISSUES:
                omitted = True
                break
            errors.append(error)
    except (RecursionError, Unresolvable):
        return (
            ValidationIssue(
                instance_path=(),
                schema_path=(),
                message="Validation could not safely resolve the local schema graph.",
                validator="$ref",
            ),
        )

    issues = sorted((_issue_from_validation_error(error) for error in errors), key=_issue_sort_key)
    if omitted:
        issues.append(
            ValidationIssue(
                instance_path=(),
                schema_path=(),
                message="Additional validation failures were omitted.",
                validator="maxIssues",
            )
        )
    return tuple(issues)


def _issue_from_schema_error(error: SchemaError) -> ValidationIssue:
    return ValidationIssue(
        instance_path=_normalize_path(error.path),
        schema_path=_normalize_path(error.schema_path),
        message="Schema does not satisfy its Draft 2020-12 metaschema constraint.",
        validator=_normalize_validator(error.validator),
    )


def _issue_from_validation_error(error: ValidationError) -> ValidationIssue:
    validator = _normalize_validator(error.validator)
    constraint = validator or "schema"
    return ValidationIssue(
        instance_path=_normalize_path(error.path),
        schema_path=_normalize_path(error.schema_path),
        message=f"Value does not satisfy the {constraint!r} constraint.",
        validator=validator,
    )


def _normalize_path(path: Iterable[object]) -> tuple[PathComponent, ...]:
    return tuple(
        component if isinstance(component, str | int) else str(component) for component in path
    )


def _normalize_validator(validator: object) -> str | None:
    if validator is None:
        return None
    return str(validator)


def _issue_sort_key(issue: ValidationIssue) -> tuple[str, str, str]:
    return (repr(issue.instance_path), repr(issue.schema_path), issue.validator or "")


def _raise_policy_error(
    schema_kind: SchemaKind,
    *,
    path: tuple[PathComponent, ...],
    message: str,
    validator: str,
    cause: Exception | None = None,
) -> None:
    issue = ValidationIssue(
        instance_path=path,
        schema_path=(),
        message=message,
        validator=validator,
    )
    error = InvalidSchemaError(
        schema_kind=schema_kind,
        reason="schema violates the SentinelMCP reference or dialect policy",
        issues=(issue,),
    )
    if cause is None:
        raise error
    raise error from cause
