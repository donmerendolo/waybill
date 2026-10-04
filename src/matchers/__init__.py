import re

from django.db.models import Q

from ..types.config import ConfigMatcher, MatcherAction, MatcherType
from .contains_any import WaybillMatcherContainsAny
from .exact_match import WaybillMatcherExactMatch
from .has_prefix import WaybillMatcherHasPrefix
from .regex import WaybillMatcherRegex

AnyMatcher = (
    WaybillMatcherRegex
    | WaybillMatcherHasPrefix
    | WaybillMatcherContainsAny
    | WaybillMatcherExactMatch
)


def build_matcher(cfg: ConfigMatcher) -> AnyMatcher:
    """Instantiate the concrete matcher for a ConfigMatcher."""
    from ..transformers import build_transformer

    action = (
        cfg.action.value if isinstance(cfg.action, MatcherAction) else str(cfg.action)
    )
    pre_transformers = [build_transformer(t) for t in cfg.transformers]

    if cfg.type == MatcherType.REGEX:
        return WaybillMatcherRegex(
            pattern=cfg.pattern,
            field=cfg.field,
            action=action,
            case_sensitive=cfg.case_sensitive,
            pre_transformers=pre_transformers,
        )
    if cfg.type == MatcherType.HAS_PREFIX:
        return WaybillMatcherHasPrefix(
            prefixes=list(cfg.prefixes),
            field=cfg.field,
            action=action,
            case_sensitive=cfg.case_sensitive,
            pre_transformers=pre_transformers,
        )
    if cfg.type == MatcherType.CONTAINS_ANY:
        return WaybillMatcherContainsAny(
            substrings=list(cfg.substrings),
            field=cfg.field,
            action=action,
            case_sensitive=cfg.case_sensitive,
            pre_transformers=pre_transformers,
        )
    if cfg.type == MatcherType.EXACT_MATCH:
        return WaybillMatcherExactMatch(
            values=list(cfg.values),
            field=cfg.field,
            action=action,
            case_sensitive=cfg.case_sensitive,
            pre_transformers=pre_transformers,
        )
    raise ValueError(f"Unknown matcher type: {cfg.type!r}")


def matcher_to_q(cfg: ConfigMatcher) -> Q:
    """Translate a ConfigMatcher to a Django ORM Q object for pre-filtering.

    This is a rough reduction hint — the ORM engine's regex semantics (iregex)
    may admit false positives. Python matcher instances provide the authoritative
    match and are always applied after the queryset is fetched.

    Matchers with pre-transformers cannot be accurately pre-filtered; an empty Q()
    (no restriction) is returned so no streams are incorrectly excluded.
    """
    is_drop = cfg.action == MatcherAction.DROP

    if cfg.type == MatcherType.REGEX:
        if cfg.transformers:
            # Cannot pre-filter accurately when field values will be transformed before matching
            return Q()
        # PostgreSQL POSIX ERE does not support Python named capture groups (?P<name>...).
        # Strip them to plain groups so the pattern is valid POSIX ERE.
        posix_pattern = re.sub(r"\(\?P<\w+>", "(", cfg.pattern)
        q = Q(**{f"{cfg.field}__iregex": posix_pattern})
        return ~q if is_drop else q

    if cfg.type == MatcherType.HAS_PREFIX:
        if not cfg.prefixes or cfg.transformers:
            return Q()
        lookup = "startswith" if cfg.case_sensitive else "istartswith"
        q = Q()
        for prefix in cfg.prefixes:
            q |= Q(**{f"{cfg.field}__{lookup}": prefix})
        return ~q if is_drop else q

    if cfg.type == MatcherType.CONTAINS_ANY:
        if not cfg.substrings or cfg.transformers:
            return Q()
        lookup = "contains" if cfg.case_sensitive else "icontains"
        q = Q()
        for substring in cfg.substrings:
            q |= Q(**{f"{cfg.field}__{lookup}": substring})
        return ~q if is_drop else q

    if cfg.type == MatcherType.EXACT_MATCH:
        if not cfg.values or cfg.transformers:
            return Q()
        if cfg.case_sensitive:
            q = Q(**{f"{cfg.field}__in": cfg.values})
        else:
            q = Q()
            for value in cfg.values:
                q |= Q(**{f"{cfg.field}__iexact": value})
        return ~q if is_drop else q

    raise ValueError(f"Cannot translate matcher type to Q: {cfg.type!r}")


def build_q_filter(matchers: list[ConfigMatcher]) -> Q:
    """AND all matcher Q objects together.

    Returns an empty Q() (no filter, all rows) when the list is empty, which
    matches the contract: no matchers → all streams are candidates.
    """
    if not matchers:
        return Q()
    result = matcher_to_q(matchers[0])
    for m in matchers[1:]:
        result &= matcher_to_q(m)
    return result
