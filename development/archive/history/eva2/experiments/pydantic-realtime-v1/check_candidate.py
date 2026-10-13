"""Offline dependency-surface check for the isolated Pydantic Realtime trial.

No provider connection, audio capture, environment secret or production import
is made here.  A real V02 receipt needs a separately authorised provider and a
physical microphone/speaker path.
"""

from __future__ import annotations

from importlib.metadata import version
import inspect
import json
from pathlib import Path
import re

from pydantic_ai import Agent
from pydantic_ai.realtime import RealtimeSession


ROOT = Path(__file__).resolve().parents[2]
REQUIREMENTS = ROOT / "experiments/pydantic-realtime-v1/requirements.in"
LOCK = ROOT / "experiments/pydantic-realtime-v1/requirements.lock"
PRODUCTION_LOCK = ROOT / "requirements.lock"
PIN = re.compile(r"^([A-Za-z0-9_.-]+)==([^ \\]+)")
EXPECTED_ADDED = {
    "certifi": "2026.7.22",
    "charset-normalizer": "3.5.1",
    "jiter": "0.16.0",
    "openai": "3.3.1",
    "regex": "2026.7.19",
    "requests": "2.34.2",
    "sniffio": "1.3.1",
    "tiktoken": "0.14.0",
    "urllib3": "2.7.0",
}
EXPECTED_CHANGED = {
    "pydantic-ai-slim": ("2.32.1", "2.34.0"),
    "pydantic-graph": ("2.32.1", "2.34.0"),
}


def _pins(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = PIN.match(line)
        if match is None:
            continue
        name, pinned = match.groups()
        name = name.lower().replace("_", "-")
        if name in result:
            raise RuntimeError(f"duplicate distribution in {path.name}: {name}")
        result[name] = pinned
    return result


def _sha256(path: Path) -> str:
    return __import__("hashlib").sha256(path.read_bytes()).hexdigest()


def main() -> None:
    production = _pins(PRODUCTION_LOCK)
    candidate = _pins(LOCK)
    added = {name: pinned for name, pinned in candidate.items() if name not in production}
    changed = {
        name: (production[name], pinned)
        for name, pinned in candidate.items()
        if name in production and production[name] != pinned
    }
    if added != EXPECTED_ADDED or changed != EXPECTED_CHANGED:
        raise SystemExit("realtime candidate dependency delta is not closed")
    if set(production) - set(candidate) or len(production) != 38 or len(candidate) != 47:
        raise SystemExit("realtime candidate unexpectedly removes or adds packages")
    agent = Agent(instructions="Eva isolated realtime dependency check.")
    realtime = agent.realtime("openai:gpt-realtime")
    session_signature = str(inspect.signature(RealtimeSession.interrupt))
    if "played_ms" not in session_signature:
        raise SystemExit("Pydantic Realtime interrupt no longer exposes played_ms")
    if not hasattr(realtime, "session"):
        raise SystemExit("Pydantic Realtime candidate lacks a session factory")
    result = {
        "candidate": "DF03b-pydantic-realtime-v1",
        "packages": {
            "openai": version("openai"),
            "pydantic-ai-slim": version("pydantic-ai-slim"),
            "tiktoken": version("tiktoken"),
            "websockets": version("websockets"),
        },
        "candidateDistributionCount": len(candidate),
        "newDistributions": [
            f"{name}=={pinned}" for name, pinned in sorted(added.items())
        ],
        "changedDistributions": [
            f"{name} {before}->{after}"
            for name, (before, after) in sorted(changed.items())
        ],
        "requirementsSha256": _sha256(REQUIREMENTS),
        "lockSha256": _sha256(LOCK),
        "productionLockSha256": _sha256(PRODUCTION_LOCK),
        "realtimeSessionInterrupt": session_signature,
        "providerConnected": False,
        "audioCaptured": False,
        "nonclaims": [
            "No provider connection or secret was used.",
            "No microphone, speaker, transcript, audio, latency or voice result was tested.",
            "No production dependency, image, service or B route was changed.",
        ],
    }
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
