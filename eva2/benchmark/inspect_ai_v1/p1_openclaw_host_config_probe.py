"""Validate two persisted main-role configs using the pinned OpenClaw CLI.

Run in a fresh user/network namespace. This checks config acceptance and
readback only; it does not establish authentication or native turn selection.
"""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    node, package, plugin, profile = map(Path, sys.argv[1:])
    # Refuse to reuse a credential-bearing profile or silently rerun this probe.
    profile.mkdir(mode=0o700)
    for name in ("home", "state", "codex", "workspace", "tmp"):
        (profile / name).mkdir(mode=0o700)
    cli = package / "openclaw.mjs"
    result = {
        "scope": "persisted_main_role_config_validation_and_readback_only",
        "packageVersion": json.loads((package / "package.json").read_text())["version"],
        "pluginVersion": json.loads((plugin / "package.json").read_text())["version"],
        "packageManifestSha256": digest(package / "package.json"),
        "pluginManifestSha256": digest(plugin / "package.json"),
        "cliEntrySha256": digest(cli),
        "networkInterfaces": [p.name for p in Path("/sys/class/net").iterdir()],
        "networkNamespace": os.readlink("/proc/self/ns/net"),
        "commands": [],
        "roles": {},
        "limits": "No gateway, authentication, thread or turn command. Helper routing, supported efforts, entitlement and inference remain unverified.",
    }
    try:
        for role, model, thinking in (
            ("contact", "gpt-6-sol", "off"),
            ("work", "gpt-6-astra", "medium"),
        ):
            defaults = {
                "workspace": str(profile / "workspace"),
                "model": {"primary": f"openai/{model}", "fallbacks": []},
                "thinkingDefault": thinking,
                "models": {
                    "openai/gpt-6-sol": {
                        "agentRuntime": {"id": "codex"},
                        "params": {"thinking": "off"},
                    },
                    "openai/gpt-6-astra": {
                        "agentRuntime": {"id": "codex"},
                        "params": {"thinking": "medium"},
                    },
                },
            }
            config = {
                "agents": {"defaults": defaults},
                "plugins": {
                    "allow": ["codex"],
                    "load": {"paths": [str(plugin)]},
                    "entries": {"codex": {"enabled": True, "config": {
                        "sessionCatalog": {"enabled": False},
                    }}},
                },
            }
            path = profile / f"{role}.json"
            path.write_text(json.dumps(config, indent=2) + "\n")
            path.chmod(0o600)
            before = digest(path)
            env = {
                "PATH": f"{node.parent}:/usr/bin:/bin",
                "LANG": "C.UTF-8",
                "HOME": str(profile / "home"),
                "CODEX_HOME": str(profile / "codex"),
                "TMPDIR": str(profile / "tmp"),
                "OPENCLAW_STATE_DIR": str(profile / "state"),
                "OPENCLAW_CONFIG_PATH": str(path),
                "OPENCLAW_CONFIG_READONLY": "1",
            }
            observations = []
            for args in (("config", "validate", "--json"),
                         ("config", "get", "agents.defaults", "--json")):
                started = time.monotonic()
                command = [str(node), str(cli), *args]
                run = subprocess.run(command, env=env, cwd=profile, text=True,
                                     capture_output=True)
                record = {
                    "role": role, "argv": command, "exitCode": run.returncode,
                    "elapsedSeconds": round(time.monotonic() - started, 3),
                    "stdout": run.stdout, "stderr": run.stderr,
                }
                result["commands"].append(record)
                observations.append(record)
            validation, readback = observations
            parsed = json.loads(readback["stdout"]) if readback["exitCode"] == 0 else None
            result["roles"][role] = {
                "configPath": str(path), "sha256Before": before,
                "sha256After": digest(path),
                "valid": validation["exitCode"] == 0,
                "defaultsReadbackMatches": parsed == defaults,
            }
    finally:
        (profile / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"roles": result["roles"], "profile": str(profile)}))


if __name__ == "__main__":
    main()
