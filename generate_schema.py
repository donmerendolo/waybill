#!/usr/bin/env python3
"""
Generate a JSON Schema for the WaybillConfig YAML configuration format.

Introspects the dataclass definitions in src/types/config.py and writes
schema/config.schema.json, which IDE YAML extensions can reference for
validation and autocomplete.

Usage (from repo root):
    python generate_schema.py

VS Code — add to .vscode/settings.json:
    "yaml.schemas": {
        "./schema/config.schema.json": ["config.yaml", "config.*.yaml"]
    }
"""

from __future__ import annotations

import dataclasses
import json
import sys
import types as _types
import typing
from enum import Enum
from pathlib import Path
from typing import Any, Union, get_args, get_origin

# Ensure the repo root is on sys.path so src/ is importable without installing.
sys.path.insert(0, str(Path(__file__).parent))

from src.types.config import (
    ConfigGroup,
    ConfigMatcher,
    ConfigMember,
    ConfigMetadata,
    ConfigProfile,
    ConfigSpec,
    ConfigTransformer,
    ConfigValidator,
    WaybillConfig,
)

# Python snake_case field names that map to camelCase YAML keys.
YAML_KEY: dict[tuple[type, str], str] = {
    (ConfigMatcher, "case_sensitive"): "caseSensitive",
    (ConfigTransformer, "output_type"): "outputType",
    (ConfigTransformer, "logo_url"): "logoUrl",
    (ConfigTransformer, "tvg_id"): "tvgId",
    (ConfigProfile, "stream_profile"): "streamProfile",
    (ConfigGroup, "stream_profile"): "streamProfile",
    (ConfigMember, "stream_profile"): "streamProfile",
    (ConfigProfile, "order_streams_by"): "orderStreamsBy",
    (ConfigGroup, "order_streams_by"): "orderStreamsBy",
    (ConfigMember, "order_streams_by"): "orderStreamsBy",
    (ConfigProfile, "start_channel_number"): "startChannelNumber",
    (ConfigProfile, "keep_empty_channels"): "keepEmptyChannels",
    (ConfigMember, "channel_number"): "channelNumber",
    (ConfigMember, "stream_priorities"): "streamPriorities",
}

# Fields entirely omitted from the schema (internal implementation details).
OMIT_FIELDS: frozenset[tuple[type, str]] = frozenset(
    {
        # 'extra' is a catch-all for unknown transformer keys; represented via
        # additionalProperties on ConfigTransformer instead.
        (ConfigTransformer, "extra"),
    }
)

# Classes that forward unknown YAML keys into their 'extra' dict.
ALLOW_ADDITIONAL_PROPS: frozenset[type] = frozenset({ConfigTransformer})

# Hard-coded const values for specific fields.
CONST_OVERRIDES: dict[tuple[type, str], str] = {
    (WaybillConfig, "kind"): "WaybillConfig",
}

CLASS_DESCRIPTIONS: dict[type, str] = {
    WaybillConfig: "Top-level Waybill channel manifest",
    ConfigMetadata: "Manifest metadata",
    ConfigSpec: "Specification containing named profile definitions",
    ConfigProfile: (
        "A named profile grouping channel groups, with an optional default stream profile"
    ),
    ConfigGroup: "A channel group that maps to a Dispatcharr ChannelGroup",
    ConfigMember: "A channel member with sequential matcher, transformer, and validator rules",
    ConfigMatcher: (
        "A filter rule evaluated against each input stream; "
        "matchers run in order and can keep or drop streams"
    ),
    ConfigTransformer: (
        "A mutation rule applied to a stream field value or explicit metadata fields; "
        "transformers run in order after all matchers pass"
    ),
    ConfigValidator: (
        "A post-transformation assertion applied to transformed streams, or for count "
        "validators to assembled channels/member output; violations are surfaced as "
        "warnings or halt execution as failures"
    ),
}

FIELD_DESCRIPTIONS: dict[tuple[type, str], str] = {
    (WaybillConfig, "kind"): "Resource kind — must be 'WaybillConfig'",
    (WaybillConfig, "version"): "API version, e.g. v1alpha1",
    (WaybillConfig, "metadata"): "Manifest name and description",
    (WaybillConfig, "spec"): "Profile definitions",
    (ConfigMetadata, "name"): "Manifest name",
    (ConfigMetadata, "description"): "Optional human-readable description",
    (ConfigSpec, "profiles"): "Map of profile key → ConfigProfile",
    (ConfigProfile, "name"): "Human-readable profile name",
    (ConfigProfile, "stream_profile"): (
        "Default stream profile applied to every channel in this profile"
    ),
    (ConfigProfile, "order_streams_by"): (
        "Default stream ordering applied to every channel in this profile"
    ),
    (ConfigProfile, "groups"): "Map of group key \u2192 ConfigGroup",
    (ConfigProfile, "keep_empty_channels"): (
        "When true, a member that matches no streams still produces a channel (named "
        "after the member). Apply keeps it disabled in the profile with no streams and "
        "leaves its number, logo and EPG untouched, re-enabling it once streams match"
    ),
    (ConfigMember, "stream_priorities"): (
        "Map of stream id to integer priority (default 0). A key matches a stream whose "
        "stream hash equals it or whose URL contains it (e.g. an AceStream id); the "
        "first matching key in manifest order wins. Streams are ordered by priority, "
        "highest first, then by orderStreamsBy within the same priority. Keys that "
        "match nothing are reported in the plan"
    ),
    (ConfigMember, "channel_number"): (
        "Pins this member's first channel to this number; numbering of following "
        "channels continues from it. Use on every member for numbers that never shift"
    ),
    (ConfigProfile, "start_channel_number"): (
        "When set, channels are numbered consecutively from this value in manifest "
        "order (group, then member, then channel); existing channels are renumbered "
        "on apply. When omitted, new channels get the next free number and existing "
        "numbers are kept"
    ),
    (ConfigGroup, "name"): "Human-readable group name",
    (ConfigGroup, "stream_profile"): (
        "Stream profile override for this group (overrides profile-level setting)"
    ),
    (ConfigGroup, "order_streams_by"): (
        "Stream ordering override for this group (overrides profile-level setting)"
    ),
    (ConfigGroup, "members"): "Ordered list of channel members",
    (ConfigMember, "name"): "Member (channel) name",
    (ConfigMember, "stream_profile"): (
        "Stream profile override for this member's channels "
        "(overrides group- and profile-level settings)"
    ),
    (ConfigMember, "order_streams_by"): (
        "Stream ordering override for this member's channels "
        "(overrides group- and profile-level settings)"
    ),
    (ConfigMember, "matchers"): "Ordered matchers used to filter input streams",
    (ConfigMember, "transformers"): "Ordered transformers applied to matched streams",
    (ConfigMember, "validators"): (
        "Post-transformation validators that assert conditions on transformed streams, "
        "assembled channels, or member output depending on validator type and scope; "
        "violations are logged as warnings or raise a failure before any database write"
    ),
    (ConfigMatcher, "type"): "Matching algorithm",
    (ConfigMatcher, "action"): "Whether matched streams are kept or dropped",
    (
        ConfigMatcher,
        "pattern",
    ): "Regex pattern to test against the target field (type: regex)",
    (ConfigMatcher, "prefixes"): (
        "One or more prefixes; the stream matches if its field starts with any of them "
        "(type: hasPrefix)"
    ),
    (ConfigMatcher, "substrings"): (
        "One or more substrings; the stream matches if its field contains any of them "
        "(type: containsAny)"
    ),
    (ConfigMatcher, "values"): (
        "Exact values; the stream matches if its field equals any of them "
        "(type: exactMatch)"
    ),
    (ConfigMatcher, "case_sensitive"): "Whether string comparison is case-sensitive",
    (
        ConfigMatcher,
        "field",
    ): "Stream field to evaluate against (e.g. 'name', 'tvg_id')",
    (ConfigMatcher, "transformers"): (
        "Transformers applied to the field value before this matcher evaluates; "
        "useful for canonicalising the value before matching"
    ),
    (ConfigTransformer, "type"): "Transformation algorithm",
    (ConfigTransformer, "action"): "Sub-action for the transformer (e.g. 'replace')",
    (ConfigTransformer, "output_type"): (
        "Target format for cardinal numbers — 'number' (e.g. 1) or 'word' (e.g. ONE) "
        "(type: convertCardinalNumbers)"
    ),
    (ConfigTransformer, "pattern"): "Regex pattern to match (type: regex)",
    (ConfigTransformer, "replacement"): (
        "Replacement string; supports $1-style back-references (type: regex)"
    ),
    (ConfigTransformer, "prefix"): "Prefix to add or strip",
    (ConfigTransformer, "suffix"): "Suffix to add or strip",
    (ConfigTransformer, "value"): (
        "Static value to assign to the field (type: set). "
        "Use as a low-level escape hatch for unusual requirements"
    ),
    (ConfigTransformer, "name"): "Name value to assign (type: setMetadata)",
    (ConfigTransformer, "logo_url"): "Logo URL value to assign (type: setMetadata)",
    (ConfigTransformer, "tvg_id"): "TVG ID value to assign (type: setMetadata)",
    (ConfigTransformer, "field"): "Stream field to transform (e.g. 'name', 'tvg_id')",
    (ConfigValidator, "type"): "Validation algorithm",
    (ConfigValidator, "action"): (
        "Response when the assertion is violated — "
        "'warn' logs a warning; 'fail' logs an error and stops execution before any database write"
    ),
    (ConfigValidator, "operator"): (
        "Comparison operator applied to the stream count (type: count); "
        "one of: gt, gte, lt, lte, eq, neq"
    ),
    (
        ConfigValidator,
        "value",
    ): "Integer to compare the stream count against (type: count)",
    (ConfigValidator, "scope"): (
        "Validator scope override. count defaults to 'member': 'member' counts channels "
        "produced by the member after matching, transformation, and merging, while 'channel' "
        "counts streams inside each assembled channel. regexMatch and nonEmpty default to "
        "'stream' and may also use 'channel' to validate assembled channel fields after merging."
    ),
    (ConfigValidator, "pattern"): (
        "Regular expression the field value must match (type: regexMatch)"
    ),
    (ConfigValidator, "field"): (
        "Field to evaluate (type: regexMatch, nonEmpty); e.g. 'name', 'tvg_id', 'logo_url'. "
        "For channel-scoped validation, 'tvg_id' maps to the assembled channel's epg_id field."
    ),
}

EXPORT_CLASSES: list[type] = [
    WaybillConfig,
    ConfigMetadata,
    ConfigSpec,
    ConfigProfile,
    ConfigGroup,
    ConfigMember,
    ConfigMatcher,
    ConfigTransformer,
    ConfigValidator,
]


_UNION_ORIGINS = {Union}
if hasattr(_types, "UnionType"):
    _UNION_ORIGINS.add(_types.UnionType)  # type: ignore[attr-defined]


def _enum_values(enum_cls: type[Enum]) -> list[str]:
    return [e.value for e in enum_cls]


def _py_type_to_schema(py_type: Any) -> dict[str, Any]:
    """Recursively convert a Python type annotation to a JSON Schema fragment."""
    origin = get_origin(py_type)
    args = get_args(py_type)

    # Union / X | Y  (handles Optional[X] = Union[X, None])
    if origin in _UNION_ORIGINS:
        non_none = [a for a in args if a is not type(None)]
        has_none = len(non_none) < len(args)

        if len(non_none) == 1:
            inner = _py_type_to_schema(non_none[0])
            if has_none:
                # Preserve enum constraint via oneOf so null is a valid alternative
                # without collapsing the enum values.
                if "enum" in inner:
                    return {"oneOf": [inner, {"type": "null"}]}
                return (
                    {"type": [inner["type"], "null"]}
                    if "type" in inner
                    else {"oneOf": [inner, {"type": "null"}]}
                )
            return inner

        # Multiple non-None types — e.g. CardinalDirection | str
        enum_types = [
            a for a in non_none if isinstance(a, type) and issubclass(a, Enum)
        ]
        str_types = [a for a in non_none if a is str]
        if enum_types and str_types:
            # Represent as enum; the str branch is the unset/empty fallback.
            values: list[str] = []
            for et in enum_types:
                values.extend(_enum_values(et))
            return {"type": "string", "enum": values}

        return {"oneOf": [_py_type_to_schema(a) for a in non_none]}

    # list[X]
    if origin is list:
        item_type = args[0] if args else Any
        return {"type": "array", "items": _py_type_to_schema(item_type)}

    # dict[str, X]
    if origin is dict:
        val_type = args[1] if len(args) > 1 else Any
        if val_type is Any:
            return {"type": "object"}
        return {"type": "object", "additionalProperties": _py_type_to_schema(val_type)}

    # Enum
    if isinstance(py_type, type) and issubclass(py_type, Enum):
        return {"type": "string", "enum": _enum_values(py_type)}

    # Nested dataclass → $ref
    if isinstance(py_type, type) and dataclasses.is_dataclass(py_type):
        return {"$ref": f"#/$defs/{py_type.__name__}"}

    # Primitives
    if py_type is str:
        return {"type": "string"}
    if py_type is int:
        return {"type": "integer"}
    if py_type is float:
        return {"type": "number"}
    if py_type is bool:
        return {"type": "boolean"}

    # Any / object / unknown → unconstrained
    return {}


def _has_default(f: dataclasses.Field) -> bool:  # type: ignore[type-arg]
    return (
        f.default is not dataclasses.MISSING
        or f.default_factory is not dataclasses.MISSING  # type: ignore[misc]
    )


def _dataclass_to_schema(cls: type) -> dict[str, Any]:
    hints = typing.get_type_hints(cls)
    fields = dataclasses.fields(cls)  # type: ignore[arg-type]

    properties: dict[str, Any] = {}
    required: list[str] = []

    for f in fields:
        if (cls, f.name) in OMIT_FIELDS:
            continue

        yaml_key = YAML_KEY.get((cls, f.name), f.name)
        py_type = hints.get(f.name, Any)

        if (cls, f.name) in CONST_OVERRIDES:
            prop: dict[str, Any] = {
                "type": "string",
                "const": CONST_OVERRIDES[(cls, f.name)],
            }
        else:
            prop = _py_type_to_schema(py_type)

        if (cls, f.name) in FIELD_DESCRIPTIONS:
            prop["description"] = FIELD_DESCRIPTIONS[(cls, f.name)]

        properties[yaml_key] = prop

        if not _has_default(f):
            required.append(yaml_key)

    schema: dict[str, Any] = {"type": "object"}

    if cls in CLASS_DESCRIPTIONS:
        schema["description"] = CLASS_DESCRIPTIONS[cls]

    schema["properties"] = properties

    if required:
        schema["required"] = required

    schema["additionalProperties"] = cls in ALLOW_ADDITIONAL_PROPS

    return schema


# Entry point


def build_schema() -> dict[str, Any]:
    defs = {cls.__name__: _dataclass_to_schema(cls) for cls in EXPORT_CLASSES}

    # Promote the root class to the top level and attach $defs.
    root = defs.pop("WaybillConfig")
    root["$schema"] = "http://json-schema.org/draft-07/schema#"
    root["title"] = "WaybillConfig"
    root["$defs"] = defs

    return root


def main() -> None:
    schema = build_schema()
    output = Path("schema/config.schema.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(schema, indent=2) + "\n")
    print(f"Written: {output}")


if __name__ == "__main__":
    main()
