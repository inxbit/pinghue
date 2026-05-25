"""Launcher script regression tests."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


def _load_launcher_module() -> ModuleType:
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "pinghue"
    loader = importlib.machinery.SourceFileLoader("pinghue_launcher", str(module_path))
    spec = importlib.util.spec_from_loader("pinghue_launcher", loader)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _build_fake_pinghue_package(root: Path) -> Path:
    package_root = root / "src" / "pinghue"
    package_root.mkdir(parents=True)
    (package_root / "__init__.py").write_text("__version__ = \"0.0.0\"\n")
    (package_root / "cli.py").write_text(
        "def main() -> int:\n    return 123\n"
    )
    return package_root.parent.parent


def test_launcher_uses_editable_direct_url_for_missing_early_import(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    launcher = _load_launcher_module()
    fake_root = _build_fake_pinghue_package(tmp_path)
    repo_src = Path(__file__).resolve().parents[1] / "src"
    repo_src = repo_src.resolve()

    def _is_repo_source_entry(value: object) -> bool:
        if not isinstance(value, str):
            return False
        try:
            return Path(value).resolve() == repo_src
        except (OSError, RuntimeError, ValueError):
            return False

    def fake_distribution(_name: str) -> SimpleNamespace:
        return SimpleNamespace(
            read_text=lambda _file_name: json.dumps(
                {
                    "dir_info": {"editable": True},
                    "url": f"file://{fake_root}",
                }
            )
        )

    monkeypatch.setattr(launcher.metadata, "distribution", fake_distribution)
    monkeypatch.setattr(
        launcher.sys,
        "path",
        [path for path in launcher.sys.path if not _is_repo_source_entry(path)],
    )

    removed_modules: dict[str, object] = {
        name: launcher.sys.modules[name]
        for name in list(launcher.sys.modules)
        if name == "pinghue" or name.startswith("pinghue.")
    }
    for name in removed_modules:
        launcher.sys.modules.pop(name)

    def _raise_missing_pinghue() -> object:
        raise ModuleNotFoundError("No module named", name="pinghue")

    try:
        entrypoint = launcher._load_main_entrypoint(
            importer=_raise_missing_pinghue
        )
        assert entrypoint() == 123
    finally:
        launcher.sys.modules.update(removed_modules)


def test_launcher_shows_clear_error_for_unrecoverable_import_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    launcher = _load_launcher_module()

    monkeypatch.setattr(
        launcher.metadata,
        "distribution",
        lambda _name: (_ for _ in ()).throw(
            launcher.metadata.PackageNotFoundError("package not installed")
        ),
    )

    def _raise_missing_pinghue() -> object:
        raise ModuleNotFoundError("No module named", name="pinghue")

    monkeypatch.setattr(launcher, "_direct_import_main", _raise_missing_pinghue)

    exit_code = launcher.main()
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "pinghue: unable to start:" in captured.err
