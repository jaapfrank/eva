from __future__ import annotations

import difflib
import json

import pytest

from tools import import_component_release as importer


CONTEXT_PATH = "adapters/postgres/delegated_work_actions.py"
OLD_PATHS = (
    "contracts/timestamps.py",
    "contracts/canonical_json.py",
    "application/cancellation.py",
)
BASE = """# preserved module comment
import json

class PostgresDelegatedWorkActions:
    @existing_decorator
    def active_context(self, turn: ConversationTurn) -> tuple[str, ...]:
        \"\"\"Existing body documentation.\"\"\"
        # padding
        return ("before",)

    def other_method(self):
        return "unchanged"

# preserved trailing comment
"""


def sized_source(size, *, after=False):
    source = BASE.replace('"before"', '"after"') if after else BASE
    padding = size - len(source.encode("utf-8"))
    assert padding >= 0
    return source.replace("# padding", "# padding" + "x" * padding)


def save_package(release):
    package, review = release["package"], release["review"]
    package["candidate_package_sha256"] = importer.digest(importer.canonical(
        {key: value for key, value in package.items() if key != "candidate_package_sha256"}))
    review["candidate_package_sha256"] = package["candidate_package_sha256"]
    release["package_file"].write_text(json.dumps(package))
    release["review_file"].write_text(json.dumps(review))


@pytest.fixture
def release_factory(tmp_path, monkeypatch):
    def make(baseline=BASE, candidate=None, path=CONTEXT_PATH):
        if candidate is None:
            candidate = baseline.replace('"before"', '"after"')
        checkout = tmp_path / "checkout"
        target = checkout / "eva2/src/eva" / path
        target.parent.mkdir(parents=True)
        target.write_bytes(baseline.encode("utf-8"))
        evidence = tmp_path / "evidence.json"
        evidence.write_bytes(b'{"controlled_test_evidence":true}')
        binding = {"path": evidence.name, "sha256": importer.digest(evidence.read_bytes())}
        outcome = {"exit_code": 0, "timed_out": False,
                   "stdout_truncated": False, "stderr_truncated": False}
        report = {
            "contract": "EvaIsolatedComponentEvaluation/v1",
            "relative_path": path, "installed": False,
            "baseline_sha256": importer.digest(baseline.encode("utf-8")),
            "candidate_sha256": importer.digest(candidate.encode("utf-8")),
            "baseline": dict(outcome), "candidate": dict(outcome),
        }
        diff = difflib.unified_diff(
            baseline.splitlines(keepends=True), candidate.splitlines(keepends=True),
            fromfile="a/" + path, tofile="b/" + path)
        patch = "".join(line if line.endswith("\n") else
                        line + "\n\\ No newline at end of file\n" for line in diff)
        package = {
            "contract": "EvaComponentReleaseCandidate/v1",
            "state": "awaiting_measured_gain_review", "installed": False,
            "path": path, "candidate_content": candidate, "patch": patch,
            "baseline_sha256": report["baseline_sha256"],
            "candidate_sha256": report["candidate_sha256"],
            "evaluation": report,
            "evaluation_sha256": importer.digest(importer.canonical(report)),
        }
        review = {
            "contract": "EvaComponentReleaseReview/v1", "decision": "accept",
            "reviewer": "independent-controlled-test",
            "measured_gain_evidence": dict(binding),
            "independent_review_evidence": dict(binding),
        }
        commands = {
            ("rev-parse", "--show-toplevel"): str(checkout),
            ("branch", "--show-current"): "codex/reviewed-component",
            ("status", "--porcelain", "--untracked-files=all"): "",
            ("rev-parse", "HEAD"): "a" * 40,
        }
        monkeypatch.setattr(importer, "git", lambda root, *args: commands[args])
        release = {
            "checkout": checkout, "target": target, "evidence": evidence,
            "package_file": tmp_path / "package.json",
            "review_file": tmp_path / "review.json",
            "package": package, "review": review,
        }
        save_package(release)
        return release
    return make


def prepare(release):
    return importer.prepare(release["package_file"], release["review_file"], release["checkout"])


@pytest.mark.parametrize("size", [35517, 65536])
def test_active_context_accepts_reviewed_body_only_up_to_64_kib(release_factory, size):
    baseline, candidate = sized_source(33492), sized_source(size, after=True)
    release = release_factory(baseline, candidate)
    target, original, imported, receipt = prepare(release)
    assert original == baseline.encode("utf-8")
    assert imported == candidate.encode("utf-8")
    assert target.read_bytes() == original  # prepare never applies or executes code
    assert receipt["candidate_sha256"] == importer.digest(imported)
    assert receipt["baseline_commit"] == "a" * 40
    assert receipt["state"] == "validated"
    assert receipt["installed"] is False


@pytest.mark.parametrize("path", OLD_PATHS)
def test_existing_paths_keep_32_kib_admission(release_factory, path):
    release = release_factory(BASE, sized_source(32768, after=True), path)
    assert len(prepare(release)[2]) == 32768


@pytest.mark.parametrize("path,limit", [(CONTEXT_PATH, 65536), *[(p, 32768) for p in OLD_PATHS]])
def test_per_path_size_limits_still_reject_one_extra_byte(release_factory, path, limit):
    release = release_factory(BASE, sized_source(limit + 1, after=True), path)
    with pytest.raises(ValueError, match="candidate bytes differ"):
        prepare(release)


def test_no_other_path_is_admitted(release_factory):
    release = release_factory(path="adapters/postgres/other.py")
    with pytest.raises(ValueError, match="outside the configured evaluation scope"):
        prepare(release)


@pytest.mark.parametrize("old,new", [
    ("import json", "import os"),
    ("class PostgresDelegatedWorkActions:", "class PostgresDelegatedWorkActions(object):"),
    ("@existing_decorator", "@other_decorator"),
    ("self, turn: ConversationTurn", "self, turn: object"),
    ("-> tuple[str, ...]", "-> object"),
    ('return "unchanged"', 'return "changed"'),
    ("# preserved module comment", "# altered module comment"),
    ("# preserved trailing comment", "# altered trailing comment"),
    ("\n# preserved trailing comment", "\nextra = 1\n# preserved trailing comment"),
])
def test_changes_outside_method_body_are_rejected(release_factory, old, new):
    candidate = BASE.replace('"before"', '"after"').replace(old, new)
    release = release_factory(candidate=candidate)
    with pytest.raises(ValueError, match="outside the active_context body"):
        prepare(release)


@pytest.mark.parametrize("candidate", [
    BASE.replace("def active_context", "async def active_context"),
    BASE.replace("    def other_method", "    def active_context"),
    BASE + "\nclass PostgresDelegatedWorkActions:\n    pass\n",
    BASE.replace("class PostgresDelegatedWorkActions", "class Other"),
])
def test_target_extent_must_be_unique_and_synchronous(release_factory, candidate):
    release = release_factory(candidate=candidate)
    with pytest.raises(ValueError, match="active_context scope requires"):
        prepare(release)


def test_utf8_columns_and_crlf_preserve_exact_surrounding_bytes(release_factory):
    baseline = BASE.replace("# preserved module comment", "# caf\u00e9 \u2603").replace("\n", "\r\n")
    candidate = baseline.replace('return ("before",)', 'return ("apr\u00e8s",)')
    release = release_factory(baseline, candidate)
    assert prepare(release)[2] == candidate.encode("utf-8")
    # One byte outside the body is still rejected, even with semantically equal Python.
    release["target"].write_bytes(baseline.replace("\r\n", "\n").encode("utf-8"))
    with pytest.raises(ValueError, match="release baseline changed"):
        prepare(release)


@pytest.mark.parametrize("tamper,message", [
    ("candidate_hash", "candidate bytes differ"),
    ("evaluation_hash", "evaluation binding differs"),
    ("evaluation_binding", "evaluation binding differs"),
    ("evaluation_failure", "evaluation is incomplete"),
    ("review", "operator review does not accept"),
    ("evidence", "review evidence is absent"),
    ("baseline", "release baseline changed"),
    ("patch", "candidate patch differs"),
])
def test_existing_exact_bindings_remain_required(release_factory, tamper, message):
    release = release_factory()
    package = release["package"]
    if tamper == "candidate_hash":
        package["candidate_sha256"] = "0" * 64
    elif tamper == "evaluation_hash":
        package["evaluation_sha256"] = "0" * 64
    elif tamper == "evaluation_binding":
        package["evaluation"]["candidate_sha256"] = "0" * 64
        package["evaluation_sha256"] = importer.digest(importer.canonical(package["evaluation"]))
    elif tamper == "evaluation_failure":
        package["evaluation"]["candidate"]["timed_out"] = True
        package["evaluation_sha256"] = importer.digest(importer.canonical(package["evaluation"]))
    elif tamper == "review":
        release["review"]["decision"] = "reject"
    elif tamper == "evidence":
        release["evidence"].write_bytes(b"changed evidence")
    elif tamper == "baseline":
        release["target"].write_bytes(BASE.encode("utf-8") + b"# changed\n")
    elif tamper == "patch":
        package["patch"] += "# extra\n"
    save_package(release)
    with pytest.raises(ValueError, match=message):
        prepare(release)


def test_package_digest_cannot_be_rebound_implicitly(release_factory):
    release = release_factory()
    package = release["package"]
    package["candidate_package_sha256"] = "0" * 64
    release["package_file"].write_text(json.dumps(package))
    with pytest.raises(ValueError, match="candidate package binding differs"):
        prepare(release)
