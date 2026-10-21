import os
import sys

# Dirty hack to bring our own vendored dependencies without interfering with the host's Python environment
sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src", "vendor")
)

import json
import yaml
from dacite import Config, from_dict
from typing import Any, Mapping, Protocol, cast

from .src.types.plugins import Plugin as DispatcharrPlugin
from .src.types.plugins import PluginFieldType
from .src.types.config import WaybillConfig
from .src.pipeline import WaybillPipeline
from .src.plan import WaybillPlanFormatter
from .src.apply import WaybillApplier
from .src import scheduler


class WaybillError(Exception):
    """Base class for plugin runtime errors."""


class WaybillConfigurationError(WaybillError):
    """Raised when plugin configuration cannot be parsed or validated."""


class WaybillContextError(WaybillError):
    """Raised when runtime context is missing required contracts."""


class WaybillActionError(WaybillError):
    """Raised when an unsupported action is requested."""


class WaybillValidationError(WaybillError):
    """Raised when one or more fail-action validators are violated."""


class WaybillLogger(Protocol):
    def info(self, message: str) -> None: ...

    def warning(self, message: str) -> None: ...

    def error(self, message: str) -> None: ...


class Plugin(DispatcharrPlugin):
    def __init__(self, path: str = "plugin.json") -> None:
        # Keep the plugin manifest as source of truth for metadata.
        plugin_dir = os.path.dirname(os.path.abspath(__file__))
        plugin = self._load_manifest(os.path.join(plugin_dir, path))
        super().__init__(
            name=plugin.name,
            version=plugin.version,
            description=plugin.description,
            author=plugin.author,
            fields=plugin.fields,
            actions=plugin.actions,
        )
        self._plugin_key = os.path.basename(plugin_dir)
        self._start_scheduler()

        return None

    def _start_scheduler(self) -> None:
        """Start the auto-apply timer when running inside Dispatcharr.

        Also called on every action, so the timer comes back if the process
        that ran it has exited.
        """
        try:
            import apps.plugins.loader  # noqa: F401  (only present in Dispatcharr)
        except ImportError:
            return
        scheduler.ensure_running(self._plugin_key)

    def stop(self, context: Any = None) -> None:
        """Called by Dispatcharr when the plugin is disabled, deleted or reloaded."""
        scheduler.stop()

    def _load_manifest(self, path: str) -> DispatcharrPlugin:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)

        return from_dict(
            data_class=DispatcharrPlugin,
            data=raw,
            config=Config(cast=[PluginFieldType]),
        )

    def _require_logger(self, context: Mapping[str, Any]) -> WaybillLogger:
        logger = context.get("logger")
        if logger is None:
            raise WaybillContextError("Runtime context is missing a logger")

        for method in ("info", "warning", "error"):
            if not callable(getattr(logger, method, None)):
                raise WaybillContextError(
                    f"Runtime logger is missing required method {method!r}"
                )

        return cast(WaybillLogger, logger)

    def _load_configuration(self, context: Mapping[str, Any]) -> None:
        config_data_raw = context.get("settings", {})
        if not isinstance(config_data_raw, dict):
            raise WaybillConfigurationError("Configuration settings must be a mapping")
        config_data = cast(dict[str, Any], config_data_raw)

        manifest_value = config_data.get("manifest", "")
        if not isinstance(manifest_value, str) or not manifest_value.strip():
            raise WaybillConfigurationError(
                "Configuration manifest is empty or missing"
            )

        try:
            parsed = yaml.safe_load(manifest_value)
        except yaml.YAMLError as e:
            raise WaybillConfigurationError(
                f"Configuration manifest is not valid YAML: {e}"
            ) from e

        if not isinstance(parsed, dict):
            raise WaybillConfigurationError(
                "Configuration manifest did not parse to a mapping "
                f"(got {type(parsed).__name__})"
            )
        parsed_dict = cast(dict[Any, Any], parsed)
        parsed_mapping: dict[str, Any] = {str(k): v for k, v in parsed_dict.items()}

        try:
            self.configuration = WaybillConfig(**parsed_mapping)
        except TypeError as e:
            raise WaybillConfigurationError(
                f"Configuration manifest shape is invalid: {e}"
            ) from e
        except ValueError as e:
            raise WaybillConfigurationError(
                f"Configuration manifest failed invariant checks: {e}"
            ) from e

        return None

    def _run_plan(self, logger: WaybillLogger) -> None:
        pipeline = WaybillPipeline(self.configuration)
        plan = pipeline.compute_plan()
        formatter = WaybillPlanFormatter()
        for line in formatter.format(plan):
            logger.info(line)
        if plan.has_failures():
            fail_count = sum(
                1
                for profile in plan.profiles
                for group in profile.groups
                for member in group.members
                for v in member.violations
                if v.action == "fail"
            )
            raise WaybillValidationError(
                f"Validation failed: {fail_count} fail violation(s) detected"
            )

    def _run_apply(
        self,
        logger: WaybillLogger,
        params: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> None:
        settings = context.get("settings", {})
        apply_mode: Any = None
        if isinstance(settings, dict):
            settings_dict = cast(dict[str, Any], settings)
            apply_mode = settings_dict.get("apply_mode")
        param_mode = params.get("mode")
        mode = (
            (param_mode if isinstance(param_mode, str) and param_mode else None)
            or (apply_mode if isinstance(apply_mode, str) and apply_mode else None)
            or "upsert"
        )
        pipeline = WaybillPipeline(self.configuration)
        plan = pipeline.compute_plan()
        if plan.has_failures():
            fail_count = sum(
                1
                for profile in plan.profiles
                for group in profile.groups
                for member in group.members
                for v in member.violations
                if v.action == "fail"
            )
            formatter = WaybillPlanFormatter()
            for line in formatter.format(plan):
                logger.error(line)
            raise WaybillValidationError(
                f"Validation failed: {fail_count} fail violation(s) detected — database not modified"
            )
        applier = WaybillApplier(plan, mode, logger)
        with scheduler.apply_lock():
            applier.apply()

    def run(self, action: str, params: dict[str, Any], context: dict[str, Any]) -> None:
        self._start_scheduler()
        self._load_configuration(context)
        logger = self._require_logger(context)

        if action == "plan":
            self._run_plan(logger)
            return None

        if action == "apply":
            self._run_apply(logger, params, context)
            return None

        logger.warning(f"Unknown action: {action!r}")
        raise WaybillActionError(f"Unknown action: {action!r}")
