"""Isolated existing-updater compensation; no Docker/model/production failures."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from eva.operations.runtime_image_update import update_runtime, recover_runtime
from eva.contracts.canonical_json import canonical_sha256


class Installation:
    def __init__(self, tmp_path):
        self.installed, self.candidate = Path("installed"), Path("candidate")
        self.receipt = tmp_path / "receipt.json"
        self.before = {"services": {}, "volumes": {"data": {"name": "retained-data"}}}
        for index, service in enumerate(("core", "delegated-work-worker")):
            self.before["services"][service] = {"image": "sha256:" + str(index + 1) * 64,
                "volumes": [{"type": "volume", "source": "data", "target": "/data"}]}
        self.after = deepcopy(self.before)
        for index, spec in enumerate(self.after["services"].values()):
            spec["image"] = "sha256:" + str(index + 3) * 64
        self.mounts = [{"Type": "volume", "Name": "retained-data", "Source": "/docker/retained-data", "Destination": "/data", "RW": True}]
        self.live = {name: self.container(name, spec["image"]) for name, spec in self.before["services"].items()}
        self.calls = []
        self.apply_failure = False
        self.recovery_failure = False
        self.data = ["retained conversation", "original effect receipt"]

    def container(self, service, image):
        target = image == self.after["services"][service]["image"] if hasattr(self, "after") else False
        return {"Image": image, "Mounts": deepcopy(self.mounts),
            "Config": {"Labels": {"com.docker.compose.project": "test", "com.docker.compose.service": service,
                                  "com.docker.compose.config-hash": service + ("-candidate" if target else "-installed")}},
            "State": {"Status": "running", "Health": {"Status": "healthy"}}}

    def arguments(self):
        return dict(project="test", installed=self.installed, candidate=self.candidate, receipt=self.receipt, run=self.run)

    def run(self, args, **kwargs):
        self.calls.append(args)
        if args[:3] == ["docker", "image", "inspect"]:
            return json.dumps([{"Id": args[-1], "Config": {}}])
        if args[:2] == ["docker", "inspect"]:
            return json.dumps([self.live[args[-1]]])
        assert args[:4] == ["docker", "compose", "--project-name", "test"]
        path = args[args.index("--file") + 1]
        if "config" in args:
            if "--hash" in args:
                service = args[-1]
                return service + " " + service + "-" + path
            return json.dumps(self.before if path == "installed" else self.after)
        if "ps" in args:
            return args[-1]
        assert "up" in args and kwargs["deadline"] is None
        assert "--no-build" in args and "--no-deps" in args and "--wait" in args
        assert args[args.index("--pull") + 1] == "never"
        # The receipt fence exists before even the fake effect-capable command.
        stored = json.loads(self.receipt.read_bytes())
        assert stored["status"] == ("dispatched_result_unknown" if path == "candidate" else "recovery_result_unknown")
        for service, spec in (self.after if path == "candidate" else self.before)["services"].items():
            self.live[service] = self.container(service, spec["image"])
        if path == "candidate" and self.apply_failure:
            self.live["core"]["State"]["Status"] = "exited"
            raise RuntimeError("synthetic failed candidate startup")
        if path == "installed" and self.recovery_failure:
            self.live["core"] = self.container("core", self.after["services"]["core"]["image"])
            raise RuntimeError("synthetic compensation outcome unknown")
        return "completed"

    def dispatches(self):
        return [call for call in self.calls if "up" in call]


def probe(observation):
    return {"verified": True, "checked": "synthetic exact behavior", "images": observation["images"]}


def regressed(observation):
    return {"verified": observation["target"] == "baseline", "checked": "synthetic candidate regression"}


def test_candidate_behavior_is_bound_to_observed_installation(tmp_path):
    installation = Installation(tmp_path)
    result = update_runtime(**installation.arguments(), verify_behavior=probe)
    assert result["status"] == "behavior_verified_with_retained_mounts"
    verification = result["behavior_verification"]
    assert verification["target"] == "candidate" and verification["verified"] is True
    assert verification["images"] == {name: row["image"] for name, row in installation.after["services"].items()}
    assert verification["configuration_sha256"] == canonical_sha256(installation.after)
    assert len(installation.dispatches()) == 1


@pytest.mark.parametrize("failure", ["startup", "behavior", "later_regression"])
def test_failed_or_regressed_candidate_restores_only_images_and_keeps_new_data(tmp_path, failure):
    installation = Installation(tmp_path)
    installation.apply_failure = failure == "startup"
    callback = (lambda observation: {"verified": False, "checked": "synthetic regression"}) if failure == "behavior" else probe
    if failure == "later_regression":
        update_runtime(**installation.arguments(), verify_behavior=callback)
    else:
        with pytest.raises(RuntimeError):
            update_runtime(**installation.arguments(), verify_behavior=callback)
    # New conversations/results remain in the same mounted storage, not in an
    # old snapshot. Recovery must not delete/reset/replay this synthetic state.
    installation.data.append("conversation after candidate dispatch")
    if failure == "startup":
        service = "delegated-work-worker"
        installation.live[service] = installation.container(service, installation.before["services"][service]["image"])
    result = recover_runtime(**installation.arguments(), verify_behavior=regressed)
    assert result["status"] == "baseline_recovered_with_retained_mounts"
    assert result["recovery"]["behavior_verification"]["target"] == "baseline"
    assert result["recovery"]["behavior_verification"]["verified"] is True
    assert len(installation.dispatches()) == 2
    assert installation.data == ["retained conversation", "original effect receipt", "conversation after candidate dispatch"]
    assert all(current["Mounts"] == installation.mounts for current in installation.live.values())
    assert not any(token in {"rm", "down", "volume", "restart", "exec"} for args in installation.calls for token in args)
    recover_runtime(**installation.arguments(), verify_behavior=probe)
    assert len(installation.dispatches()) == 2  # Baseline inspection never redispatches.


def test_already_restored_healthy_baseline_is_inspected_without_compensation(tmp_path):
    installation = Installation(tmp_path)
    update_runtime(**installation.arguments(), verify_behavior=probe)
    for name, row in installation.before["services"].items():
        installation.live[name] = installation.container(name, row["image"])
    result = recover_runtime(**installation.arguments(), verify_behavior=probe)
    assert result["status"] == "baseline_recovered_with_retained_mounts"
    assert result["recovery"]["dispatch_attempted"] is False
    assert len(installation.dispatches()) == 1


@pytest.mark.parametrize("change", ["image", "mount", "compose_hash", "configuration", "receipt_image", "unknown_dispatch"])
def test_stale_recovery_binding_never_dispatches(tmp_path, change):
    installation = Installation(tmp_path)
    update_runtime(**installation.arguments(), verify_behavior=probe)
    if change == "image": installation.live["core"]["Image"] = "sha256:" + "f" * 64
    elif change == "mount": installation.live["core"]["Mounts"][0]["Name"] = "other-data"
    elif change == "compose_hash": installation.live["core"]["Config"]["Labels"]["com.docker.compose.config-hash"] = "foreign"
    elif change == "configuration": installation.before["volumes"]["data"]["name"] = "other-data"
    else:
        stored = json.loads(installation.receipt.read_bytes())
        if change == "receipt_image": stored["services"]["core"]["previous_image"] = "sha256:" + "f" * 64
        else: stored.pop("dispatch_callback_returned")
        installation.receipt.write_text(json.dumps(stored))
    with pytest.raises(ValueError):
        recover_runtime(**installation.arguments(), verify_behavior=regressed)
    assert len(installation.dispatches()) == 1


def test_uncertain_compensation_is_never_redispatched(tmp_path):
    installation = Installation(tmp_path)
    update_runtime(**installation.arguments(), verify_behavior=probe)
    installation.recovery_failure = True
    with pytest.raises(RuntimeError): recover_runtime(**installation.arguments(), verify_behavior=regressed)
    assert json.loads(installation.receipt.read_bytes())["status"] == "recovery_unconfirmed"
    with pytest.raises(ValueError, match="already attempted"):
        recover_runtime(**installation.arguments(), verify_behavior=regressed)
    assert len(installation.dispatches()) == 2
    for name, row in installation.before["services"].items():
        installation.live[name] = installation.container(name, row["image"])
    assert recover_runtime(**installation.arguments(), verify_behavior=probe)["status"] == "baseline_recovered_with_retained_mounts"
    assert len(installation.dispatches()) == 2


@pytest.mark.parametrize("verdict", [{"verified": False}, {"verified": 1}, {}, {"verified": True, "invalid": float("nan")}])
def test_recovery_requires_complete_truthful_baseline_verification(tmp_path, verdict):
    installation = Installation(tmp_path)
    update_runtime(**installation.arguments(), verify_behavior=probe)
    with pytest.raises((ValueError, RuntimeError)):
        recover_runtime(**installation.arguments(), verify_behavior=lambda observation: verdict if observation["target"] == "baseline" else {"verified": False})
    assert json.loads(installation.receipt.read_bytes())["status"] == "recovery_unconfirmed"
    assert len(installation.dispatches()) == 2


def test_concurrent_recovery_cannot_race_existing_dispatch(tmp_path):
    from eva.operations.runtime_image_update import _update_lock
    installation = Installation(tmp_path)
    update_runtime(**installation.arguments(), verify_behavior=probe)
    with _update_lock(installation.receipt):
        with pytest.raises(ValueError, match="another update or recovery"):
            recover_runtime(**installation.arguments(), verify_behavior=regressed)
    assert len(installation.dispatches()) == 1


def test_healthy_candidate_status_read_never_rolls_back(tmp_path):
    installation = Installation(tmp_path)
    update_runtime(**installation.arguments(), verify_behavior=probe)
    result = recover_runtime(**installation.arguments(), verify_behavior=probe)
    assert result["status"] == "behavior_verified_with_retained_mounts"
    assert "recovery" not in result
    assert len(installation.dispatches()) == 1


@pytest.mark.parametrize("verdict", [None, {"verified": 1}, {"verified": True, "unknown": float("nan")}])
def test_unknown_candidate_behavior_is_not_regression_or_rollback_authority(tmp_path, verdict):
    installation = Installation(tmp_path)
    update_runtime(**installation.arguments(), verify_behavior=probe)
    with pytest.raises(ValueError):
        recover_runtime(**installation.arguments(), verify_behavior=lambda observation: verdict)
    assert len(installation.dispatches()) == 1
    stored = json.loads(installation.receipt.read_bytes())
    assert "inspection_failure_type" in stored and "recovery" not in stored
    assert stored["status"] == "incomplete_inspect_retained_installation"


@pytest.mark.parametrize("guard_result", [False, "exception"])
def test_recovery_readiness_refusal_never_dispatches_or_burns_compensation_fence(tmp_path, guard_result):
    installation = Installation(tmp_path)
    update_runtime(**installation.arguments(), verify_behavior=probe)
    def guard():
        if guard_result == "exception": raise RuntimeError("synthetic foreground check unavailable")
        return False
    with pytest.raises((ValueError, RuntimeError)):
        recover_runtime(**installation.arguments(), verify_behavior=regressed, before_recovery_dispatch=guard)
    assert len(installation.dispatches()) == 1
    assert "recovery" not in json.loads(installation.receipt.read_bytes())
    result = recover_runtime(**installation.arguments(), verify_behavior=regressed, before_recovery_dispatch=lambda: True)
    assert result["status"] == "baseline_recovered_with_retained_mounts"
    assert len(installation.dispatches()) == 2
    assert result["recovery"]["current_containers"] == result["recovery"]["behavior_verification"]["containers"]


@pytest.mark.parametrize("baseline", [False, True])
def test_readonly_healthy_inspection_does_not_need_compensation_readiness(tmp_path, baseline):
    installation = Installation(tmp_path)
    update_runtime(**installation.arguments(), verify_behavior=probe)
    if baseline:
        for name, row in installation.before["services"].items():
            installation.live[name] = installation.container(name, row["image"])
    else:
        stored = json.loads(installation.receipt.read_bytes())
        for entry in stored["services"].values(): entry["current_container"] = "old-id"
        installation.receipt.write_text(json.dumps(stored))
    result = recover_runtime(**installation.arguments(), verify_behavior=probe,
        before_recovery_dispatch=lambda: pytest.fail("read-only inspect must not require restart authority"))
    assert len(installation.dispatches()) == 1
    if not baseline:
        assert {name: entry["current_container"] for name, entry in result["services"].items()} == result["behavior_verification"]["containers"]


def test_other_executor_image_change_during_readiness_is_not_overwritten(tmp_path):
    installation = Installation(tmp_path)
    update_runtime(**installation.arguments(), verify_behavior=probe)
    def guard():
        installation.live["core"]["Image"] = "sha256:" + "f" * 64
        return True
    with pytest.raises(ValueError, match="changed during recovery readiness"):
        recover_runtime(**installation.arguments(), verify_behavior=regressed, before_recovery_dispatch=guard)
    assert len(installation.dispatches()) == 1
    assert "recovery" not in json.loads(installation.receipt.read_bytes())
