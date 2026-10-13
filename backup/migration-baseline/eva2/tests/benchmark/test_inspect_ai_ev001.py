from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
PROBE_PATH = ROOT / "benchmark/inspect_ai_v1/bridge_probe.py"
SPEC = importlib.util.spec_from_file_location("eva2_inspect_bridge_probe", PROBE_PATH)
assert SPEC is not None and SPEC.loader is not None
probe = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = probe
SPEC.loader.exec_module(probe)


def test_exact_evaluation_lock_is_closed_and_separate_from_runtime() -> None:
    result = probe.verify_lock()

    assert result == {
        "lockSha256": "2826d5d25d138b81add376819b6ca73f27caba2df02ff74bd8e80c0a3fef5f5e",
        "packageCount": 86,
        "inspectVersion": "0.3.263",
        "openaiVersion": "3.6.0",
        "python": "==3.12.*",
        "environment": "sys_platform == 'linux' and platform_machine == 'x86_64'",
    }
    runtime_project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "inspect-ai" not in runtime_project


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ('lock-version = "1.0"', 'lock-version = "0.9"', "lock-version"),
        ('version = "0.3.263"', 'version = "0.3.262"', "Inspect version"),
        (
            "503e7ff509d77fcdf65989027a19fea966fb668deb88a234468c3290964fd86a",
            "0" * 64,
            "Inspect version",
        ),
        ("https://files.pythonhosted.org/", "https://example.invalid/", "trusted"),
    ],
)
def test_lock_drift_fails_closed(
    tmp_path: Path, old: str, new: str, message: str
) -> None:
    changed = probe.LOCK_PATH.read_text(encoding="utf-8").replace(old, new, 1)
    path = tmp_path / "pylock.toml"
    path.write_text(changed, encoding="utf-8")

    with pytest.raises(RuntimeError, match=message):
        probe.verify_lock(path)


def test_probe_refuses_to_mix_results(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        probe,
        "verify_source_checkout",
        lambda commit: {"commit": commit, "tree": "b" * 40, "files": {}},
    )
    monkeypatch.setattr(probe, "_verify_runtime", lambda: None)
    output = tmp_path / "existing"
    output.mkdir()
    (output / "old.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="absent or empty"):
        probe.run_probe(output, probe.DEFAULT_SANDBOX_IMAGE, "a" * 40)


def test_probe_requires_an_exact_source_commit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with pytest.raises(RuntimeError, match="exact lowercase Git"):
        probe.run_probe(tmp_path / "output", probe.DEFAULT_SANDBOX_IMAGE, "main")


def test_probe_requires_the_current_source_commit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(probe, "_verify_runtime", lambda: None)
    monkeypatch.setattr(probe, "current_source_commit", lambda: "b" * 40)

    with pytest.raises(RuntimeError, match="does not match"):
        probe.run_probe(tmp_path / "output", probe.DEFAULT_SANDBOX_IMAGE, "a" * 40)
    assert not (tmp_path / "output").exists()


def test_source_binding_rejects_dirty_tracked_bytes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    local = tmp_path / "probe.py"
    local.write_bytes(b"dirty")
    monkeypatch.setattr(probe, "current_source_commit", lambda: "a" * 40)
    monkeypatch.setattr(probe, "_git_blob", lambda *_args: b"committed")

    with pytest.raises(RuntimeError, match="differs from Git"):
        probe.verify_source_checkout("a" * 40, {"eva2/probe.py": local})


def test_source_binding_records_tree_and_file_hashes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    local = tmp_path / "probe.py"
    local.write_bytes(b"committed")
    monkeypatch.setattr(probe, "current_source_commit", lambda: "a" * 40)
    monkeypatch.setattr(probe, "_git_blob", lambda *_args: b"committed")
    monkeypatch.setattr(probe, "_git_tree", lambda *_args: "b" * 40)

    assert probe.verify_source_checkout("a" * 40, {"eva2/probe.py": local}) == {
        "commit": "a" * 40,
        "tree": "b" * 40,
        "files": {"eva2/probe.py": probe.bytes_sha256(b"committed")},
    }


def test_docker_cleanup_is_measured_and_residuals_fail_closed() -> None:
    before = {"containers": ["c1"], "networks": ["n1"], "volumes": ["v1"]}
    clean = probe.verified_docker_cleanup(before, before)
    assert clean["residualAddedCount"] == 0
    assert clean["added"] == {"containers": [], "networks": [], "volumes": []}

    after = {**before, "containers": ["c1", "c2"]}
    with pytest.raises(RuntimeError, match="left Docker objects"):
        probe.verified_docker_cleanup(before, after)


def test_only_local_log_locations_are_hashed() -> None:
    assert probe._local_log_path("file:///tmp/example.eval") == Path(
        "/tmp/example.eval"
    )
    with pytest.raises(RuntimeError, match="not a local file"):
        probe._local_log_path("https://example.invalid/result.eval")


def test_probe_contract_withholds_network_tools_and_paid_models() -> None:
    source = PROBE_PATH.read_text(encoding="utf-8")

    assert 'model="mockllm/model"' in source
    assert source.count("web_search=False") == 2
    assert source.count("code_execution=False") == 2
    assert source.count("client_mcp_servers=False") == 2
    assert 'network_mode="none"' in source
    assert 'pull_policy="never"' in source
    assert '"paidModelCalls": 0' in source
