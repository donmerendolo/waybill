"""The ORM pre-filter must never exclude a stream the Python matchers would keep.

A matcher with pre-transformers compares the *transformed* value, which the
database cannot see, so it must not narrow the queryset at all.
"""

from __future__ import annotations

import pytest
from django.db.models import Q

from src.matchers import build_q_filter, matcher_to_q
from src.types.config import ConfigMember

_CLEAN = [
    {
        "type": "regex",
        "field": "name",
        "action": "replace",
        "pattern": "(?i)\\b(FHD|HD|SD)\\b",
        "replacement": "",
    }
]

_MATCHERS = {
    "exactMatch": {"type": "exactMatch", "values": ["DAZN 1 BAR"]},
    "containsAny": {"type": "containsAny", "substrings": ["DAZN 1 BAR"]},
    "hasPrefix": {"type": "hasPrefix", "prefixes": ["DAZN 1 BAR"]},
    "regex": {"type": "regex", "pattern": "^DAZN 1 BAR$"},
}


def _matcher(spec: dict, transformers: list | None = None):
    raw = {**spec, **({"transformers": transformers} if transformers else {})}
    return ConfigMember(name="m", matchers=[raw]).matchers[0]


@pytest.mark.parametrize("kind", sorted(_MATCHERS))
def test_pre_transformers_disable_the_pre_filter(kind: str) -> None:
    assert matcher_to_q(_matcher(_MATCHERS[kind], _CLEAN)) == Q()


@pytest.mark.parametrize("kind", sorted(_MATCHERS))
def test_plain_matchers_still_pre_filter(kind: str) -> None:
    assert matcher_to_q(_matcher(_MATCHERS[kind])) != Q()


def test_exact_match_with_clean_chain_keeps_fhd_variant_in_queryset() -> None:
    # Regression: "DAZN 1 BAR FHD" only equals "DAZN 1 BAR" after cleaning, so the
    # database must not be asked for name iexact "DAZN 1 BAR".
    member = ConfigMember(
        name="DAZN 1 Bar",
        matchers=[{**_MATCHERS["exactMatch"], "transformers": _CLEAN}],
    )

    assert build_q_filter(member.matchers) == Q()
