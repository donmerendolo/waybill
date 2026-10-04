from __future__ import annotations

from functools import lru_cache

import jinja2

_JINJA_ENV = jinja2.Environment(
    undefined=jinja2.StrictUndefined,
    autoescape=False,
)


def render_template(
    value: str,
    variables: "dict[str, str]",
    context_desc: str = "",
) -> str:
    """Render *value* as a Jinja2 template using *variables* as context.

    If *value* contains no template syntax it is returned unchanged.

    Raises :class:`ValueError` when a referenced variable is absent from
    *variables*.  *context_desc* is appended to the error message to identify
    the calling site (e.g. ``'set transformer "name" field'``).
    """
    # Most values are plain text and this runs per stream per matcher/transformer,
    # so skip Jinja entirely when there is nothing to render.
    if not _has_template_syntax(value):
        return value
    try:
        return _compile(value).render(**variables)
    except jinja2.UndefinedError as exc:
        suffix = f" in {context_desc}" if context_desc else ""
        raise ValueError(f"Undefined template variable{suffix}: {exc}") from exc


def extract_template_variables(template_str: str) -> list[str]:
    """Return all variable names referenced in a Jinja2 template string.

    Walks the parsed AST for :class:`jinja2.nodes.Name` nodes, which
    correspond to variable look-ups such as ``{{ ch_name }}``.

    Note: names that appear inside control-flow blocks (``{% for %}``,
    ``{% if %}``) are also included — acceptable for an observational logging
    feature.
    """
    if not _has_template_syntax(template_str):
        return []
    return list(_template_variables(template_str))


def _has_template_syntax(value: str) -> bool:
    return "{{" in value or "{%" in value or "{#" in value


@lru_cache(maxsize=1024)
def _compile(value: str) -> jinja2.Template:
    # Compiling is far more expensive than rendering; manifests reuse a small
    # set of template strings across every stream.
    return _JINJA_ENV.from_string(value)


@lru_cache(maxsize=1024)
def _template_variables(template_str: str) -> tuple[str, ...]:
    ast = _JINJA_ENV.parse(template_str)
    return tuple(node.name for node in ast.find_all(jinja2.nodes.Name))
