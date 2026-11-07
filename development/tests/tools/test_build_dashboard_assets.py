from __future__ import annotations

import json
from pathlib import Path

import pytest

import tools.build_dashboard_assets as dashboard_assets


def _configure(monkeypatch: pytest.MonkeyPatch, root: Path) -> tuple[Path, Path]:
    source = root / "development/tools" / "project-dashboard" / "dist"
    destination = root / "src" / "eva" / "adapters" / "api" / "dashboard_static"
    source.mkdir(parents=True)
    package_lock = source.parent / "package-lock.json"
    (source.parent / "package.json").write_text(
        json.dumps({"dependencies": {"react-admin": "5.15.5"}}), encoding="utf-8"
    )
    package_lock.write_text(
        json.dumps({"lockfileVersion": 3, "packages": {
            "": {"dependencies": {"react-admin": "5.15.5"}},
            "node_modules/react-admin": {"version": "5.15.5"},
        }}), encoding="utf-8"
    )
    (source / "assets").mkdir()
    (source / "index.html").write_text("<main>Eva</main>\n", encoding="utf-8")
    (source / "assets" / "app.js").write_text("export default 'eva';\n", encoding="utf-8")
    (source / "assets" / "app.css").write_text("body{margin:0}\n", encoding="utf-8")
    (source / "asset-manifest.json").write_text(
        json.dumps(
            {
                "index.html": {
                    "file": "assets/app.js",
                    "css": ["assets/app.css"],
                    "isEntry": True,
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(dashboard_assets, "ROOT", root)
    monkeypatch.setattr(dashboard_assets, "SOURCE", source)
    monkeypatch.setattr(dashboard_assets, "DESTINATION", destination)
    return source, destination


def test_materialize_and_check_exact_dashboard_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _source, destination = _configure(monkeypatch, tmp_path)

    dashboard_assets.materialize()
    dashboard_assets.check()

    assert {
        path.relative_to(destination).as_posix()
        for path in destination.rglob("*")
        if path.is_file()
    } == {
        "assets/app.css",
        "assets/app.js",
        "eva-dashboard-assets.json",
        "index.html",
    }
    manifest = json.loads(
        (destination / "eva-dashboard-assets.json").read_text(encoding="utf-8")
    )
    assert manifest["contract"] == "EvaDashboardAssets/v1"
    assert manifest["react_admin"] == "5.15.5"
    assert set(manifest["files"]) == {
        "assets/app.css",
        "assets/app.js",
        "index.html",
    }


@pytest.mark.parametrize("drift", ["changed", "extra", "missing"])
def test_check_rejects_packaged_asset_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drift: str
) -> None:
    _source, destination = _configure(monkeypatch, tmp_path)
    dashboard_assets.materialize()
    if drift == "changed":
        (destination / "assets" / "app.js").write_text("changed\n", encoding="utf-8")
    elif drift == "extra":
        (destination / "extra.js").write_text("extra\n", encoding="utf-8")
    else:
        (destination / "index.html").unlink()

    with pytest.raises(RuntimeError, match="dashboard package assets differ"):
        dashboard_assets.check()


def test_source_manifest_rejects_unsafe_asset_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _destination = _configure(monkeypatch, tmp_path)
    (source / "asset-manifest.json").write_text(
        json.dumps(
            {
                "index.html": {
                    "file": "../outside.js",
                    "css": ["assets/app.css"],
                    "isEntry": True,
                }
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="unsafe path"):
        dashboard_assets.materialize()


def test_check_rejects_symlink_in_packaged_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _source, destination = _configure(monkeypatch, tmp_path)
    dashboard_assets.materialize()
    (destination / "assets" / "link.js").symlink_to(destination / "index.html")

    with pytest.raises(RuntimeError, match="contain a link"):
        dashboard_assets.check()


def test_dashboard_upgrade_replaces_old_bundle_without_stale_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, destination = _configure(monkeypatch, tmp_path)
    dashboard_assets.materialize()
    (source / "assets" / "app.js").rename(source / "assets" / "memory.js")
    updated = "export default 'Jouw geheugenbronnen';\n"
    (source / "assets" / "memory.js").write_text(updated, encoding="utf-8")
    manifest_path = source / "asset-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["index.html"]["file"] = "assets/memory.js"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    dashboard_assets.materialize()
    dashboard_assets.check()

    assert not (destination / "assets" / "app.js").exists()
    assert (destination / "assets" / "memory.js").read_text(encoding="utf-8") == updated
    packaged = json.loads((destination / "eva-dashboard-assets.json").read_text())
    assert "assets/memory.js" in packaged["files"]
    assert "assets/app.js" not in packaged["files"]


@pytest.mark.parametrize("drift", ["missing", "range", "resolved_version"])
def test_build_rejects_unbound_react_admin_version(tmp_path, monkeypatch, drift):
    source, _destination = _configure(monkeypatch, tmp_path)
    lock_path = source.parent / "package-lock.json"
    lock = json.loads(lock_path.read_text())
    if drift == "missing":
        del lock["packages"]["node_modules/react-admin"]
    elif drift == "range":
        lock["packages"][""]["dependencies"]["react-admin"] = "^5.15.5"
    else:
        lock["packages"]["node_modules/react-admin"]["version"] = "5.15.1"
    lock_path.write_text(json.dumps(lock))
    with pytest.raises(RuntimeError, match="dependency evidence"):
        dashboard_assets.materialize()


def _load_runtime_from_packaged_assets(monkeypatch, destination):
    import importlib.resources
    import importlib.util
    import sys

    runtime_path = Path(dashboard_assets.__file__).parents[1] / "eva/adapters/api/dashboard_assets.py"
    spec = importlib.util.spec_from_file_location("dashboard_asset_contract_probe", runtime_path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    with monkeypatch.context() as context:
        context.setattr(importlib.resources, "files", lambda _name: destination.parent)
        spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("version", ["5.15.1", "5.15.5"])
def test_runtime_accepts_hash_verified_version_evidence(tmp_path, monkeypatch, version):
    _source, destination = _configure(monkeypatch, tmp_path)
    dashboard_assets.materialize()
    manifest_path = destination / "eva-dashboard-assets.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["react_admin"] = version
    manifest_path.write_text(json.dumps(manifest))
    runtime = _load_runtime_from_packaged_assets(monkeypatch, destination)
    assert runtime.DASHBOARD_ENTRY == "index.html"
    assert len(runtime.DASHBOARD_ASSETS) == 3


@pytest.mark.parametrize("field,value", [
    ("react_admin", None), ("react_admin", True), ("react_admin", "5.15"),
    ("package_lock_sha256", "invalid"),
])
def test_runtime_rejects_invalid_dependency_evidence(tmp_path, monkeypatch, field, value):
    _source, destination = _configure(monkeypatch, tmp_path)
    dashboard_assets.materialize()
    manifest_path = destination / "eva-dashboard-assets.json"
    manifest = json.loads(manifest_path.read_text())
    manifest[field] = value
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(RuntimeError, match="invalid contract"):
        _load_runtime_from_packaged_assets(monkeypatch, destination)


def test_runtime_keeps_asset_hash_enforcement_across_library_update(tmp_path, monkeypatch):
    _source, destination = _configure(monkeypatch, tmp_path)
    dashboard_assets.materialize()
    (destination / "assets/app.js").write_text("tampered")
    with pytest.raises(RuntimeError, match="differs from its manifest"):
        _load_runtime_from_packaged_assets(monkeypatch, destination)
