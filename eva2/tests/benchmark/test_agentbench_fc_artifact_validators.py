from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import stat

import pytest


ROOT = Path(__file__).resolve().parents[3]


def _load_tool(name: str):
    source = ROOT / "benchmark" / name
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    ) + b"\n"


def _worker_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    tool = _load_tool("check_agentbench_fc_worker_wheelhouse.py")
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir(parents=True)
    wheel = wheelhouse / "alpha-1.0-py3-none-any.whl"
    wheel.write_bytes(b"alpha-wheel")
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    lock = tmp_path / "requirements.lock"
    lock.write_text(f"alpha==1.0 --hash=sha256:{digest}\n", encoding="utf-8")
    requirements_in = tmp_path / "requirements.in"
    requirements_in.write_text("alpha==1.0\n", encoding="utf-8")
    monkeypatch.setattr(tool, "LOCK", lock)
    monkeypatch.setattr(tool, "INPUT", requirements_in)
    manifest = tmp_path / "wheelhouse.json"
    manifest.write_bytes(_canonical(tool._manifest(wheelhouse)))
    return tool, wheelhouse, wheel, lock, manifest


def test_worker_wheelhouse_validator_accepts_exact_local_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool, wheelhouse, _, _, manifest = _worker_fixture(tmp_path, monkeypatch)

    assert tool.validate(wheelhouse, manifest)["artifacts"] == [
        {
            "byteLength": len(b"alpha-wheel"),
            "filename": "alpha-1.0-py3-none-any.whl",
            "sha256": hashlib.sha256(b"alpha-wheel").hexdigest(),
        }
    ]


@pytest.mark.parametrize("mutation", ["missing", "extra", "tampered", "source"])
def test_worker_wheelhouse_validator_refuses_changed_artifact_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    tool, wheelhouse, wheel, _, manifest = _worker_fixture(tmp_path, monkeypatch)
    if mutation == "missing":
        wheel.unlink()
    elif mutation == "extra":
        (wheelhouse / "extra-1.0-py3-none-any.whl").write_bytes(b"extra")
    elif mutation == "tampered":
        wheel.write_bytes(b"tampered")
    else:
        wheel.unlink()
        (wheelhouse / "alpha-1.0.tar.gz").write_bytes(b"source")

    with pytest.raises(RuntimeError):
        tool.validate(wheelhouse, manifest)


def test_worker_wheelhouse_validator_refuses_symlink_manifest_and_lock_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool, wheelhouse, wheel, lock, manifest = _worker_fixture(tmp_path, monkeypatch)
    wheel.unlink()
    wheel.symlink_to(wheelhouse / "does-not-matter.whl")
    with pytest.raises(RuntimeError, match="unsafe worker wheelhouse entry"):
        tool.validate(wheelhouse, manifest)

    wheel.unlink()
    wheel.write_bytes(b"alpha-wheel")
    manifest.write_bytes(manifest.read_bytes() + b" ")
    with pytest.raises(RuntimeError, match="not canonical"):
        tool.validate(wheelhouse, manifest)

    manifest.write_bytes(_canonical(tool._manifest(wheelhouse)))
    lock.write_text(lock.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="manifest differs"):
        tool.validate(wheelhouse, manifest)


def _controller_fixture(tmp_path: Path):
    tool = _load_tool("check_agentbench_fc_controller_inputs.py")
    inputs = tmp_path / "inputs"
    inputs.mkdir(parents=True)
    entries = {
        "agentrl-controller-gomodcache.tar": b"module-cache",
        "protoc-31.1-linux-x86_64.zip": b"protoc",
        "protoc-gen-go": b"go-generator",
        "protoc-gen-go-grpc": b"grpc-generator",
    }
    for name, content in entries.items():
        (inputs / name).write_bytes(content)
    manifest = tmp_path / "inputs.json"
    manifest.write_bytes(_canonical(tool.build_manifest(inputs)))
    return tool, inputs, entries, manifest


def test_controller_input_validator_accepts_exact_local_bytes(tmp_path: Path) -> None:
    tool, inputs, entries, manifest = _controller_fixture(tmp_path)

    checked = tool.validate(inputs, manifest)
    assert [entry["filename"] for entry in checked["artifacts"]] == sorted(entries)


@pytest.mark.parametrize("mutation", ["missing", "extra", "tampered"])
def test_controller_input_validator_refuses_changed_artifact_set(
    tmp_path: Path, mutation: str
) -> None:
    tool, inputs, entries, manifest = _controller_fixture(tmp_path)
    target = inputs / "protoc-gen-go"
    if mutation == "missing":
        target.unlink()
    elif mutation == "extra":
        (inputs / "surprise").write_bytes(b"surprise")
    else:
        target.write_bytes(b"changed")

    with pytest.raises(RuntimeError):
        tool.validate(inputs, manifest)


def test_controller_input_validator_refuses_symlink_and_noncanonical_manifest(tmp_path: Path) -> None:
    tool, inputs, entries, manifest = _controller_fixture(tmp_path)
    target = inputs / "protoc-gen-go"
    target.unlink()
    target.symlink_to(inputs / "protoc-gen-go-grpc")
    with pytest.raises(RuntimeError, match="unsafe controller build input"):
        tool.validate(inputs, manifest)

    target.unlink()
    target.write_bytes(entries["protoc-gen-go"])
    manifest.write_bytes(manifest.read_bytes() + b"\n")
    with pytest.raises(RuntimeError, match="not canonical"):
        tool.validate(inputs, manifest)


def test_validators_reject_writable_materialized_artifacts_when_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worker_tool, wheelhouse, wheel, _, manifest = _worker_fixture(tmp_path / "worker", monkeypatch)
    controller_tool, inputs, _, controller_manifest = _controller_fixture(tmp_path / "controller")
    wheel.chmod(stat.S_IRUSR | stat.S_IWUSR)
    (inputs / "protoc").mkdir()

    with pytest.raises(RuntimeError, match="not read-only"):
        worker_tool.validate(wheelhouse, manifest, require_read_only=True)
    with pytest.raises(RuntimeError, match="unsafe controller build input"):
        controller_tool.validate(inputs, controller_manifest)
