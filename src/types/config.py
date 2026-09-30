from dataclasses import dataclass, field
from enum import Enum as Enum
from typing import Any, Mapping, cast


SUPPORTED_CONFIG_KIND = "WaybillConfig"
SUPPORTED_CONFIG_VERSION = "v1alpha1"


def _empty_str_list() -> list[str]:
    return []


def _empty_str_any_dict() -> dict[str, Any]:
    return {}


def _empty_matchers() -> list["ConfigMatcher"]:
    return []


def _empty_transformers() -> list["ConfigTransformer"]:
    return []


def _empty_members() -> list["ConfigMember"]:
    return []


def _empty_validators() -> list["ConfigValidator"]:
    return []


def _empty_str_group_dict() -> dict[str, "ConfigGroup"]:
    return {}


def _empty_str_profile_dict() -> dict[str, "ConfigProfile"]:
    return {}


def _empty_variable_dict() -> dict[str, "ConfigVariable"]:
    return {}


def _require_mapping(item: Any, where: str) -> Mapping[str, Any]:
    """Reject empty or non-mapping manifest sections with a message naming them."""
    if item is None:
        raise ValueError(
            f"{where} is empty; check its indentation and that it has fields under it"
        )
    if not isinstance(item, Mapping):
        raise ValueError(
            f"{where} must be a mapping of fields, got {type(item).__name__}"
        )
    return cast(Mapping[str, Any], item)


class MatcherType(Enum):
    REGEX = "regex"
    HAS_PREFIX = "hasPrefix"
    CONTAINS_ANY = "containsAny"
    EXACT_MATCH = "exactMatch"


class MatcherAction(Enum):
    KEEP = "keep"
    DROP = "drop"


class TransformerType(Enum):
    CONVERT_CARDINAL_NUMBERS = "convertCardinalNumbers"
    REGEX = "regex"
    STRIP = "strip"
    SET = "set"
    SET_METADATA = "setMetadata"
    TEMPLATE = "template"


class CardinalOutputType(Enum):
    NUMBER = "number"
    WORD = "word"


class OrderStreamsBy(Enum):
    QUALITY = "quality"


class ValidatorType(Enum):
    COUNT = "count"
    REGEX_MATCH = "regexMatch"
    NON_EMPTY = "nonEmpty"


class ValidatorAction(Enum):
    WARN = "warn"
    FAIL = "fail"


class ValidatorOperator(Enum):
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    EQ = "eq"
    NEQ = "neq"


class ValidatorScope(Enum):
    STREAM = "stream"
    CHANNEL = "channel"
    MEMBER = "member"


@dataclass(frozen=True)
class ConfigVariable:
    value: str
    mutable: bool = True


def _to_variable_dict(
    raw: "dict[str, ConfigVariable | Mapping[str, Any]] | Any",
) -> "dict[str, ConfigVariable]":
    """Coerce a raw mapping (from YAML) into a dict of ConfigVariable instances."""
    if not isinstance(raw, dict):
        return {}
    result: dict[str, ConfigVariable] = {}
    for key, item in raw.items():
        if isinstance(item, ConfigVariable):
            result[str(key)] = item
        elif isinstance(item, dict):
            result[str(key)] = ConfigVariable(
                value=str(item.get("value", "")),
                mutable=bool(item.get("mutable", True)),
            )
        else:
            # Shorthand: just a scalar value, default mutable=True
            result[str(key)] = ConfigVariable(value=str(item), mutable=True)
    return result


@dataclass(frozen=True)
class ConfigMetadata:
    name: str
    description: str = ""


@dataclass(frozen=True)
class ConfigTransformer:
    type: TransformerType
    action: str = ""
    output_type: CardinalOutputType | str = ""
    pattern: str = ""
    replacement: str = ""
    prefix: str = ""
    suffix: str = ""
    value: str = ""
    name: str = ""
    logo_url: str = ""
    tvg_id: str = ""
    extra: dict[str, Any] = field(default_factory=_empty_str_any_dict)
    # NOTE: `field` must be last — it shadows the dataclasses.field() import after this line
    field: str = "name"


def _to_transformer(
    item: "ConfigTransformer | Mapping[str, Any]",
) -> "ConfigTransformer":
    """Coerce a raw mapping (from YAML) or an existing ConfigTransformer into a ConfigTransformer."""
    if isinstance(item, ConfigTransformer):
        return item

    raw_type = item.get("type", "")
    transformer_type = (
        raw_type
        if isinstance(raw_type, TransformerType)
        else TransformerType(str(raw_type))
    )

    raw_output_type = item.get("outputType", "")
    output_type: CardinalOutputType | str
    if isinstance(raw_output_type, CardinalOutputType):
        output_type = raw_output_type
    elif isinstance(raw_output_type, str) and raw_output_type:
        output_type = CardinalOutputType(raw_output_type)
    else:
        output_type = ""

    known_keys = {
        "type",
        "action",
        "outputType",
        "field",
        "pattern",
        "replacement",
        "prefix",
        "suffix",
        "value",
        "name",
        "logoUrl",
        "tvgId",
    }
    extra = {str(key): value for key, value in item.items() if key not in known_keys}

    return ConfigTransformer(
        type=transformer_type,
        action=str(item.get("action", "")),
        output_type=output_type,
        pattern=str(item.get("pattern", "")),
        replacement=str(item.get("replacement", "")),
        prefix=str(item.get("prefix", "")),
        suffix=str(item.get("suffix", "")),
        value=str(item.get("value", "")),
        name=str(item.get("name", "")),
        logo_url=str(item.get("logoUrl", "")),
        tvg_id=str(item.get("tvgId", "")),
        extra=extra,
        field=str(item.get("field", "name")),
    )


@dataclass
class ConfigMatcher:
    type: MatcherType
    action: MatcherAction = MatcherAction.KEEP
    pattern: str = ""
    prefixes: list[str] = field(default_factory=_empty_str_list)
    substrings: list[str] = field(default_factory=_empty_str_list)
    values: list[str] = field(default_factory=_empty_str_list)
    case_sensitive: bool = False
    transformers: list[ConfigTransformer] = field(default_factory=_empty_transformers)
    # NOTE: `field` must be last — it shadows the dataclasses.field() import after this line
    field: str = "name"

    def __post_init__(self) -> None:
        self.transformers = [_to_transformer(item) for item in self.transformers]


@dataclass(frozen=True)
class ConfigValidator:
    type: ValidatorType
    action: ValidatorAction = ValidatorAction.WARN
    operator: ValidatorOperator = ValidatorOperator.GT
    value: int = 0
    pattern: str = ""
    scope: ValidatorScope | None = None
    # NOTE: `field` must be last — it shadows the dataclasses.field() import after this line
    field: str = "name"


def _to_validator(
    item: "ConfigValidator | Mapping[str, Any]",
) -> "ConfigValidator":
    """Coerce a raw mapping (from YAML) or an existing ConfigValidator into a ConfigValidator."""
    if isinstance(item, ConfigValidator):
        return item

    raw_type = item.get("type", "")
    validator_type = (
        raw_type
        if isinstance(raw_type, ValidatorType)
        else ValidatorType(str(raw_type))
    )

    raw_action = item.get("action", ValidatorAction.WARN)
    validator_action: ValidatorAction
    if isinstance(raw_action, ValidatorAction):
        validator_action = raw_action
    else:
        validator_action = (
            ValidatorAction(str(raw_action)) if raw_action else ValidatorAction.WARN
        )

    raw_operator = item.get("operator", ValidatorOperator.GT)
    validator_operator: ValidatorOperator
    if isinstance(raw_operator, ValidatorOperator):
        validator_operator = raw_operator
    else:
        validator_operator = (
            ValidatorOperator(str(raw_operator))
            if raw_operator
            else ValidatorOperator.GT
        )

    raw_value = item.get("value", 0)
    try:
        validator_value = int(raw_value)
    except (TypeError, ValueError):
        validator_value = 0

    raw_scope = item.get("scope")
    validator_scope: ValidatorScope | None
    if raw_scope is None or raw_scope == "":
        validator_scope = None
    elif isinstance(raw_scope, ValidatorScope):
        validator_scope = raw_scope
    else:
        validator_scope = ValidatorScope(str(raw_scope))

    return ConfigValidator(
        type=validator_type,
        action=validator_action,
        operator=validator_operator,
        value=validator_value,
        pattern=str(item.get("pattern", "")),
        scope=validator_scope,
        field=str(item.get("field", "name")),
    )


def _to_start_channel_number(raw: Any, where: str) -> "int | None":
    """Coerce startChannelNumber to a positive int, or None if absent."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        raise ValueError(f"{where} startChannelNumber must be a positive integer")
    try:
        number = float(raw)
    except ValueError:
        raise ValueError(
            f"{where} startChannelNumber must be a positive integer, got {raw!r}"
        ) from None
    if number < 1 or not number.is_integer():
        raise ValueError(
            f"{where} startChannelNumber must be a positive integer, got {raw!r}"
        )
    return int(number)


def _to_order_streams_by(raw: Any) -> "OrderStreamsBy | None":
    """Coerce a raw YAML value to OrderStreamsBy, or None if absent."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, OrderStreamsBy):
        return raw
    return OrderStreamsBy(str(raw))


@dataclass
class ConfigMember:
    name: str
    matchers: list[ConfigMatcher] = field(default_factory=_empty_matchers)
    transformers: list[ConfigTransformer] = field(default_factory=_empty_transformers)
    validators: list[ConfigValidator] = field(default_factory=_empty_validators)
    stream_profile: str | None = None
    order_streams_by: OrderStreamsBy | None = None
    variables: dict[str, ConfigVariable] = field(default_factory=_empty_variable_dict)

    def __post_init__(self):
        where = f"member {self.name!r}"
        self.matchers = [
            self._to_matcher(
                item
                if isinstance(item, ConfigMatcher)
                else _require_mapping(item, f"matcher #{i} of {where}")
            )
            for i, item in enumerate(self.matchers, 1)
        ]
        self.transformers = [
            _to_transformer(
                item
                if isinstance(item, ConfigTransformer)
                else _require_mapping(item, f"transformer #{i} of {where}")
            )
            for i, item in enumerate(self.transformers, 1)
        ]
        self.validators = [
            _to_validator(
                item
                if isinstance(item, ConfigValidator)
                else _require_mapping(item, f"validator #{i} of {where}")
            )
            for i, item in enumerate(self.validators, 1)
        ]
        self.variables = _to_variable_dict(self.variables)

    @staticmethod
    def _to_matcher(item: ConfigMatcher | Mapping[str, Any]) -> ConfigMatcher:
        if isinstance(item, ConfigMatcher):
            return item

        raw_type = item.get("type", MatcherType.REGEX)
        matcher_type = (
            raw_type
            if isinstance(raw_type, MatcherType)
            else MatcherType(str(raw_type))
        )

        raw_action = item.get("action", MatcherAction.KEEP)
        matcher_action: MatcherAction
        if isinstance(raw_action, MatcherAction):
            matcher_action = raw_action
        else:
            matcher_action = (
                MatcherAction(str(raw_action)) if raw_action else MatcherAction.KEEP
            )

        raw_prefixes = item.get("prefixes", [])
        prefixes = (
            [str(p) for p in cast(list[Any], raw_prefixes)]
            if isinstance(raw_prefixes, list)
            else _empty_str_list()
        )

        raw_substrings = item.get("substrings", [])
        substrings = (
            [str(s) for s in cast(list[Any], raw_substrings)]
            if isinstance(raw_substrings, list)
            else _empty_str_list()
        )

        raw_values = item.get("values", [])
        values = (
            [str(v) for v in cast(list[Any], raw_values)]
            if isinstance(raw_values, list)
            else _empty_str_list()
        )

        case_sensitive = bool(item.get("caseSensitive", False))

        raw_transformers = item.get("transformers", [])
        transformers = (
            cast(list[ConfigTransformer], raw_transformers)
            if isinstance(raw_transformers, list)
            else _empty_transformers()
        )

        return ConfigMatcher(
            type=matcher_type,
            action=matcher_action,
            pattern=str(item.get("pattern", "")),
            prefixes=prefixes,
            substrings=substrings,
            values=values,
            case_sensitive=case_sensitive,
            transformers=transformers,
            field=str(item.get("field", "name")),
        )


@dataclass
class ConfigGroup:
    name: str
    members: list[ConfigMember] = field(default_factory=_empty_members)
    stream_profile: str | None = None
    order_streams_by: OrderStreamsBy | None = None
    variables: dict[str, ConfigVariable] = field(default_factory=_empty_variable_dict)

    def __post_init__(self):
        self.members = [
            self._to_member(
                _require_mapping(item, f"member #{i} of group {self.name!r}")
                if not isinstance(item, ConfigMember)
                else item
            )
            for i, item in enumerate(self.members, 1)
        ]
        self.variables = _to_variable_dict(self.variables)

    @staticmethod
    def _to_member(item: ConfigMember | Mapping[str, Any]) -> ConfigMember:
        if isinstance(item, ConfigMember):
            return item

        raw_matchers = item.get("matchers", [])
        raw_transformers = item.get("transformers", [])

        matchers = (
            cast(list[ConfigMatcher], raw_matchers)
            if isinstance(raw_matchers, list)
            else _empty_matchers()
        )
        transformers = (
            cast(list[ConfigTransformer], raw_transformers)
            if isinstance(raw_transformers, list)
            else _empty_transformers()
        )

        raw_validators = item.get("validators", [])
        validators = (
            cast(list[ConfigValidator], raw_validators)
            if isinstance(raw_validators, list)
            else _empty_validators()
        )

        raw_variables = item.get("variables", {})
        variables = (
            cast(dict[str, ConfigVariable], raw_variables)
            if isinstance(raw_variables, dict)
            else _empty_variable_dict()
        )

        return ConfigMember(
            name=str(item.get("name", "")),
            matchers=matchers,
            transformers=transformers,
            validators=validators,
            stream_profile=item.get("streamProfile") or None,
            order_streams_by=_to_order_streams_by(item.get("orderStreamsBy")),
            variables=variables,
        )


@dataclass
class ConfigProfile:
    name: str = ""
    groups: dict[str, ConfigGroup] = field(default_factory=_empty_str_group_dict)
    stream_profile: str | None = None
    order_streams_by: OrderStreamsBy | None = None
    variables: dict[str, ConfigVariable] = field(default_factory=_empty_variable_dict)
    start_channel_number: int | None = None

    def __post_init__(self):
        self.groups = {
            name: self._to_group(
                _require_mapping(value, f"group {name!r}")
                if not isinstance(value, ConfigGroup)
                else value
            )
            for name, value in self.groups.items()
        }
        self.variables = _to_variable_dict(self.variables)

    @staticmethod
    def _to_group(item: ConfigGroup | Mapping[str, Any]) -> ConfigGroup:
        if isinstance(item, ConfigGroup):
            return item

        raw_members = item.get("members", [])
        members = (
            cast(list[ConfigMember], raw_members)
            if isinstance(raw_members, list)
            else _empty_members()
        )

        raw_variables = item.get("variables", {})
        variables = (
            cast(dict[str, ConfigVariable], raw_variables)
            if isinstance(raw_variables, dict)
            else _empty_variable_dict()
        )

        return ConfigGroup(
            name=str(item.get("name", "")),
            members=members,
            stream_profile=item.get("streamProfile") or None,
            order_streams_by=_to_order_streams_by(item.get("orderStreamsBy")),
            variables=variables,
        )


@dataclass
class ConfigSpec:
    profiles: dict[str, ConfigProfile] = field(default_factory=_empty_str_profile_dict)

    def __post_init__(self):
        self.profiles = {
            name: self._to_profile(
                _require_mapping(value, f"profile {name!r}")
                if not isinstance(value, ConfigProfile)
                else value
            )
            for name, value in self.profiles.items()
        }

    @staticmethod
    def _to_profile(item: ConfigProfile | Mapping[str, Any]) -> ConfigProfile:
        if isinstance(item, ConfigProfile):
            return item

        raw_groups = item.get("groups", {})
        groups = (
            cast(dict[str, ConfigGroup], raw_groups)
            if isinstance(raw_groups, dict)
            else _empty_str_group_dict()
        )

        raw_variables = item.get("variables", {})
        variables = (
            cast(dict[str, ConfigVariable], raw_variables)
            if isinstance(raw_variables, dict)
            else _empty_variable_dict()
        )

        return ConfigProfile(
            name=str(item.get("name", "")),
            groups=groups,
            stream_profile=item.get("streamProfile") or None,
            order_streams_by=_to_order_streams_by(item.get("orderStreamsBy")),
            variables=variables,
            start_channel_number=_to_start_channel_number(
                item.get("startChannelNumber"),
                f"profile {item.get('name', '')!r}",
            ),
        )


@dataclass
class WaybillConfig:
    kind: str
    version: str
    metadata: ConfigMetadata
    spec: ConfigSpec = field(default_factory=ConfigSpec)

    def __post_init__(self):
        if not isinstance(self.metadata, ConfigMetadata):
            self.metadata = self._to_metadata(
                _require_mapping(self.metadata, "metadata")
            )
        if not isinstance(self.spec, ConfigSpec):
            self.spec = self._to_spec(_require_mapping(self.spec, "spec"))
        if self.kind != SUPPORTED_CONFIG_KIND:
            raise ValueError(
                f"Unsupported config kind {self.kind!r}; expected {SUPPORTED_CONFIG_KIND!r}"
            )
        if self.version != SUPPORTED_CONFIG_VERSION:
            raise ValueError(
                f"Unsupported config version {self.version!r}; expected {SUPPORTED_CONFIG_VERSION!r}"
            )
        if not self.metadata.name.strip():
            raise ValueError("metadata.name must be a non-empty string")

    @staticmethod
    def _to_metadata(item: ConfigMetadata | Mapping[str, Any]) -> ConfigMetadata:
        if isinstance(item, ConfigMetadata):
            return item

        return ConfigMetadata(
            name=str(item.get("name", "")),
            description=str(item.get("description", "")),
        )

    @staticmethod
    def _to_spec(item: ConfigSpec | Mapping[str, Any]) -> ConfigSpec:
        if isinstance(item, ConfigSpec):
            return item

        raw_profiles = item.get("profiles", {})
        profiles = (
            cast(dict[str, ConfigProfile], raw_profiles)
            if isinstance(raw_profiles, dict)
            else _empty_str_profile_dict()
        )

        return ConfigSpec(profiles=profiles)
