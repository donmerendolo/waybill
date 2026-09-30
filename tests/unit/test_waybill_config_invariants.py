from __future__ import annotations

import pytest

from src.types.config import WaybillConfig


def _valid_config_payload() -> dict[str, object]:
    return {
        "kind": "WaybillConfig",
        "version": "v1alpha1",
        "metadata": {"name": "demo"},
        "spec": {"profiles": {}},
    }


def test_waybill_config_accepts_supported_kind_and_version() -> None:
    cfg = WaybillConfig(**_valid_config_payload())
    assert cfg.kind == "WaybillConfig"
    assert cfg.version == "v1alpha1"


def test_waybill_config_rejects_unsupported_kind() -> None:
    payload = _valid_config_payload()
    payload["kind"] = "OtherConfig"

    with pytest.raises(ValueError, match="Unsupported config kind"):
        WaybillConfig(**payload)


def test_waybill_config_rejects_unsupported_version() -> None:
    payload = _valid_config_payload()
    payload["version"] = "v2"

    with pytest.raises(ValueError, match="Unsupported config version"):
        WaybillConfig(**payload)


def test_waybill_config_requires_non_empty_metadata_name() -> None:
    payload = _valid_config_payload()
    payload["metadata"] = {"name": "   "}

    with pytest.raises(ValueError, match="metadata.name must be a non-empty string"):
        WaybillConfig(**payload)


def _load(manifest: str) -> WaybillConfig:
    import yaml

    return WaybillConfig(**yaml.safe_load(manifest))


_HEAD = """
kind: WaybillConfig
version: v1alpha1
metadata:
  name: demo
"""


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("spec:\n", "spec is empty"),
        ("spec:\n  profiles:\n    main:\n", "profile 'main' is empty"),
        (
            "spec:\n  profiles:\n    main:\n      groups:\n        futbol:\n",
            "group 'futbol' is empty",
        ),
        (
            "spec:\n  profiles:\n    main:\n      groups:\n        g:\n"
            "          name: G\n          members:\n            -\n",
            "member #1 of group 'G' is empty",
        ),
        (
            "spec:\n  profiles:\n    main:\n      groups:\n        g:\n"
            "          name: G\n          members:\n            - name: A\n"
            "              matchers:\n                -\n",
            "matcher #1 of member 'A' is empty",
        ),
        (
            "spec:\n  profiles:\n    main:\n      groups:\n        g: just-a-string\n",
            "group 'g' must be a mapping of fields, got str",
        ),
    ],
)
def test_empty_manifest_sections_raise_named_errors(body: str, expected: str) -> None:
    with pytest.raises(ValueError, match=expected):
        _load(_HEAD + body)


def test_empty_metadata_raises_named_error() -> None:
    with pytest.raises(ValueError, match="metadata is empty"):
        _load("kind: WaybillConfig\nversion: v1alpha1\nmetadata:\nspec: {}\n")


@pytest.mark.parametrize("value", [0, -1, 1.5, "abc", True, [1]])
def test_start_channel_number_rejects_invalid_values(value: object) -> None:
    payload = _valid_config_payload()
    payload["spec"] = {"profiles": {"todo": {"startChannelNumber": value}}}

    with pytest.raises(
        ValueError, match="startChannelNumber must be a positive integer"
    ):
        WaybillConfig(**payload)


@pytest.mark.parametrize(
    ("value", "expected"), [(1, 1), (100, 100), ("7", 7), (2.0, 2)]
)
def test_start_channel_number_accepts_positive_integers(
    value: object, expected: int
) -> None:
    payload = _valid_config_payload()
    payload["spec"] = {"profiles": {"todo": {"startChannelNumber": value}}}

    cfg = WaybillConfig(**payload)

    assert cfg.spec.profiles["todo"].start_channel_number == expected


@pytest.mark.parametrize("value", [0, -3, 2.5, "x", False])
def test_member_channel_number_rejects_invalid_values(value: object) -> None:
    payload = _valid_config_payload()
    payload["spec"] = {
        "profiles": {
            "p": {
                "groups": {
                    "g": {
                        "name": "G",
                        "members": [{"name": "A", "channelNumber": value}],
                    }
                }
            }
        }
    }

    with pytest.raises(ValueError, match="channelNumber must be a positive integer"):
        WaybillConfig(**payload)


@pytest.mark.parametrize("value", ["yes", 1, "true"])
def test_keep_empty_channels_must_be_boolean(value: object) -> None:
    payload = _valid_config_payload()
    payload["spec"] = {"profiles": {"p": {"keepEmptyChannels": value}}}

    with pytest.raises(ValueError, match="keepEmptyChannels must be true or false"):
        WaybillConfig(**payload)
