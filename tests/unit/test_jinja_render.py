"""render_template runs per stream per matcher/transformer, so it must stay cheap."""

from __future__ import annotations

import pytest

from src import _jinja
from src._jinja import extract_template_variables, render_template


def test_plain_text_is_returned_without_compiling() -> None:
    _jinja._compile.cache_clear()

    # Regex syntax such as "{2,}" is not Jinja syntax.
    assert render_template("\\s{2,}", {}) == "\\s{2,}"
    assert render_template("DAZN 1 BAR", {"x": "y"}) == "DAZN 1 BAR"
    assert _jinja._compile.cache_info().currsize == 0


def test_templates_are_compiled_once_and_reused() -> None:
    _jinja._compile.cache_clear()

    for name in ("One", "Two", "Three"):
        assert render_template("{{ ch }} HD", {"ch": name}) == f"{name} HD"

    info = _jinja._compile.cache_info()
    assert (info.misses, info.hits) == (1, 2)


def test_undefined_variables_still_raise() -> None:
    with pytest.raises(ValueError, match="Undefined template variable in here"):
        render_template("{{ missing }}", {}, context_desc="here")


def test_extract_template_variables_skips_plain_text() -> None:
    assert extract_template_variables("plain") == []
    assert extract_template_variables("{{ a }}-{{ b }}") == ["a", "b"]
