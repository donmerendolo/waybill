from __future__ import annotations

import importlib
import importlib.util
import pathlib
import sys
import types
import uuid

import pytest


def _load_plugin_module_with_stubs() -> types.ModuleType:
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    plugin_path = repo_root / "plugin.py"

    package_name = f"waybill_testpkg_{uuid.uuid4().hex}"
    package_mod = types.ModuleType(package_name)
    package_mod.__path__ = [str(repo_root)]
    sys.modules[package_name] = package_mod

    # Alias real src modules under a synthetic package so plugin's relative imports resolve.
    for module_name in (
        "src",
        "src.types",
        "src.types.config",
        "src.types.plugins",
    ):
        real_mod = importlib.import_module(module_name)
        synthetic_name = f"{package_name}.{module_name}"
        sys.modules[synthetic_name] = real_mod

    # Stub modules that require the full Dispatcharr/Django runtime.
    dacite_mod = types.ModuleType("dacite")

    class Config:
        def __init__(self, cast: list[object] | None = None) -> None:
            self.cast = cast or []

    def from_dict(
        data_class: type[object], data: dict[str, object], config: object | None = None
    ) -> object:
        del config
        return data_class(**data)

    dacite_mod.Config = Config
    dacite_mod.from_dict = from_dict
    sys.modules[dacite_mod.__name__] = dacite_mod

    pipeline_mod = types.ModuleType(f"{package_name}.src.pipeline")

    class WaybillPipeline:
        def __init__(self, configuration: object) -> None:
            self.configuration = configuration

        def compute_plan(self) -> object:
            return {"plan": True}

    class WaybillPlanFormatter:
        def format(self, plan: object) -> list[str]:
            return [f"formatted={bool(plan)}"]

    pipeline_mod.WaybillPipeline = WaybillPipeline
    pipeline_mod.WaybillPlanFormatter = WaybillPlanFormatter
    sys.modules[pipeline_mod.__name__] = pipeline_mod

    apply_mod = types.ModuleType(f"{package_name}.src.apply")

    class WaybillApplier:
        def __init__(self, plan: object, mode: str, logger: object) -> None:
            self.plan = plan
            self.mode = mode
            self.logger = logger

        def apply(self) -> dict[str, int]:
            return {}

    apply_mod.WaybillApplier = WaybillApplier
    sys.modules[apply_mod.__name__] = apply_mod

    spec = importlib.util.spec_from_file_location(f"{package_name}.plugin", plugin_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _GoodLogger:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def info(self, message: str) -> None:
        self.messages.append(f"INFO:{message}")

    def warning(self, message: str) -> None:
        self.messages.append(f"WARN:{message}")

    def error(self, message: str) -> None:
        self.messages.append(f"ERR:{message}")


def _valid_manifest() -> str:
    return "\n".join(
        [
            "kind: WaybillConfig",
            "version: v1alpha1",
            "metadata:",
            "  name: test-manifest",
            "spec:",
            "  profiles: {}",
        ]
    )


def test_require_logger_rejects_missing_logger() -> None:
    module = _load_plugin_module_with_stubs()
    plugin = module.Plugin.__new__(module.Plugin)

    with pytest.raises(module.WaybillContextError, match="missing a logger"):
        plugin._require_logger({})


def test_load_configuration_rejects_invalid_yaml() -> None:
    module = _load_plugin_module_with_stubs()
    plugin = module.Plugin.__new__(module.Plugin)

    with pytest.raises(module.WaybillConfigurationError, match="not valid YAML"):
        plugin._load_configuration({"settings": {"manifest": "kind: ["}})


def test_load_configuration_enforces_manifest_invariants() -> None:
    module = _load_plugin_module_with_stubs()
    plugin = module.Plugin.__new__(module.Plugin)

    manifest = "\n".join(
        [
            "kind: NotWaybill",
            "version: v1alpha1",
            "metadata:",
            "  name: test-manifest",
            "spec:",
            "  profiles: {}",
        ]
    )

    with pytest.raises(module.WaybillConfigurationError, match="invariant checks"):
        plugin._load_configuration({"settings": {"manifest": manifest}})


def test_run_raises_for_unknown_action() -> None:
    module = _load_plugin_module_with_stubs()
    plugin = module.Plugin.__new__(module.Plugin)
    logger = _GoodLogger()

    with pytest.raises(module.WaybillActionError, match="Unknown action"):
        plugin.run(
            action="unknown",
            params={},
            context={
                "logger": logger,
                "settings": {"manifest": _valid_manifest()},
            },
        )

    assert any("WARN:Unknown action" in msg for msg in logger.messages)


def test_load_logs_where_the_manifest_came_from() -> None:
    module = _load_plugin_module_with_stubs()
    plugin = module.Plugin.__new__(module.Plugin)
    logger = _GoodLogger()

    plugin._load_configuration({"settings": {"manifest": _valid_manifest()}}, logger)

    assert logger.messages == ["INFO:Manifest: from the plugin settings"]


def test_manifest_url_failure_changes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_plugin_module_with_stubs()
    plugin = module.Plugin.__new__(module.Plugin)
    source_mod = module.manifest_source

    def failing_resolve(settings):
        raise source_mod.ManifestSourceError(
            "Could not download the manifest from x: boom"
        )

    monkeypatch.setattr(source_mod, "resolve", failing_resolve)
    with pytest.raises(
        module.WaybillConfigurationError, match="boom — nothing was changed"
    ):
        plugin.run(
            action="apply",
            params={},
            context={
                "logger": _GoodLogger(),
                "settings": {"manifest_url": "https://x/w.yaml"},
            },
        )
