"""Apply an image-only update to an existing Compose installation.

Reuse prepared Compose configurations and Docker's retained volumes. Schema
migrations and module/configuration changes require their existing separate paths.
A failed start is retained for inspection. Explicit trusted recovery compensates
only selected images, without database snapshots or original action replay.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import tempfile

from eva.contracts.canonical_json import canonical_sha256

def _command(args, *, deadline=15, limit=1024 * 1024):
    # Pure image comparison is also used by the minimal host application service.
    # Load installer/database dependencies only for the default command runner.
    from eva.operations.component_evaluation import _capture
    result = _capture(args, deadline=deadline, limit=limit)
    if (result["exit_code"] != 0 or result["timed_out"]
            or result["stdout_truncated"] or result.get("stderr_truncated", False)):
        raise RuntimeError("document connection command did not complete")
    return result["stdout"]


from eva.operations.image_update import image_update_services


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


@contextmanager
def _update_lock(receipt):
    descriptor = os.open(str(receipt) + ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("another update or recovery owns this installation receipt") from None
        yield
    finally:
        os.close(descriptor)


def _save_receipt(path, value):
    raw = json.dumps(value, allow_nan=False, indent=2).encode() + b"\n"
    descriptor, temporary = tempfile.mkstemp(prefix=".image-update-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _configuration(prefix, path, run):
    return json.loads(run([*prefix, "--file", str(path), "--profile", "*", "config", "--format", "json"]))


def _inspect_selected(prefix, path, service, run, *, include_stopped=False):
    command = [*prefix, "--file", str(path), "--profile", "*", "ps"]
    ids = run([*command, *(["--all"] if include_stopped else []), "-q", service]).split()
    if len(ids) != 1: raise RuntimeError("selected service is unavailable or ambiguous")
    return ids[0], json.loads(run(["docker", "inspect", ids[0]]))[0]


def _require_compose_binding(prefix, path, service, current, run):
    labels = current["Config"].get("Labels", {})
    values = run([*prefix, "--file", str(path), "--profile", "*", "config", "--hash", service]).split()
    if (labels.get("com.docker.compose.project") != prefix[-1]
            or labels.get("com.docker.compose.service") != service
            or len(values) != 2 or values[0] != service
            or labels.get("com.docker.compose.config-hash") != values[1]):
        raise ValueError("selected service configuration differs from the bound installation")


def _healthy(current):
    state = current.get("State", {})
    return state.get("Status") == "running" and state.get("Health", {}).get("Status", "healthy") == "healthy"


def _behavior_verification(result, target, observed, configuration, verify_behavior):
    observation = {"target": target, "project": result["project"],
        "configuration_sha256": canonical_sha256(configuration),
        "images": {service: current["Image"] for service, (_, current) in observed.items()},
        "containers": {service: identifier for service, (identifier, _) in observed.items()}}
    proof = verify_behavior(deepcopy(observation))
    if not isinstance(proof, dict) or type(proof.get("verified")) is not bool:
        raise ValueError("trusted behavior verification must return an explicit JSON verdict")
    encoded = json.dumps(proof, allow_nan=False).encode()
    if len(encoded) > 65_536: raise ValueError("trusted behavior verification evidence is too large")
    return {"contract": "EvaImageBehaviorVerification/v1", **observation,
            "verified": proof["verified"], "evidence": json.loads(encoded)}


def update_runtime(*, project, installed, candidate, receipt, run=_command, verify_behavior=None):
    with _update_lock(receipt):
        return _update_runtime(project=project, installed=installed, candidate=candidate,
                               receipt=receipt, run=run, verify_behavior=verify_behavior)


def _update_runtime(*, project: str, installed: Path, candidate: Path,
                   receipt: Path, run=_command, verify_behavior=None) -> dict:
    """Use existing Compose execution once, with a durable pre-dispatch receipt."""
    if receipt.exists():
        raise ValueError("receipt already exists; inspect the previous update before continuing")
    prefix = ["docker", "compose", "--project-name", project]
    def config(path):
        return _configuration(prefix, path, run)
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
              "services": containers,
              "installed_configuration_sha256": canonical_sha256(before),
              "candidate_configuration_sha256": canonical_sha256(after)}
    def save():
        _save_receipt(receipt, result)
    # Reserve the receipt; a second invocation cannot replay a partial update.
    descriptor = os.open(receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        output.write(json.dumps(result))
        output.flush()
        os.fsync(output.fileno())
    if not changed:
        return result
    try:
        if config(installed) != before or config(candidate) != after:
            raise ValueError("update inputs changed before dispatch")
        result["status"] = "dispatched_result_unknown"
        save()
        # Deployment duration is not failure: follow this same Compose process
        # until it completes or reports an actual error. Never kill it on elapsed time.
        try:
            run([*prefix, "--file", str(candidate), "--profile", "*", "up", "--detach",
                 "--no-deps", "--no-build", "--pull", "never", "--wait",
                 *changed], deadline=None)
        finally:
            # Trusted runner owns the single Compose process until return. A
            # process crash before this point does not authorize compensation.
            result["dispatch_callback_returned"] = True
            save()
        observed = {}
        for service, entry in containers.items():
            identifier, current = _inspect_selected(prefix, candidate, service, run)
            observed[service] = (identifier, current)
            if current["Image"] != entry["candidate_image"] or not same_mounts(current["Mounts"], entry["mounts"]):
                raise RuntimeError("updated image or retained mounts differ")
            entry["current_container"] = identifier
        result["status"] = "running_with_retained_mounts"
        save()
        if verify_behavior is not None:
            result["behavior_verification"] = _behavior_verification(result, "candidate", observed, after, verify_behavior)
            if not result["behavior_verification"]["verified"]:
                raise RuntimeError("updated behavior verification failed")
            result["status"] = "behavior_verified_with_retained_mounts"
            save()
        return result
    except Exception as error:
        result["failure_type"] = type(error).__name__
        result["status"] = "incomplete_inspect_retained_installation"
        save()
        raise


def recover_runtime(*, project, installed, candidate, receipt, verify_behavior, run=_command,
                    before_recovery_dispatch=None):
    """Compensate one authorized image-only update while retaining all live data.

    The caller owns reversible-class authority, idle readiness and the fixed
    behavior probe. A crash before dispatch returned stays inspection-only.
    An existing recovery dispatch never authorizes a second compensation.
    """
    with _update_lock(receipt):
        return _recover_runtime(project=project, installed=installed, candidate=candidate,
            receipt=receipt, verify_behavior=verify_behavior, run=run,
            before_recovery_dispatch=before_recovery_dispatch)


def _recover_runtime(*, project, installed, candidate, receipt, verify_behavior, run,
                     before_recovery_dispatch):
    if not callable(verify_behavior): raise ValueError("recovery requires trusted baseline behavior verification")
    result = json.loads(receipt.read_bytes())
    prefix = ["docker", "compose", "--project-name", project]
    before, after = (_configuration(prefix, path, run) for path in (installed, candidate))
    changed = image_update_services(before, after)
    services = result.get("services", {})
    if (result.get("contract") != "EvaImageUpdate/v1" or result.get("project") != project
            or not changed or set(services) != set(changed)
            or result.get("installed_configuration_sha256") != canonical_sha256(before)
            or result.get("candidate_configuration_sha256") != canonical_sha256(after)
            or result.get("status") not in {"dispatched_result_unknown", "running_with_retained_mounts",
                "behavior_verified_with_retained_mounts", "incomplete_inspect_retained_installation",
                "recovery_result_unknown", "recovery_unconfirmed", "baseline_recovered_with_retained_mounts"}):
        raise ValueError("recovery requires the exact retained image-update binding")
    needs_dispatch = False
    candidate_ready = True
    observed = {}
    for service, entry in services.items():
        baseline_image, target = (value["services"][service]["image"] for value in (before, after))
        if entry.get("previous_image") != baseline_image or entry.get("candidate_image") != target:
            raise ValueError("recovery image binding differs")
        baseline = json.loads(run(["docker", "image", "inspect", baseline_image]))[0]
        proposed = json.loads(run(["docker", "image", "inspect", target]))[0]
        if (baseline["Id"] != baseline_image or proposed["Id"] != target
                or baseline.get("Config", {}).get("Volumes") != proposed.get("Config", {}).get("Volumes")):
            raise ValueError("recovery requires unchanged local image storage declarations")
        identifier, current = _inspect_selected(prefix, candidate, service, run, include_stopped=True)
        observed[service] = (identifier, current)
        image = current["Image"]
        if image not in {baseline_image, target} or not same_mounts(current["Mounts"], entry["mounts"]):
            raise ValueError("recovery refuses changed live images or retained mounts")
        _require_compose_binding(prefix, installed if image == baseline_image else candidate, service, current, run)
        require_current_storage(before, service, current)
        needs_dispatch |= image != baseline_image or not _healthy(current)
        candidate_ready &= image == target and _healthy(current)
    previous = result.get("recovery")
    if candidate_ready:
        if (isinstance(previous, dict) and previous.get("dispatch_attempted") is True
                and previous.get("dispatch_callback_returned") is not True):
            raise ValueError("recovery dispatch completion is unknown; inspect without replay")
        try:
            verification = _behavior_verification(result, "candidate", observed, after, verify_behavior)
        except Exception as error:
            result["inspection_failure_type"] = type(error).__name__
            result["status"] = "incomplete_inspect_retained_installation"
            _save_receipt(receipt, result)
            raise  # An unknown check is not evidence that behavior regressed.
        result["behavior_verification"] = verification
        if verification["verified"]:
            for service, (identifier, _) in observed.items():
                services[service]["current_container"] = identifier
            result["status"] = "behavior_verified_with_retained_mounts"
            result.pop("inspection_failure_type", None)
            _save_receipt(receipt, result)
            return result
        # Preserve the explicit failed observation before one compensation.
        result["status"] = "incomplete_inspect_retained_installation"
        _save_receipt(receipt, result)
    if previous is not None and (not isinstance(previous, dict) or needs_dispatch):
        raise ValueError("recovery already attempted; inspect retained installation without replay")
    if needs_dispatch and result.get("dispatch_callback_returned") is not True:
        raise ValueError("original dispatch completion is unknown; inspect without compensation")
    if needs_dispatch and before_recovery_dispatch is not None:
        if not callable(before_recovery_dispatch) or before_recovery_dispatch() is not True:
            raise ValueError("recovery dispatch is not currently authorized")
        # Readiness may involve another host read. Refuse a concurrent image or
        # mount change rather than overwriting another executor's installation.
        for service, (identifier, current) in observed.items():
            fresh_id, fresh = _inspect_selected(prefix, candidate, service, run, include_stopped=True)
            if (fresh_id != identifier or fresh["Image"] != current["Image"]
                    or not same_mounts(fresh["Mounts"], current["Mounts"])
                    or fresh["Config"].get("Labels") != current["Config"].get("Labels")):
                raise ValueError("live installation changed during recovery readiness")
    if previous is None:
        result["recovery"] = {"trigger_status": result["status"], "dispatch_attempted": False}
    recovery = result["recovery"]
    try:
        if needs_dispatch:
            # Fence before the only compensation. No database/config write,
            # volume reset, remote tool invocation or original action replay.
            recovery["dispatch_attempted"] = True
            result["status"] = "recovery_result_unknown"
            _save_receipt(receipt, result)
            if (_configuration(prefix, installed, run) != before
                    or _configuration(prefix, candidate, run) != after):
                raise ValueError("recovery inputs changed before dispatch")
            try:
                run([*prefix, "--file", str(installed), "--profile", "*", "up", "--detach",
                     "--no-deps", "--no-build", "--pull", "never", "--wait", *changed], deadline=None)
            finally:
                recovery["dispatch_callback_returned"] = True
                _save_receipt(receipt, result)
        observed = {}
        for service, entry in services.items():
            identifier, current = _inspect_selected(prefix, installed, service, run)
            if (current["Image"] != entry["previous_image"]
                    or not same_mounts(current["Mounts"], entry["mounts"])):
                raise RuntimeError("recovered image or retained mounts differ")
            _require_compose_binding(prefix, installed, service, current, run)
            require_current_storage(before, service, current)
            if not _healthy(current): raise RuntimeError("recovered baseline is not healthy")
            observed[service] = (identifier, current)
        recovery["current_containers"] = {service: identifier for service, (identifier, _) in observed.items()}
        recovery["behavior_verification"] = _behavior_verification(result, "baseline", observed, before, verify_behavior)
        if not recovery["behavior_verification"]["verified"]:
            raise RuntimeError("recovered baseline behavior verification failed")
        result["status"] = "baseline_recovered_with_retained_mounts"
        _save_receipt(receipt, result)
        return result
    except Exception as error:
        recovery["failure_type"] = type(error).__name__
        result["status"] = "recovery_unconfirmed"
        _save_receipt(receipt, result)
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


