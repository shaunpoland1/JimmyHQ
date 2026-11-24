"""Schema-backed kinds: the JSON Schema checker an app's kinds are held to, and how the
registry keeps them."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ConfigDict

from jimmyhq.tape import registry
from jimmyhq.tape import schema as js


class Inner(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str
    count: int = 0


class Drafted(BaseModel):
    model_config = ConfigDict(extra="forbid")
    post: int
    title: str | None = None
    tags: list[str] = []
    inner: Inner | None = None
    score: float = 0.5
    done: bool = False


SCHEMA = Drafted.model_json_schema()


def test_a_pydantic_models_schema_is_understood_whole():
    assert js.check_schema(SCHEMA) is SCHEMA


@pytest.mark.parametrize("payload", [
    {"post": 1},
    {"post": 1, "title": None, "tags": ["a"], "inner": {"label": "x"}, "score": 1, "done": True},
])
def test_a_payload_the_model_accepts_fits_its_schema(payload):
    Drafted.model_validate(payload)
    js.validate(SCHEMA, payload)


@pytest.mark.parametrize("payload, word", [
    ({}, "post"),
    ({"post": "1"}, "post"),
    ({"post": True}, "post"),
    ({"post": 1, "surprise": 1}, "surprise"),
    ({"post": 1, "inner": {"label": "x", "extra": 1}}, "inner.extra"),
    ({"post": 1, "tags": [1]}, "tags.0"),
    ({"post": 1, "score": float("nan")}, "score"),
])
def test_a_payload_that_does_not_fit_is_refused_naming_the_field(payload, word):
    with pytest.raises(js.PayloadError) as refused:
        js.validate(SCHEMA, payload)
    assert word in str(refused.value)


@pytest.mark.parametrize("schema", [
    {"type": "array"},
    {"type": "object", "properties": {"x": {"type": "string", "pattern": "(a+)+$"}}},
    {"type": "object", "if": {}, "then": {}},
    {"type": "object", "properties": {"x": {"$ref": "https://example.com/s.json"}}},
    {"type": "object", "properties": {"x": {"$ref": "#/$defs/Missing"}}},
    {"type": "object", "properties": {"x": {"type": "texty"}}},
])
def test_a_schema_with_anything_the_checker_does_not_read_is_refused(schema):
    with pytest.raises(js.SchemaError):
        js.check_schema(schema)


def test_a_schema_nested_too_deep_is_refused():
    deep: dict = {"type": "object"}
    node = deep
    for _ in range(js.MAX_DEPTH + 2):
        node["properties"] = {"x": {"type": "object"}}
        node = node["properties"]["x"]
    with pytest.raises(js.SchemaError):
        js.check_schema(deep)


def test_an_apps_kind_is_registered_from_its_schema_and_never_changes():
    name = "app.schematest.noted"
    assert registry.register_schema(name, 1, schema=SCHEMA, example={"post": 1},
                                    owner="schematest")
    assert not registry.register_schema(name, 1, schema=SCHEMA, example={"post": 1},
                                        owner="schematest")
    with pytest.raises(ValueError):
        registry.register_schema(name, 1, schema={"type": "object"}, example={},
                                 owner="schematest")
    assert registry.validate_payload(name, 1, {"post": 2}) == {"post": 2}


def test_an_apps_kind_can_only_be_registered_under_its_own_prefix():
    for name, owner in (("app.other.noted", "schematest"), ("task.filed", "schematest")):
        with pytest.raises(ValueError):
            registry.register_schema(name, 1, schema=SCHEMA, example={"post": 1},
                                     owner=owner)
    with pytest.raises(ValueError):
        registry.register_schema("app.core.x", 1, schema=SCHEMA, example={"post": 1},
                                 owner="core")


def test_an_older_version_of_an_apps_kind_reads_back_as_it_was_written():
    name = "app.schematest.moved"
    registry.register_schema(name, 1, schema=SCHEMA, example={"post": 1}, owner="schematest")
    wider = {"type": "object", "properties": {"post": {"type": "string"}}}
    registry.register_schema(name, 2, schema=wider, example={"post": "1"}, owner="schematest")
    assert registry.upcast(name, 1, {"post": 1}) == (1, {"post": 1})


def test_an_apps_kinds_are_left_out_of_the_web_apps_types():
    registry.register_schema("app.schematest.typed", 1, schema=SCHEMA, example={"post": 1},
                             owner="schematest")
    assert "app.schematest.typed" not in registry.json_schemas()
