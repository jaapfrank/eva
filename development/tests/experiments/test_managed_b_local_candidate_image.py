"""Fail-closed checks for the disposable daemon-local candidate path."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[3]
PROBE_PATH = ROOT / "experiments" / "managed-b-clean-graph-v1" / "probe.py"
SPEC = importlib.util.spec_from_file_location(
    "managed_b_clean_graph_local_candidate_probe", PROBE_PATH
)
assert SPEC is not None and SPEC.loader is not None
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)

IMAGE_ID = "sha256:" + "a" * 64
SOURCE_COMMIT = "b" * 40


def _authority_document() -> dict[str, object]:
    return {
        "candidate_image_id": IMAGE_ID,
        "candidate_receipt_sha256": "d" * 64,
        "contract": "EvaManagedBCandidateArtifactAnalysis/v1",
        "files": {"image/image.tar": "c" * 64},
        "findings": {"CRITICAL": 0, "HIGH": 0},
        "network_used": False,
        "nonclaims": [
            "The candidate was not tagged, pushed, signed, started, promoted, or attached to Compose.",
        ],
        "sbom": {
            "format": "SPDX-2.3-json",
        },
        "status": "passed_candidate_artifacts",
        "tool_authority_sha256": "e" * 64,
        "trivy": {
            "database_updated_at": "2026-08-31T00:00:00Z",
            "scanner_version": "0.74.0",
        },
    }


def _write_authority(tmp_path: Path, document: dict[str, object]) -> tuple[Path, str]:
    path = tmp_path / "authority.json"
    payload = json.dumps(document, sort_keys=True).encode()
    path.write_bytes(payload)
    return path, hashlib.sha256(payload).hexdigest()


def _valid_image_inspection() -> list[dict[str, object]]:
    return [
        {
            "Architecture": "amd64",
            "Config": {
                "Env": ["LD_LIBRARY_PATH=/usr/local/lib"],
                "Labels": {
                    "io.eva2.source-commit": SOURCE_COMMIT,
                    "io.eva2.source-date-epoch": "1788242803",
                    "io.eva2.source-refresh": "git-archive-v1",
                },
            },
            "Id": IMAGE_ID,
            "Os": "linux",
            "RepoDigests": [],
            "RepoTags": [],
        }
    ]


def test_development_scan_is_explicit_and_not_release_authority(tmp_path: Path) -> None:
    document = {
        "status": "development_scan_complete",
        "imageId": IMAGE_ID,
        "sourceCommit": SOURCE_COMMIT,
        "archiveSha256": "c" * 64,
        "findings": {"HIGH": 0, "CRITICAL": 0},
        "network": "none",
        "packages": 75,
        "files": 1519,
        "databaseUpdatedAt": "2026-08-31T00:00:00Z",
    }
    path, digest = _write_authority(tmp_path, document)
    with pytest.raises(probe.ProbeError):
        probe._candidate_authority(path, expected_sha256=digest, image_id=IMAGE_ID)
    result = probe._candidate_authority(
        path, expected_sha256=digest, image_id=IMAGE_ID, development_summary=True
    )
    assert result["contract"] == "development_scan_only_not_release_authority"
    assert result["sourceCommit"] == SOURCE_COMMIT
    for field, value in (
        ("imageId", "sha256:" + "e" * 64),
        ("sourceCommit", "bad"),
        ("archiveSha256", "bad"),
        ("findings", {"HIGH": 1, "CRITICAL": 0}),
        ("packages", 0),
        ("files", True),
        ("network", "internet"),
    ):
        changed = {**document, field: value}
        path, digest = _write_authority(tmp_path, changed)
        with pytest.raises(probe.ProbeError):
            probe._candidate_authority(
                path,
                expected_sha256=digest,
                image_id=IMAGE_ID,
                development_summary=True,
            )


@pytest.mark.parametrize(
    "extra", [{"clone_current_route": True}, {"retain_slot": True}]
)
def test_development_scan_never_admits_live_data_or_retained_slots(
    monkeypatch: pytest.MonkeyPatch, extra: dict[str, bool]
) -> None:
    monkeypatch.setattr(probe.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        probe, "_live_status", lambda *_args: pytest.fail("no resources")
    )
    assert (
        probe.main(
            candidate_local_image_id=IMAGE_ID, development_scan_summary=True, **extra
        )
        == 2
    )


def test_conversation_measurement_binds_requested_model() -> None:
    value = {
        "text": "beheerd",
        "provider": "chatgpt-codex:gpt-6-astra",
        "model_usage": {
            "measurement": "measured",
            "input_tokens": 40,
            "output_tokens": 5,
            "model_attempts": 1,
            "tool_calls": 0,
        },
    }
    assert (
        probe._conversation_measurement(
            value, expected_text="beheerd", model="gpt-6-astra"
        )["provider"]
        == value["provider"]
    )
    with pytest.raises(probe.ProbeError):
        probe._conversation_measurement(value, expected_text="beheerd")


@pytest.mark.parametrize("word", ["", "x", "Bad", "abc def", "a" * 33])
def test_memory_word_is_bounded_before_resources(
    monkeypatch: pytest.MonkeyPatch, word: str
) -> None:
    monkeypatch.setattr(probe.os, "geteuid", lambda: 0)
    monkeypatch.setattr(probe, "_live_status", lambda *_args: pytest.fail("no network"))
    assert probe.main(conversation_memory_word=word) == 2


def test_local_candidate_authority_and_image_inspection_are_exact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, digest = _write_authority(tmp_path, _authority_document())
    authority = probe._candidate_authority(
        path,
        expected_sha256=digest,
        image_id=IMAGE_ID,
    )
    monkeypatch.setattr(
        probe,
        "_run",
        lambda command, **_kwargs: json.dumps(_valid_image_inspection()),
    )

    inspected = probe._inspect_local_candidate_image(
        IMAGE_ID, expected_source_commit=SOURCE_COMMIT
    )

    assert authority["fileSha256"] == digest
    assert authority["candidateReceiptSha256"] == "d" * 64
    assert inspected == {
        "architecture": "amd64",
        "id": IMAGE_ID,
        "os": "linux",
        "runtimeLibraryPath": "/usr/local/lib",
        "sourceCommit": SOURCE_COMMIT,
        "sourceDateEpoch": "1788242803",
        "sourceRefresh": "git-archive-v1",
    }


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("findings", "HIGH", 1),
        ("findings", "CRITICAL", 1),
        ("trivy", "scanner_version", 74),
    ],
)
def test_local_candidate_authority_rejects_semantic_mutation(
    tmp_path: Path, section: str, field: str, value: object
) -> None:
    document = _authority_document()
    nested = document[section]
    assert isinstance(nested, dict)
    nested[field] = value
    path, digest = _write_authority(tmp_path, document)

    with pytest.raises(probe.ProbeError):
        probe._candidate_authority(
            path,
            expected_sha256=digest,
            image_id=IMAGE_ID,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("candidate_image_id", "sha256:" + "d" * 64),
        ("network_used", True),
        ("status", "rejected_vulnerabilities"),
    ],
)
def test_local_candidate_authority_rejects_top_level_mutation(
    tmp_path: Path, field: str, value: object
) -> None:
    document = _authority_document()
    document[field] = value
    path, digest = _write_authority(tmp_path, document)

    with pytest.raises(probe.ProbeError):
        probe._candidate_authority(
            path,
            expected_sha256=digest,
            image_id=IMAGE_ID,
        )


def test_local_candidate_authority_rejects_external_hash_mismatch(
    tmp_path: Path,
) -> None:
    path, _digest = _write_authority(tmp_path, _authority_document())

    with pytest.raises(probe.ProbeError, match="SHA-256 differs"):
        probe._candidate_authority(
            path,
            expected_sha256="f" * 64,
            image_id=IMAGE_ID,
        )


def test_local_candidate_image_inspection_fails_before_probe_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    authority, digest = _write_authority(tmp_path, _authority_document())
    staging_parent = tmp_path / "staging"
    staging_parent.mkdir()
    monkeypatch.setattr(probe, "STAGING_PARENT", staging_parent)
    monkeypatch.setattr(probe.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        probe,
        "_inspect_local_candidate_image",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            probe.ProbeError("inspection stopped")
        ),
    )
    monkeypatch.setattr(
        probe,
        "_live_status",
        lambda *_args, **_kwargs: pytest.fail("live status must not be reached"),
    )

    status = probe.main(
        candidate_local_image_id=IMAGE_ID,
        candidate_authority=authority,
        expected_candidate_authority_sha256=digest,
        expected_source_commit=SOURCE_COMMIT,
    )

    assert status == 2
    assert list(staging_parent.iterdir()) == []
    assert "inspection stopped" in capsys.readouterr().out


@pytest.mark.parametrize(
    "value",
    [
        "candidate:latest",
        "candidate@sha256:" + "a" * 64,
        "sha256:" + "a" * 63,
        "a" * 64,
    ],
)
def test_local_candidate_image_id_rejects_names_tags_and_short_ids(value: str) -> None:
    with pytest.raises(probe.ProbeError):
        probe._immutable_local_candidate_image_id(value)


def test_probe_receipt_writer_persists_one_private_bound_receipt() -> None:
    with tempfile.TemporaryDirectory(prefix="eva2-probe-receipt-", dir="/tmp") as root:
        private = Path(root)
        private.chmod(0o700)
        receipt = private / "probe-receipt.json"
        value = {"candidate": IMAGE_ID, "sourceCommit": SOURCE_COMMIT}

        probe._write_new_private_receipt(receipt, value)

        assert json.loads(receipt.read_bytes()) == value
        assert receipt.stat().st_mode & 0o777 == 0o600
        with pytest.raises(probe.ProbeError, match="one new file"):
            probe._write_new_private_receipt(receipt, value)


def test_probe_main_persists_final_result_after_cleanup_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with tempfile.TemporaryDirectory(prefix="eva2-probe-main-", dir="/tmp") as root:
        private = Path(root)
        private.chmod(0o700)
        staging_parent = private / "staging"
        staging_parent.mkdir(mode=0o700)
        receipt = private / "final-receipt.json"
        original_mkdir = Path.mkdir

        def fail_probe_staging(path: Path, *args: object, **kwargs: object) -> None:
            if path.parent == staging_parent and path.name.startswith(
                "managed-b-clean-probe-"
            ):
                raise probe.ProbeError("synthetic pre-resource failure")
            original_mkdir(path, *args, **kwargs)

        monkeypatch.setattr(probe, "STAGING_PARENT", staging_parent)
        monkeypatch.setattr(probe.os, "geteuid", lambda: 0)
        monkeypatch.setattr(probe, "_live_status", lambda *_args: False)
        # This test covers final persistence after a pre-resource failure,
        # not root-owned destination validation (tested separately).
        monkeypatch.setattr(probe, "_validate_receipt_destination", lambda _path: None)
        monkeypatch.setattr(
            probe, "_run", lambda *_args, **_kwargs: pytest.fail("no subprocesses")
        )
        monkeypatch.setattr(Path, "mkdir", fail_probe_staging)

        assert probe.main(receipt_path=receipt) == 2
        persisted = json.loads(receipt.read_bytes())
        assert persisted["status"] == "failed"
        assert persisted["temporaryResourcesRemoved"] is True
        assert persisted["error"] == "synthetic pre-resource failure"


def test_local_candidate_image_rejects_tag_or_provenance_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inspection = _valid_image_inspection()
    inspection[0]["RepoTags"] = ["candidate:latest"]
    monkeypatch.setattr(
        probe, "_run", lambda command, **_kwargs: json.dumps(inspection)
    )
    with pytest.raises(probe.ProbeError):
        probe._inspect_local_candidate_image(
            IMAGE_ID, expected_source_commit=SOURCE_COMMIT
        )


@pytest.mark.parametrize(
    "environment",
    [
        [],
        ["LD_LIBRARY_PATH=/lib"],
        [
            "LD_LIBRARY_PATH=/usr/local/lib",
            "LD_LIBRARY_PATH=/usr/local/lib",
        ],
    ],
)
def test_local_candidate_image_rejects_runtime_library_path_drift(
    environment: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    inspection = _valid_image_inspection()
    config = inspection[0]["Config"]
    assert isinstance(config, dict)
    config["Env"] = environment
    monkeypatch.setattr(
        probe, "_run", lambda command, **_kwargs: json.dumps(inspection)
    )

    with pytest.raises(probe.ProbeError, match="runtime library path differs"):
        probe._inspect_local_candidate_image(
            IMAGE_ID, expected_source_commit=SOURCE_COMMIT
        )


def test_local_candidate_render_binds_only_the_six_core_image_services() -> None:
    services = {
        name: {"image": IMAGE_ID, "pull_policy": "never"}
        for name in probe.CORE_IMAGE_SERVICES
    }
    services.update(
        {
            "postgres": {"image": "postgres@sha256:" + "1" * 64},
            "hindsight": {"image": "hindsight@sha256:" + "2" * 64},
            "hindsight-postgres": {"image": "postgres@sha256:" + "1" * 64},
        }
    )

    bindings = probe._validate_local_candidate_render(
        {"services": services}, image_id=IMAGE_ID
    )

    assert set(bindings) == probe.CORE_IMAGE_SERVICES
    services["core"]["pull_policy"] = "always"
    with pytest.raises(probe.ProbeError):
        probe._validate_local_candidate_render(
            {"services": services}, image_id=IMAGE_ID
        )


def test_started_candidate_binding_uses_actual_container_image_and_labels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    container_ids = {
        name: hashlib.sha256(name.encode()).hexdigest()[:12]
        for name in probe.STARTED_CORE_IMAGE_SERVICES
    }

    def fake_run(command: list[str], **_kwargs: object) -> str:
        if "compose" in command:
            service = command[-1]
            return container_ids[service] + "\n"
        container_id = command[-1]
        service = next(
            name
            for name in probe.STARTED_CORE_IMAGE_SERVICES
            if container_id == container_ids[name]
        )
        return json.dumps(
            [
                {
                    "Config": {
                        "Env": ["LD_LIBRARY_PATH=/usr/local/lib"],
                        "Image": IMAGE_ID,
                        "Labels": {
                            "com.docker.compose.project": "probe-project",
                            "com.docker.compose.service": service,
                            "io.eva2.source-commit": SOURCE_COMMIT,
                        },
                    },
                    "Image": IMAGE_ID,
                }
            ]
        )

    monkeypatch.setattr(probe, "_run", fake_run)
    bindings = probe._inspect_started_local_candidate_services(
        "probe-project",
        tmp_path / "candidate.env",
        image_id=IMAGE_ID,
        expected_source_commit=SOURCE_COMMIT,
    )

    assert set(bindings) == probe.STARTED_CORE_IMAGE_SERVICES


def test_started_candidate_binding_rejects_runtime_library_path_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    container_ids = {
        name: hashlib.sha256(name.encode()).hexdigest()[:12]
        for name in probe.STARTED_CORE_IMAGE_SERVICES
    }

    def fake_run(command: list[str], **_kwargs: object) -> str:
        if "compose" in command:
            return container_ids[command[-1]] + "\n"
        container_id = command[-1]
        service = next(
            name
            for name in probe.STARTED_CORE_IMAGE_SERVICES
            if container_id == container_ids[name]
        )
        environment = (
            ["LD_LIBRARY_PATH=/lib"]
            if service == "core"
            else ["LD_LIBRARY_PATH=/usr/local/lib"]
        )
        return json.dumps(
            [
                {
                    "Config": {
                        "Env": environment,
                        "Image": IMAGE_ID,
                        "Labels": {
                            "com.docker.compose.project": "probe-project",
                            "com.docker.compose.service": service,
                            "io.eva2.source-commit": SOURCE_COMMIT,
                        },
                    },
                    "Image": IMAGE_ID,
                }
            ]
        )

    monkeypatch.setattr(probe, "_run", fake_run)

    with pytest.raises(probe.ProbeError, match="runtime library path differs"):
        probe._inspect_started_local_candidate_services(
            "probe-project",
            tmp_path / "candidate.env",
            image_id=IMAGE_ID,
            expected_source_commit=SOURCE_COMMIT,
        )


@pytest.mark.parametrize(
    ("clone_current_route", "retain_slot"), [(True, False), (False, True)]
)
def test_local_candidate_is_rejected_before_resources_for_non_disposable_modes(
    clone_current_route: bool,
    retain_slot: bool,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(probe.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        probe,
        "_run",
        lambda *_args, **_kwargs: pytest.fail("Docker must not be reached"),
    )

    status = probe.main(
        candidate_local_image_id=IMAGE_ID,
        clone_current_route=clone_current_route,
        retain_slot=retain_slot,
    )

    assert status == 2
    assert "only with fresh disposable state" in capsys.readouterr().out
