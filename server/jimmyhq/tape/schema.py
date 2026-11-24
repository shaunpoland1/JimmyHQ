"""A small, strict JSON Schema checker for event kinds that an app declares.

An app's kinds arrive as JSON Schema (``PUT /api/app/v1/kinds``), because the core never
imports or runs app code. This checks a schema once, when it is registered, and then
checks every payload written against it. Only the part of JSON Schema that a pydantic
model produces is understood; anything else is refused when the schema is registered,
so a payload is never waved through by a keyword that was not read.

Understood: ``type``, ``properties``, ``required``, ``additionalProperties``, ``items``,
``prefixItems``, ``enum``, ``const``, ``anyOf``, ``oneOf``, ``allOf``, ``$ref`` to
``#/$defs/<name>``, ``$defs``, ``minimum``, ``maximum``, ``exclusiveMinimum``,
``exclusiveMaximum``, ``minLength``, ``maxLength``, ``minItems``, ``maxItems``,
``uniqueItems``, and the notes ``title``, ``description``, ``default``, ``examples``,
``format``, ``discriminator``, ``deprecated``, ``readOnly``, ``writeOnly``.

``pattern`` is refused: a regular expression from an app could take the core a very long
time to run. Check text shapes in the app.
"""

from __future__ import annotations

import json
import math
from typing import Any

MAX_SCHEMA_BYTES = 64 * 1024
MAX_DEPTH = 24

_TYPES = ("object", "array", "string", "integer", "number", "boolean", "null")
_NOTES = frozenset({"title", "description", "default", "examples", "format", "discriminator",
                    "deprecated", "readOnly", "writeOnly", "$schema", "$comment"})
_RULES = frozenset({"type", "properties", "required", "additionalProperties", "items",
                    "prefixItems", "enum", "const", "anyOf", "oneOf", "allOf", "$ref", "$defs",
                    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "minLength",
                    "maxLength", "minItems", "maxItems", "uniqueItems"})


class SchemaError(ValueError):
    """The schema itself cannot be used. The message names where."""


class PayloadError(ValueError):
    """A payload does not fit its schema. The message names the field."""


def _at(path: str, key: str | int) -> str:
    return f"{path}.{key}" if path else str(key)


def _number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def check_schema(schema: Any) -> dict[str, Any]:
    """Refuse a schema that is too large, too deep, not an object at its root, or uses a
    keyword this checker does not understand. Returns it unchanged."""
    if not isinstance(schema, dict):
        raise SchemaError("A kind's schema must be a JSON object.")
    size = len(json.dumps(schema, ensure_ascii=False))
    if size > MAX_SCHEMA_BYTES:
        raise SchemaError(f"A kind's schema can be at most {MAX_SCHEMA_BYTES // 1024} KB, "
                          f"and this one is {size // 1024} KB.")
    if schema.get("type") != "object":
        raise SchemaError("A kind's schema must describe an object (\"type\": \"object\"), "
                          "as a pydantic model does.")
    defs = schema.get("$defs", {})
    if not isinstance(defs, dict):
        raise SchemaError("\"$defs\" must be an object of named schemas.")
    _walk(schema, "", defs, 0)
    return schema


def _walk(node: Any, path: str, defs: dict[str, Any], depth: int) -> None:
    where = path or "the schema's root"
    if depth > MAX_DEPTH:
        raise SchemaError(f"The schema is nested more than {MAX_DEPTH} levels deep at {where}.")
    if isinstance(node, bool):
        return  # true and false are schemas too
    if not isinstance(node, dict):
        raise SchemaError(f"{where} must be a schema object.")
    for key in node:
        if key == "pattern":
            raise SchemaError(f"{where} uses \"pattern\", which JimmyHQ does not check. "
                              "Check the text's shape in your app instead.")
        if key not in _RULES and key not in _NOTES:
            raise SchemaError(f"{where} uses \"{key}\", which JimmyHQ does not understand.")
    kind = node.get("type")
    kinds = kind if isinstance(kind, list) else [kind] if kind is not None else []
    for name in kinds:
        if name not in _TYPES:
            raise SchemaError(f"{where} has a \"type\" that is not one of: {', '.join(_TYPES)}.")
    ref = node.get("$ref")
    if ref is not None:
        if not isinstance(ref, str) or not ref.startswith("#/$defs/") \
                or ref.removeprefix("#/$defs/") not in defs:
            raise SchemaError(f"{where} refers to \"{ref}\", which is not one of the "
                              "schema's own \"$defs\".")
    for key in ("properties", "$defs"):
        sub = node.get(key)
        if sub is None:
            continue
        if not isinstance(sub, dict):
            raise SchemaError(f"\"{key}\" at {where} must be an object.")
        for name, child in sub.items():
            _walk(child, _at(path, name), defs, depth + 1)
    for key in ("items", "additionalProperties"):
        if key in node:
            _walk(node[key], _at(path, f"<{key}>"), defs, depth + 1)
    for key in ("anyOf", "oneOf", "allOf", "prefixItems"):
        if key in node:
            if not isinstance(node[key], list) or not node[key]:
                raise SchemaError(f"\"{key}\" at {where} must be a list of schemas.")
            for child in node[key]:
                _walk(child, path, defs, depth + 1)
    required = node.get("required")
    if required is not None and (not isinstance(required, list)
                                 or not all(isinstance(r, str) for r in required)):
        raise SchemaError(f"\"required\" at {where} must be a list of names.")
    if "enum" in node and not isinstance(node["enum"], list):
        raise SchemaError(f"\"enum\" at {where} must be a list.")
    for key in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"):
        if key in node and not _number(node[key]):
            raise SchemaError(f"\"{key}\" at {where} must be a number.")
    for key in ("minLength", "maxLength", "minItems", "maxItems"):
        if key in node and (not isinstance(node[key], int) or isinstance(node[key], bool)
                            or node[key] < 0):
            raise SchemaError(f"\"{key}\" at {where} must be a whole number of 0 or more.")


def _type_fits(value: Any, name: str) -> bool:
    if name == "object":
        return isinstance(value, dict)
    if name == "array":
        return isinstance(value, list)
    if name == "string":
        return isinstance(value, str)
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return _number(value) and math.isfinite(value)
    if name == "boolean":
        return isinstance(value, bool)
    return value is None  # "null"


def _same(a: Any, b: Any) -> bool:
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True) \
        and isinstance(a, bool) == isinstance(b, bool)


def validate(schema: dict[str, Any], value: Any) -> None:
    """Raise ``PayloadError`` naming the first field that does not fit. ``schema`` must
    have passed ``check_schema``."""
    _fit(schema, value, "", schema.get("$defs", {}), 0)


def fits(schema: dict[str, Any], value: Any) -> bool:
    try:
        validate(schema, value)
    except PayloadError:
        return False
    return True


def _fit(node: Any, value: Any, path: str, defs: dict[str, Any], depth: int) -> None:
    where = f"\"{path}\"" if path else "The payload"
    if depth > MAX_DEPTH * 2:
        raise PayloadError(f"{where} is nested too deeply.")
    if node is True:
        return
    if node is False:
        raise PayloadError(f"{where} is not allowed here.")
    if "$ref" in node:
        _fit(defs[node["$ref"].removeprefix("#/$defs/")], value, path, defs, depth + 1)
    for child in node.get("allOf", ()):
        _fit(child, value, path, defs, depth + 1)
    for key in ("anyOf", "oneOf"):
        if key in node:
            matched = sum(1 for child in node[key] if _quiet(child, value, path, defs, depth))
            if matched == 0:
                # "X or nothing", as a model writes an optional field: say what is wrong
                # with the X.
                real = [c for c in node[key] if c != {"type": "null"}]
                if value is not None and len(real) == 1:
                    _fit(real[0], value, path, defs, depth + 1)
                raise PayloadError(f"{where} does not match any of the shapes it may take.")
            if key == "oneOf" and matched > 1:
                raise PayloadError(f"{where} matches more than one of the shapes it may take.")
    kind = node.get("type")
    if kind is not None:
        kinds = kind if isinstance(kind, list) else [kind]
        if not any(_type_fits(value, k) for k in kinds):
            raise PayloadError(f"{where} must be {' or '.join(kinds)}.")
    if "const" in node and not _same(node["const"], value):
        raise PayloadError(f"{where} must be exactly {json.dumps(node['const'])}.")
    if "enum" in node and not any(_same(option, value) for option in node["enum"]):
        raise PayloadError(f"{where} must be one of the values its schema lists.")
    if _number(value):
        if "minimum" in node and value < node["minimum"]:
            raise PayloadError(f"{where} must be at least {node['minimum']}.")
        if "maximum" in node and value > node["maximum"]:
            raise PayloadError(f"{where} must be at most {node['maximum']}.")
        if "exclusiveMinimum" in node and value <= node["exclusiveMinimum"]:
            raise PayloadError(f"{where} must be more than {node['exclusiveMinimum']}.")
        if "exclusiveMaximum" in node and value >= node["exclusiveMaximum"]:
            raise PayloadError(f"{where} must be less than {node['exclusiveMaximum']}.")
    if isinstance(value, str):
        if len(value) < node.get("minLength", 0):
            raise PayloadError(f"{where} must be at least {node['minLength']} characters.")
        if "maxLength" in node and len(value) > node["maxLength"]:
            raise PayloadError(f"{where} must be at most {node['maxLength']} characters.")
    if isinstance(value, list):
        _fit_list(node, value, path, defs, depth)
    if isinstance(value, dict):
        _fit_object(node, value, path, defs, depth)


def _quiet(node: Any, value: Any, path: str, defs: dict[str, Any], depth: int) -> bool:
    try:
        _fit(node, value, path, defs, depth + 1)
    except PayloadError:
        return False
    return True


def _fit_list(node: dict[str, Any], value: list[Any], path: str, defs: dict[str, Any],
              depth: int) -> None:
    where = f"\"{path}\"" if path else "The payload"
    if len(value) < node.get("minItems", 0):
        raise PayloadError(f"{where} must have at least {node['minItems']} items.")
    if "maxItems" in node and len(value) > node["maxItems"]:
        raise PayloadError(f"{where} must have at most {node['maxItems']} items.")
    if node.get("uniqueItems"):
        seen = [json.dumps(item, sort_keys=True) for item in value]
        if len(set(seen)) != len(seen):
            raise PayloadError(f"{where} must not hold the same item twice.")
    prefix = node.get("prefixItems", [])
    for i, item in enumerate(value):
        if i < len(prefix):
            _fit(prefix[i], item, _at(path, i), defs, depth + 1)
        elif "items" in node:
            _fit(node["items"], item, _at(path, i), defs, depth + 1)


def _fit_object(node: dict[str, Any], value: dict[str, Any], path: str,
                defs: dict[str, Any], depth: int) -> None:
    props = node.get("properties", {})
    for name in node.get("required", ()):
        if name not in value:
            raise PayloadError(f"\"{_at(path, name)}\" is missing.")
    extra = node.get("additionalProperties", True)
    for name, item in value.items():
        if not isinstance(name, str):
            raise PayloadError(f"{path or 'The payload'} has a name that is not text.")
        if name in props:
            _fit(props[name], item, _at(path, name), defs, depth + 1)
        elif extra is False:
            raise PayloadError(f"\"{_at(path, name)}\" is not a field of this kind.")
        elif isinstance(extra, dict):
            _fit(extra, item, _at(path, name), defs, depth + 1)
