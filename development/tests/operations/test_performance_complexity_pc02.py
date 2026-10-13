from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest


TOOLS = Path(__file__).resolve().parents[3] / "src/tools"
sys.path.insert(0, str(TOOLS))
import check_performance_complexity_pc02 as pc02  # noqa: E402


def _run(*args: str, cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    repo = tmp_path / "repo"
    project = repo / "eva2"
    source = project / "src" / "eva"
    source.mkdir(parents=True)
    hot = source / "hot.py"
    hot.write_text("x = 1\n" * 120, encoding="utf-8")
    (source / "small.py").write_text("x = 1\n", encoding="utf-8")
    authority = {
        "contract": pc02.AUTHORITY_CONTRACT,
        "extensions": [".py"],
        "generatedExactPaths": [],
        "hotPaths": [
            {
                "baselineSha256": pc02._sha256(hot.read_bytes()),
                "componentOwner": "fixture owner",
                "contract": "Fixture/v1",
                "path": "src/eva/hot.py",
            }
        ],
        "classificationThresholdPhysicalLines": 100,
        "productRoots": ["src/eva"],
    }
    authority_path = project / "authority.json"
    authority_path.write_text(json.dumps(authority), encoding="utf-8")
    _run("git", "init", "-q", cwd=repo)
    _run("git", "config", "user.email", "fixture@example.invalid", cwd=repo)
    _run("git", "config", "user.name", "Fixture", cwd=repo)
    _run("git", "add", "eva2", cwd=repo)
    _run("git", "commit", "-qm", "fixture", cwd=repo)
    return repo, project, authority_path


def test_pc02_passes_closed_owned_hot_paths(tmp_path: Path) -> None:
    repo, project, authority = _fixture(tmp_path)

    receipt = pc02.check(repo, project, authority)

    assert receipt["status"] == "passed"
    assert receipt["observedProductFiles"] == 2
    assert receipt["hotPaths"][0]["physicalLines"] == 120
    assert receipt["violations"] == []


def test_pc02_measures_growth_without_treating_it_as_a_quality_failure(
    tmp_path: Path,
) -> None:
    repo, project, authority = _fixture(tmp_path)
    with (project / "src/eva/hot.py").open("a", encoding="utf-8") as handle:
        handle.write("x = 2\n")

    receipt = pc02.check(repo, project, authority)

    assert receipt["status"] == "passed"
    assert receipt["hotPaths"][0]["physicalLines"] == 121
    assert receipt["violations"] == []


def test_pc02_rejects_new_unclassified_hot_path(tmp_path: Path) -> None:
    repo, project, authority = _fixture(tmp_path)
    (project / "src/eva/other.py").write_text("x = 2\n" * 101, encoding="utf-8")

    receipt = pc02.check(repo, project, authority)

    assert receipt["status"] == "failed"
    assert receipt["unclassifiedHotPaths"] == ["src/eva/other.py"]


def test_pc02_allows_content_change_without_line_growth(tmp_path: Path) -> None:
    repo, project, authority = _fixture(tmp_path)
    (project / "src/eva/hot.py").write_text("y = 2\n" * 120, encoding="utf-8")

    receipt = pc02.check(repo, project, authority)

    assert receipt["status"] == "passed"
    assert receipt["hotPaths"][0]["currentSha256"] != receipt["hotPaths"][0]["baselineSha256"]


def test_pc02_rejects_unknown_authority_field(tmp_path: Path) -> None:
    repo, project, authority = _fixture(tmp_path)
    value = json.loads(authority.read_text(encoding="utf-8"))
    value["surprise"] = True
    authority.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(pc02.Pc02Error, match="fields differ"):
        pc02.check(repo, project, authority)
