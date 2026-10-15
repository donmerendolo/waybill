"""Tests for the build_transformer factory."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from src.transformers import build_transformer
from src.transformers.convert_cardinal_numbers import (
    WaybillTransformerConvertCardinalNumbers,
)
from src.transformers.normalize_unicode import WaybillTransformerNormalizeUnicode
from src.transformers.regex import WaybillTransformerRegex
from src.transformers.set import WaybillTransformerSet
from src.transformers.set_metadata import WaybillTransformerSetMetadata
from src.transformers.strip import WaybillTransformerStrip
from src.transformers.template import WaybillTransformerTemplate
from src.types.config import (
    CardinalOutputType,
    ConfigTransformer,
    TransformerType,
)


class TestBuildTransformer:
    def test_builds_regex_transformer(self) -> None:
        cfg = ConfigTransformer(
            type=TransformerType.REGEX, pattern=r"^UK: ", action="replace"
        )
        assert isinstance(build_transformer(cfg), WaybillTransformerRegex)

    def test_builds_strip_transformer(self) -> None:
        cfg = ConfigTransformer(type=TransformerType.STRIP, prefix="UK: ")
        assert isinstance(build_transformer(cfg), WaybillTransformerStrip)

    def test_builds_normalize_unicode_transformer(self) -> None:
        cfg = ConfigTransformer(type=TransformerType.NORMALIZE_UNICODE, field="tvg_id")
        t = build_transformer(cfg)
        assert isinstance(t, WaybillTransformerNormalizeUnicode)
        assert t.field == "tvg_id"

    def test_builds_set_transformer(self) -> None:
        cfg = ConfigTransformer(type=TransformerType.SET, value="NBS One")
        assert isinstance(build_transformer(cfg), WaybillTransformerSet)

    def test_builds_set_metadata_transformer(self) -> None:
        cfg = ConfigTransformer(type=TransformerType.SET_METADATA, name="NBS One")
        assert isinstance(build_transformer(cfg), WaybillTransformerSetMetadata)

    def test_builds_convert_cardinal_numbers_transformer(self) -> None:
        cfg = ConfigTransformer(
            type=TransformerType.CONVERT_CARDINAL_NUMBERS,
            output_type=CardinalOutputType.NUMBER,
        )
        assert isinstance(
            build_transformer(cfg), WaybillTransformerConvertCardinalNumbers
        )

    def test_regex_propagates_pattern(self) -> None:
        cfg = ConfigTransformer(
            type=TransformerType.REGEX,
            pattern=r"^UK: ",
            action="replace",
            replacement="GB: ",
        )
        t = build_transformer(cfg)
        assert isinstance(t, WaybillTransformerRegex)
        assert t.pattern == r"^UK: "

    def test_strip_propagates_prefix_and_suffix(self) -> None:
        cfg = ConfigTransformer(type=TransformerType.STRIP, prefix="UK: ", suffix=" HD")
        t = build_transformer(cfg)
        assert isinstance(t, WaybillTransformerStrip)
        assert t.prefix == "UK: "
        assert t.suffix == " HD"

    def test_set_propagates_value(self) -> None:
        cfg = ConfigTransformer(type=TransformerType.SET, value="NBS One")
        t = build_transformer(cfg)
        assert isinstance(t, WaybillTransformerSet)
        assert t.value == "NBS One"

    def test_convert_cardinal_numbers_enum_output_type(self) -> None:
        cfg = ConfigTransformer(
            type=TransformerType.CONVERT_CARDINAL_NUMBERS,
            output_type=CardinalOutputType.WORD,
        )
        t = build_transformer(cfg)
        assert isinstance(t, WaybillTransformerConvertCardinalNumbers)
        assert t.output_type == "word"

    def test_convert_cardinal_numbers_string_output_type(self) -> None:
        cfg = ConfigTransformer(
            type=TransformerType.CONVERT_CARDINAL_NUMBERS,
            output_type="number",
        )
        t = build_transformer(cfg)
        assert isinstance(t, WaybillTransformerConvertCardinalNumbers)
        assert t.output_type == "number"

    def test_unknown_type_raises(self) -> None:
        cfg = MagicMock(spec=ConfigTransformer)
        cfg.type = "totally_unknown"
        with pytest.raises((ValueError, AttributeError)):
            build_transformer(cfg)

    def test_builds_template_transformer(self) -> None:
        cfg = ConfigTransformer(
            type=TransformerType.TEMPLATE, value="{{ ch_name }} ({{ quality }})"
        )
        assert isinstance(build_transformer(cfg), WaybillTransformerTemplate)

    def test_template_propagates_value_and_field(self) -> None:
        cfg = ConfigTransformer(
            type=TransformerType.TEMPLATE,
            value="{{ ch_name }} ({{ quality }})",
            field="tvg_id",
        )
        t = build_transformer(cfg)
        assert isinstance(t, WaybillTransformerTemplate)
        assert t._raw_value == "{{ ch_name }} ({{ quality }})"
        assert t.field == "tvg_id"
