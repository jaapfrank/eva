from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Sequence
import xml.etree.ElementTree as ET

import pytest

from tools import run_managed_b_browser_release_subgate as gate


ROOT = Path(__file__).resolve().parents[3]
CANDIDATE = "sha256:" + "a" * 64
COMMIT = "b" * 40


class DockerFixture:
    def __init__(
        self,
        *,
        bad_collection: bool = False,
        bad_junit: bool = False,
        bad_controller_label: bool = False,
        missing_runtime_library_path: bool = False,
    ):
        self.calls: list[tuple[str, ...]] = []
        self.bad_collection = bad_collection
        self.bad_junit = bad_junit
        self.bad_controller_label = bad_controller_label
        self.missing_runtime_library_path = missing_runtime_library_path
        self.removed: set[tuple[str, str]] = set()
        self.stopped: set[str] = set()
        self.image_ids = {
            CANDIDATE: CANDIDATE,
            gate.PLAYWRIGHT_IMAGE: "sha256:" + "c" * 64,
            gate.FIXTURE_IMAGE: "sha256:" + "d" * 64,
            gate.POSTGRES_IMAGE: "sha256:" + "e" * 64,
        }

    def __call__(self, argv: Sequence[str], timeout: float) -> gate.Completed:
        del timeout
        call = tuple(argv)
        self.calls.append(call)
        if call[:3] == ("docker", "image", "inspect"):
            reference = call[3]
            config: dict[str, object] = {"User": ""}
            if reference == CANDIDATE:
                config = {
                    "Env": (
                        []
                        if self.missing_runtime_library_path
                        else ["LD_LIBRARY_PATH=/usr/local/lib"]
                    ),
                    "Labels": {"io.eva2.source-commit": COMMIT},
                    "User": "65532:65532",
                }
            return self._json(
                [
                    {
                        "Config": config,
                        "Id": self.image_ids[reference],
                        "RepoDigests": ([] if reference == CANDIDATE else [reference]),
                    }
                ]
            )
        if call[:3] == ("docker", "container", "inspect"):
            name = call[3]
            if ("container", name) in self.removed:
                return gate.Completed(1, "", "absent")
            return self._json([self._container(name)])
        if call[:3] == ("docker", "network", "inspect"):
            name = call[3]
            if ("network", name) in self.removed:
                return gate.Completed(1, "", "absent")
            return self._json(
                [
                    {
                        "Id": hashlib.sha256(name.encode()).hexdigest(),
                        "IPAM": {"Config": [{"Gateway": ""}]},
                        "Internal": True,
                        "Labels": {
                            "io.eva2.browser-release-subgate": self._nonce(name)
                        },
                        "Options": {"com.docker.network.bridge.inhibit_ipv4": "true"},
                    }
                ]
            )
        if call[:3] == ("docker", "rm", "--force"):
            self.removed.add(("container", call[3]))
        elif call[:3] == ("docker", "network", "rm"):
            self.removed.add(("network", call[3]))
        if "--collect-only" in call:
            nodeids = (
                gate.EXPECTED_NODEIDS[:-1]
                if self.bad_collection
                else gate.EXPECTED_NODEIDS
            )
            return gate.Completed(0, "\n".join("/gate/" + item for item in nodeids), "")
        if "--junitxml=/gate-output/junit.xml" in call:
            return gate.Completed(0, "not trusted", "")
        if call[:2] == ("docker", "stop"):
            self.stopped.add(call[-1])
            return gate.Completed(0, "", "")
        if call[:2] == ("docker", "cp"):
            destination = Path(call[-1])
            suite = ET.Element(
                "testsuite",
                tests="3" if self.bad_junit else "4",
                failures="0",
                errors="0",
                skipped="0",
            )
            ET.ElementTree(suite).write(
                destination, encoding="utf-8", xml_declaration=True
            )
        return gate.Completed(0, "", "")

    @staticmethod
    def _json(value: object) -> gate.Completed:
        return gate.Completed(0, json.dumps(value), "")

    def _container(self, name: str) -> dict[str, object]:
        nonce_prefix = name.rsplit("-", 1)[0]
        db_network = nonce_prefix + "-db-net"
        browser_network = nonce_prefix + "-browser-net"
        if name.endswith("-db"):
            image, user, networks, mounts = (
                self.image_ids[gate.POSTGRES_IMAGE],
                "999:999",
                {db_network},
                [],
            )
            declared_ports = {"5432/tcp": None}
        elif name.endswith("-fixture"):
            image, user, networks, mounts = (
                self.image_ids[gate.FIXTURE_IMAGE],
                "65532:65532",
                {browser_network},
                [
                    {
                        "Destination": "/fixture_server.py",
                        "RW": False,
                        "Source": str(ROOT / gate.FIXTURE_RELATIVE),
                        "Type": "bind",
                    }
                ],
            )
            declared_ports = {}
        elif name.endswith("-browser"):
            image, user, networks, mounts = (
                self.image_ids[gate.PLAYWRIGHT_IMAGE],
                "1000:1000",
                {browser_network},
                [],
            )
            declared_ports = {"8931/tcp": None}
        else:
            image, user, networks, mounts = (
                CANDIDATE,
                "65532:65532",
                {db_network, browser_network},
                [
                    {
                        "Destination": "/gate/tests",
                        "RW": False,
                        "Source": self._mount_source("/gate/tests"),
                        "Type": "bind",
                    },
                    {
                        "Destination": "/gate/site-packages",
                        "RW": False,
                        "Source": self._mount_source("/gate/site-packages"),
                        "Type": "bind",
                    },
                    {
                        "Destination": "/gate/tools",
                        "RW": False,
                        "Source": self._mount_source("/gate/tools"),
                        "Type": "bind",
                    },
                    {
                        "Destination": "/gate/" + gate.SCHEDULE_RELATIVE,
                        "RW": False,
                        "Source": self._mount_source("/gate/" + gate.SCHEDULE_RELATIVE),
                        "Type": "bind",
                    },
                    {
                        "Destination": "/gate-output",
                        "RW": True,
                        "Source": self._mount_source("/gate-output"),
                        "Type": "bind",
                    },
                ],
            )
            declared_ports = {}
        authority_nonce = self._nonce(name)
        if self.bad_controller_label and name.endswith("-controller"):
            authority_nonce = "0" * 16
        return {
            "Config": {
                "Labels": {"io.eva2.browser-release-subgate": authority_nonce},
                "User": user,
            },
            "HostConfig": {
                "CapDrop": ["ALL"],
                "PortBindings": {},
                "Privileged": False,
                "ReadonlyRootfs": True,
                "Tmpfs": (
                    {"/gate": "bound", "/tmp": "bound"}
                    if name.endswith("-controller")
                    else {}
                ),
            },
            "Image": image,
            "Id": hashlib.sha256(name.encode()).hexdigest(),
            "Mounts": mounts,
            "NetworkSettings": {
                "Networks": {item: {} for item in networks},
                "Ports": declared_ports,
            },
            "State": {"Running": name not in self.stopped},
        }

    def _mount_source(self, destination: str) -> str:
        suffixes = {f",dst={destination}", f",dst={destination},readonly"}
        return next(
            item.split("src=", 1)[1].split(",dst=", 1)[0]
            for call in self.calls
            for item in call
            if item.startswith("type=bind,src=")
            and any(item.endswith(suffix) for suffix in suffixes)
        )

    @staticmethod
    def _nonce(name: str) -> str:
        return name.split("eva2-browser-gate-", 1)[1].split("-", 1)[0]


def _run(
    tmp_path: Path, docker: DockerFixture, *, candidate: str = CANDIDATE
) -> tuple[dict[str, object], Path]:
    site = tmp_path / "site"
    site.mkdir()
    (site / "pytest.py").write_text("# fixture\n", encoding="utf-8")
    receipt = tmp_path / "receipt.json"
    result = gate.run_browser_subgate(
        repository_root=ROOT,
        candidate_image_id=candidate,
        expected_candidate_source_commit=COMMIT,
        gate_site_packages=site.absolute(),
        expected_gate_site_packages_sha256=gate.directory_sha256(site.absolute()),
        expected_tests_sha256=gate.directory_sha256(
            (ROOT / "development/tests").absolute(), ignore_python_cache=True
        ),
        expected_tools_sha256=gate.directory_sha256(
            (ROOT / "src/tools").absolute(), ignore_python_cache=True
        ),
        expected_harness_sha256=gate._sha256(Path(gate.__file__).read_bytes()),
        receipt_path=receipt.absolute(),
        command_runner=docker,
        pause=lambda _delay: None,
    )
    return result, receipt


def test_success_uses_candidate_product_code_exact_nodeids_and_junit(
    tmp_path: Path,
) -> None:
    docker = DockerFixture()

    result, receipt = _run(tmp_path, docker)

    assert result["status"] == "passed"
    assert result["execution"]["collectedNodeids"] == list(gate.EXPECTED_NODEIDS)
    assert result["execution"]["junit"] == {
        "tests": 4,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
    }
    assert receipt.is_file()
    creates = [
        call
        for call in docker.calls
        if call[:2] in {("docker", "run"), ("docker", "create")}
    ]
    assert len(creates) == 4
    assert all(
        "--pull" in call and call[call.index("--pull") + 1] == "never"
        for call in creates
    )
    controller = next(call for call in creates if call[1] == "create")
    assert CANDIDATE in controller
    assert not any("src=" + str(ROOT / "src") in item for item in controller)
    assert any(
        "os.symlink('/opt/eva/migrations','/gate/migrations')" in item
        for item in controller
    )
    assert (
        "PYTHONPATH=/opt/eva/src:/opt/eva:/gate:/gate/tests/postgres:/gate/site-packages"
        in controller
    )
    assert all("--publish" not in call and "-p" not in call for call in creates)
    network_creates = [
        call for call in docker.calls if call[:3] == ("docker", "network", "create")
    ]
    assert len(network_creates) == 2
    assert all(
        "--internal" in call and "com.docker.network.bridge.inhibit_ipv4=true" in call
        for call in network_creates
    )
    stop_index = next(
        index
        for index, call in enumerate(docker.calls)
        if call[:2] == ("docker", "stop")
    )
    copy_index = next(
        index for index, call in enumerate(docker.calls) if call[:2] == ("docker", "cp")
    )
    assert stop_index < copy_index


def test_non_image_id_is_rejected_before_docker(tmp_path: Path) -> None:
    docker = DockerFixture()

    with pytest.raises(gate.BrowserSubgateError, match="authority argument"):
        _run(tmp_path, docker, candidate="candidate:latest")

    assert docker.calls == []


def test_candidate_without_runtime_library_path_is_rejected_before_resources(
    tmp_path: Path,
) -> None:
    docker = DockerFixture(missing_runtime_library_path=True)

    with pytest.raises(gate.BrowserSubgateError, match="candidate image authority"):
        _run(tmp_path, docker)

    assert not any(
        call[:2] in {("docker", "run"), ("docker", "create")} for call in docker.calls
    )


@pytest.mark.parametrize("failure", ("collection", "junit"))
def test_nodeid_or_junit_drift_emits_no_receipt_and_cleans(
    tmp_path: Path, failure: str
) -> None:
    docker = DockerFixture(
        bad_collection=failure == "collection", bad_junit=failure == "junit"
    )

    with pytest.raises(gate.BrowserSubgateError):
        _run(tmp_path, docker)

    assert not (tmp_path / "receipt.json").exists()
    assert any(call[:3] == ("docker", "rm", "--force") for call in docker.calls)
    assert any(call[:3] == ("docker", "network", "rm") for call in docker.calls)


def test_inspected_authority_label_drift_fails_and_cleans(tmp_path: Path) -> None:
    docker = DockerFixture(bad_controller_label=True)

    with pytest.raises(gate.BrowserSubgateError, match="isolation differs"):
        _run(tmp_path, docker)

    assert not (tmp_path / "receipt.json").exists()
    assert any(call[:3] == ("docker", "rm", "--force") for call in docker.calls)


def test_directory_hash_rejects_symlink(tmp_path: Path) -> None:
    tree = tmp_path / "tree"
    tree.mkdir()
    target = tree / "target"
    target.write_text("bound", encoding="utf-8")
    (tree / "alias").symlink_to(target)

    with pytest.raises(gate.BrowserSubgateError, match="unsafe tree entry"):
        gate.directory_sha256(tree.absolute())
