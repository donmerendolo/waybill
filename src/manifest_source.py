"""Where the manifest comes from: the plugin's YAML box, or a URL (manifest_url).

With a URL, Plan and Apply download the manifest on every run. A failed
download stops the run, so Apply never works from an old or partial copy.
"""

from __future__ import annotations

import hashlib
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

URL_SETTING = "manifest_url"
TOKEN_SETTING = "manifest_url_token"
TIMEOUT_SECONDS = 20
MAX_BYTES = 2 * 1024 * 1024

# https://github.com/<owner>/<repo>/blob/<ref>/<path> -> raw file
_GITHUB_BLOB = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/(?:blob|raw)/(.+)$")


class ManifestSourceError(Exception):
    """The manifest could not be read from its source."""


@dataclass(frozen=True)
class ManifestSource:
    text: str
    # "settings" or the URL it was downloaded from (without query or credentials)
    origin: str
    size: int = 0
    sha256: str = ""

    def describe(self) -> str:
        if self.origin == "settings":
            return "Manifest: from the plugin settings"
        return (
            f"Manifest: downloaded from {self.origin} "
            f"({self.size / 1024:.1f} KB, sha256 {self.sha256[:12]})"
        )


def normalise_url(url: str) -> str:
    """Accept a GitHub page link and turn it into its raw file link."""
    url = url.strip()
    match = _GITHUB_BLOB.match(url)
    if match:
        owner, repo, rest = match.groups()
        return f"https://raw.githubusercontent.com/{owner}/{repo}/{rest}"
    return url


def display_url(url: str) -> str:
    """The URL without user info, query or fragment, safe to write to the log."""
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return urllib.parse.urlunsplit((parts.scheme, host, parts.path, "", ""))


Opener = Callable[[urllib.request.Request, float], Any]


def _default_opener(request: urllib.request.Request, timeout: float) -> Any:
    return urllib.request.urlopen(request, timeout=timeout)


def fetch(
    url: str, token: str = "", opener: Opener = _default_opener
) -> ManifestSource:
    """Download the manifest at *url* (http or https), at most MAX_BYTES."""
    url = normalise_url(url)
    shown = display_url(url)
    if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
        raise ManifestSourceError(
            f"Manifest URL must start with http:// or https:// (got {url!r})"
        )
    headers = {"User-Agent": "waybill", "Accept": "text/plain, application/yaml, */*"}
    if token.strip():
        headers["Authorization"] = f"Bearer {token.strip()}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with opener(request, TIMEOUT_SECONDS) as response:
            data = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as e:
        hint = ""
        if e.code in (401, 403, 404) and not token.strip():
            hint = " (a private repository needs the Manifest URL token)"
        raise ManifestSourceError(
            f"Could not download the manifest from {shown}: HTTP {e.code} {e.reason}{hint}"
        ) from e
    except (urllib.error.URLError, OSError, ValueError) as e:
        reason = getattr(e, "reason", e)
        raise ManifestSourceError(
            f"Could not download the manifest from {shown}: {reason}"
        ) from e
    if len(data) > MAX_BYTES:
        raise ManifestSourceError(
            f"The manifest at {shown} is larger than {MAX_BYTES // (1024 * 1024)} MB"
        )
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise ManifestSourceError(f"The manifest at {shown} is not UTF-8 text") from e
    if not text.strip():
        raise ManifestSourceError(f"The manifest at {shown} is empty")
    return ManifestSource(
        text=text,
        origin=shown,
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def resolve(
    settings: dict[str, Any], opener: Opener = _default_opener
) -> ManifestSource:
    """The manifest text: from manifest_url when set, else from the YAML box."""
    url = settings.get(URL_SETTING) or ""
    if isinstance(url, str) and url.strip():
        token = settings.get(TOKEN_SETTING) or ""
        return fetch(url, token if isinstance(token, str) else "", opener)
    text = settings.get("manifest", "")
    return ManifestSource(text=text if isinstance(text, str) else "", origin="settings")
