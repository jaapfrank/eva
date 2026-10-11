"""Execute a fixed operator check against isolated baseline/candidate containers.

This is measurement, not installation or a semantic improvement verdict. The
image and argv must come from trusted operator configuration, never model input.
The image must contain no secrets or user data. Environment inherited from the
image is cleared before execution; no host environment or credentials are mounted.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
import threading
import time
import uuid


@dataclass(frozen=True)
class EvaluationConfig:
    image: str
    command: tuple[str, ...]
    deadline_seconds: int = 60
    output_bytes: int = 16384
    memory_mb: int = 256
    cpus: float = 1.0
    pids: int = 64

    def __post_init__(self):
        if (not isinstance(self.image, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/:@-]{0,255}", self.image)
                or not isinstance(self.command, tuple) or not 1 <= len(self.command) <= 32
                or any(not isinstance(x, str) or not x or len(x) > 4096 or "\x00" in x for x in self.command)
                or type(self.deadline_seconds) is not int or not 1 <= self.deadline_seconds <= 300
                or type(self.output_bytes) is not int or not 1024 <= self.output_bytes <= 65536
                or type(self.memory_mb) is not int or not 64 <= self.memory_mb <= 1024
                or type(self.pids) is not int or not 16 <= self.pids <= 128
                or not isinstance(self.cpus, (int, float)) or not 0.1 <= self.cpus <= 2):
            raise ValueError("invalid trusted component evaluation configuration")


_SAVED_TARGET_KEYS = {"kind", "procedure_reference_id", "scope", "artifact_path",
    "content_sha256", "receipt_sha256", "source_call_id", "source_message_id",
    "tool_configuration_sha256", "retention_until"}
_SHA256 = re.compile(r"[0-9a-f]{64}")


def validate_saved_artifact_target(target, *, relative_path=None, baseline_sha256=None):
    """Validate a host-resolved source manifest; this does not grant source access.

    Current authority, source availability and retention are checked by the
    existing procedure resolver before dispatch. No executable path or suite is
    taken from this manifest: its only runner path is derived from the reference.
    """
    from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256
    if (not isinstance(target, dict) or set(target) != _SAVED_TARGET_KEYS
            or target.get("kind") != "saved_artifact"):
        raise ValueError("saved artifact target fields differ")
    scope = target["scope"]
    if (not isinstance(scope, dict) or set(scope) != {
            "tenant_id", "installation_id", "principal_id", "project_id"}
            or any(not isinstance(scope[key], str) or not scope[key].strip()
                   or len(scope[key]) > 256 or "\x00" in scope[key]
                   for key in ("tenant_id", "installation_id", "principal_id"))
            or (scope["project_id"] is not None and (
                not isinstance(scope["project_id"], str) or not scope["project_id"].strip()
                or len(scope["project_id"]) > 200 or "\x00" in scope["project_id"]))):
        raise ValueError("saved artifact source scope is invalid")
    for key in ("content_sha256", "receipt_sha256", "tool_configuration_sha256"):
        if not isinstance(target[key], str) or not _SHA256.fullmatch(target[key]):
            raise ValueError("saved artifact source hash is invalid")
    for key in ("source_call_id", "source_message_id"):
        if (not isinstance(target[key], str) or not target[key].strip()
                or len(target[key]) > 1024 or "\x00" in target[key]):
            raise ValueError("saved artifact source identity is invalid")
    artifact = target["artifact_path"]
    if not isinstance(artifact, str):
        raise ValueError("saved artifact path is invalid")
    path = PurePosixPath(artifact)
    if (not path.is_absolute() or str(path) != artifact or len(artifact) > 4096
            or path.suffix != ".py" or any(part in {".", ".."} for part in path.parts)
            or "\x00" in artifact):
        raise ValueError("saved artifact path is invalid")
    retention = target["retention_until"]
    if not isinstance(retention, str) or len(retention) > 64:
        raise ValueError("saved artifact retention is invalid")
    try:
        until = datetime.fromisoformat(retention.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("saved artifact retention is invalid") from None
    if until.tzinfo is None or until.utcoffset() is None:
        raise ValueError("saved artifact retention is unbound")
    reference_hash = canonical_sha256({"scope": scope, "receipt": target["receipt_sha256"]})
    if target["procedure_reference_id"] != "procedure-reference:" + reference_hash:
        raise ValueError("saved artifact reference scope and receipt binding differs")
    if relative_path is not None and relative_path != "saved_artifact_" + reference_hash + ".py":
        raise ValueError("saved artifact virtual path differs")
    if baseline_sha256 is not None and target["content_sha256"] != baseline_sha256:
        raise ValueError("saved artifact baseline manifest hash differs")
    return json.loads(canonical_json_bytes(target))


@dataclass(frozen=True)
class CandidateRequest:
    relative_path: str
    baseline_sha256: str
    source: str
    baseline_source: str | None = None
    source_target: dict | None = None

    def __post_init__(self):
        if not isinstance(self.relative_path, str):
            raise ValueError("candidate path must be text")
        path = PurePosixPath(self.relative_path)
        if (not isinstance(self.relative_path, str) or len(self.relative_path) > 240
                or path.is_absolute() or str(path) != self.relative_path
                or not path.parts or any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", p) for p in path.parts[:-1])
                or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\.py", path.name)
                or not isinstance(self.baseline_sha256, str)
                or not re.fullmatch(r"[0-9a-f]{64}", self.baseline_sha256)
                or not isinstance(self.source, str) or not 1 <= len(self.source.encode("utf-8")) <= 65536):
            raise ValueError("invalid bounded Python candidate")
        if (self.baseline_source is None) != (self.source_target is None):
            raise ValueError("saved artifact requires baseline source and target together")
        if self.source_target is not None:
            if (not isinstance(self.baseline_source, str)
                    or not 1 <= len(self.baseline_source.encode("utf-8")) <= 65536
                    or hashlib.sha256(self.baseline_source.encode("utf-8")).hexdigest() != self.baseline_sha256):
                raise ValueError("saved artifact baseline source hash differs")
            object.__setattr__(self, "source_target", validate_saved_artifact_target(
                self.source_target, relative_path=self.relative_path,
                baseline_sha256=self.baseline_sha256))


def _capture(argv, *, deadline, limit):
    """Drain both streams without retaining unbounded output in parent memory."""
    started = time.monotonic()
    process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")})
    buffers = [bytearray(), bytearray()]
    counts = [0, 0]
    def drain(pipe, index):
        try:
            while chunk := pipe.read(8192):
                counts[index] += len(chunk)
                buffers[index].extend(chunk[:max(0, limit - len(buffers[index]))])
        finally:
            pipe.close()
    threads = [threading.Thread(target=drain, args=(pipe, index), daemon=True)
               for index, pipe in enumerate((process.stdout, process.stderr))]
    for thread in threads:
        thread.start()
    timed_out = False
    try:
        process.wait(timeout=deadline)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        process.wait()
    for thread in threads:
        thread.join(timeout=2)
    if any(thread.is_alive() for thread in threads):
        raise RuntimeError("component evaluator output stream did not close")
    return {"exit_code": process.returncode, "timed_out": timed_out,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "stdout": bytes(buffers[0]).decode("utf-8", "replace"),
            "stderr": bytes(buffers[1]).decode("utf-8", "replace"),
            "stdout_truncated": counts[0] > limit, "stderr_truncated": counts[1] > limit}


class ComponentEvaluator:
    def __init__(self, config: EvaluationConfig, *, assessment=None, source_target=None):
        self.config = config
        self.assessment = assessment
        self.source_target = None if source_target is None else validate_saved_artifact_target(source_target)

    def _container(self, image_id, command, *, mount=None, output_limit=None):
        cfg = self.config
        name = "eva-component-evaluation-" + uuid.uuid4().hex
        argv = ["docker", "run", "--rm", "--no-healthcheck", "--pull", "never", "--name", name,
                "--network", "none", "--log-driver", "none", "--read-only", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges", "--memory", f"{cfg.memory_mb}m",
                "--memory-swap", f"{cfg.memory_mb}m", "--cpus", str(cfg.cpus),
                "--pids-limit", str(cfg.pids), "--ulimit", "nofile=128:128",
                "--user", "65534:65534", "--workdir", "/opt/eva/src",
                "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=16777216,mode=1777",
                "--entrypoint", "/usr/local/bin/python"]
        if mount is not None:
            source, destination = mount
            argv += ["--mount", f"type=bind,src={source},dst={destination},readonly"]
        # The existing minimal runtime image has no shell or /usr/bin/env.
        bootstrap = ("import os,sys; os.execve(sys.argv[1],sys.argv[1:],"
                     "{'PATH':'/usr/local/bin:/usr/bin:/bin','HOME':'/tmp',"
                     "'PYTHONDONTWRITEBYTECODE':'1','PYTHONPATH':'/opt/eva/src',"
                     "'PYTEST_DISABLE_PLUGIN_AUTOLOAD':'1','LD_LIBRARY_PATH':'/usr/local/lib'})")
        argv += [image_id, "-I", "-c", bootstrap, *command]
        try:
            return _capture(argv, deadline=cfg.deadline_seconds,
                            limit=output_limit or cfg.output_bytes)
        finally:
            # Kill/remove even when the CLI timed out or the check never started.
            cleanup = _capture(["docker", "rm", "--force", name], deadline=10, limit=2048)
            if cleanup["timed_out"] or (cleanup["exit_code"] != 0
                    and "No such container" not in cleanup["stderr"]):
                raise RuntimeError("component evaluation container cleanup failed")

    def evaluate(self, request: CandidateRequest) -> dict:
        if self.source_target is not None and request.source_target != self.source_target:
            raise ValueError("saved artifact differs from fixed assessment target")
        cfg = self.config
        inspection = _capture(["docker", "image", "inspect", "--format", "{{.Id}}", cfg.image],
                              deadline=10, limit=1024)
        image_id = inspection["stdout"].strip()
        if (inspection["exit_code"] != 0 or inspection["timed_out"]
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id)):
            raise RuntimeError("trusted local evaluation image is unavailable")
        target = "/opt/eva/src/eva/" + request.relative_path
        manifest = None
        if request.source_target is None:
            reader = ("/usr/local/bin/python", "-I", "-c",
                "import hashlib,json,pathlib,sys; p=pathlib.Path(sys.argv[1]); "
                "assert str(p.resolve())==str(p) and p.is_file(); "
                "f=p.open('rb'); b=f.read(65537); assert len(b)<=65536; "
                "print(json.dumps({'sha256':hashlib.sha256(b).hexdigest()}))", target)
            source_read = self._container(image_id, reader, output_limit=1024)
            if source_read["exit_code"] != 0 or source_read["timed_out"] or source_read["stdout_truncated"]:
                raise RuntimeError("baseline Python source unavailable or exceeds bound")
            if json.loads(source_read["stdout"]).get("sha256") != request.baseline_sha256:
                raise ValueError("baseline source changed")
            baseline = self._container(image_id, cfg.command)
        else:
            # Revalidate the copied manifest at dispatch; both legs use the same
            # trusted image/command and identical virtual module destination.
            request = CandidateRequest(request.relative_path, request.baseline_sha256,
                request.source, request.baseline_source, request.source_target)
            manifest = request.source_target
        candidate_bytes = request.source.encode("utf-8")
        with tempfile.TemporaryDirectory(prefix="eva-component-evaluation-") as directory:
            if manifest is not None:
                original = Path(directory) / "baseline.py"
                original.write_bytes(request.baseline_source.encode("utf-8"))
                original.chmod(0o444)
                baseline = self._container(image_id, cfg.command, mount=(str(original), target))
            candidate = Path(directory) / "candidate.py"
            candidate.write_bytes(candidate_bytes)
            candidate.chmod(0o444)
            measured = self._container(image_id, cfg.command, mount=(str(candidate), target))
        command_json = json.dumps(cfg.command, separators=(",", ":")).encode()
        report = {"contract": "EvaIsolatedComponentEvaluation/v1", "configured_image": cfg.image,
                "image_id": image_id, "relative_path": request.relative_path,
                "baseline_sha256": request.baseline_sha256,
                "candidate_sha256": hashlib.sha256(candidate_bytes).hexdigest(),
                "command": list(cfg.command), "command_sha256": hashlib.sha256(command_json).hexdigest(),
                "limits": {"deadline_seconds_per_container": cfg.deadline_seconds,
                           "output_bytes_per_stream": cfg.output_bytes, "memory_mb": cfg.memory_mb,
                           "cpu_millicores": round(cfg.cpus * 1000), "pids": cfg.pids},
                "baseline": baseline, "candidate": measured,
                "semantic_improvement_verified": False, "installed": False}
        if manifest is not None:
            report["source_target"] = manifest
        if self.assessment is not None:
            report["development_assessment"] = self.assessment.assess(report)
        return report
