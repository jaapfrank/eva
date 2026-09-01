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
    materialize_base_image_layout,
    materialize_pinned_base_image,
    materialize_pinned_base_image_archive,
    validate_materialized_base_image_layout,
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
        "Id": config_digest,
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

        assert result.receipt["status"] == "sealed_archive_validated"
        assert result.receipt["network_used"] is False
        assert result.receipt["platform"] == "linux/amd64"
        assert result.path.stat().st_mode & 0o222 == 0
        assert result.path.parent.stat().st_mode & 0o222 == 0
        receipt = validate_materialized_base_image(result)
        assert receipt["status"] == "sealed_archive_validated"
        assert len(
            {
                receipt["oci_root_index_digest"],
                receipt["oci_selected_manifest_digest"],
                receipt["oci_config_digest"],
            }
        ) == 3
        assert receipt["source_local_image_id"] == receipt["oci_config_digest"]


def test_materializes_a_non_dockerhub_runtime_repository_by_full_authority(
    tmp_path: Path,
) -> None:
    source = tmp_path / "runtime-source.tar"
    digest, inspection = _archive(source)
    repository = "gcr.io/distroless/cc-debian13"
    inspection["RepoDigests"] = [f"{repository}@{digest}"]
    with tempfile.TemporaryDirectory(prefix="eva2-runtime-base-test-", dir="/tmp") as path:
        result = materialize_pinned_base_image(
            digest,
            Path(path),
            repository=repository,
            inspector=lambda reference: inspection,
            exporter=lambda reference, destination: destination.write_bytes(
                source.read_bytes()
            ),
        )

        receipt = validate_materialized_base_image(result)
        assert receipt["base_image_repository"] == repository
        assert receipt["source_local_repo_digest"] == f"{repository}@{digest}"


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


def test_copies_one_hash_bound_archive_and_materializes_a_read_only_layout(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.tar"
    digest, _inspection = _archive(source)
    archive_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory(prefix="eva2-sealed-base-test-", dir="/tmp") as path:
        output = Path(path)
        materialized = materialize_pinned_base_image_archive(
            source.resolve(),
            archive_sha256,
            digest,
            output,
        )
        layout_parent = output / "layouts"
        layout_parent.mkdir(mode=0o700)
        layout = materialize_base_image_layout(
            materialized,
            layout_parent,
            role="build",
        )
        receipt = validate_materialized_base_image_layout(layout)

        assert receipt["archive_sha256"] == archive_sha256
        assert receipt["base_image_digest"] == digest
        assert receipt["role"] == "build"
        assert (layout.path / "oci-layout").read_bytes() == _json(
            {"imageLayoutVersion": "1.0.0"}
        )
        assert not (layout.path / "manifest.json").exists()
        assert layout.path.stat().st_mode & 0o777 == 0o700
        assert all(
            path.stat().st_mode & 0o222 == 0
            for path in layout.path.rglob("*")
            if path.is_file()
        )


def test_refuses_wrong_archive_bytes_before_materializing_a_layout(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.tar"
    digest, _inspection = _archive(source)
    with tempfile.TemporaryDirectory(prefix="eva2-sealed-base-test-", dir="/tmp") as path:
        output = Path(path)
        with pytest.raises(RuntimeError, match="bytes differ from their authority"):
            materialize_pinned_base_image_archive(
                source.resolve(),
                "0" * 64,
                digest,
                output,
            )

        assert list(output.iterdir()) == []


def test_refuses_a_tagged_archive(tmp_path: Path) -> None:
    archive = tmp_path / "source.tar"
    digest, _inspection = _archive(archive)
    tagged = tmp_path / "tagged.tar"
    with tarfile.open(archive, "r:") as source, tarfile.open(tagged, "w") as target:
        for member in source.getmembers():
            data = source.extractfile(member).read() if member.isfile() else None
            if member.name == "manifest.json":
                value = json.loads(data)
                value[0]["RepoTags"] = ["python:latest"]
                data = _json(value)
                member.size = len(data)
            target.addfile(member, None if data is None else io.BytesIO(data))
    with pytest.raises(RuntimeError, match="compatibility manifest differs"):
        validate_pinned_base_image_archive(tagged, digest)


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
