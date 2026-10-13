#!/usr/bin/env python3
"""Prove Inspect's Python and sandbox agent bridges without paid model calls."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import subprocess
import sys
import tomllib
from typing import Any
from urllib.parse import unquote, urlparse


INSPECT_VERSION = "0.3.263"
OPENAI_VERSION = "3.6.0"
INSPECT_WHEEL_SHA256 = (
    "503e7ff509d77fcdf65989027a19fea966fb668deb88a234468c3290964fd86a"
)
DEFAULT_SANDBOX_IMAGE = (
    "eva2/pc03-python@"
    "sha256:09f7da3bc104798d0afb40bc08d23ab2da20a76130cec1f2ef170848f5d85217"
)
LOCK_PATH = Path(__file__).with_name("pylock.toml")
REPO_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_PYTHON = "==3.12.*"
EXPECTED_ENVIRONMENTS = [
    "sys_platform == 'linux' and platform_machine == 'x86_64'"
]
EXPECTED_COMPLETION = "Default output from mockllm/model"
PYTHON_SAMPLE = "python-agent-bridge"
SANDBOX_SAMPLE = "sandbox-cli-agent-bridge"
SHA256 = re.compile(r"[0-9a-f]{64}")
GIT_COMMIT = re.compile(r"[0-9a-f]{40}")
SOURCE_FILES = {
    "benchmark/inspect_ai_v1/bridge_probe.py": Path(__file__).resolve(),
    "benchmark/inspect_ai_v1/pylock.toml": LOCK_PATH.resolve(),
}
DOCKER_INVENTORY_COMMANDS = {
    "containers": ["docker", "container", "ls", "--all", "--quiet", "--no-trunc"],
    "networks": ["docker", "network", "ls", "--quiet", "--no-trunc"],
    "volumes": ["docker", "volume", "ls", "--quiet"],
}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(131_072), b""):
            digest.update(chunk)
    return digest.hexdigest()


def bytes_sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _local_log_path(location: str) -> Path:
    parsed = urlparse(location)
    if parsed.scheme not in {"", "file"} or parsed.netloc not in {"", "localhost"}:
        raise RuntimeError(f"Inspect log is not a local file: {location!r}")
    return Path(unquote(parsed.path if parsed.scheme else location))


def verify_lock(path: Path = LOCK_PATH) -> dict[str, Any]:
    """Validate the platform-specific PEP 751 lock used only for evaluations."""
    document = tomllib.loads(path.read_text(encoding="utf-8"))
    if document.get("lock-version") != "1.0":
        raise RuntimeError("Inspect lock must use PEP 751 lock-version 1.0")
    if document.get("requires-python") != EXPECTED_PYTHON:
        raise RuntimeError("Inspect lock Python version differs")
    if document.get("environments") != EXPECTED_ENVIRONMENTS:
        raise RuntimeError("Inspect lock platform environment differs")
    packages = document.get("packages")
    if not isinstance(packages, list) or not packages:
        raise RuntimeError("Inspect lock has no packages")

    locked: dict[str, tuple[str, str]] = {}
    for package in packages:
        if not isinstance(package, dict):
            raise RuntimeError("Inspect lock contains a malformed package")
        name = package.get("name")
        version = package.get("version")
        wheels = package.get("wheels")
        if (
            not isinstance(name, str)
            or not isinstance(version, str)
            or not isinstance(wheels, list)
            or len(wheels) != 1
        ):
            raise RuntimeError("Every locked package must select exactly one wheel")
        wheel = wheels[0]
        hashes = wheel.get("hashes") if isinstance(wheel, dict) else None
        url = wheel.get("url") if isinstance(wheel, dict) else None
        sha256 = hashes.get("sha256") if isinstance(hashes, dict) else None
        if (
            not isinstance(url, str)
            or not url.startswith("https://files.pythonhosted.org/")
            or not isinstance(sha256, str)
            or SHA256.fullmatch(sha256) is None
        ):
            raise RuntimeError(f"Locked wheel for {name!r} lacks a trusted URL/hash")
        if name in locked:
            raise RuntimeError(f"Duplicate locked package: {name}")
        locked[name] = (version, sha256)

    if locked.get("inspect-ai") != (INSPECT_VERSION, INSPECT_WHEEL_SHA256):
        raise RuntimeError("Inspect version or wheel hash differs")
    if locked.get("openai", (None,))[0] != OPENAI_VERSION:
        raise RuntimeError("OpenAI SDK version differs")
    return {
        "lockSha256": file_sha256(path),
        "packageCount": len(locked),
        "inspectVersion": INSPECT_VERSION,
        "openaiVersion": OPENAI_VERSION,
        "python": EXPECTED_PYTHON,
        "environment": EXPECTED_ENVIRONMENTS[0],
    }


def current_source_commit(root: Path = REPO_ROOT) -> str:
    """Return the exact checked-out commit used to execute the probe."""
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    commit = result.stdout.strip()
    if GIT_COMMIT.fullmatch(commit) is None:
        raise RuntimeError("Probe source checkout has no exact Git commit")
    return commit


def _git_blob(commit: str, path: str, root: Path = REPO_ROOT) -> bytes:
    result = subprocess.run(
        ["git", "show", f"{commit}:{path}"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return result.stdout


def _git_tree(commit: str, root: Path = REPO_ROOT) -> str:
    result = subprocess.run(
        ["git", "rev-parse", f"{commit}^{{tree}}"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    tree = result.stdout.strip()
    if GIT_COMMIT.fullmatch(tree) is None:
        raise RuntimeError("Probe source commit has no exact Git tree")
    return tree


def verify_source_checkout(
    source_commit: str,
    source_files: dict[str, Path] = SOURCE_FILES,
) -> dict[str, Any]:
    """Bind the executable probe and lock bytes to the requested Git commit."""
    if GIT_COMMIT.fullmatch(source_commit) is None:
        raise RuntimeError("Probe requires an exact lowercase Git source commit")
    if source_commit != current_source_commit():
        raise RuntimeError("Probe source commit does not match the current checkout")
    hashes: dict[str, str] = {}
    for repository_path, local_path in source_files.items():
        if local_path.is_symlink() or not local_path.is_file():
            raise RuntimeError(f"Probe source file is not regular: {repository_path}")
        committed = _git_blob(source_commit, repository_path)
        working = local_path.read_bytes()
        if working != committed:
            raise RuntimeError(f"Probe source file differs from Git: {repository_path}")
        hashes[repository_path] = bytes_sha256(committed)
    return {
        "commit": source_commit,
        "tree": _git_tree(source_commit),
        "files": hashes,
    }


def docker_inventory() -> dict[str, list[str]]:
    """Read the Docker objects that a sandbox run could leave behind."""
    inventory: dict[str, list[str]] = {}
    for kind, command in DOCKER_INVENTORY_COMMANDS.items():
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        inventory[kind] = sorted(set(result.stdout.splitlines()))
    return inventory


def verified_docker_cleanup(
    before: dict[str, list[str]], after: dict[str, list[str]]
) -> dict[str, Any]:
    """Return a hashbound before/after inventory or reject residual objects."""
    added = {
        kind: sorted(set(after[kind]) - set(before[kind]))
        for kind in DOCKER_INVENTORY_COMMANDS
    }
    removed = {
        kind: sorted(set(before[kind]) - set(after[kind]))
        for kind in DOCKER_INVENTORY_COMMANDS
    }
    if any(added.values()):
        raise RuntimeError(f"Inspect sandbox left Docker objects behind: {added}")
    canonical_before = json.dumps(
        before, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    canonical_after = json.dumps(
        after, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return {
        "before": before,
        "after": after,
        "beforeSha256": bytes_sha256(canonical_before),
        "afterSha256": bytes_sha256(canonical_after),
        "added": added,
        "removed": removed,
        "residualAddedCount": 0,
    }


def _verify_runtime() -> None:
    document = tomllib.loads(LOCK_PATH.read_text(encoding="utf-8"))
    expected = {
        package["name"]: package["version"] for package in document["packages"]
    }
    actual: dict[str, str | None] = {}
    for name in expected:
        try:
            actual[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            actual[name] = None
    drift = {
        name: {"expected": version, "actual": actual[name]}
        for name, version in expected.items()
        if actual[name] != version
    }
    if drift:
        raise RuntimeError(f"Evaluator dependency versions differ: {drift}")


def _sandbox_cli_source() -> str:
    return """
import json
import os
import sys
from urllib.request import Request, urlopen

body = json.dumps({
    "model": "inspect",
    "messages": [{"role": "user", "content": sys.argv[1]}],
}).encode("utf-8")
request = Request(
    os.environ["OPENAI_BASE_URL"].rstrip("/") + "/chat/completions",
    data=body,
    headers={"Authorization": "Bearer inspect", "Content-Type": "application/json"},
)
with urlopen(request, timeout=20) as response:
    payload = json.load(response)
print(payload["choices"][0]["message"]["content"])
""".strip()


def _build_task(sandbox_image: str, path_hits: list[str]) -> Any:
    from inspect_ai import Task
    from inspect_ai.agent import agent, agent_bridge, sandbox_agent_bridge
    from inspect_ai.dataset import Sample
    from inspect_ai.model import ModelOutput
    from inspect_ai.scorer import match
    from inspect_ai.util import (
        ComposeConfig,
        ComposeService,
        SandboxEnvironmentSpec,
        sandbox,
    )
    from openai import AsyncOpenAI

    @agent(name="ev001_bridge_probe")
    def bridge_probe_agent() -> Any:
        async def execute(state: Any) -> Any:
            prompt = state.messages[-1].text
            if prompt == PYTHON_SAMPLE:
                async with agent_bridge(
                    state,
                    web_search=False,
                    code_execution=False,
                    client_mcp_servers=False,
                ) as bridge:
                    response = await AsyncOpenAI(api_key="inspect").responses.create(
                        model="inspect", input=prompt
                    )
                    completion = response.output_text
                    bridge.state.output = ModelOutput.from_content(
                        model="inspect", content=completion
                    )
                    path_hits.append(PYTHON_SAMPLE)
                    return bridge.state

            if prompt == SANDBOX_SAMPLE:
                async with sandbox_agent_bridge(
                    state,
                    sandbox="default",
                    web_search=False,
                    code_execution=False,
                    client_mcp_servers=False,
                ) as bridge:
                    result = await sandbox().exec(
                        ["python", "-c", _sandbox_cli_source(), prompt],
                        env={
                            "OPENAI_API_KEY": "inspect",
                            "OPENAI_BASE_URL": "http://localhost:13131/v1",
                        },
                        timeout=30,
                    )
                    if not result.success:
                        raise RuntimeError(f"Sandbox CLI failed: {result.stderr}")
                    completion = result.stdout.strip()
                    bridge.state.output = ModelOutput.from_content(
                        model="inspect", content=completion
                    )
                    path_hits.append(SANDBOX_SAMPLE)
                    return bridge.state
            raise RuntimeError(f"Unknown bridge probe sample: {prompt!r}")

        return execute

    compose = ComposeConfig(
        services={
            "default": ComposeService(
                image=sandbox_image,
                command=["tail", "-f", "/dev/null"],
                network_mode="none",
                init=True,
                pull_policy="never",
            )
        }
    )
    return Task(
        name="ev001_bridge_probe",
        dataset=[
            Sample(input=PYTHON_SAMPLE, target=EXPECTED_COMPLETION),
            Sample(input=SANDBOX_SAMPLE, target=EXPECTED_COMPLETION),
        ],
        solver=bridge_probe_agent(),
        scorer=match(),
        sandbox=SandboxEnvironmentSpec("docker", compose),
    )


def _assert_complete_log(log: Any) -> None:
    samples = log.samples or []
    if [sample.input for sample in samples] != [PYTHON_SAMPLE, SANDBOX_SAMPLE]:
        raise RuntimeError("Inspect log does not contain both bridge samples")
    if any(sample.output.completion != EXPECTED_COMPLETION for sample in samples):
        raise RuntimeError("A bridge returned an unexpected completion")
    if any(
        not sample.scores or next(iter(sample.scores.values())).value != "C"
        for sample in samples
    ):
        raise RuntimeError("A bridge sample did not receive a correct score")


def run_probe(
    output_dir: Path, sandbox_image: str, source_commit: str
) -> dict[str, Any]:
    """Run both bridges and prove immutable-log re-scoring in a new directory."""
    source = verify_source_checkout(source_commit)
    _verify_runtime()
    lock = verify_lock()
    if output_dir.exists() and any(output_dir.iterdir()):
        raise RuntimeError("Probe output directory must be absent or empty")
    docker_before = docker_inventory()
    output_dir.mkdir(parents=True, exist_ok=True)
    original_dir = output_dir / "original"
    original_dir.mkdir()

    from inspect_ai import eval as inspect_eval
    from inspect_ai import score
    from inspect_ai.log import list_eval_logs, read_eval_log, write_eval_log
    from inspect_ai.scorer import match

    path_hits: list[str] = []
    logs = inspect_eval(
        _build_task(sandbox_image, path_hits),
        model="mockllm/model",
        display="none",
        fail_on_error=True,
        log_dir=str(original_dir),
        log_format="eval",
        log_model_api=True,
        max_samples=1,
        sandbox_cleanup=True,
        sandbox_prebuilt=True,
    )
    if len(logs) != 1 or logs[0].status != "success":
        raise RuntimeError("Inspect bridge evaluation did not complete successfully")
    _assert_complete_log(logs[0])
    if path_hits != [PYTHON_SAMPLE, SANDBOX_SAMPLE]:
        raise RuntimeError(f"Not every bridge ran exactly once: {path_hits!r}")
    docker_cleanup = verified_docker_cleanup(docker_before, docker_inventory())

    infos = list_eval_logs(str(original_dir), formats=["eval"])
    if len(infos) != 1:
        raise RuntimeError("Expected exactly one original Inspect log")
    original_path = _local_log_path(infos[0].name)
    original_sha256 = file_sha256(original_path)
    original_log = read_eval_log(original_path)
    _assert_complete_log(original_log)
    rescored_log = score(original_log, scorers=[match()], copy=True, display="none")
    _assert_complete_log(rescored_log)
    rescored_path = output_dir / "rescored.eval"
    write_eval_log(rescored_log, rescored_path)
    if file_sha256(original_path) != original_sha256:
        raise RuntimeError("Re-scoring changed the original Inspect log")

    receipt = {
        "contract": "EvaInspectBridgeProbe/v1",
        "status": "passed",
        "sourceCommit": source_commit,
        "source": source,
        "paidModelCalls": 0,
        "model": "mockllm/model",
        "bridges": path_hits,
        "sandboxImage": sandbox_image,
        "sandboxNetworkMode": "none",
        "sandboxPullPolicy": "never",
        "dockerObjects": docker_cleanup,
        "lock": lock,
        "accuracy": 1.0,
        "originalLog": {
            "path": str(original_path.resolve()),
            "sha256": original_sha256,
        },
        "rescoredLog": {
            "path": str(rescored_path.resolve()),
            "sha256": file_sha256(rescored_path),
        },
    }
    receipt_path = output_dir / "receipt.json"
    receipt_path.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-lock", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--sandbox-image", default=DEFAULT_SANDBOX_IMAGE)
    parser.add_argument("--source-commit")
    arguments = parser.parse_args()
    if arguments.check_lock:
        print(json.dumps(verify_lock(), sort_keys=True))
        return 0
    if arguments.output is None:
        parser.error("--output is required unless --check-lock is used")
    if arguments.source_commit is None:
        parser.error("--source-commit is required unless --check-lock is used")
    print(
        json.dumps(
            run_probe(
                arguments.output, arguments.sandbox_image, arguments.source_commit
            ),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
