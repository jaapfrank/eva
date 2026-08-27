from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
CONTROLLER = ROOT / "benchmark" / "agentbench-fc-v1" / "agentrl-controller-pinned.Dockerfile"
CONTROLLER_INPUTS = (
    ROOT / "benchmark" / "agentbench-fc-v1" / "agentrl-controller-build-inputs-amd64.json"
)
WORKER = ROOT / "benchmark" / "agentbench-fc-v1" / "agentrl-worker-pinned.Dockerfile"
WORKER_LOCK = ROOT / "benchmark" / "agentbench-fc-v1" / "agentrl-worker-requirements.lock"
WORKER_WHEELHOUSE = (
    ROOT / "benchmark" / "agentbench-fc-v1" / "agentrl-worker-wheelhouse-amd64-py310.json"
)
CONTROLLER_PROVENANCE = (
    ROOT / "benchmark" / "agentbench-fc-v1" / "agentrl-controller-inputs-provenance-amd64.json"
)
CONTROLLER_INPUTS_V2 = (
    ROOT / "benchmark" / "agentbench-fc-v1" / "agentrl-controller-build-inputs-amd64-v2.json"
)
CONTROLLER_PROVENANCE_V2 = (
    ROOT / "benchmark" / "agentbench-fc-v1" / "agentrl-controller-inputs-provenance-amd64-v2.json"
)
WORKER_PROVENANCE = (
    ROOT / "benchmark" / "agentbench-fc-v1" / "agentrl-worker-wheelhouse-provenance-amd64-py310.json"
)
PROBE_CONFIG = ROOT / "benchmark" / "agentbench-fc-v1" / "agentrl-protocol-probe.yaml"
RECEIPT = (
    ROOT
    / "eva2"
    / "evidence"
    / "living-core"
    / "agentbench-fc-agentrl-source-images-2026-08-27.json"
)
PROBE_RECEIPT = ROOT / "eva2" / "evidence" / "living-core" / "agentbench-fc-protocol-probe-2026-08-27.json"
PROBE_READBACK = (
    ROOT / "eva2" / "evidence" / "living-core" / "agentbench-fc-protocol-probe-list-workers-2026-08-27.json"
)
RETAINED_REBUILD_RECEIPT = (
    ROOT
    / "eva2"
    / "evidence"
    / "living-core"
    / "agentbench-fc-agentrl-retained-rebuild-2026-08-27.json"
)
CONTROLLER_V2_REPRO_RECEIPT = (
    ROOT
    / "eva2"
    / "evidence"
    / "living-core"
    / "agentbench-fc-agentrl-controller-repro-v2-2026-08-27.json"
)
CONTROLLER_V2_DOCKERFILE = (
    ROOT / "benchmark" / "agentbench-fc-v1" / "agentrl-controller-repro-v2.Dockerfile"
)
SECURITY_UPGRADE_RECEIPT = (
    ROOT
    / "eva2"
    / "evidence"
    / "living-core"
    / "agentbench-fc-agentrl-security-upgrade-probes-2026-08-27.json"
)
SECURITY_UPGRADE_CONTROLLER = (
    ROOT / "benchmark" / "agentbench-fc-v1" / "agentrl-controller-chainguard-probe.Dockerfile"
)
SECURITY_UPGRADE_CONTROLLER_PATCH = (
    ROOT / "benchmark" / "agentbench-fc-v1" / "agentrl-controller-security-upgrade-v1.patch"
)
SECURITY_UPGRADE_WORKER = (
    ROOT / "benchmark" / "agentbench-fc-v1" / "agentrl-worker-chainguard-probe.Dockerfile"
)
OFFLINE_WORKER_RECEIPT = (
    ROOT
    / "eva2"
    / "evidence"
    / "living-core"
    / "agentbench-fc-agentrl-worker-wheelhouse-py314-2026-08-27.json"
)
OFFLINE_WORKER_DOCKERFILE = (
    ROOT / "benchmark" / "agentbench-fc-v1" / "agentrl-worker-chainguard-offline.Dockerfile"
)
OFFLINE_WORKER_LOCK = (
    ROOT / "benchmark" / "agentbench-fc-v1" / "agentrl-worker-requirements-py314.lock"
)
OFFLINE_WORKER_WHEELHOUSE = (
    ROOT / "benchmark" / "agentbench-fc-v1" / "agentrl-worker-wheelhouse-amd64-py314.json"
)
OFFLINE_CONTROLLER_RECEIPT = (
    ROOT
    / "eva2"
    / "evidence"
    / "living-core"
    / "agentbench-fc-agentrl-controller-go125-2026-08-27.json"
)
OFFLINE_CONTROLLER_DOCKERFILE = (
    ROOT / "benchmark" / "agentbench-fc-v1" / "agentrl-controller-chainguard-offline.Dockerfile"
)
OFFLINE_CONTROLLER_INPUTS = (
    ROOT / "benchmark" / "agentbench-fc-v1" / "agentrl-controller-build-inputs-amd64-go125.json"
)
GO125_PROTOCOL_PROBE_RECEIPT = (
    ROOT
    / "eva2"
    / "evidence"
    / "living-core"
    / "agentbench-fc-agentrl-go125-protocol-probe-2026-08-27.json"
)
GO125_PROTOCOL_PROBE_READBACK = (
    ROOT
    / "eva2"
    / "evidence"
    / "living-core"
    / "agentbench-fc-agentrl-go125-protocol-probe-list-workers-2026-08-27.json"
)
DBBENCH_DEV_CONFIG = ROOT / "benchmark" / "agentbench-fc-v1" / "agentbench-dbbench-dev-registration.yaml"
DBBENCH_DEV_DOCKERFILE = (
    ROOT / "benchmark" / "agentbench-fc-v1" / "agentbench-dbbench-dev-worker-chainguard-offline.Dockerfile"
)
DBBENCH_DEV_INPUTS = (
    ROOT / "benchmark" / "agentbench-fc-v1" / "agentbench-dbbench-dev-worker-inputs-amd64-py314.json"
)
DBBENCH_DEV_RECEIPT = (
    ROOT / "eva2" / "evidence" / "living-core" / "agentbench-fc-dbbench-dev-registration-2026-08-27.json"
)
DBBENCH_DEV_READBACK = (
    ROOT
    / "eva2"
    / "evidence"
    / "living-core"
    / "agentbench-fc-dbbench-dev-registration-list-workers-2026-08-27.json"
)


def test_agentrl_controller_build_has_pinned_primary_inputs_and_declares_build_tools() -> None:
    source = CONTROLLER.read_text(encoding="utf-8")
    inputs = json.loads(CONTROLLER_INPUTS.read_text(encoding="utf-8"))

    assert "golang:1.24.5-bookworm@sha256:" in source
    assert "base-debian12:nonroot@sha256:" in source
    assert "PROTOC_SHA256=96553041f1a91ea0efee963cb16f462f5985b4d65365f3907414c360044d8065" in source
    assert "GOPROXY=off" in source
    assert "GOSUMDB=off" in source
    assert "apt-get" not in source
    assert "curl --fail" not in source
    assert "COPY agentrl-controller-gomodcache.tar" in source
    assert "COPY protoc-31.1-linux-x86_64.zip" in source
    assert "COPY protoc-gen-go /usr/local/bin/protoc-gen-go" in source
    assert "go mod verify" in source
    assert "AGENTRL_GIT_ARCHIVE_SHA256=76a50942687e5646ad401adbb579e70fafb0761175e61019cc2c72cd0b7ce2ee" in source
    assert ":latest" not in source
    assert "USER 65532:65532" in source
    assert inputs["contract"] == "EvaAgentBenchFcControllerBuildInputs/v1"
    assert inputs["agentrlCommit"] == "6a73409d31ba695d383b978a8ad3ef400d90c054"
    assert [item["filename"] for item in inputs["artifacts"]] == [
        "agentrl-controller-gomodcache.tar",
        "protoc-31.1-linux-x86_64.zip",
        "protoc-gen-go",
        "protoc-gen-go-grpc",
    ]


def test_agentrl_worker_is_a_source_build_with_only_the_sealed_wheelhouse() -> None:
    source = WORKER.read_text(encoding="utf-8")

    assert "python:3.10.18-slim-bookworm@sha256:" in source
    assert "COPY agentrl-worker-requirements.lock ./requirements.lock" in source
    assert "COPY agentrl-worker-wheelhouse ./wheelhouse" in source
    assert "--no-index --only-binary=:all:" in source
    assert "--require-hashes -r requirements.lock" in source
    assert "COPY proto ./proto" in source
    assert "COPY worker ./worker" in source
    assert "--no-deps --no-build-isolation ./worker" in source
    assert "pip install agentrl-worker" not in source
    assert ":latest" not in source
    assert "USER 65532:65532" in source


def test_agentrl_worker_lock_and_wheelhouse_are_hash_bound() -> None:
    lock = WORKER_LOCK.read_bytes()
    manifest = json.loads(WORKER_WHEELHOUSE.read_text(encoding="utf-8"))

    assert "autogenerated with pip-tools 7.5.2 on Python 3.10.18" in lock.decode("utf-8")
    assert "etcd3==0.12.0" in lock.decode("utf-8")
    assert "75e579ee5f0c9db18569da11eb27ebd9144958ddec308a12910575f241b85f12" in lock.decode(
        "utf-8"
    )
    assert manifest["contract"] == "EvaAgentBenchFcWorkerWheelhouse/v1"
    assert manifest["requirementsLock"]["sha256"] == hashlib.sha256(lock).hexdigest()
    assert manifest["target"] == {
        "architecture": "amd64",
        "os": "debian-bookworm",
        "pythonAbi": "cp310",
        "pythonVersion": "3.10.18",
    }
    assert len(manifest["artifacts"]) == 57
    assert next(
        item for item in manifest["artifacts"] if item["filename"] == "etcd3-0.12.0-py2.py3-none-any.whl"
    )["sha256"] == "75e579ee5f0c9db18569da11eb27ebd9144958ddec308a12910575f241b85f12"


def test_agentrl_artifact_provenance_binds_exact_manifests_and_rebuild_inputs() -> None:
    controller = json.loads(CONTROLLER_PROVENANCE.read_text(encoding="utf-8"))
    controller_v2 = json.loads(CONTROLLER_PROVENANCE_V2.read_text(encoding="utf-8"))
    worker = json.loads(WORKER_PROVENANCE.read_text(encoding="utf-8"))

    assert controller["artifact"] == "EvaAgentBenchFcControllerInputsProvenance/v1"
    assert controller["status"] == "historical_provenance_incomplete_superseded_by_v2"
    assert controller["replacement"]["path"] == (
        "benchmark/agentbench-fc-v1/agentrl-controller-inputs-provenance-amd64-v2.json"
    )
    assert controller_v2["artifact"] == "EvaAgentBenchFcControllerInputsProvenance/v2"
    assert controller_v2["artifactManifest"]["sha256"] == hashlib.sha256(
        CONTROLLER_INPUTS_V2.read_bytes()
    ).hexdigest()
    assert controller_v2["moduleCache"]["go"] == "1.24.5"
    assert controller_v2["moduleCache"]["repeatedRebuildSha256"] == (
        controller_v2["moduleCache"]["sha256"]
    )
    assert worker["artifact"] == "EvaAgentBenchFcWorkerWheelhouseProvenance/v1"
    assert worker["artifactManifest"]["sha256"] == hashlib.sha256(
        WORKER_WHEELHOUSE.read_bytes()
    ).hexdigest()
    assert worker["localBuildException"]["sourceDistribution"]["sha256"] == (
        "89a704cb389bf0a010a1fa050ce19342d23bf6371ebda1c21cfe8ff3ed488726"
    )
    assert "--allow-unsafe" in worker["resolver"]["command"]
    assert worker["materialization"]["ordinaryWheelCount"] == 56


def test_agentrl_protocol_probe_records_an_idle_nonbenchmark_registration() -> None:
    receipt = json.loads(PROBE_RECEIPT.read_text(encoding="utf-8"))
    readback = json.loads(PROBE_READBACK.read_text(encoding="utf-8"))

    assert receipt["artifact"] == "EvaAgentBenchFcProtocolProbeReceipt/v1"
    assert receipt["status"] == "protocol_registration_proven"
    assert receipt["inputs"]["probeConfig"]["sha256"] == hashlib.sha256(
        PROBE_CONFIG.read_bytes()
    ).hexdigest()
    assert receipt["observed"]["listWorkers"]["sha256"] == hashlib.sha256(
        PROBE_READBACK.read_bytes()
    ).hexdigest()
    assert receipt["observed"]["activeSamples"] == 0
    assert receipt["observed"]["registeredWorkerCount"] == 1
    assert readback["virtual-task"]["workers"]["1"]["current"] == 0
    assert receipt["verification"]["controllerWorkerRegistration"] is True


def test_agentrl_retained_rebuild_is_explicitly_blocked_by_its_vulnerability_gate() -> None:
    receipt = json.loads(RETAINED_REBUILD_RECEIPT.read_text(encoding="utf-8"))

    assert receipt["artifact"] == "EvaAgentBenchFcAgentRLRetainedRebuildReceipt/v1"
    assert receipt["status"] == "retained_offline_rebuild_proven_vulnerability_gate_failed"
    assert receipt["inputs"]["controllerInputs"]["manifestSha256"] == hashlib.sha256(
        CONTROLLER_INPUTS.read_bytes()
    ).hexdigest()
    assert receipt["inputs"]["workerWheelhouse"]["manifestSha256"] == hashlib.sha256(
        WORKER_WHEELHOUSE.read_bytes()
    ).hexdigest()
    assert receipt["verification"]["networklessNoCacheRebuild"] is True
    assert receipt["vulnerabilityGate"]["allowed"] is False
    assert receipt["vulnerabilityGate"]["controller"] == {
        "critical": 4,
        "high": 39,
        "rawReport": {
            "localReadOnlyPath": "/home/administrator/project/.eva2-artifacts/agentbench-fc/security-scan-2026-08-27/controller-trivy.json",
            "sha256": "c652b6e8fbcc77e43403cc97b758cbf0bfbf09c808af951b2b36fa7e9fa3d461",
        },
        "sbom": {
            "localReadOnlyPath": "/home/administrator/project/.eva2-artifacts/agentbench-fc/security-scan-2026-08-27/controller-sbom.cdx.json",
            "sha256": "45c874164383c2570ff0b0611fe51ed3a46e4aa552bd085c3d39c2f237f2e6ed",
        },
    }
    assert receipt["vulnerabilityGate"]["worker"]["critical"] == 9
    assert receipt["vulnerabilityGate"]["worker"]["high"] == 63


def test_agentrl_controller_v2_repro_receipt_binds_only_its_own_candidate() -> None:
    receipt = json.loads(CONTROLLER_V2_REPRO_RECEIPT.read_text(encoding="utf-8"))

    assert receipt["artifact"] == "EvaAgentBenchFcControllerReproReceipt/v2"
    assert receipt["status"] == "reproducible_controller_build_proven_security_and_stack_gates_open"
    assert receipt["inputs"]["controllerDockerfile"]["sha256"] == hashlib.sha256(
        CONTROLLER_V2_DOCKERFILE.read_bytes()
    ).hexdigest()
    assert receipt["inputs"]["controllerInputsManifest"]["sha256"] == hashlib.sha256(
        CONTROLLER_INPUTS_V2.read_bytes()
    ).hexdigest()
    assert receipt["inputs"]["controllerProvenance"]["sha256"] == hashlib.sha256(
        CONTROLLER_PROVENANCE_V2.read_bytes()
    ).hexdigest()
    assert receipt["verification"] == {
        "networklessNoCacheBuild": True,
        "readOnlyNonRootHelp": True,
        "twoIndependentModuleCacheRebuildsSameHash": True,
    }
    assert "not re-registered" in receipt["openBoundary"]
    assert "not passed a fresh vulnerability scan" in receipt["openBoundary"]


def test_agentrl_security_upgrade_probes_are_pinned_and_explicitly_incomplete() -> None:
    receipt = json.loads(SECURITY_UPGRADE_RECEIPT.read_text(encoding="utf-8"))
    controller = SECURITY_UPGRADE_CONTROLLER.read_text(encoding="utf-8")
    worker = SECURITY_UPGRADE_WORKER.read_text(encoding="utf-8")

    assert receipt["artifact"] == "EvaAgentBenchFcAgentRLSecurityUpgradeProbeReceipt/v1"
    assert receipt["status"] == "security_upgrade_probes_green_offline_dependency_closure_open"
    assert receipt["inputs"]["controllerDockerfile"]["sha256"] == hashlib.sha256(
        SECURITY_UPGRADE_CONTROLLER.read_bytes()
    ).hexdigest()
    assert receipt["inputs"]["controllerSecurityPatch"]["sha256"] == hashlib.sha256(
        SECURITY_UPGRADE_CONTROLLER_PATCH.read_bytes()
    ).hexdigest()
    assert receipt["inputs"]["workerDockerfile"]["sha256"] == hashlib.sha256(
        SECURITY_UPGRADE_WORKER.read_bytes()
    ).hexdigest()
    assert ":latest" not in controller
    assert ":latest" not in worker
    assert "git apply /tmp/security-upgrade.patch" in controller
    assert "cgr.dev/chainguard/static@sha256:" in controller
    assert "cgr.dev/chainguard/python@sha256:" in worker
    assert receipt["verification"]["controllerVulnerabilities"] == {"critical": 0, "high": 0}
    assert receipt["verification"]["workerVulnerabilities"] == {"critical": 0, "high": 0}
    assert receipt["artifacts"]["controllerScan"]["sha256"] == (
        "ee4cdc814417be343302ba5368f92285d83746087c2144f809e72cfd538d2e2e"
    )
    assert receipt["artifacts"]["workerScan"]["sha256"] == (
        "62dc306f9eec42084cdce43344e0f9e6f0dfc0069ce3da9f7a8241d01332aa69"
    )
    assert "must still be sealed" in receipt["openBoundary"]


def test_agentrl_offline_worker_wheelhouse_binds_exact_new_inputs() -> None:
    receipt = json.loads(OFFLINE_WORKER_RECEIPT.read_text(encoding="utf-8"))
    dockerfile = OFFLINE_WORKER_DOCKERFILE.read_text(encoding="utf-8")
    manifest = json.loads(OFFLINE_WORKER_WHEELHOUSE.read_text(encoding="utf-8"))

    assert receipt["artifact"] == "EvaAgentBenchFcAgentRLWorkerWheelhouseReceipt/v1"
    assert receipt["status"] == "sealed_worker_wheelhouse_and_offline_image_proven"
    assert receipt["inputs"]["workerDockerfile"]["sha256"] == hashlib.sha256(
        OFFLINE_WORKER_DOCKERFILE.read_bytes()
    ).hexdigest()
    assert receipt["inputs"]["workerRequirementsLock"]["sha256"] == hashlib.sha256(
        OFFLINE_WORKER_LOCK.read_bytes()
    ).hexdigest()
    assert receipt["inputs"]["wheelhouseManifest"]["sha256"] == hashlib.sha256(
        OFFLINE_WORKER_WHEELHOUSE.read_bytes()
    ).hexdigest()
    assert manifest["contract"] == "EvaAgentBenchFcWorkerWheelhouse/v2"
    assert len(manifest["artifacts"]) == 55
    assert {item["filename"] for item in manifest["localBuildArtifacts"]} == {
        "agentrl_worker-0.4.1.dev8+g6a73409-py3-none-any.whl",
        "etcd3-0.12.0-py2.py3-none-any.whl",
    }
    assert "--no-index --no-deps" in dockerfile
    assert "wheelhouse/*.whl" in dockerfile
    assert ":latest" not in dockerfile
    assert receipt["verification"]["dockerBuildNetwork"] == "none"
    assert receipt["verification"]["trivyVulnerabilities"] == {"critical": 0, "high": 0}
    assert "controller's upgraded Go module cache" in receipt["openBoundary"]


def test_agentrl_offline_go125_controller_binds_exact_new_inputs() -> None:
    receipt = json.loads(OFFLINE_CONTROLLER_RECEIPT.read_text(encoding="utf-8"))
    dockerfile = OFFLINE_CONTROLLER_DOCKERFILE.read_text(encoding="utf-8")
    manifest = json.loads(OFFLINE_CONTROLLER_INPUTS.read_text(encoding="utf-8"))

    assert receipt["artifact"] == "EvaAgentBenchFcAgentRLControllerGo125Receipt/v1"
    assert receipt["status"] == "sealed_controller_inputs_and_offline_image_proven"
    assert receipt["inputs"]["controllerDockerfile"]["sha256"] == hashlib.sha256(
        OFFLINE_CONTROLLER_DOCKERFILE.read_bytes()
    ).hexdigest()
    assert receipt["inputs"]["controllerInputsManifest"]["sha256"] == hashlib.sha256(
        OFFLINE_CONTROLLER_INPUTS.read_bytes()
    ).hexdigest()
    assert manifest["contract"] == "EvaAgentBenchFcControllerBuildInputs/v3"
    assert manifest["target"]["goToolchainVersion"] == "1.25.14"
    assert "GOPROXY=off" in dockerfile
    assert "go mod download" not in dockerfile
    assert "git apply /tmp/security-upgrade.patch" in dockerfile
    assert "cgr.dev/chainguard/static@sha256:" in dockerfile
    assert receipt["verification"]["dockerBuildNetwork"] == "none"
    assert receipt["verification"]["trivyVulnerabilities"] == {"critical": 0, "high": 0}
    assert "not yet been registered" in receipt["openBoundary"]


def test_agentrl_go125_protocol_probe_has_no_sample_or_benchmark_claim() -> None:
    receipt = json.loads(GO125_PROTOCOL_PROBE_RECEIPT.read_text(encoding="utf-8"))
    readback = json.loads(GO125_PROTOCOL_PROBE_READBACK.read_text(encoding="utf-8"))

    assert receipt["artifact"] == "EvaAgentBenchFcAgentRLGo125ProtocolProbeReceipt/v1"
    assert receipt["status"] == "sealed_candidate_protocol_registration_proven"
    assert receipt["inputs"]["probeConfig"]["sha256"] == hashlib.sha256(
        PROBE_CONFIG.read_bytes()
    ).hexdigest()
    assert receipt["observed"]["listWorkers"]["sha256"] == hashlib.sha256(
        GO125_PROTOCOL_PROBE_READBACK.read_bytes()
    ).hexdigest()
    assert receipt["observed"]["activeSamples"] == 0
    assert receipt["observed"]["registeredWorkerCount"] == 1
    assert readback["virtual-task"]["workers"]["1"]["current"] == 0
    assert readback["virtual-task"]["workers"]["1"]["status"] == "ALIVE"
    assert receipt["verification"]["temporaryContainersAndNetworkRemoved"] is True
    assert "No AgentBench task server" in receipt["openBoundary"]


def test_dbbench_dev_task_registration_is_real_but_unscored_and_socket_free() -> None:
    receipt = json.loads(DBBENCH_DEV_RECEIPT.read_text(encoding="utf-8"))
    readback = json.loads(DBBENCH_DEV_READBACK.read_text(encoding="utf-8"))
    config = DBBENCH_DEV_CONFIG.read_text(encoding="utf-8")
    dockerfile = DBBENCH_DEV_DOCKERFILE.read_text(encoding="utf-8")
    inputs = json.loads(DBBENCH_DEV_INPUTS.read_text(encoding="utf-8"))

    assert receipt["artifact"] == "EvaAgentBenchFcDbbenchDevRegistrationReceipt/v1"
    assert receipt["status"] == "sealed_official_dbbench_dev_task_registration_proven"
    assert receipt["inputs"]["dbbenchRegistrationConfig"]["sha256"] == hashlib.sha256(
        DBBENCH_DEV_CONFIG.read_bytes()
    ).hexdigest()
    assert receipt["inputs"]["dbbenchWorkerDockerfile"]["sha256"] == hashlib.sha256(
        DBBENCH_DEV_DOCKERFILE.read_bytes()
    ).hexdigest()
    assert receipt["inputs"]["dbbenchWorkerInputs"]["sha256"] == hashlib.sha256(
        DBBENCH_DEV_INPUTS.read_bytes()
    ).hexdigest()
    assert receipt["observed"]["devCaseCount"] == 60
    assert receipt["observed"]["activeSamples"] == 0
    assert readback["dbbench-dev-registration"]["workers"]["1"]["status"] == "ALIVE"
    assert len(readback["dbbench-dev-registration"]["indices"]) == 60
    assert "module: src.server.tasks.dbbench.DBBenchTask" in config
    assert "env_driver: manual" in config
    assert "docker.sock" not in dockerfile
    assert "--no-index --no-deps" in dockerfile
    assert inputs["contract"] == "EvaAgentBenchFcDbbenchDevWorkerInputs/v1"
    assert {entry["filename"] for entry in inputs["artifacts"]} == {
        "agentbench-dbbench-dev-d1e4a10.tar.gz",
        "mysql-connector-python-9.3.0.tar.gz",
        "mysql_connector_python-9.3.0-cp314-cp314-linux_x86_64.whl",
    }
    assert receipt["security"]["trivy"]["vulnerabilities"] == {"critical": 0, "high": 0}
    assert receipt["verification"]["manualEnvironmentHasNoDockerSocket"] is True
    assert "No sample" in receipt["openBoundary"]


def test_agentrl_source_images_receipt_binds_current_source_and_image_bytes() -> None:
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))

    assert receipt["artifact"] == "EvaAgentBenchFcAgentRLSourceImagesReceipt/v1"
    assert receipt["status"] == "local_source_builds_superseded_by_retained_rebuild_security_no_go"
    assert receipt["inputs"]["agentbenchCommit"] == "d1e4a10db08c87075c78972e48ecc182be03e2d5"
    assert receipt["inputs"]["agentrlCommit"] == "6a73409d31ba695d383b978a8ad3ef400d90c054"
    assert receipt["inputs"]["agentrlGitArchive"]["sha256"] == (
        "76a50942687e5646ad401adbb579e70fafb0761175e61019cc2c72cd0b7ce2ee"
    )
    assert receipt["inputs"]["controllerDockerfile"]["sha256"] == hashlib.sha256(
        CONTROLLER.read_bytes()
    ).hexdigest()
    assert receipt["inputs"]["controllerBuildInputs"]["sha256"] == hashlib.sha256(
        CONTROLLER_INPUTS.read_bytes()
    ).hexdigest()
    assert receipt["inputs"]["controllerInputsProvenance"]["sha256"] == hashlib.sha256(
        CONTROLLER_PROVENANCE.read_bytes()
    ).hexdigest()
    assert receipt["inputs"]["workerDockerfile"]["sha256"] == hashlib.sha256(
        WORKER.read_bytes()
    ).hexdigest()
    assert receipt["inputs"]["workerRequirementsLock"]["sha256"] == hashlib.sha256(
        WORKER_LOCK.read_bytes()
    ).hexdigest()
    assert receipt["inputs"]["workerWheelhouseManifest"]["sha256"] == hashlib.sha256(
        WORKER_WHEELHOUSE.read_bytes()
    ).hexdigest()
    assert receipt["inputs"]["workerWheelhouseProvenance"]["sha256"] == hashlib.sha256(
        WORKER_PROVENANCE.read_bytes()
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
    assert receipt["images"]["worker"]["labels"]["io.eva.agentrl-worker-lock-sha256"] == (
        receipt["inputs"]["workerRequirementsLock"]["sha256"]
    )
    assert receipt["images"]["controller"]["labels"]["io.eva.agentrl-controller-gomodcache-sha256"] == (
        "4abfff3108ce315dc34b7b0d3474c8a6431534a219d37b2369e4d08f71765b57"
    )
    assert receipt["verification"]["controllerOfflineImageBuild"] is True
    assert receipt["openBoundary"].startswith("This receipt records the original local source builds")
    assert "retained-rebuild" in receipt["openBoundary"]
