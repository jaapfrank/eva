"""Apply an image-only update to an existing Compose installation.

Reuse prepared Compose configurations and Docker's retained volumes. Schema
migrations and module/configuration changes require their existing separate paths.
A failed start is retained for inspection; this command never rolls back or retries.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path

def _command(*args, **kwargs):
    # Pure image comparison is also used by the minimal host application service.
    # Load installer/database dependencies only for the default command runner.
    from tools.installation_document_connection import _command as run
    return run(*args, **kwargs)


def image_update_services(installed: dict, candidate: dict) -> list[str]:
    """Reject configuration/data changes and return only changed image services."""
    expected = deepcopy(installed)
    if set(installed.get("services", {})) != set(candidate.get("services", {})):
        raise ValueError("update must retain the existing service inventory")
    changed = []
    for name, current in installed["services"].items():
        target = candidate["services"][name]
        image = target.get("image", "")
        if image != current.get("image"):
            if name not in {"core", "delegated-work-worker"}:
                raise ValueError("this updater changes only existing core and delegated worker images")
            if not image.startswith("sha256:") or len(image) != 71:
                raise ValueError("update requires an exact locally available image ID")
            int(image[7:], 16)
            expected["services"][name]["image"] = image
            changed.append(name)
    if expected != candidate:
        raise ValueError("image-only update cannot change mounts, configuration or data services")
    return changed


def require_current_storage(config: dict, service: str, current: dict) -> None:
    """Bind project-level storage references as well as the service hash."""
    spec = config["services"][service]
    expected = {}
    for volume in spec.get("volumes", []):
        kind, source, target = volume["type"], volume.get("source"), volume["target"]
        if not source or kind not in {"bind", "volume"}:
            raise ValueError("update requires explicit persistent mount sources")
        source = (config["volumes"][source]["name"] if kind == "volume"
                  else str(Path(source).resolve()))
        expected[target] = (kind, source, not volume.get("read_only", False))
    for category, prefix in (("secrets", "/run/secrets/"), ("configs", "/")):
        for binding in spec.get(category, []):
            source = binding["source"]
            definition = config[category][source]
            if "file" not in definition:
                raise ValueError("update requires inspectable file-backed configuration")
            target = binding.get("target", source)
            if not target.startswith("/"):
                target = prefix + target
            expected[target] = ("bind", str(Path(definition["file"]).resolve()), False)
    observed = {m["Destination"]: (m["Type"], m.get("Name") if m["Type"] == "volume"
                                      else str(Path(m["Source"]).resolve()), m["RW"])
                for m in current["Mounts"]}
    if observed != expected:
        raise ValueError("running storage differs from the installed configuration")


def same_mounts(left: list[dict], right: list[dict]) -> bool:
    """Docker inspect mount ordering is not part of storage identity."""
    return sorted(left, key=lambda mount: json.dumps(mount, sort_keys=True)) == sorted(
        right, key=lambda mount: json.dumps(mount, sort_keys=True))


def update_runtime(*, project: str, installed: Path, candidate: Path,
                   receipt: Path, run=_command) -> dict:
    """Use existing Compose execution once, with a durable pre-dispatch receipt."""
    if receipt.exists():
        raise ValueError("receipt already exists; inspect the previous update before continuing")
    prefix = ["docker", "compose", "--project-name", project]
    def config(path):
        return json.loads(run([*prefix, "--file", str(path), "--profile", "*",
                               "config", "--format", "json"]))
    before, after = config(installed), config(candidate)
    changed = image_update_services(before, after)
    containers = {}
    for service in changed:
        # A failed first start may leave the exact retained container stopped.
        # Include it for identity/storage validation; the post-update check still
        # requires a running container.
        ids = run([*prefix, "--file", str(installed), "--profile", "*", "ps", "--all", "-q", service]).split()
        if len(ids) != 1:
            raise ValueError("update requires one existing container per changed service")
        current = json.loads(run(["docker", "inspect", ids[0]]))[0]
        labels = current["Config"].get("Labels", {})
        if (labels.get("com.docker.compose.project") != project
                or labels.get("com.docker.compose.service") != service):
            raise ValueError("running service differs from the selected installation")
        config_hash = run([*prefix, "--file", str(installed), "--profile", "*",
                           "config", "--hash", service]).split()
        if (len(config_hash) != 2 or config_hash[0] != service
                or labels.get("com.docker.compose.config-hash") != config_hash[1]):
            raise ValueError("running configuration differs from the installed configuration")
        require_current_storage(before, service, current)
        baseline = json.loads(run(["docker", "image", "inspect", before["services"][service]["image"]]))[0]
        if current["Image"] != baseline["Id"]:
            raise ValueError("running image differs from the installed configuration")
        target = after["services"][service]["image"]
        target_image = json.loads(run(["docker", "image", "inspect", target]))[0]
        if target_image.get("Config", {}).get("Volumes") != baseline.get("Config", {}).get("Volumes"):
            raise ValueError("image-declared volumes must remain unchanged")
        if target_image["Id"] != target:
            raise ValueError("candidate image is unavailable locally")
        containers[service] = {"previous_container": ids[0], "previous_image": current["Image"],
                               "candidate_image": target, "mounts": current["Mounts"]}
    result = {"contract": "EvaImageUpdate/v1", "project": project,
              "status": "dispatch_pending" if changed else "unchanged",
              "services": containers}
    def save():
        receipt.write_text(json.dumps(result, indent=2) + "\n")
        receipt.chmod(0o600)
    # Reserve the receipt; a second invocation cannot replay a partial update.
    with receipt.open("x") as output:
        output.write(json.dumps(result))
    receipt.chmod(0o600)
    if not changed:
        return result
    try:
        if config(installed) != before or config(candidate) != after:
            raise ValueError("update inputs changed before dispatch")
        result["status"] = "dispatched_result_unknown"
        save()
        # Deployment duration is not failure: follow this same Compose process
        # until it completes or reports an actual error. Never kill it on elapsed time.
        run([*prefix, "--file", str(candidate), "--profile", "*", "up", "--detach",
             "--no-deps", "--no-build", "--pull", "never", "--wait",
             *changed], deadline=None)
        for service, entry in containers.items():
            ids = run([*prefix, "--file", str(candidate), "--profile", "*", "ps", "-q", service]).split()
            if len(ids) != 1:
                raise RuntimeError("updated service is unavailable")
            current = json.loads(run(["docker", "inspect", ids[0]]))[0]
            if current["Image"] != entry["candidate_image"] or not same_mounts(current["Mounts"], entry["mounts"]):
                raise RuntimeError("updated image or retained mounts differ")
            entry["current_container"] = ids[0]
        result["status"] = "running_with_retained_mounts"
        save()
        return result
    except Exception:
        result["status"] = "incomplete_inspect_retained_installation"
        save()
        raise


class InstallerRuntimeUpdate:
    """Expose a trusted prepared update without accepting browser file paths."""
    def __init__(self, config: dict):
        if set(config) != {"project", "installed", "candidate", "receipt", "label"}:
            raise ValueError("prepared update configuration differs")
        if any(not isinstance(value, str) or not value for value in config.values()):
            raise ValueError("prepared update configuration requires nonempty strings")
        self.label = config["label"]
        self.arguments = {key: Path(value) if key != "project" else value
                          for key, value in config.items() if key != "label"}

    def status(self) -> dict:
        path = self.arguments["receipt"]
        state = "ready"
        if path.exists():
            try:
                state = json.loads(path.read_text())["status"]
            except (OSError, ValueError, KeyError):
                state = "incomplete_inspect_retained_installation"
        return {"status": state, "label": self.label, "can_apply": state == "ready"}

    def apply(self) -> dict:
        update_runtime(**self.arguments)
        return self.status()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--installed", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    result = update_runtime(**vars(args))
    print(json.dumps({"status": result["status"], "services": list(result["services"])}))


if __name__ == "__main__":
    main()
