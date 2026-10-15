import unicodedata

from apps.channels.models import Stream

from .base import WaybillTransformerBase


class WaybillTransformerNormalizeUnicode(WaybillTransformerBase):
    """Applies Unicode NFKC normalisation to a stream field.

    Folds compatibility characters into their plain form, so decorated
    letters such as squared (🄼🄾🅅), full-width (ＤＡＺＮ) or circled ones
    become ordinary ASCII. Emoji and flags are left as they are.
    """

    def __init__(self, field: str = "name"):
        self.field = field

    def transform(
        self, stream: Stream, variables: "dict[str, str] | None" = None
    ) -> "Stream | None":
        value = getattr(stream, self.field)
        setattr(stream, self.field, unicodedata.normalize("NFKC", value))
        return stream

    def _describe_self(self) -> str:
        field_note = f' on "{self.field}"' if self.field != "name" else ""
        return f"normalizeUnicode(NFKC){field_note}"
