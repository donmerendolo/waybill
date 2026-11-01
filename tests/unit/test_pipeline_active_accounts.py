"""Streams of disabled M3U accounts never reach channels."""

from __future__ import annotations

import test_pipeline_validators  # noqa: F401  (installs Django/Dispatcharr stubs)

import src.pipeline as pipeline
from src.types.config import ConfigMember


class _RecQ:
    """Q stand-in that keeps the expression tree for inspection."""

    def __init__(self, *children, op="AND", **kwargs) -> None:
        self.op, self.kwargs, self.children = op, kwargs, list(children)

    def __and__(self, other):
        return _RecQ(self, other, op="AND")

    def __or__(self, other):
        return _RecQ(self, other, op="OR")

    def __bool__(self) -> bool:
        return bool(self.kwargs or self.children)

    def lookups(self) -> set:
        out = set(self.kwargs.items())
        for child in self.children:
            out |= child.lookups()
        return out


class _QS:
    def only(self, *fields):
        return self

    def iterator(self, chunk_size=1000):
        return iter([])


class _Manager:
    def __init__(self) -> None:
        self.filters: list[_RecQ] = []

    def filter(self, q):
        self.filters.append(q)
        return _QS()


def _install(monkeypatch) -> _Manager:
    manager = _Manager()
    monkeypatch.setattr(pipeline, "Q", _RecQ)
    monkeypatch.setattr(pipeline, "build_q_filter", lambda matchers: _RecQ())
    monkeypatch.setattr(pipeline.Stream, "objects", manager, raising=False)
    return manager


ACTIVE = {("m3u_account__isnull", True), ("m3u_account__is_active", True)}


def test_member_query_excludes_disabled_accounts(monkeypatch) -> None:
    manager = _install(monkeypatch)
    pipeline.MemberPipeline(ConfigMember(name="DAZN F1")).process()
    assert manager.filters and ACTIVE <= manager.filters[0].lookups()


def test_full_scan_excludes_disabled_accounts(monkeypatch) -> None:
    manager = _install(monkeypatch)
    pipeline.AllStreams().get()
    assert ACTIVE <= manager.filters[0].lookups()


def test_condition_keeps_streams_without_account(monkeypatch) -> None:
    monkeypatch.setattr(pipeline, "Q", _RecQ)
    q = pipeline.active_accounts_q()
    assert q.op == "OR"
    assert q.lookups() == ACTIVE
