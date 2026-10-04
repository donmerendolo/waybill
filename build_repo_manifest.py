"""Write the Dispatcharr plugin-repo manifest for a Waybill release.

Dispatcharr can track third-party plugin repositories: add this repo's manifest
URL under Plugins and the dashboard shows when a new version is out and installs
it in place. The release workflow commits the generated ``manifest.json`` to the
``releases`` branch after each release, so

    https://raw.githubusercontent.com/<owner>/<repo>/releases/manifest.json

always describes the newest release. It is also attached to each GitHub
release next to ``waybill.zip``.

Usage:
    python build_repo_manifest.py --zip dist/waybill.zip --repo owner/name \\
        --tag v1.9.1 --out dist/manifest.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent
SLUG = "waybill"  # also the installed plugin key, so updates replace the same plugin


def build_manifest(
    plugin: dict,
    zip_bytes: bytes,
    repo: str,
    tag: str,
    zip_name: str,
    now: datetime,
) -> dict:
    """Return the repo manifest describing one release of the plugin."""
    repo_url = f"https://github.com/{repo}"
    timestamp = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry = {
        "slug": SLUG,
        "name": plugin.get("display_name") or plugin["name"],
        "description": plugin.get("description", ""),
        "author": plugin.get("author", ""),
        "license": plugin.get("license", ""),
        "repo_url": repo_url,
        "last_updated": timestamp,
        "latest_version": plugin["version"],
        # Relative to root_url, so Dispatcharr resolves .../releases/download/<tag>/<zip>.
        "latest_url": f"{tag}/{zip_name}",
        "latest_sha256": hashlib.sha256(zip_bytes).hexdigest(),
        "latest_md5": hashlib.md5(zip_bytes).hexdigest(),
        "latest_size": len(zip_bytes),
    }
    for key in ("min_dispatcharr_version", "max_dispatcharr_version"):
        if plugin.get(key):
            entry[key] = plugin[key]
    return {
        "manifest": {
            "registry_name": repo,
            "registry_url": repo_url,
            "root_url": f"{repo_url}/releases/download",
            "plugins": [entry],
        }
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--zip", required=True, type=Path, help="Built plugin ZIP")
    parser.add_argument("--repo", required=True, help="GitHub owner/name")
    parser.add_argument("--tag", required=True, help="Release tag, e.g. v1.9.1")
    parser.add_argument("--out", required=True, type=Path, help="Output path")
    args = parser.parse_args()

    plugin = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
    manifest = build_manifest(
        plugin,
        args.zip.read_bytes(),
        repo=args.repo,
        tag=args.tag,
        zip_name=args.zip.name,
        now=datetime.now(timezone.utc),
    )
    args.out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Written: {args.out}")


if __name__ == "__main__":
    main()
