from ..types.config import ConfigTransformer, TransformerType
from .convert_cardinal_numbers import WaybillTransformerConvertCardinalNumbers
from .normalize_unicode import WaybillTransformerNormalizeUnicode
from .regex import WaybillTransformerRegex
from .set import WaybillTransformerSet
from .set_metadata import WaybillTransformerSetMetadata
from .strip import WaybillTransformerStrip
from .template import WaybillTransformerTemplate

AnyTransformer = (
    WaybillTransformerRegex
    | WaybillTransformerConvertCardinalNumbers
    | WaybillTransformerStrip
    | WaybillTransformerNormalizeUnicode
    | WaybillTransformerSet
    | WaybillTransformerSetMetadata
    | WaybillTransformerTemplate
)


def build_transformer(cfg: ConfigTransformer) -> AnyTransformer:
    """Instantiate the concrete transformer for a ConfigTransformer."""
    if cfg.type == TransformerType.REGEX:
        return WaybillTransformerRegex(
            pattern=cfg.pattern,
            action=cfg.action,
            replacement=cfg.replacement,
            field=cfg.field,
        )

    if cfg.type == TransformerType.CONVERT_CARDINAL_NUMBERS:
        output_type = (
            cfg.output_type.value
            if hasattr(cfg.output_type, "value")
            else str(cfg.output_type)
        )
        return WaybillTransformerConvertCardinalNumbers(
            output_type=output_type,
            field=cfg.field,
        )

    if cfg.type == TransformerType.STRIP:
        return WaybillTransformerStrip(
            field=cfg.field, prefix=cfg.prefix, suffix=cfg.suffix
        )

    if cfg.type == TransformerType.NORMALIZE_UNICODE:
        return WaybillTransformerNormalizeUnicode(field=cfg.field)

    if cfg.type == TransformerType.SET:
        return WaybillTransformerSet(value=cfg.value, field=cfg.field)

    if cfg.type == TransformerType.SET_METADATA:
        return WaybillTransformerSetMetadata(
            name=cfg.name,
            logo_url=cfg.logo_url,
            tvg_id=cfg.tvg_id,
        )

    if cfg.type == TransformerType.TEMPLATE:
        return WaybillTransformerTemplate(value=cfg.value, field=cfg.field)

    raise ValueError(f"Unknown transformer type: {cfg.type!r}")
