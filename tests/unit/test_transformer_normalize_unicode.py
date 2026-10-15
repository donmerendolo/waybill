"""Tests for WaybillTransformerNormalizeUnicode."""

from __future__ import annotations

from src.transformers.normalize_unicode import WaybillTransformerNormalizeUnicode


class TestWaybillTransformerNormalizeUnicode:
    def test_folds_squared_letters(self, stream_factory) -> None:
        t = WaybillTransformerNormalizeUnicode()
        s = stream_factory(name="🄼🄾🅅🄸🅂🅃🄰🅁 🄳🄴🄿🄾🅁🅃🄴🅂 4")
        t.transform(s)
        assert s.name == "MOVISTAR DEPORTES 4"

    def test_folds_full_width_letters(self, stream_factory) -> None:
        t = WaybillTransformerNormalizeUnicode()
        s = stream_factory(name="ＤＡＺＮ １")
        t.transform(s)
        assert s.name == "DAZN 1"

    def test_keeps_emoji_and_accents(self, stream_factory) -> None:
        t = WaybillTransformerNormalizeUnicode()
        s = stream_factory(name="🇪🇸M+ Cine Español⚽")
        t.transform(s)
        assert s.name == "🇪🇸M+ Cine Español⚽"

    def test_targets_configured_field(self, stream_factory) -> None:
        t = WaybillTransformerNormalizeUnicode(field="tvg_id")
        s = stream_factory(name="🄳🄰🅉🄽", tvg_id="🄳🄰🅉🄽")
        t.transform(s)
        assert (s.name, s.tvg_id) == ("🄳🄰🅉🄽", "DAZN")

    def test_returns_stream_instance(self, stream_factory) -> None:
        t = WaybillTransformerNormalizeUnicode()
        s = stream_factory(name="DAZN 1")
        assert t.transform(s) is s

    def test_describe(self) -> None:
        assert WaybillTransformerNormalizeUnicode().describe() == (
            "normalizeUnicode(NFKC)"
        )
