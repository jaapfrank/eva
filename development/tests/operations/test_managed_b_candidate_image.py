from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

import pytest

from tools.managed_b_candidate.base_image import (
    MaterializedBaseImage,
    MaterializedBaseImageLayout,
)
from tools.managed_b_candidate.build_context import MaterializedBuildContext
from tools.managed_b_candidate.candidate_image import (
    _buildx_executable_sha256,
    _builder_measurement,
    _hash_image_archive,
    _loaded_image_id,
    _read_iid,
    build_offline_candidate_image,
    validate_offline_candidate_image,
)
from tools.managed_b_candidate.private_receipt import (
    PrivateReceiptError,
    write_new_private_json,
)


BASE_DIGEST = "sha256:" + "1" * 64
BASE_ID = "sha256:" + "2" * 64
LIBS_DIGEST = "sha256:" + "5" * 64
LIBS_ID = "sha256:" + "6" * 64
RUNTIME_DIGEST = "sha256:" + "7" * 64
RUNTIME_ID = "sha256:" + "8" * 64
IMAGE_ID = "sha256:" + "3" * 64
ARCHIVE_SHA = "4" * 64
REPO_DIGEST = f"docker.io/library/python@{BASE_DIGEST}"
LIBS_REPO_DIGEST = f"docker.io/library/python@{LIBS_DIGEST}"
RUNTIME_REPO_DIGEST = f"gcr.io/distroless/cc-debian13@{RUNTIME_DIGEST}"
SOURCE_COMMIT = "a" * 40
SOURCE_DATE_EPOCH = 1787480000
PROJECT_ROOT = Path(__file__).resolve().parents[3]
BUILDER_IDENTITY = {
    "backend": "docker-buildx/docker",
    "buildkitNodeId": "buildkit-node-a",
    "buildkitVersion": "v0.26.2",
    "buildxExecutableSha256": "a" * 64,
    "buildxVersion": "github.com/docker/buildx v0.18.0 test",
    "containerdContainersNamespace": "moby",
    "containerdImageStore": "io.containerd.snapshotter.v1",
    "containerdPluginsNamespace": "plugins.moby",
    "containerdSocketIdentitySha256": "c" * 64,
    "daemonId": "daemon-a",
    "dockerExecutableSha256": "d" * 64,
    "dockerServerVersion": "29.1.3",
    "socketIdentitySha256": "e" * 64,
    "stateRootIdentitySha256": "f" * 64,
}


def test_runtime_image_installs_eva_package_before_removing_pip() -> None:
    dockerfile = (PROJECT_ROOT / "deploy/living-core/Dockerfile").read_text(
        encoding="utf-8"
    )
    install = "python -m pip install --no-cache-dir --no-index --no-deps --no-build-isolation ."
    provider_check = (
        "bundled_provider_factories(); assert any(factory.provider_id == 'mcp_gateway'"
    )
    dependency_check = "python -m pip check"
    remove_pip = "python -m pip uninstall -y pip wheel"
    remove_build_inputs = "rm -rf ./build ./wheelhouse ./*.egg-info ./src/*.egg-info"
    copy_runtime = "COPY --from=build /opt/eva /opt/eva"

    assert "COPY pyproject.toml ./pyproject.toml" in dockerfile
    assert install in dockerfile
    assert provider_check in dockerfile
    assert dockerfile.index(install) < dockerfile.index(provider_check)
    assert dockerfile.index(provider_check) < dockerfile.index(dependency_check)
    assert dockerfile.index(dependency_check) < dockerfile.index(remove_pip)
    assert dockerfile.index(remove_pip) < dockerfile.index(remove_build_inputs)
    assert dockerfile.index(remove_build_inputs) < dockerfile.index(copy_runtime)
    assert "io.eva2.source-commit=${SOURCE_COMMIT}" in dockerfile
    assert "io.eva2.source-date-epoch=${SOURCE_DATE_EPOCH}" in dockerfile
    assert 'io.eva2.source-refresh="git-archive-v1"' in dockerfile


@pytest.fixture
def native_tmp_path() -> Path:
    with tempfile.TemporaryDirectory(
        prefix="eva2-candidate-image-test-", dir="/tmp"
    ) as path:
        yield Path(path)


def _inputs(
    native_tmp_path: Path,
) -> tuple[
    MaterializedBuildContext,
    MaterializedBaseImage,
    MaterializedBaseImage,
    MaterializedBaseImage,
]:
    return (
        MaterializedBuildContext(native_tmp_path / "context", {}),
        MaterializedBaseImage(native_tmp_path / "build.oci.tar", {}),
        MaterializedBaseImage(native_tmp_path / "runtime-libraries.oci.tar", {}),
        MaterializedBaseImage(native_tmp_path / "runtime.oci.tar", {}),
    )


def _receipts() -> tuple[dict[str, object], list[dict[str, object]]]:
    return (
        {
            "build_image_archive_sha256": ARCHIVE_SHA,
            "build_image_digest": BASE_DIGEST,
            "build_image_repository": "docker.io/library/python",
            "runtime_libraries_image_archive_sha256": "9" * 64,
            "runtime_libraries_image_digest": LIBS_DIGEST,
            "runtime_libraries_image_repository": "docker.io/library/python",
            "runtime_image_archive_sha256": "a" * 64,
            "runtime_image_digest": RUNTIME_DIGEST,
            "runtime_image_repository": "gcr.io/distroless/cc-debian13",
            "context_tree_sha256": "5" * 64,
            "candidate_source_commit": SOURCE_COMMIT,
            "candidate_source_date_epoch": SOURCE_DATE_EPOCH,
        },
        [
            {
                "archive_sha256": ARCHIVE_SHA,
                "base_image_digest": BASE_DIGEST,
                "base_image_repository": "docker.io/library/python",
                "local_image_id": BASE_ID,
                "local_repo_digest": REPO_DIGEST,
                "oci_config_digest": BASE_ID,
                "oci_selected_manifest_digest": "sha256:" + "a" * 64,
            },
            {
                "archive_sha256": "9" * 64,
                "base_image_digest": LIBS_DIGEST,
                "base_image_repository": "docker.io/library/python",
                "local_image_id": LIBS_ID,
                "local_repo_digest": LIBS_REPO_DIGEST,
                "oci_config_digest": LIBS_ID,
                "oci_selected_manifest_digest": "sha256:" + "b" * 64,
            },
            {
                "archive_sha256": "a" * 64,
                "base_image_digest": RUNTIME_DIGEST,
                "base_image_repository": "gcr.io/distroless/cc-debian13",
                "local_image_id": RUNTIME_ID,
                "local_repo_digest": RUNTIME_REPO_DIGEST,
                "oci_config_digest": RUNTIME_ID,
                "oci_selected_manifest_digest": "sha256:" + "c" * 64,
            },
        ],
    )


def _stub_validators(monkeypatch: pytest.MonkeyPatch) -> None:
    context, bases = _receipts()
    monkeypatch.setattr(
        "tools.managed_b_candidate.candidate_image.validate_materialized_build_context",
        lambda _context: context,
    )
    monkeypatch.setattr(
        "tools.managed_b_candidate.candidate_image.validate_materialized_base_image",
        lambda value: bases[
            {"build.oci.tar": 0, "runtime-libraries.oci.tar": 1, "runtime.oci.tar": 2}[
                value.path.name
            ]
        ],
    )
    monkeypatch.setattr(
        "tools.managed_b_candidate.candidate_image._builder_measurement",
        lambda _runner: dict(BUILDER_IDENTITY),
    )
    role_by_name = {
        "build.oci.tar": "build",
        "runtime-libraries.oci.tar": "runtime_libraries",
        "runtime.oci.tar": "runtime",
    }
    receipt_by_role = dict(zip(("build", "runtime_libraries", "runtime"), bases))

    def materialize_layout(value, output_parent, *, role):
        assert role_by_name[value.path.name] == role
        base = receipt_by_role[role]
        return MaterializedBaseImageLayout(
            output_parent / f"{role}-oci-layout",
            {
                "archive_sha256": base["archive_sha256"],
                "base_image_digest": base["base_image_digest"],
                "base_image_repository": base["base_image_repository"],
                "contract": "EvaPinnedBaseImageLayout/v1",
                "content_read_only": True,
                "entries": [],
                "layout_tree_sha256": {
                    "build": "d" * 64,
                    "runtime_libraries": "e" * 64,
                    "runtime": "f" * 64,
                }[role],
                "oci_config_digest": base["oci_config_digest"],
                "oci_selected_manifest_digest": base[
                    "oci_selected_manifest_digest"
                ],
                "platform": "linux/amd64",
                "role": role,
                "status": "materialized_private_store",
            },
        )

    monkeypatch.setattr(
        "tools.managed_b_candidate.candidate_image.materialize_base_image_layout",
        materialize_layout,
    )
    monkeypatch.setattr(
        "tools.managed_b_candidate.candidate_image.validate_materialized_base_image_layout",
        lambda layout: layout.receipt,
    )


def _runner(
    *,
    buildkit_result_id: str = IMAGE_ID,
    candidate_tags: object = None,
    candidate_labels: object = None,
    candidate_environment: object = None,
):
    commands: list[list[str]] = []

    def run(command: list[str], _timeout: int) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if command[:3] == ["docker", "image", "inspect"]:
            reference = command[3]
            if reference == IMAGE_ID:
                labels = (
                    candidate_labels
                    if candidate_labels is not None
                    else {
                        "io.eva2.source-commit": SOURCE_COMMIT,
                        "io.eva2.source-date-epoch": str(SOURCE_DATE_EPOCH),
                        "io.eva2.source-refresh": "git-archive-v1",
                    }
                )
                value = [
                    {
                        "Config": {
                            "Env": (
                                [
                                    "PATH=/usr/local/bin",
                                    "LD_LIBRARY_PATH=/usr/local/lib",
                                ]
                                if candidate_environment is None
                                else candidate_environment
                            ),
                            "Labels": labels,
                        },
                        "Id": IMAGE_ID,
                        "RepoTags": candidate_tags,
                        "Size": 123,
                    }
                ]
            else:
                raise AssertionError(f"unexpected inspected image: {reference}")
            return subprocess.CompletedProcess(
                command, 0, stdout=json.dumps(value), stderr=""
            )
        if command[:3] == ["docker", "buildx", "build"]:
            iidfile = Path(command[command.index("--iidfile") + 1])
            iidfile.write_text(f"{buildkit_result_id}\n", encoding="ascii")
            output = command[command.index("--output") + 1]
            destination = Path(output.split(",dest=", 1)[1].split(",", 1)[0])
            destination.write_bytes(b"deterministic candidate archive")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        assert command[:4] == ["docker", "image", "load", "--input"]
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=f"Loaded image ID: {IMAGE_ID}\n",
            stderr="",
        )

    return commands, run


def test_measures_real_docker_and_buildkit_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tools.managed_b_candidate.candidate_image._identity_sha256",
        lambda _path, *, socket, label: (
            "c" if label == "containerd socket" else "a" if socket else "b"
        )
        * 64,
    )
    monkeypatch.setattr(
        "tools.managed_b_candidate.candidate_image._docker_executable_sha256",
        lambda: "c" * 64,
    )
    monkeypatch.setattr(
        "tools.managed_b_candidate.candidate_image._buildx_executable_sha256",
        lambda: "d" * 64,
    )

    def runner(command: list[str], _timeout: int) -> subprocess.CompletedProcess[str]:
        if command[:2] == ["docker", "info"]:
            value = {
                "Architecture": "x86_64",
                "DockerRootDir": "/private/builder-a",
                "DriverStatus": [
                    ["driver-type", "io.containerd.snapshotter.v1"]
                ],
                "ID": "daemon-a",
                "OSType": "linux",
                "Containerd": {
                    "Address": "/private/containerd-a.sock",
                    "Namespaces": {
                        "Containers": "moby",
                        "Plugins": "plugins.moby",
                    },
                },
            }
            return subprocess.CompletedProcess(command, 0, json.dumps(value), "")
        if command[:2] == ["docker", "version"]:
            value = {"Arch": "amd64", "Os": "linux", "Version": "29.1.3"}
            return subprocess.CompletedProcess(command, 0, json.dumps(value), "")
        if command[:3] == ["docker", "buildx", "version"]:
            return subprocess.CompletedProcess(
                command, 0, "github.com/docker/buildx v0.18.0 test\n", ""
            )
        if command[:3] == ["docker", "buildx", "ls"]:
            value = {
                "Driver": "docker",
                "Name": "default",
                "Nodes": [
                    {
                        "Endpoint": "default",
                        "IDs": ["node-a"],
                        "Status": "running",
                        "Version": "v0.26.2",
                    }
                ],
            }
            raw = json.dumps(value)
            return subprocess.CompletedProcess(command, 0, f"{raw}\n{raw}\n", "")
        raise AssertionError(command)

    assert _builder_measurement(runner) == {
        "backend": "docker-buildx/docker",
        "buildkitNodeId": "node-a",
        "buildkitVersion": "v0.26.2",
        "buildxExecutableSha256": "d" * 64,
        "buildxVersion": "github.com/docker/buildx v0.18.0 test",
        "containerdContainersNamespace": "moby",
        "containerdImageStore": "io.containerd.snapshotter.v1",
        "containerdPluginsNamespace": "plugins.moby",
        "containerdSocketIdentitySha256": "c" * 64,
        "daemonId": "daemon-a",
        "dockerExecutableSha256": "c" * 64,
        "dockerServerVersion": "29.1.3",
        "socketIdentitySha256": "a" * 64,
        "stateRootIdentitySha256": "b" * 64,
    }


def test_buildx_authority_hashes_only_the_private_plugin(
    native_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    docker_config = native_tmp_path / "docker-config"
    docker_config.mkdir(mode=0o700)
    plugin_directory = docker_config / "cli-plugins"
    plugin_directory.mkdir(mode=0o700)
    plugin = plugin_directory / "docker-buildx"
    plugin.write_bytes(b"bound buildx executable")
    plugin.chmod(0o500)
    expected = hashlib.sha256(plugin.read_bytes()).hexdigest()
    monkeypatch.setenv("DOCKER_CONFIG", str(docker_config))
    monkeypatch.setenv("EVA_RR002_BUILDX_SHA256", expected)

    assert _buildx_executable_sha256() == expected

    monkeypatch.setenv("EVA_RR002_BUILDX_SHA256", "0" * 64)
    with pytest.raises(RuntimeError, match="differs from authority"):
        _buildx_executable_sha256()

    monkeypatch.setenv("EVA_RR002_BUILDX_SHA256", expected)
    docker_config.chmod(0o755)
    with pytest.raises(RuntimeError, match="executable is unsafe"):
        _buildx_executable_sha256()


@pytest.mark.parametrize(
    ("driver_status", "containerd"),
    [
        ([], {"Address": "/private/containerd.sock", "Namespaces": {"Containers": "moby", "Plugins": "plugins.moby"}}),
        (["not-a-pair"], {"Address": "/private/containerd.sock", "Namespaces": {"Containers": "moby", "Plugins": "plugins.moby"}}),
        ([["driver-type", "io.containerd.snapshotter.v1"]], None),
        ([["driver-type", "io.containerd.snapshotter.v1"]], {"Address": "", "Namespaces": {"Containers": "moby", "Plugins": "plugins.moby"}}),
        ([["driver-type", "io.containerd.snapshotter.v1"]], {"Address": "/private/containerd.sock", "Namespaces": {"Containers": "moby", "Plugins": "moby"}}),
    ],
)
def test_refuses_builder_without_bound_containerd_export_capability(
    monkeypatch: pytest.MonkeyPatch,
    driver_status: object,
    containerd: object,
) -> None:
    monkeypatch.setattr(
        "tools.managed_b_candidate.candidate_image._identity_sha256",
        lambda _path, *, socket, label: "a" * 64,
    )
    monkeypatch.setattr(
        "tools.managed_b_candidate.candidate_image._docker_executable_sha256",
        lambda: "b" * 64,
    )
    monkeypatch.setattr(
        "tools.managed_b_candidate.candidate_image._buildx_executable_sha256",
        lambda: "c" * 64,
    )
    commands: list[list[str]] = []

    def runner(command: list[str], _timeout: int) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if command[:2] == ["docker", "info"]:
            value = {
                "Architecture": "x86_64",
                "Containerd": containerd,
                "DockerRootDir": "/private/builder-a",
                "DriverStatus": driver_status,
                "ID": "daemon-a",
                "OSType": "linux",
            }
            return subprocess.CompletedProcess(command, 0, json.dumps(value), "")
        if command[:2] == ["docker", "version"]:
            value = {"Arch": "amd64", "Os": "linux", "Version": "29.1.3"}
            return subprocess.CompletedProcess(command, 0, json.dumps(value), "")
        if command[:3] == ["docker", "buildx", "version"]:
            return subprocess.CompletedProcess(
                command, 0, "github.com/docker/buildx v0.18.0 test\n", ""
            )
        if command[:3] == ["docker", "buildx", "ls"]:
            value = {
                "Driver": "docker",
                "Name": "default",
                "Nodes": [{"Endpoint": "default", "IDs": ["node-a"], "Status": "running", "Version": "v0.26.2"}],
            }
            return subprocess.CompletedProcess(command, 0, json.dumps(value), "")
        raise AssertionError(command)

    with pytest.raises(RuntimeError, match="containerd image store or Docker exporter"):
        _builder_measurement(runner)

    assert not any(command[:3] == ["docker", "buildx", "build"] for command in commands)


def test_private_receipt_handles_partial_writes_and_rejects_linked_parent(
    native_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = native_tmp_path / "private"
    parent.mkdir(mode=0o700)
    target = parent / "receipt.json"
    real_write = os.write

    def partial_write(descriptor: int, raw: object) -> int:
        view = memoryview(raw)[:3]
        return real_write(descriptor, view)

    monkeypatch.setattr(
        "tools.managed_b_candidate.private_receipt._write", partial_write
    )
    write_new_private_json(target, {"status": "passed"}, label="test receipt")
    assert json.loads(target.read_bytes()) == {"status": "passed"}
    assert target.stat().st_mode & 0o777 == 0o600
    with pytest.raises(PrivateReceiptError, match="one new file"):
        write_new_private_json(target, {}, label="test receipt")

    actual = native_tmp_path / "actual"
    actual.mkdir(mode=0o700)
    linked = native_tmp_path / "linked"
    linked.symlink_to(actual, target_is_directory=True)
    with pytest.raises(PrivateReceiptError, match="must not use links"):
        write_new_private_json(linked / "receipt.json", {}, label="linked test receipt")


def test_builds_an_untagged_networkless_candidate_without_b(
    monkeypatch: pytest.MonkeyPatch, native_tmp_path: Path
) -> None:
    _stub_validators(monkeypatch)
    context, build, runtime_libraries, runtime = _inputs(native_tmp_path)
    output = native_tmp_path / "output"
    output.mkdir(mode=0o700)
    commands, runner = _runner()

    candidate = build_offline_candidate_image(
        context, build, runtime_libraries, runtime, output, runner=runner
    )

    assert candidate.image_id == IMAGE_ID
    assert candidate.receipt["network_used"] is False
    builder_binding = candidate.receipt["builder"]
    assert builder_binding["before"] == BUILDER_IDENTITY
    assert builder_binding["after"] == BUILDER_IDENTITY
    assert len(builder_binding["runChallenge"]) == 64
    assert candidate.receipt["candidate_runtime_library_path"] == (
        "LD_LIBRARY_PATH=/usr/local/lib"
    )
    assert (
        validate_offline_candidate_image(candidate, runner=runner)["status"]
        == "built_local_candidate"
    )
    build = next(
        command for command in commands if command[:3] == ["docker", "buildx", "build"]
    )
    assert "--network=none" in build
    assert build[build.index("--builder") + 1] == "default"
    assert "--platform=linux/amd64" in build
    assert "--pull=false" in build
    assert "--no-cache" in build
    assert "--load" not in build
    assert build[build.index("--provenance=false")] == "--provenance=false"
    exporter = build[build.index("--output") + 1]
    assert exporter.endswith(",rewrite-timestamp=true")
    assert exporter.startswith("type=docker,dest=")
    assert build[build.index("--build-arg") + 1] == f"SOURCE_COMMIT={SOURCE_COMMIT}"
    second_build_arg = build.index("--build-arg", build.index("--build-arg") + 1)
    assert build[second_build_arg + 1] == f"SOURCE_DATE_EPOCH={SOURCE_DATE_EPOCH}"
    assert build[build.index("--file") + 1].endswith("deploy/living-core/Dockerfile")
    assert "--tag" not in build and "--push" not in build
    contexts = [
        build[index + 1]
        for index, value in enumerate(build)
        if value == "--build-context"
    ]
    expected_contexts = (
        (REPO_DIGEST, "build", BASE_DIGEST),
        (LIBS_REPO_DIGEST, "runtime_libraries", LIBS_DIGEST),
        (RUNTIME_REPO_DIGEST, "runtime", RUNTIME_DIGEST),
    )
    assert len(contexts) == 3
    for value, (reference, role, digest) in zip(contexts, expected_contexts):
        name, source = value.split("=", 1)
        assert name == reference
        layout_path, selector = source.removeprefix("oci-layout://").rsplit("@", 1)
        assert Path(layout_path).parent.parent == output
        assert Path(layout_path).parent.name.startswith("eva2-base-layouts-")
        assert Path(layout_path).name == f"{role}-oci-layout"
        assert selector == digest
    assert candidate.receipt["candidate_image_archive_sha256"] == (
        "b149322f319af29425d045ed3d9b5e94a44129bc5b5dabf045fb139fad802a3f"
    )
    assert candidate.receipt["candidate_image_config_digest"] == IMAGE_ID
    assert candidate.receipt["candidate_buildkit_result_digest"] == IMAGE_ID
    assert candidate.receipt["candidate_timestamp_rewrite_enabled"] is True
    assert candidate.receipt["candidate_provenance_attestation_enabled"] is False
    assert candidate.receipt["candidate_image_exporter"] == "docker"
    inspected = [
        command[3]
        for command in commands
        if command[:3] == ["docker", "image", "inspect"]
    ]
    assert inspected == [IMAGE_ID, IMAGE_ID]
    assert candidate.receipt["build_image_layout_tree_sha256"] == "d" * 64
    assert candidate.receipt["runtime_libraries_image_layout_tree_sha256"] == "e" * 64
    assert candidate.receipt["runtime_image_layout_tree_sha256"] == "f" * 64
    assert candidate.artifact_path is not None
    assert candidate.artifact_path.parent == output
    assert (candidate.artifact_path / "candidate.tar").read_bytes() == (
        b"deterministic candidate archive"
    )
    persisted_receipt = json.loads(
        (candidate.artifact_path / "candidate-image-receipt.json").read_bytes()
    )
    assert persisted_receipt == candidate.receipt
    assert (candidate.artifact_path / "candidate.tar").stat().st_mode & 0o777 == 0o600
    assert (
        candidate.artifact_path / "candidate-image-receipt.json"
    ).stat().st_mode & 0o777 == 0o600


def test_refuses_a_role_swapped_oci_layout_before_build(
    monkeypatch: pytest.MonkeyPatch, native_tmp_path: Path
) -> None:
    _stub_validators(monkeypatch)
    context, build, runtime_libraries, runtime = _inputs(native_tmp_path)
    output = native_tmp_path / "output"
    output.mkdir(mode=0o700)

    called = False

    def swapped(value, output_parent, *, role):
        nonlocal called
        called = True
        return MaterializedBaseImageLayout(
            output_parent / "swapped",
            {
                "archive_sha256": "9" * 64,
                "base_image_digest": LIBS_DIGEST,
                "base_image_repository": "docker.io/library/python",
                "layout_tree_sha256": "e" * 64,
                "oci_config_digest": LIBS_ID,
                "oci_selected_manifest_digest": "sha256:" + "b" * 64,
                "role": "runtime_libraries",
            },
        )

    monkeypatch.setattr(
        "tools.managed_b_candidate.candidate_image.materialize_base_image_layout",
        swapped,
    )
    monkeypatch.setattr(
        "tools.managed_b_candidate.candidate_image.validate_materialized_base_image_layout",
        lambda layout: layout.receipt,
    )

    with pytest.raises(RuntimeError, match="build OCI layout binding differs"):
        build_offline_candidate_image(
            context,
            build,
            runtime_libraries,
            runtime,
            output,
            runner=lambda *_args: (_ for _ in ()).throw(AssertionError("Docker called")),
        )

    assert called is True
    assert list(output.iterdir()) == []


def test_keeps_buildkit_and_rewritten_archive_identities_distinct(
    monkeypatch: pytest.MonkeyPatch, native_tmp_path: Path
) -> None:
    _stub_validators(monkeypatch)
    context, build, runtime_libraries, runtime = _inputs(native_tmp_path)
    output = native_tmp_path / "output"
    output.mkdir(mode=0o700)
    pre_rewrite = "sha256:" + "4" * 64
    _commands, runner = _runner(buildkit_result_id=pre_rewrite)

    candidate = build_offline_candidate_image(
        context,
        build,
        runtime_libraries,
        runtime,
        output,
        runner=runner,
    )

    assert candidate.receipt["candidate_buildkit_result_digest"] == pre_rewrite
    assert candidate.receipt["candidate_image_config_digest"] == pre_rewrite

    candidate.receipt["candidate_image_config_digest"] = IMAGE_ID
    with pytest.raises(RuntimeError, match="candidate config digest differs"):
        validate_offline_candidate_image(candidate, runner=runner)


def test_refuses_a_candidate_that_receives_a_tag(
    monkeypatch: pytest.MonkeyPatch, native_tmp_path: Path
) -> None:
    _stub_validators(monkeypatch)
    context, build, runtime_libraries, runtime = _inputs(native_tmp_path)
    output = native_tmp_path / "output"
    output.mkdir(mode=0o700)
    _commands, runner = _runner(candidate_tags=["unexpected:latest"])

    with pytest.raises(RuntimeError, match="must not receive a tag"):
        build_offline_candidate_image(
            context, build, runtime_libraries, runtime, output, runner=runner
        )

    assert list(output.iterdir()) == []


def test_refuses_builder_identity_change_during_build(
    monkeypatch: pytest.MonkeyPatch, native_tmp_path: Path
) -> None:
    _stub_validators(monkeypatch)
    context, build, runtime_libraries, runtime = _inputs(native_tmp_path)
    output = native_tmp_path / "output"
    output.mkdir(mode=0o700)
    changed = dict(BUILDER_IDENTITY)
    changed["daemonId"] = "daemon-b"
    measurements = iter((dict(BUILDER_IDENTITY), changed))
    monkeypatch.setattr(
        "tools.managed_b_candidate.candidate_image._builder_measurement",
        lambda _runner: next(measurements),
    )
    _commands, runner = _runner()

    with pytest.raises(RuntimeError, match="identity changed during build"):
        build_offline_candidate_image(
            context, build, runtime_libraries, runtime, output, runner=runner
        )

    assert list(output.iterdir()) == []


def test_refuses_an_exporter_option_injected_output_path(
    monkeypatch: pytest.MonkeyPatch, native_tmp_path: Path
) -> None:
    _stub_validators(monkeypatch)
    context, build, runtime_libraries, runtime = _inputs(native_tmp_path)
    output = native_tmp_path / "output,rewrite-timestamp=false"
    output.mkdir(mode=0o700)
    _commands, runner = _runner()

    with pytest.raises(RuntimeError, match="explicit safe path"):
        build_offline_candidate_image(
            context, build, runtime_libraries, runtime, output, runner=runner
        )


def test_refuses_a_candidate_with_different_source_identity(
    monkeypatch: pytest.MonkeyPatch, native_tmp_path: Path
) -> None:
    _stub_validators(monkeypatch)
    context, build, runtime_libraries, runtime = _inputs(native_tmp_path)
    output = native_tmp_path / "output"
    output.mkdir(mode=0o700)
    _commands, runner = _runner(
        candidate_labels={
            "io.eva2.source-commit": "b" * 40,
            "io.eva2.source-date-epoch": str(SOURCE_DATE_EPOCH),
            "io.eva2.source-refresh": "git-archive-v1",
        }
    )

    with pytest.raises(RuntimeError, match="source identity differs"):
        build_offline_candidate_image(
            context, build, runtime_libraries, runtime, output, runner=runner
        )

    assert list(output.iterdir()) == []


@pytest.mark.parametrize(
    "environment",
    (
        [],
        ["LD_LIBRARY_PATH=/tmp"],
        [
            "LD_LIBRARY_PATH=/usr/local/lib",
            "LD_LIBRARY_PATH=/tmp",
        ],
    ),
)
def test_refuses_a_candidate_without_one_exact_runtime_library_path(
    monkeypatch: pytest.MonkeyPatch,
    native_tmp_path: Path,
    environment: list[str],
) -> None:
    _stub_validators(monkeypatch)
    context, build, runtime_libraries, runtime = _inputs(native_tmp_path)
    output = native_tmp_path / "output"
    output.mkdir(mode=0o700)
    _commands, runner = _runner(candidate_environment=environment)

    with pytest.raises(RuntimeError, match="runtime library path differs"):
        build_offline_candidate_image(
            context, build, runtime_libraries, runtime, output, runner=runner
        )

    assert list(output.iterdir()) == []


def test_refuses_a_symlinked_candidate_id_file(native_tmp_path: Path) -> None:
    target = native_tmp_path / "target"
    target.write_text(f"{IMAGE_ID}\n", encoding="ascii")
    iidfile = native_tmp_path / "image.iid"
    iidfile.symlink_to(target)

    with pytest.raises(RuntimeError, match="ID file is unsafe"):
        _read_iid(iidfile)


def test_hashes_only_one_safe_regular_candidate_archive(native_tmp_path: Path) -> None:
    archive = native_tmp_path / "candidate.tar"
    archive.write_bytes(b"deterministic candidate archive")

    assert _hash_image_archive(archive) == (
        "b149322f319af29425d045ed3d9b5e94a44129bc5b5dabf045fb139fad802a3f"
    )

    unsafe = native_tmp_path / "unsafe.tar"
    unsafe.write_bytes(b"archive")
    unsafe.chmod(0o666)
    with pytest.raises(RuntimeError, match="archive is unsafe"):
        _hash_image_archive(unsafe)


@pytest.mark.parametrize(
    "stdout",
    (
        "",
        "Loaded image: candidate:latest\n",
        f"Loaded image ID: {IMAGE_ID}\nLoaded image ID: {IMAGE_ID}\n",
        "Loaded image ID: sha256:not-a-digest\n",
    ),
)
def test_refuses_ambiguous_or_invalid_docker_load_output(stdout: str) -> None:
    result = subprocess.CompletedProcess(["docker", "image", "load"], 0, stdout, "")

    with pytest.raises(RuntimeError):
        _loaded_image_id(result)
