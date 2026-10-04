"""The repo manifest must satisfy what Dispatcharr's plugin-repo code reads."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from build_repo_manifest import build_manifest

REPO_ROOT = Path(__file__).resolve().parents[2]
ZIP = b"PK\x03\x04" + b"\0" * 1_204_770  # 1,204,774 bytes, the size of a real build
NOW = datetime(2026, 10, 4, 12, 0, 0, tzinfo=timezone.utc)

# Mirrors apps/plugins/api_views.py _OFFICIAL_NAME_PATTERNS in Dispatcharr v0.31.
_OFFICIAL_NAME_PATTERNS = [
    "official",
    "official repo",
    "dispatcharr plugins",
    "dispatcharr repo",
    "dispatcharr official",
]


def _plugin_json() -> dict:
    return json.loads((REPO_ROOT / "plugin.json").read_text(encoding="utf-8"))


def _manifest() -> dict:
    return build_manifest(
        _plugin_json(),
        ZIP,
        repo="donmerendolo/waybill",
        tag="v1.9.1",
        zip_name="waybill.zip",
        now=NOW,
    )


def test_manifest_has_registry_fields_dispatcharr_requires() -> None:
    inner = _manifest()["manifest"]

    registry_name = inner["registry_name"]
    assert registry_name == "donmerendolo/waybill"
    assert not any(p in registry_name.lower() for p in _OFFICIAL_NAME_PATTERNS)
    assert isinstance(inner["plugins"], list) and len(inner["plugins"]) == 1


def test_download_url_resolves_to_release_asset() -> None:
    inner = _manifest()["manifest"]
    entry = inner["plugins"][0]

    # Dispatcharr joins root_url and a relative latest_url with "/".
    url = f"{inner['root_url'].rstrip('/')}/{entry['latest_url']}"
    assert (
        url
        == "https://github.com/donmerendolo/waybill/releases/download/v1.9.1/waybill.zip"
    )


def test_entry_describes_this_release() -> None:
    plugin = _plugin_json()
    entry = _manifest()["manifest"]["plugins"][0]

    # The slug becomes the installed plugin key; it must stay "waybill" so a repo
    # install replaces an existing manual install instead of adding a second one.
    assert entry["slug"] == "waybill"
    assert entry["latest_version"] == plugin["version"]
    assert entry["latest_sha256"] == hashlib.sha256(ZIP).hexdigest()
    assert entry["latest_md5"] == hashlib.md5(ZIP).hexdigest()
    # Dispatcharr shows latest_size as kilobytes (formatKB), not bytes.
    assert entry["latest_size"] == 1176
    assert entry["min_dispatcharr_version"] == plugin["min_dispatcharr_version"]
    assert entry["last_updated"] == "2026-10-04T12:00:00Z"
    assert "max_dispatcharr_version" not in entry


def test_plugin_json_version_matches_pyproject() -> None:
    import tomllib

    pyproject = tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert _plugin_json()["version"] == pyproject["project"]["version"]
