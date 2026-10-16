from __future__ import annotations

import shutil
import subprocess
import tempfile
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).parent
VENDOR = ROOT / "src" / "vendor"

_INCLUDE = ("plugin.json", "plugin.py", "src")
_EXCLUDE_DIRS = frozenset({"__pycache__"})
# Compiled extensions are left out: the plugin must not depend on the build
# machine's Python version and CPU, and every vendored package (markupsafe,
# pyyaml) falls back to pure Python without them. A loaded .so also stays open
# in Dispatcharr's processes, and on some filesystems that blocks deleting the
# old plugin folder on update ("Failed to back up existing plugin").
_EXCLUDE_SUFFIXES = frozenset({".pyc", ".pyo", ".so", ".pyd", ".dylib"})


def _version() -> str:
    with open(ROOT / "pyproject.toml", "rb") as fh:
        return tomllib.load(fh)["project"]["version"]


def _clean_vendor() -> None:
    if VENDOR.exists():
        shutil.rmtree(VENDOR)
    VENDOR.mkdir(parents=True)


def _export_requirements(req_path: Path) -> None:
    subprocess.run(
        ["uv", "export", "--only-group", "bundle", "--no-hashes", "-o", str(req_path)],
        check=True,
        cwd=ROOT,
    )


def _vendor_dependencies(req_path: Path) -> None:
    subprocess.run(
        ["uv", "pip", "install", "--target", str(VENDOR), "-r", str(req_path)],
        check=True,
        cwd=ROOT,
    )


def _prepare_vendor() -> None:
    _clean_vendor()
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as fh:
        req_path = Path(fh.name)
    try:
        _export_requirements(req_path)
        _vendor_dependencies(req_path)
    finally:
        req_path.unlink(missing_ok=True)


def _write_zip(out_dir: Path, filename: str) -> None:
    with zipfile.ZipFile(out_dir / filename, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in _INCLUDE:
            path = ROOT / name
            if path.is_file():
                zf.write(path, name)
            elif path.is_dir():
                for f in sorted(path.rglob("*")):
                    if not f.is_file():
                        continue
                    if any(part in _EXCLUDE_DIRS for part in f.parts):
                        continue
                    if f.suffix in _EXCLUDE_SUFFIXES:
                        continue
                    zf.write(f, f.relative_to(ROOT))


def get_requires_for_build_wheel(config_settings=None) -> list[None]:
    return []


def get_requires_for_build_editable(config_settings=None) -> list[None]:
    return []


def build_editable(wheel_directory, config_settings=None, metadata_directory=None):
    version = _version()
    whl_name = f"waybill-{version}-py3-none-any.whl"
    out_dir = Path(wheel_directory)

    with zipfile.ZipFile(out_dir / whl_name, "w", zipfile.ZIP_DEFLATED) as zf:
        # Write a .pth file so the src/ directory is on sys.path
        pth_content = str(ROOT / "src")
        zf.writestr("waybill.pth", pth_content + "\n")

        # Write minimal dist-info
        dist_info = f"waybill-{version}.dist-info"
        zf.writestr(
            f"{dist_info}/METADATA",
            f"Metadata-Version: 2.1\nName: waybill\nVersion: {version}\n",
        )
        zf.writestr(
            f"{dist_info}/WHEEL",
            "Wheel-Version: 1.0\nGenerator: _build_backend\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )
        zf.writestr(f"{dist_info}/RECORD", "")

    return whl_name


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    version = _version()
    whl_name = f"waybill-{version}-py3-none-any.whl"
    zip_name = f"waybill-{version}-py3-none-any.zip"
    out_dir = Path(wheel_directory)

    _prepare_vendor()
    _write_zip(out_dir, whl_name)

    shutil.copy2(out_dir / whl_name, out_dir / zip_name)

    return whl_name


def build_sdist(sdist_directory, config_settings=None) -> NotImplementedError:
    raise NotImplementedError(
        "waybill does not produce a source distribution — use `uv build --wheel`."
    )
