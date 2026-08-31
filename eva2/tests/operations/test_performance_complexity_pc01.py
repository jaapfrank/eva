from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest


TOOLS = Path(__file__).resolve().parents[2] / "tools"
sys.path.insert(0, str(TOOLS))
import check_performance_complexity_pc01 as pc01  # noqa: E402


DIGEST = "sha256:" + "1" * 64
CONFIG = "sha256:" + "2" * 64


def _run(*args: str, cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


def _authority() -> dict[str, object]:
    return {
        "contract": pc01.CONTRACT,
        "source": {
            "extensions": [".py", ".rs", ".ts", ".tsx"],
            "productRoots": ["src/eva"],
            "classifiedNonProductRoots": ["tests"],
            "generatedExactPaths": ["src/eva/generated.py"],
            "livingCoreRoot": "src/eva",
        },
        "dependencies": {
            "source": "pyproject.toml",
            "expected": ["alpha"],
        },
        "preVoiceBrowserCoreImageBaseline": {
            "activeContainer": "core",
            "platform": "linux/amd64",
            "manifestDigest": DIGEST,
            "configDigest": CONFIG,
            "compressedLayerBytes": 12,
            "layerCount": 2,
            "sourceCommit": "a" * 40,
            "sourceEvidence": "evidence.json",
            "sourceEvidenceSha256": "pending",
        },
    }


def _repository(tmp_path: Path) -> tuple[Path, Path, Path]:
    repo = tmp_path / "repo"
    project = repo / "eva2"
    (project / "src/eva").mkdir(parents=True)
    (project / "tests").mkdir()
    (project / "src/eva/core.py").write_text("one = 1\ntwo = 2\n", encoding="utf-8")
    (project / "src/eva/generated.py").write_text(
        "generated = True\n", encoding="utf-8"
    )
    (project / "tests/test_core.py").write_text(
        "def test_ok():\n    pass\n", encoding="utf-8"
    )
    (project / "pyproject.toml").write_text(
        '[project]\nname="fixture"\nversion="0"\ndependencies=["alpha==1"]\n',
        encoding="utf-8",
    )
    evidence = {
        "build": {"sourceCommit": "a" * 40},
        "image": {"id": DIGEST},
    }
    evidence_raw = json.dumps(evidence).encode()
    (project / "evidence.json").write_bytes(evidence_raw)
    authority = _authority()
    authority["preVoiceBrowserCoreImageBaseline"]["sourceEvidenceSha256"] = (
        pc01._sha256(evidence_raw)
    )
    authority_path = project / "authority.json"
    authority_path.write_text(json.dumps(authority), encoding="utf-8")
    _run("git", "init", "-q", cwd=repo)
    _run("git", "config", "user.email", "fixture@example.invalid", cwd=repo)
    _run("git", "config", "user.name", "Fixture", cwd=repo)
    _run("git", "add", "eva2", cwd=repo)
    _run("git", "commit", "-qm", "fixture", cwd=repo)
    return repo, project, authority_path


def _image_ok(*_args: object, **_kwargs: object) -> tuple[dict[str, object], list[str]]:
    return {"manifestDigest": DIGEST}, []


def test_pc01_passes_a_closed_small_source_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, project, authority = _repository(tmp_path)
    monkeypatch.setattr(pc01, "_measure_image", _image_ok)

    receipt = pc01.check(repo, project, authority)

    assert receipt["status"] == "passed"
    assert receipt["source"]["product"]["physicalLines"] == 2
    assert receipt["source"]["generated"]["physicalLines"] == 1
    assert receipt["dependencies"]["names"] == ["alpha"]


def test_pc01_measures_a_large_source_tree_without_an_arbitrary_line_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, project, authority = _repository(tmp_path)
    (project / "src/eva/core.py").write_text(
        "\n".join(f"value_{index} = {index}" for index in range(10_000)) + "\n",
        encoding="utf-8",
    )
    _run("git", "add", ".", cwd=repo)
    _run("git", "commit", "-qm", "large but classified", cwd=repo)
    monkeypatch.setattr(pc01, "_measure_image", _image_ok)

    receipt = pc01.check(repo, project, authority)

    assert receipt["status"] == "passed"
    assert receipt["source"]["product"]["physicalLines"] == 10_000
    assert receipt["violations"] == []


def test_pc01_refuses_an_unclassified_source_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, project, authority = _repository(tmp_path)
    (project / "rogue").mkdir()
    (project / "rogue/hidden.py").write_text("hidden = True\n", encoding="utf-8")
    _run("git", "add", ".", cwd=repo)
    _run("git", "commit", "-qm", "rogue", cwd=repo)
    monkeypatch.setattr(pc01, "_measure_image", _image_ok)

    receipt = pc01.check(repo, project, authority)

    assert "unclassified_source_root" in receipt["violations"]
    assert receipt["source"]["unclassifiedSourcePaths"] == ["rogue/hidden.py"]


def test_pc01_refuses_direct_dependency_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, project, authority = _repository(tmp_path)
    (project / "pyproject.toml").write_text(
        '[project]\nname="fixture"\nversion="0"\ndependencies=["alpha==1","beta==1"]\n',
        encoding="utf-8",
    )
    _run("git", "add", ".", cwd=repo)
    _run("git", "commit", "-qm", "dependency drift", cwd=repo)
    monkeypatch.setattr(pc01, "_measure_image", _image_ok)

    receipt = pc01.check(repo, project, authority)

    assert "direct_runtime_dependency_set_differs" in receipt["violations"]


def test_pc01_refuses_a_reordered_root_authority(tmp_path: Path) -> None:
    repo, project, authority_path = _repository(tmp_path)
    authority = json.loads(authority_path.read_text())
    authority["source"]["extensions"] = [".tsx", ".py", ".rs", ".ts"]
    authority_path.write_text(json.dumps(authority), encoding="utf-8")

    with pytest.raises(pc01.Pc01Error, match="extensions must be"):
        pc01.check(repo, project, authority_path)


def test_pc01_measures_exact_oci_descriptors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _repo, project, authority_path = _repository(tmp_path)
    authority = json.loads(authority_path.read_text())[
        "preVoiceBrowserCoreImageBaseline"
    ]

    def fake_run(arguments: list[str], **_kwargs: object) -> SimpleNamespace:
        if arguments[:2] == ["docker", "inspect"]:
            raw = [{"Image": DIGEST, "State": {"Running": True}}]
        else:
            raw = {
                "config": {"digest": CONFIG},
                "layers": [{"size": 5}, {"size": 7}],
            }
        return SimpleNamespace(returncode=0, stdout=json.dumps(raw).encode())

    monkeypatch.setattr(pc01.subprocess, "run", fake_run)

    observed, violations = pc01._measure_image(project, authority)

    assert violations == []
    assert observed["compressedLayerBytes"] == 12


def test_pc01_detects_changed_oci_layer_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _repo, project, authority_path = _repository(tmp_path)
    authority = json.loads(authority_path.read_text())[
        "preVoiceBrowserCoreImageBaseline"
    ]

    def fake_run(arguments: list[str], **_kwargs: object) -> SimpleNamespace:
        if arguments[:2] == ["docker", "inspect"]:
            raw = [{"Image": DIGEST, "State": {"Running": True}}]
        else:
            raw = {
                "config": {"digest": CONFIG},
                "layers": [{"size": 5}, {"size": 8}],
            }
        return SimpleNamespace(returncode=0, stdout=json.dumps(raw).encode())

    monkeypatch.setattr(pc01.subprocess, "run", fake_run)

    _observed, violations = pc01._measure_image(project, authority)

    assert violations == ["core_image_compressed_bytes_differ"]


def test_pc01_resolves_one_exact_platform_manifest_from_an_oci_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _repo, project, authority_path = _repository(tmp_path)
    authority = json.loads(authority_path.read_text())[
        "preVoiceBrowserCoreImageBaseline"
    ]
    platform_manifest = "sha256:" + "3" * 64

    def fake_run(arguments: list[str], **_kwargs: object) -> SimpleNamespace:
        if arguments[:2] == ["docker", "inspect"]:
            raw = [{"Image": DIGEST, "State": {"Running": True}}]
        elif arguments[-1] == DIGEST:
            raw = {
                "mediaType": "application/vnd.oci.image.index.v1+json",
                "manifests": [
                    {
                        "digest": platform_manifest,
                        "platform": {"architecture": "amd64", "os": "linux"},
                    }
                ],
            }
        else:
            assert arguments[-1] == platform_manifest
            raw = {"config": {"digest": CONFIG}, "layers": [{"size": 5}, {"size": 7}]}
        return SimpleNamespace(returncode=0, stdout=json.dumps(raw).encode())

    monkeypatch.setattr(pc01.subprocess, "run", fake_run)

    observed, violations = pc01._measure_image(project, authority)

    assert violations == []
    assert observed["compressedLayerBytes"] == 12
