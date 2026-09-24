"""Run a saved Python procedure offline in an operator-pinned local image.

Procedure convention: read one JSON value from stdin (for example
``json.load(sys.stdin)``), then print its result to stdout. Output is untrusted
text, never a semantic success verdict. The trusted image must contain no user
data or secrets; no host credentials, socket or writable host directory is mounted.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import time
import uuid

from eva.operations.component_evaluation import EvaluationConfig, _capture


@dataclass(frozen=True)
class ProcedureExecutionConfig:
    image: str
    deadline_seconds: int = 30
    output_bytes: int = 16384
    memory_mb: int = 256
    cpus: float = 1.0
    pids: int = 64

    def __post_init__(self):
        if not isinstance(self.image, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", self.image):
            raise ValueError("procedure execution requires an operator-pinned local image ID")
        EvaluationConfig(self.image, ("/usr/local/bin/python",), self.deadline_seconds,
                         self.output_bytes, self.memory_mb, self.cpus, self.pids)


def _json_bytes(value):
    def validate(item, depth=0):
        if depth > 64:
            raise ValueError("procedure JSON nesting exceeds bound")
        if item is None or type(item) in (bool, int, float, str):
            return
        if type(item) is list:
            for child in item:
                validate(child, depth + 1)
            return
        if type(item) is dict and all(type(key) is str for key in item):
            for child in item.values():
                validate(child, depth + 1)
            return
        raise ValueError("procedure input must contain JSON values only")
    validate(value)
    try:
        payload = json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, UnicodeError, OverflowError) as error:
        raise ValueError("procedure input is not valid bounded JSON") from error
    if len(payload) > 32768:
        raise ValueError("procedure JSON input exceeds 32768 bytes")
    return payload


def _capture_input(argv, payload, *, deadline, limit, cancel_event):
    started = time.monotonic()
    process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")})
    buffers, counts = [bytearray(), bytearray()], [0, 0]
    def drain(pipe, index):
        try:
            while chunk := pipe.read(8192):
                counts[index] += len(chunk)
                buffers[index].extend(chunk[:max(0, limit - len(buffers[index]))])
        finally:
            pipe.close()
    def feed():
        try:
            process.stdin.write(payload)
            process.stdin.flush()
        except (BrokenPipeError, OSError):
            pass
        finally:
            try:
                process.stdin.close()
            except (BrokenPipeError, OSError):
                pass
    threads = [threading.Thread(target=drain, args=(pipe, index), daemon=True)
               for index, pipe in enumerate((process.stdout, process.stderr))]
    threads.append(threading.Thread(target=feed, daemon=True))
    for thread in threads:
        thread.start()
    timed_out = cancelled = False
    try:
        while process.poll() is None:
            cancelled = cancel_event is not None and cancel_event.is_set()
            timed_out = time.monotonic() - started >= deadline
            if cancelled or timed_out:
                process.kill()
                break
            try:
                process.wait(timeout=0.1)
            except subprocess.TimeoutExpired:
                pass
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        for thread in threads:
            thread.join(timeout=2)
    if any(thread.is_alive() for thread in threads):
        raise RuntimeError("procedure process streams did not close")
    return {"exit_code": process.returncode, "timed_out": timed_out, "cancelled": cancelled,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "stdout": bytes(buffers[0]).decode("utf-8", "replace"),
            "stderr": bytes(buffers[1]).decode("utf-8", "replace"),
            "stdout_truncated": counts[0] > limit, "stderr_truncated": counts[1] > limit}


class ProcedureExecutor:
    def __init__(self, config: ProcedureExecutionConfig):
        if not isinstance(config, ProcedureExecutionConfig):
            raise ValueError("procedure executor requires trusted operator configuration")
        self.config = config

    def execute(self, source: str, input_data, *, cancel_event: threading.Event | None = None) -> dict:
        if not isinstance(source, str):
            raise ValueError("procedure source must be UTF-8 text")
        source_bytes = source.encode("utf-8")
        if not 1 <= len(source_bytes) <= 16384:
            raise ValueError("procedure source exceeds 16384 bytes or is empty")
        payload = _json_bytes(input_data)
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("procedure execution cancelled before dispatch")
        cfg = self.config
        name = "eva-procedure-execution-" + uuid.uuid4().hex
        with tempfile.TemporaryDirectory(prefix="eva-procedure-execution-") as directory:
            path = Path(directory) / "procedure.py"
            path.write_bytes(source_bytes)
            path.chmod(0o444)
            argv = ["docker", "run", "--rm", "--no-healthcheck", "--pull", "never", "--name", name,
                "--network", "none", "--log-driver", "none", "--read-only", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges", "--memory", f"{cfg.memory_mb}m",
                "--memory-swap", f"{cfg.memory_mb}m", "--cpus", str(cfg.cpus),
                "--pids-limit", str(cfg.pids), "--ulimit", "nofile=128:128",
                "--user", "65534:65534", "--workdir", "/tmp", "--interactive",
                "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=16777216,mode=1777",
                "--mount", f"type=bind,src={path},dst=/eva-procedure.py,readonly",
                "--entrypoint", "/usr/local/bin/python"]
            bootstrap = ("import os; os.execve('/usr/local/bin/python',"
                "['/usr/local/bin/python','-I','-B','/eva-procedure.py'],"
                "{'PATH':'/usr/local/bin:/usr/bin:/bin','HOME':'/tmp',"
                "'PYTHONDONTWRITEBYTECODE':'1','LD_LIBRARY_PATH':'/usr/local/lib'})")
            argv += [cfg.image, "-I", "-c", bootstrap]
            try:
                result = _capture_input(argv, payload, deadline=cfg.deadline_seconds,
                                        limit=cfg.output_bytes, cancel_event=cancel_event)
            finally:
                cleanup = _capture(["docker", "rm", "--force", name], deadline=10, limit=2048)
                if cleanup["timed_out"] or (cleanup["exit_code"] != 0
                        and "No such container" not in cleanup["stderr"]):
                    raise RuntimeError("procedure execution container cleanup failed")
        return {"contract": "EvaIsolatedProcedureExecution/v1", "image_id": cfg.image,
                "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
                "input_sha256": hashlib.sha256(payload).hexdigest(),
                "input_convention": "one_json_value_on_stdin", "result": result,
                "limits": {"deadline_seconds": cfg.deadline_seconds,
                    "output_bytes_per_stream": cfg.output_bytes, "memory_mb": cfg.memory_mb,
                    "cpu_millicores": round(cfg.cpus * 1000), "pids": cfg.pids},
                "semantic_success_verified": False, "installed": False}
