from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile

import pytest

from tools.managed_b_candidate.base_image import (
    _docker_export,
    materialize_pinned_base_image,
    validate_materialized_base_image,
    validate_pinned_base_image_archive,
)


def _json(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode()


def _digest(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _archive(path: Path, *, bad_config_platform: bool = False) -> tuple[str, dict[str, object]]:
    layer = b"one-layer"
    config = _json({"architecture": "arm64" if bad_config_platform else "amd64", "os": "linux"})
    layer_digest = _digest(layer)
    config_digest = _digest(config)
    manifest = _json(
        {
            "config": {
                "digest": config_digest,
                "mediaType": "application/vnd.oci.image.config.v1+json",
                "size": len(config),
            },
            "layers": [
                {
                    "digest": layer_digest,
                    "mediaType": "application/vnd.oci.image.layer.v1.tar",
                    "size": len(layer),
                }
            ],
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "schemaVersion": 2,
        }
    )
    manifest_digest = _digest(manifest)
    nested = _json(
        {
            "manifests": [
                {
                    "digest": manifest_digest,
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "platform": {"architecture": "amd64", "os": "linux"},
                    "size": len(manifest),
                }
            ],
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "schemaVersion": 2,
        }
    )
    root_digest = _digest(nested)
    index = _json(
        {
            "manifests": [
                {
                    "digest": root_digest,
                    "mediaType": "application/vnd.oci.image.index.v1+json",
                    "size": len(nested),
                }
            ],
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "schemaVersion": 2,
        }
    )
    compatibility = _json(
        [
            {
                "Config": f"blobs/sha256/{config_digest[7:]}",
                "Layers": [f"blobs/sha256/{layer_digest[7:]}"],
                "RepoTags": None,
            }
        ]
    )
    files = {
        "oci-layout": _json({"imageLayoutVersion": "1.0.0"}),
        "index.json": index,
        "manifest.json": compatibility,
        f"blobs/sha256/{root_digest[7:]}": nested,
        f"blobs/sha256/{manifest_digest[7:]}": manifest,
        f"blobs/sha256/{config_digest[7:]}": config,
        f"blobs/sha256/{layer_digest[7:]}": layer,
    }
    with tarfile.open(path, "w") as bundle:
        for name, content in sorted(files.items()):
            member = tarfile.TarInfo(name)
            member.size = len(content)
            member.mode = 0o644
            bundle.addfile(member, io.BytesIO(content))
    return root_digest, {
        "Architecture": "amd64",
        "Id": root_digest,
        "Os": "linux",
        "RepoDigests": [f"python@{root_digest}"],
    }


def test_validates_and_materializes_a_local_pinned_oci_export(tmp_path: Path) -> None:
    source = tmp_path / "source.tar"
    digest, inspection = _archive(source)
    with tempfile.TemporaryDirectory(prefix="eva2-base-test-", dir="/tmp") as path:
        output = Path(path)

        result = materialize_pinned_base_image(
            digest,
            output,
            inspector=lambda _reference: inspection,
            exporter=lambda _reference, destination: destination.write_bytes(source.read_bytes()),
        )

        assert result.receipt["status"] == "local_export_validated"
        assert result.receipt["network_used"] is False
        assert result.receipt["platform"] == "linux/amd64"
        assert result.path.stat().st_mode & 0o222 == 0
        assert result.path.parent.stat().st_mode & 0o222 == 0
        assert validate_materialized_base_image(result)["status"] == "local_export_validated"


def test_refuses_missing_repo_digest_or_wrong_config_platform(tmp_path: Path) -> None:
    archive = tmp_path / "source.tar"
    digest, inspection = _archive(archive)
    invalid_inspection = {**inspection, "RepoDigests": []}
    with pytest.raises(RuntimeError, match="lacks the pinned repo digest"):
        validate_pinned_base_image_archive(archive, digest, invalid_inspection)

    bad_archive = tmp_path / "wrong-platform.tar"
    bad_digest, bad_inspection = _archive(bad_archive, bad_config_platform=True)
    with pytest.raises(RuntimeError, match="config platform differs"):
        validate_pinned_base_image_archive(bad_archive, bad_digest, bad_inspection)


def test_refuses_a_corrupted_blob_before_accepting_an_export(tmp_path: Path) -> None:
    archive = tmp_path / "source.tar"
    digest, inspection = _archive(archive)
    replacement = tmp_path / "corrupted.tar"
    with tarfile.open(archive, "r:") as source, tarfile.open(replacement, "w") as target:
        for member in source.getmembers():
            data = source.extractfile(member).read() if member.isfile() else None
            if member.isfile() and member.name.startswith("blobs/sha256/"):
                data = b"x" * member.size
            target.addfile(member, None if data is None else io.BytesIO(data))

    with pytest.raises(RuntimeError, match="blob digest differs"):
        validate_pinned_base_image_archive(replacement, digest, inspection)


def test_refuses_a_valid_but_foreign_oci_export(tmp_path: Path) -> None:
    expected_archive = tmp_path / "expected.tar"
    expected_digest, inspection = _archive(expected_archive)
    foreign_archive = tmp_path / "foreign.tar"
    _archive(foreign_archive, bad_config_platform=True)

    with pytest.raises(RuntimeError, match="root digest differs"):
        validate_pinned_base_image_archive(foreign_archive, expected_digest, inspection)


def test_revalidator_refuses_a_materialized_export_that_becomes_writable(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.tar"
    digest, inspection = _archive(source)
    with tempfile.TemporaryDirectory(prefix="eva2-base-test-", dir="/tmp") as path:
        result = materialize_pinned_base_image(
            digest,
            Path(path),
            inspector=lambda _reference: inspection,
            exporter=lambda _reference, destination: destination.write_bytes(source.read_bytes()),
        )
        result.path.chmod(0o644)

        with pytest.raises(RuntimeError, match="must remain read-only"):
            validate_materialized_base_image(result)


def test_default_exporter_preserves_the_pinned_source_index(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    commands: list[list[str]] = []

    def run(command: list[str], **_kwargs: object) -> None:
        commands.append(command)

    monkeypatch.setattr("tools.managed_b_candidate.base_image.subprocess.run", run)
    _docker_export("docker.io/library/python@sha256:" + "1" * 64, tmp_path / "base.tar")

    assert commands == [
        [
            "docker",
            "image",
            "save",
            "--output",
            str(tmp_path / "base.tar"),
            "docker.io/library/python@sha256:" + "1" * 64,
        ]
    ]
