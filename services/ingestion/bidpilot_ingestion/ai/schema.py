"""Structured-output enforcement (SPEC §17.4: "JSON-schema enforcement with auto-retry").

A deliberately small JSON Schema subset - the keywords the prompt registry actually uses - rather
than a dependency. Two reasons, and the second is the real one:

  * The schemas here are ours, written alongside the prompts, so the subset is known and closed.
  * The *error message* is the retry. A generic validator reports "does not match schema"; what
    changes a model's second attempt is being told which field, and how. So the messages are
    written to be read by the model, not only by an operator.

Anything a schema needs that is not implemented raises rather than passing silently, because a
keyword that is quietly ignored is a contract that is quietly not enforced.
"""

from __future__ import annotations

import json
import re
from typing import Any

_SUPPORTED = {
    "type",
    "properties",
    "required",
    "items",
    "enum",
    "additionalProperties",
    "minimum",
    "maximum",
    "minItems",
    "maxItems",
    "minLength",
    "maxLength",
    "pattern",
    "description",
    "title",
    "$schema",
    "nullable",
}

_TYPES: dict[str, type | tuple[type, ...]] = {
    "object": dict,
    "array": list,
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
}


class SchemaViolation(ValueError):
    """The model's output does not satisfy the prompt's schema."""


def validate_against(schema: dict[str, Any], raw: str) -> dict[str, Any]:
    """Parse `raw` as JSON and check it against `schema`, returning the parsed object."""
    data = _parse(raw)
    _check(schema, data, path="$")
    if not isinstance(data, dict):
        raise SchemaViolation("top-level value must be a JSON object")
    return data


def _parse(raw: str) -> Any:
    """Parse JSON, tolerating the fence a model wraps it in but nothing more.

    Stripping ```json is worth doing because it is the single most common way a correct answer
    arrives unusable. Repairing malformed JSON is not: a guessed comma is a guessed fact, and the
    retry path exists precisely so that broken output can be rejected rather than patched.
    """
    text = raw.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, flags=re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise SchemaViolation(f"output is not valid JSON ({exc.msg} at line {exc.lineno})") from exc


def _check(schema: dict[str, Any], value: Any, *, path: str) -> None:
    unsupported = set(schema) - _SUPPORTED
    if unsupported:
        raise NotImplementedError(
            f"schema at {path} uses unsupported keywords {sorted(unsupported)}; "
            "implement them in ai/schema.py rather than letting them be ignored"
        )

    expected = schema.get("type")
    if expected:
        if value is None and schema.get("nullable"):
            return
        python_type = _TYPES.get(expected)
        if python_type is None:
            raise NotImplementedError(f"unsupported schema type {expected!r} at {path}")
        # bool is a subclass of int in Python; a boolean where a number belongs is still wrong.
        if expected in ("number", "integer") and isinstance(value, bool):
            raise SchemaViolation(f"{path} must be {expected}, got boolean")
        if not isinstance(value, python_type):
            raise SchemaViolation(f"{path} must be {expected}, got {_describe(value)}")

    if "enum" in schema and value not in schema["enum"]:
        raise SchemaViolation(f"{path} must be one of {schema['enum']}, got {value!r}")

    if isinstance(value, str):
        _check_string(schema, value, path=path)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise SchemaViolation(f"{path} must be >= {schema['minimum']}, got {value}")
        if "maximum" in schema and value > schema["maximum"]:
            raise SchemaViolation(f"{path} must be <= {schema['maximum']}, got {value}")
    elif isinstance(value, list):
        _check_array(schema, value, path=path)
    elif isinstance(value, dict):
        _check_object(schema, value, path=path)


def _check_string(schema: dict[str, Any], value: str, *, path: str) -> None:
    if "minLength" in schema and len(value) < schema["minLength"]:
        raise SchemaViolation(f"{path} must be at least {schema['minLength']} characters, got {len(value)}")
    if "maxLength" in schema and len(value) > schema["maxLength"]:
        raise SchemaViolation(f"{path} must be at most {schema['maxLength']} characters, got {len(value)}")
    pattern = schema.get("pattern")
    if pattern and not re.search(pattern, value):
        raise SchemaViolation(f"{path} must match {pattern!r}, got {value!r}")


def _check_array(schema: dict[str, Any], value: list[Any], *, path: str) -> None:
    if "minItems" in schema and len(value) < schema["minItems"]:
        raise SchemaViolation(f"{path} must have at least {schema['minItems']} items")
    if "maxItems" in schema and len(value) > schema["maxItems"]:
        raise SchemaViolation(f"{path} must have at most {schema['maxItems']} items")
    item_schema = schema.get("items")
    if item_schema:
        for index, item in enumerate(value):
            _check(item_schema, item, path=f"{path}[{index}]")


def _check_object(schema: dict[str, Any], value: dict[str, Any], *, path: str) -> None:
    properties = schema.get("properties", {})
    for name in schema.get("required", []):
        if name not in value:
            raise SchemaViolation(f"{path} is missing required field {name!r}")
    if schema.get("additionalProperties") is False:
        extra = sorted(set(value) - set(properties))
        if extra:
            raise SchemaViolation(f"{path} has fields not in the schema: {extra}")
    for name, sub_schema in properties.items():
        if name in value:
            _check(sub_schema, value[name], path=f"{path}.{name}")


def _describe(value: Any) -> str:
    if value is None:
        return "null"
    return {dict: "object", list: "array", str: "string", bool: "boolean"}.get(
        type(value), type(value).__name__
    )
