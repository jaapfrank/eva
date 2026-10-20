"""Execute a fixed operator check against isolated baseline/candidate containers.

This is measurement, not installation or a semantic improvement verdict. The
image and argv must come from trusted operator configuration, never model input.
The image must contain no secrets or user data. Environment inherited from the
image is cleared before execution; no host environment or credentials are mounted.
"""
from __future__ import annotations

from dataclasses import dataclass
import ast
import base64
import csv
import io
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

# Compatibility export for existing operator callers.
from eva.contracts.component_target import validate_saved_artifact_target


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


WORK_STRATEGY_PATH = "bootstrap/delegated_digital_runner.py"


def _work_strategy_surroundings(raw):
    """Only one literal-return body may vary; all surrounding UTF-8 bytes are fixed."""
    module = ast.parse(raw.decode("utf-8"))
    classes = [node for node in module.body if isinstance(node, ast.ClassDef)
               and node.name == "_TaskModel"]
    if len(classes) != 1:
        raise ValueError("work strategy requires exactly one target class")
    methods = [node for node in classes[0].body
               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
               and node.name == "_work_strategy"]
    if len(methods) != 1 or not isinstance(methods[0], ast.FunctionDef):
        raise ValueError("work strategy requires exactly one synchronous method")
    method = methods[0]
    if (len(method.body) != 1 or not isinstance(method.body[0], ast.Return)
            or not isinstance(method.body[0].value, ast.Constant)
            or not isinstance(method.body[0].value.value, str)
            or not method.body[0].value.value.strip()
            or len(method.body[0].value.value.encode("utf-8")) > 4096):
        raise ValueError("work strategy body must return only a bounded nonempty literal string")
    first = method.body[0]
    lines = raw.splitlines(keepends=True)
    start = sum(map(len, lines[:first.lineno - 1])) + first.col_offset
    end = sum(map(len, lines[:method.end_lineno - 1])) + method.end_col_offset
    return raw[:start], raw[end:]


def validate_work_strategy_candidate(baseline, candidate):
    if _work_strategy_surroundings(baseline) != _work_strategy_surroundings(candidate):
        raise ValueError("candidate changes bytes outside the work strategy body")


@dataclass(frozen=True)
class CandidateRequest:
    relative_path: str
    baseline_sha256: str
    source: str
    baseline_source: str | None = None
    source_target: dict | None = None
    baseline_package_content: str | None = None

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
        if self.baseline_package_content is not None:
            validated_skill_package_context(self.source_target, self.baseline_package_content,
                                            baseline_source=self.baseline_source)


def validated_skill_package_context(source_target, package_content, *, baseline_source=None):
    """Validate a transport-only copy against the original saved package identity."""
    binding = source_target.get("skill_package_binding") if isinstance(source_target, dict) else None
    if binding is None:
        if package_content is not None:
            raise ValueError("package context requires a saved package resource target")
        return None
    from eva.application.skill_packages import AgentSkillPackage, MAX_PACKAGE_BYTES
    if (not isinstance(package_content, str)
            or not 1 <= len(package_content.encode("utf-8")) <= MAX_PACKAGE_BYTES * 2):
        raise ValueError("saved skill package context is missing or exceeds bound")
    raw = package_content.encode("utf-8")
    if hashlib.sha256(raw).hexdigest() != binding["package_content_sha256"]:
        raise ValueError("saved skill package envelope hash differs")
    package = AgentSkillPackage.from_wire(json.loads(package_content))
    if package.manifest() != binding["package_manifest"]:
        raise ValueError("saved skill package manifest differs")
    resource = package.resource(binding["resource_path"])
    if (hashlib.sha256(resource).hexdigest() != source_target["content_sha256"]
            or (baseline_source is not None and resource != baseline_source.encode("utf-8"))):
        raise ValueError("saved skill package resource baseline differs")
    return package


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

    def _container(self, image_id, command, *, mount=None, output_limit=None, extra_mounts=()):
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
        for source, destination in (*extra_mounts, *((mount,) if mount is not None else ())):
            # Docker parses --mount as CSV; valid package paths may contain commas.
            encoded = io.StringIO()
            csv.writer(encoded, lineterminator="").writerow(
                ("type=bind", f"src={source}", f"dst={destination}", "readonly"))
            argv += ["--mount", encoded.getvalue()]
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
        # Revalidate before any image/container access, including mutated requests.
        request = CandidateRequest(request.relative_path, request.baseline_sha256,
            request.source, request.baseline_source, request.source_target,
            request.baseline_package_content)
        package = validated_skill_package_context(request.source_target,
            request.baseline_package_content, baseline_source=request.baseline_source)
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
            strategy = request.relative_path == WORK_STRATEGY_PATH
            source_result = ("{'sha256':hashlib.sha256(b).hexdigest(),"
                "'source_base64':base64.b64encode(b).decode('ascii')}" if strategy else
                "{'sha256':hashlib.sha256(b).hexdigest()}")
            reader = ("/usr/local/bin/python", "-I", "-c",
                "import base64,hashlib,json,pathlib,sys; p=pathlib.Path(sys.argv[1]); "
                "assert str(p.resolve())==str(p) and p.is_file(); "
                "f=p.open('rb'); b=f.read(65537); assert len(b)<=65536; "
                "print(json.dumps(" + source_result + "))", target)
            source_read = self._container(image_id, reader, output_limit=90112 if strategy else 1024)
            if source_read["exit_code"] != 0 or source_read["timed_out"] or source_read["stdout_truncated"]:
                raise RuntimeError("baseline Python source unavailable or exceeds bound")
            readback = json.loads(source_read["stdout"])
            if readback.get("sha256") != request.baseline_sha256:
                raise ValueError("baseline source changed")
            if strategy:
                try:
                    original = base64.b64decode(readback["source_base64"], validate=True)
                except (KeyError, TypeError, ValueError):
                    raise ValueError("work strategy baseline source is missing or malformed") from None
                if (len(original) > 65536
                        or hashlib.sha256(original).hexdigest() != request.baseline_sha256):
                    raise ValueError("work strategy baseline bytes differ")
                validate_work_strategy_candidate(original, request.source.encode("utf-8"))
            baseline = self._container(image_id, cfg.command)
        else:
            # Revalidate the copied manifest at dispatch; both legs use the same
            # trusted image/command and identical virtual module destination.
            manifest = request.source_target
        candidate_bytes = request.source.encode("utf-8")
        with tempfile.TemporaryDirectory(prefix="eva-component-evaluation-") as directory:
            context = {}
            package_directories = []
            if package is not None:
                package_root = Path(directory) / "package"
                package_root.mkdir()
                for resource in package.files:
                    destination = package_root / resource.path
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(resource.content)
                    destination.chmod(0o444)
                package_directories = [package_root, *(p for p in package_root.rglob("*") if p.is_dir())]
                for path in package_directories:
                    path.chmod(0o555)
                context = {"extra_mounts": ((str(package_root), "/eva-skill"),)}
                target = "/eva-skill/" + manifest["skill_package_binding"]["resource_path"]
            try:
                if manifest is not None:
                    original = Path(directory) / "baseline.py"
                    original.write_bytes(request.baseline_source.encode("utf-8"))
                    original.chmod(0o444)
                    baseline = self._container(image_id, cfg.command,
                        mount=(str(original), target), **context)
                candidate = Path(directory) / "candidate.py"
                candidate.write_bytes(candidate_bytes)
                candidate.chmod(0o444)
                measured = self._container(image_id, cfg.command,
                    mount=(str(candidate), target), **context)
            finally:
                # Only the host's disposable directory regains cleanup access.
                for path in package_directories:
                    path.chmod(0o700)
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
