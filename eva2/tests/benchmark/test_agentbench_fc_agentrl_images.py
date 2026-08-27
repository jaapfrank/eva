from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
CONTROLLER = ROOT / "benchmark" / "agentbench-fc-v1" / "agentrl-controller-pinned.Dockerfile"
WORKER = ROOT / "benchmark" / "agentbench-fc-v1" / "agentrl-worker-pinned.Dockerfile"
RECEIPT = (
    ROOT
    / "eva2"
    / "evidence"
    / "living-core"
    / "agentbench-fc-agentrl-source-images-2026-08-27.json"
)


def test_agentrl_controller_build_has_pinned_primary_inputs_and_declares_build_tools() -> None:
    source = CONTROLLER.read_text(encoding="utf-8")

    assert "golang:1.24.5-bookworm@sha256:" in source
    assert "base-debian12:nonroot@sha256:" in source
    assert "PROTOC_VERSION=31.1" in source
    assert "PROTOC_SHA256=96553041f1a91ea0efee963cb16f462f5985b4d65365f3907414c360044d8065" in source
    assert "protoc-gen-go@v1.36.10" in source
    assert "protoc-gen-go-grpc@v1.5.1" in source
    assert "AGENTRL_GIT_ARCHIVE_SHA256=76a50942687e5646ad401adbb579e70fafb0761175e61019cc2c72cd0b7ce2ee" in source
    assert "apt-get install -y --no-install-recommends curl unzip" in source
    assert ":latest" not in source
    assert "USER 65532:65532" in source


def test_agentrl_worker_installs_the_pinned_source_instead_of_pypi() -> None:
    source = WORKER.read_text(encoding="utf-8")

    assert "python:3.10.18-slim-bookworm@sha256:" in source
    assert "COPY proto ./proto" in source
    assert "COPY worker ./worker" in source
    assert "python -m pip install --no-cache-dir ./worker" in source
    assert "pip install agentrl-worker" not in source
    assert ":latest" not in source
    assert "USER 65532:65532" in source


def test_agentrl_source_images_receipt_binds_current_source_and_image_bytes() -> None:
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))

    assert receipt["artifact"] == "EvaAgentBenchFcAgentRLSourceImagesReceipt/v1"
    assert receipt["status"] == "source_images_ready_transitive_lock_open"
    assert receipt["inputs"]["agentbenchCommit"] == "d1e4a10db08c87075c78972e48ecc182be03e2d5"
    assert receipt["inputs"]["agentrlCommit"] == "6a73409d31ba695d383b978a8ad3ef400d90c054"
    assert receipt["inputs"]["agentrlGitArchive"]["sha256"] == (
        "76a50942687e5646ad401adbb579e70fafb0761175e61019cc2c72cd0b7ce2ee"
    )
    assert receipt["inputs"]["controllerDockerfile"]["sha256"] == hashlib.sha256(
        CONTROLLER.read_bytes()
    ).hexdigest()
    assert receipt["inputs"]["workerDockerfile"]["sha256"] == hashlib.sha256(
        WORKER.read_bytes()
    ).hexdigest()
    assert receipt["verification"]["controllerReadOnlyHelp"] is True
    assert receipt["verification"]["workerReadOnlyHelp"] is True
    assert receipt["verification"]["taskOrScoreStarted"] is False
    assert receipt["images"]["controller"]["labels"]["org.opencontainers.image.revision"] == (
        receipt["inputs"]["agentrlCommit"]
    )
    assert receipt["images"]["worker"]["labels"]["org.opencontainers.image.source-archive-sha256"] == (
        receipt["inputs"]["agentrlGitArchive"]["sha256"]
    )
    assert "apt-get update" in receipt["openBoundary"]
