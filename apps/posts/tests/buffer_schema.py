"""A small check that a query Posts sends to Buffer uses only what Buffer's documentation
names: the operations, their arguments, the input fields, the enum values and the fields
asked for. The stand-in for Buffer in the tests runs every query through ``check``, so a
misspelled field fails a test instead of being answered by a canned reply.

This is not a GraphQL implementation: it reads the small part of the language Posts writes
(one operation, fields with aliases and arguments, inline fragments, string, number, enum,
list and object values) and knows only the types below. No dependency is added for it.

Every name below is copied from developers.buffer.com, read for this check:
reference (the Query and Mutation fields), types/Post, PostInput, PostsInput,
PostsFiltersInput, DateTimeComparator, PostSortInput, PostSortableKey, SortDirection,
PostStatus, PostsResults, PostsEdge, PaginationPageInfo, PostMetric, PostPublishingError,
CreatePostInput, EditPostInput, DeletePostInput, ShareMode, SchedulingType,
PostActionPayload, PostActionSuccess, DeletePostPayload, DeletePostSuccess, MutationError,
Account, Organization, Channel, ChannelsInput. The error types implementing MutationError
are those guides/error-handling and the PostActionPayload and DeletePostPayload unions
name (the MutationError page itself lists none).
"""

from __future__ import annotations

import json
import re
from typing import Any

# --- the schema, as far as Posts uses it ---------------------------------------------------

# Output types: field -> (type, {argument: input type}). A type in SCALARS or ENUMS is a leaf.
OBJECTS: dict[str, dict[str, tuple[str, dict[str, str]]]] = {
    "Query": {
        "post": ("Post", {"input": "PostInput"}),
        "posts": ("PostsResults", {"first": "Int", "after": "String", "input": "PostsInput"}),
        "account": ("Account", {}),
        "channels": ("Channel", {"input": "ChannelsInput"}),
    },
    "Mutation": {
        "createPost": ("PostActionPayload", {"input": "CreatePostInput"}),
        "editPost": ("PostActionPayload", {"input": "EditPostInput"}),
        "deletePost": ("DeletePostPayload", {"input": "DeletePostInput"}),
    },
    "Post": {name: (kind, {}) for name, kind in {
        "id": "PostId", "allowedActions": "String", "channelId": "ChannelId",
        "channelService": "Service", "dueAt": "DateTime", "error": "PostPublishingError",
        "externalLink": "String", "ideaId": "String", "isCustomScheduled": "Boolean",
        "metrics": "PostMetric", "metricsUpdatedAt": "DateTime", "schedulingType":
        "SchedulingType", "sentAt": "DateTime", "sharedNow": "Boolean", "shareMode":
        "ShareMode", "status": "PostStatus", "text": "String", "createdAt": "DateTime",
        "updatedAt": "DateTime"}.items()},
    "PostPublishingError": {"message": ("String", {}), "rawError": ("String", {}),
                            "supportUrl": ("String", {})},
    "PostMetric": {"description": ("String", {}), "name": ("String", {}),
                   "type": ("String", {}), "unit": ("String", {}), "value": ("Float", {})},
    "PostsResults": {"edges": ("PostsEdge", {}), "pageInfo": ("PaginationPageInfo", {})},
    "PostsEdge": {"cursor": ("String", {}), "node": ("Post", {})},
    "PaginationPageInfo": {"endCursor": ("String", {}), "hasNextPage": ("Boolean", {}),
                           "hasPreviousPage": ("Boolean", {}),
                           "startCursor": ("String", {})},
    "PostActionSuccess": {"post": ("Post", {})},
    "DeletePostSuccess": {"id": ("PostId", {})},
    "MutationError": {"message": ("String", {})},
    "Account": {"id": ("ID", {}), "name": ("String", {}), "timezone": ("String", {}),
                "organizations": ("Organization", {})},
    "Organization": {"id": ("OrganizationId", {}), "name": ("String", {}),
                     "channelCount": ("Int", {})},
    "Channel": {name: (kind, {}) for name, kind in {
        "id": "ChannelId", "name": "String", "displayName": "String", "service": "Service",
        "organizationId": "OrganizationId", "isDisconnected": "Boolean",
        "timezone": "String"}.items()},
}
ERRORS = ("NotFoundError", "UnauthorizedError", "UnexpectedError", "RestProxyError",
          "LimitReachedError", "InvalidInputError", "VoidMutationError")
for _error in ERRORS:
    OBJECTS[_error] = {"message": ("String", {})}
UNIONS = {
    "PostActionPayload": {"PostActionSuccess", "NotFoundError", "UnauthorizedError",
                          "UnexpectedError", "RestProxyError", "LimitReachedError",
                          "InvalidInputError", "MutationError"},
    "DeletePostPayload": {"DeletePostSuccess", "VoidMutationError", "MutationError"},
}

# Input types: field -> type. "[X]" is a list of X.
INPUTS: dict[str, dict[str, str]] = {
    "PostInput": {"id": "PostId"},
    "PostsInput": {"organizationId": "OrganizationId", "filter": "PostsFiltersInput",
                   "sort": "[PostSortInput]"},
    "PostsFiltersInput": {"channelIds": "[ChannelId]", "dueAt": "DateTimeComparator",
                          "dueAtPresence": "String", "endDate": "DateTime",
                          "startDate": "DateTime", "status": "[PostStatus]",
                          "tagIds": "[String]", "createdAt": "DateTimeComparator"},
    "DateTimeComparator": {"start": "DateTime", "end": "DateTime"},
    "PostSortInput": {"field": "PostSortableKey", "direction": "SortDirection"},
    "ChannelsInput": {"organizationId": "OrganizationId"},
    "CreatePostInput": {"aiAssisted": "Boolean", "assets": "[AssetInput]",
                        "channelId": "ChannelId", "draftId": "String", "dueAt": "DateTime",
                        "ideaId": "String", "mode": "ShareMode", "needsApproval": "Boolean",
                        "saveToDraft": "Boolean", "schedulingType": "SchedulingType",
                        "source": "String", "tagIds": "[String]", "text": "String"},
    "EditPostInput": {"id": "PostId", "aiAssisted": "Boolean", "assets": "[AssetInput]",
                      "draftId": "String", "dueAt": "DateTime", "ideaId": "String",
                      "mode": "ShareMode", "saveToDraft": "Boolean",
                      "schedulingType": "SchedulingType", "source": "String",
                      "tagIds": "[String]", "text": "String"},
    "DeletePostInput": {"id": "PostId"},
    "AssetInput": {},  # Posts sends text only: an empty list, never an asset
}
ENUMS = {
    "ShareMode": {"addToQueue", "customScheduled", "shareNext", "shareNow"},
    "SchedulingType": {"automatic", "notification"},
    "PostStatus": {"draft", "error", "needs_approval", "scheduled", "sending", "sent"},
    "PostSortableKey": {"dueAt", "createdAt"},
    "SortDirection": {"asc", "desc"},
}
STRINGS = {"String", "ID", "PostId", "ChannelId", "OrganizationId", "DateTime"}
SCALARS = STRINGS | {"Int", "Float", "Boolean", "Service"}


class SchemaError(AssertionError):
    """A query used something Buffer's documentation does not name."""


# --- reading a query ------------------------------------------------------------------------

_TOKEN = re.compile(r'\s*(?:(\.\.\.)|([{}()\[\]:!])|("(?:[^"\\]|\\.)*")|(-?\d+(?:\.\d+)?)'
                    r"|([_A-Za-z][_0-9A-Za-z]*)|(,))")


def tokens(query: str) -> list[tuple[str, str]]:
    out, at = [], 0
    while at < len(query):
        if query[at:].strip() == "":
            break
        found = _TOKEN.match(query, at)
        if not found:
            raise SchemaError(f"cannot read the query at {query[at:at + 30]!r}")
        at = found.end()
        spread, punct, string, number, name, comma = found.groups()
        if comma:
            continue  # commas mean nothing in GraphQL
        kind = ("spread" if spread else "punct" if punct else "string" if string
                else "number" if number else "name")
        out.append((kind, spread or punct or string or number or name))
    return out


class Reader:
    def __init__(self, query: str):
        self.items = tokens(query)
        self.at = 0

    def peek(self) -> tuple[str, str]:
        return self.items[self.at] if self.at < len(self.items) else ("end", "")

    def take(self, value: str | None = None) -> tuple[str, str]:
        item = self.peek()
        if value is not None and item[1] != value:
            raise SchemaError(f"expected {value!r}, found {item[1]!r}")
        self.at += 1
        return item

    def selections(self) -> list[tuple]:
        self.take("{")
        out = []
        while self.peek()[1] != "}":
            if self.peek()[0] == "spread":
                self.take()
                self.take("on")
                kind = self.take()[1]
                out.append(("on", kind, self.selections()))
                continue
            name = self.take()[1]
            if self.peek()[1] == ":":
                self.take(":")
                name = self.take()[1]  # the alias is the response key; the field follows
            args = self.arguments() if self.peek()[1] == "(" else {}
            inner = self.selections() if self.peek()[1] == "{" else None
            out.append(("field", name, args, inner))
        self.take("}")
        return out

    def arguments(self) -> dict[str, Any]:
        self.take("(")
        out = {}
        while self.peek()[1] != ")":
            name = self.take()[1]
            self.take(":")
            out[name] = self.value()
        self.take(")")
        return out

    def value(self) -> tuple:
        kind, text = self.peek()
        if text == "[":
            self.take("[")
            items = []
            while self.peek()[1] != "]":
                items.append(self.value())
            self.take("]")
            return ("list", items)
        if text == "{":
            self.take("{")
            fields = {}
            while self.peek()[1] != "}":
                name = self.take()[1]
                self.take(":")
                fields[name] = self.value()
            self.take("}")
            return ("object", fields)
        self.take()
        if kind == "string":
            json.loads(text)  # a string Posts writes must be a valid literal
            return ("string", text)
        if kind == "number":
            return ("number", text)
        if text in ("true", "false"):
            return ("bool", text)
        if text == "null":
            return ("null", text)
        return ("enum", text)


# --- checking it -----------------------------------------------------------------------------


def check(query: str) -> None:
    """Raise ``SchemaError`` unless every name in ``query`` is one the table holds."""
    reader = Reader(query)
    operation = reader.take()[1]
    if operation not in ("query", "mutation"):
        raise SchemaError(f"an operation is a query or a mutation, not {operation!r}")
    root = "Query" if operation == "query" else "Mutation"
    _check_selections(root, reader.selections(), root)
    if reader.peek()[0] != "end":
        raise SchemaError("the query goes on after its operation")


def _check_selections(kind: str, selections: list[tuple], where: str) -> None:
    for selection in selections:
        if selection[0] == "on":
            _, on, inner = selection
            if kind not in UNIONS or on not in UNIONS[kind]:
                raise SchemaError(f"{where}: {on} is not a type of {kind}")
            _check_selections(on, inner, f"{where} on {on}")
            continue
        _, name, args, inner = selection
        if name == "__typename":
            continue
        if kind in UNIONS:
            raise SchemaError(f"{where}: {kind} is a union; ask for {name!r} inside a fragment")
        fields = OBJECTS.get(kind, {})
        if name not in fields:
            raise SchemaError(f"{where}: {kind} has no field {name!r}")
        result, takes = fields[name]
        for arg, value in args.items():
            if arg not in takes:
                raise SchemaError(f"{where}.{name}: there is no argument {arg!r}")
            _check_value(takes[arg], value, f"{where}.{name}({arg})")
        leaf = result in SCALARS or result in ENUMS
        if leaf and inner is not None:
            raise SchemaError(f"{where}.{name} is a {result}: it has no fields to ask for")
        if not leaf and inner is None:
            raise SchemaError(f"{where}.{name} is a {result}: say which of its fields")
        if inner is not None:
            _check_selections(result, inner, f"{where}.{name}")


def _check_value(kind: str, value: tuple, where: str) -> None:
    if kind.startswith("["):
        inner = kind[1:-1]
        items = value[1] if value[0] == "list" else [value]  # one value may stand for a list
        for item in items:
            _check_value(inner, item, where)
        return
    if kind in INPUTS:
        if value[0] != "object":
            raise SchemaError(f"{where}: {kind} is an object of fields")
        for field, inner in value[1].items():
            if field not in INPUTS[kind]:
                raise SchemaError(f"{where}: {kind} has no field {field!r}")
            _check_value(INPUTS[kind][field], inner, f"{where}.{field}")
        return
    if kind in ENUMS:
        if value[0] != "enum" or value[1] not in ENUMS[kind]:
            raise SchemaError(f"{where}: {value[1]!r} is not a {kind}")
        return
    wanted = {"Int": "number", "Float": "number", "Boolean": "bool"}.get(kind, "string")
    if kind not in SCALARS and kind not in STRINGS:
        raise SchemaError(f"{where}: the table does not know the type {kind}")
    if value[0] not in (wanted, "null"):
        raise SchemaError(f"{where}: a {kind} is written as a {wanted}, not {value[1]!r}")
