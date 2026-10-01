"""Read pinned Hermes startup settings without inference or borrowed credentials.

Run under an OS network namespace. Constructor-only cases deliberately use an
explicit inert key and no tools; they do not establish authenticated admission.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time


MODELS = {"contact": ("gpt-6-sol", "none"), "work": ("gpt-6-astra", "medium")}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--role", choices=MODELS, required=True)
    args = parser.parse_args()
    source = args.source.resolve()
    profile = args.profile.resolve()
    output = args.output.resolve()
    profile.mkdir(mode=0o700, parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    if (source / ".env").exists():
        raise RuntimeError("Source dotenv must be absent for this isolated startup check")
    # Do not inherit paid-provider keys or native auth/config locations.
    retained = {key: os.environ[key] for key in ("PATH", "HOME", "LANG") if key in os.environ}
    os.environ.clear()
    os.environ.update(retained)
    os.environ["HERMES_HOME"] = str(profile)
    os.environ["CODEX_HOME"] = str(profile / "empty-codex-home")
    os.environ["HERMES_CWD"] = str(profile)
    os.chdir(profile)
    sys.path.insert(0, str(source))
    model, effort = MODELS[args.role]
    config = {
        "model": {"provider": "openai-codex", "default": model,
                  "base_url": "https://chatgpt.com/backend-api/codex"},
        "agent": {"reasoning_effort": effort},
        "auth": {"adopt_external_logins": False},
    }
    # JSON is a YAML subset; native Hermes reads this file, not a replacement loader.
    (profile / "config.yaml").write_text(json.dumps(config, indent=2) + "\n")
    report = {
        "contract": "HermesNativeStartupReadback/v1", "role": args.role,
        "sourceCommit": subprocess.check_output(
            ["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip(),
        "sourceDirty": bool(subprocess.check_output(
            ["git", "-C", str(source), "status", "--porcelain"], text=True).strip()),
        "lockSha256": hashlib.sha256((source / "uv.lock").read_bytes()).hexdigest(),
        "packages": {name: importlib.metadata.version(name)
                     for name in ("hermes-agent", "openai", "httpx", "pydantic")},
        "externalInferenceRequests": 0, "authRequests": 0,
        "networkIsolation": "Caller must use unshare -n; no provider transport invoked",
        "constructorCredential": "Inert explicit startup key; not native authentication",
        "tools": "Empty constructor toolsets; no tool execution or comparison admission",
        "admission": "not_admitted",
    }
    log = io.StringIO()
    started = time.monotonic()
    try:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            from hermes_cli.config import load_config_readonly
            from hermes_cli.auth import AuthError, resolve_codex_runtime_credentials
            from agent.credential_sources import adopt_external_logins_enabled
            from run_agent import AIAgent
            native = load_config_readonly()
            report["externalLoginAdoption"] = adopt_external_logins_enabled()
            try:
                credentials = resolve_codex_runtime_credentials(read_only=True)
                report["auth"] = {"state": "present_read_only", "source": credentials.get("source")}
                del credentials
            except AuthError as exc:
                report["auth"] = {"state": "missing_or_unavailable", "code": exc.code}
            agent = AIAgent(
                model=native["model"]["default"], provider=native["model"]["provider"],
                base_url=native["model"]["base_url"], api_key="inert-startup-only",
                reasoning_config={"effort": native["agent"]["reasoning_effort"]},
                enabled_toolsets=[], quiet_mode=True, skip_context_files=True,
                skip_memory=True, skip_background_review=True, cwd=str(profile),
            )
            report["nativeReadback"] = {"model": agent.model, "provider": agent.provider,
                "apiMode": agent.api_mode, "baseUrl": agent.base_url,
                "reasoning": agent.reasoning_config, "toolCount": len(agent.tools or [])}
            assert agent.model == model and agent.reasoning_config == {"effort": effort}
            assert agent.api_mode == "codex_responses" and not report["externalLoginAdoption"]
            agent.client.close()
            report["status"] = "constructor_readback_passed"
    except Exception as exc:
        report["status"] = "failed"
        report["errorType"] = type(exc).__name__
        report["errorCode"] = getattr(exc, "code", None)
    report["durationSeconds"] = round(time.monotonic() - started, 3)
    output.write_text(json.dumps(report, indent=2) + "\n")
    output.with_suffix(".log").write_text(log.getvalue())
    print(json.dumps(report))
    if report["status"] != "constructor_readback_passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
