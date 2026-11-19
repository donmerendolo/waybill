"""manifest_url: downloading the manifest instead of reading the YAML box."""

from __future__ import annotations

import io
import urllib.error

import pytest

from src import manifest_source as ms


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _opener(body: bytes = b"kind: WaybillConfig\n", error: Exception | None = None):
    seen: dict = {}

    def opener(request, timeout):
        seen["url"] = request.full_url
        seen["headers"] = dict(request.header_items())
        seen["timeout"] = timeout
        if error is not None:
            raise error
        return _Response(body)

    return opener, seen


def test_settings_box_when_no_url() -> None:
    source = ms.resolve({"manifest": "kind: x", "manifest_url": "  "})
    assert source.text == "kind: x"
    assert source.describe() == "Manifest: from the plugin settings"


def test_url_wins_over_box() -> None:
    opener, seen = _opener(b"\xef\xbb\xbfkind: WaybillConfig\n")
    source = ms.resolve(
        {"manifest": "ignored", "manifest_url": "https://example.com/w.yaml"}, opener
    )
    assert source.text == "kind: WaybillConfig\n"  # BOM dropped
    assert seen["url"] == "https://example.com/w.yaml"
    assert "Authorization" not in seen["headers"]
    assert seen["timeout"] == ms.TIMEOUT_SECONDS
    assert source.describe().startswith(
        "Manifest: downloaded from https://example.com/w.yaml ("
    )
    assert len(source.sha256) == 64


def test_token_is_sent_as_bearer() -> None:
    opener, seen = _opener()
    ms.resolve(
        {"manifest_url": "https://example.com/w.yaml", "manifest_url_token": " abc "},
        opener,
    )
    assert seen["headers"]["Authorization"] == "Bearer abc"


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        (
            "https://github.com/me/repo/blob/main/dir/w.yaml",
            "https://raw.githubusercontent.com/me/repo/main/dir/w.yaml",
        ),
        (
            "https://github.com/me/repo/raw/0123abc/w.yaml",
            "https://raw.githubusercontent.com/me/repo/0123abc/w.yaml",
        ),
        (
            "https://raw.githubusercontent.com/me/repo/main/w.yaml",
            "https://raw.githubusercontent.com/me/repo/main/w.yaml",
        ),
    ],
)
def test_github_links_become_raw(given: str, expected: str) -> None:
    assert ms.normalise_url(given) == expected


def test_log_hides_credentials_and_query() -> None:
    opener, _ = _opener()
    source = ms.fetch(
        "https://user:pw@example.com:8443/w.yaml?token=s3cret#x", opener=opener
    )
    assert source.origin == "https://example.com:8443/w.yaml"
    assert "s3cret" not in source.describe() and "pw" not in source.describe()


def test_rejects_other_schemes() -> None:
    with pytest.raises(ms.ManifestSourceError, match="http:// or https://"):
        ms.fetch("file:///etc/passwd")


def test_http_error_hints_at_token() -> None:
    opener, _ = _opener(
        error=urllib.error.HTTPError("https://x/w.yaml", 404, "Not Found", {}, None)
    )
    with pytest.raises(ms.ManifestSourceError, match="HTTP 404 Not Found .*token"):
        ms.fetch("https://x/w.yaml", opener=opener)


def test_network_error() -> None:
    opener, _ = _opener(error=urllib.error.URLError("timed out"))
    with pytest.raises(ms.ManifestSourceError, match="Could not download .*timed out"):
        ms.fetch("https://x/w.yaml", opener=opener)


def test_too_large(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ms, "MAX_BYTES", 10)
    opener, _ = _opener(b"x" * 11)
    with pytest.raises(ms.ManifestSourceError, match="larger than"):
        ms.fetch("https://x/w.yaml", opener=opener)


@pytest.mark.parametrize(
    ("body", "match"), [(b"  \n", "empty"), (b"\xff\xfe", "UTF-8")]
)
def test_empty_or_binary(body: bytes, match: str) -> None:
    opener, _ = _opener(body)
    with pytest.raises(ms.ManifestSourceError, match=match):
        ms.fetch("https://x/w.yaml", opener=opener)
