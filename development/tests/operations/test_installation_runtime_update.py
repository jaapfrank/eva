from pathlib import Path
from copy import deepcopy

import pytest

from tools.installation_runtime_update import image_update_services, update_runtime


BASE = {"services": {"core": {"image": "sha256:" + "a" * 64,
                              "volumes": [{"source": "existing", "target": "/data"}]}},
        "volumes": {"existing": {"external": True}}}


def test_image_update_preserves_configuration_and_limits_service_scope():
    candidate = deepcopy(BASE)
    candidate["services"]["core"]["image"] = "sha256:" + "b" * 64
    assert image_update_services(BASE, candidate) == ["core"]
    assert image_update_services(BASE, BASE) == []
    candidate["services"]["core"]["volumes"][0]["source"] = "empty"
    with pytest.raises(ValueError, match="cannot change"):
        image_update_services(BASE, candidate)
    db = {"services": {"postgres": {"image": "old"}}}
    with pytest.raises(ValueError, match="only existing core"):
        image_update_services(db, {"services": {"postgres": {"image": "sha256:" + "b" * 64}}})


def test_existing_receipt_never_replays_dispatch(tmp_path):
    receipt = tmp_path / "receipt.json"
    receipt.write_text('{"status":"dispatched_result_unknown"}')
    def forbidden(*args, **kwargs):
        pytest.fail("an uncertain update must not be replayed")
    with pytest.raises(ValueError, match="already exists"):
        update_runtime(project="test", installed=tmp_path / "before", candidate=tmp_path / "after",
                       receipt=receipt, run=forbidden)


def test_stale_installed_configuration_is_rejected_before_update(tmp_path):
    import json
    candidate = deepcopy(BASE)
    candidate["services"]["core"]["image"] = "sha256:" + "b" * 64
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        if "--format" in args:
            return json.dumps(candidate if "after" in args else BASE)
        if "ps" in args:
            return "container-id"
        if "--hash" in args:
            return "core supplied-config-hash"
        if args[:2] == ["docker", "inspect"]:
            return json.dumps([{"Config": {"Labels": {
                "com.docker.compose.project": "test", "com.docker.compose.service": "core",
                "com.docker.compose.config-hash": "actual-other-mount-config-hash"}},
                "Image": BASE["services"]["core"]["image"]}])
        pytest.fail("unexpected command")
    with pytest.raises(ValueError, match="running configuration differs"):
        update_runtime(project="test", installed=Path("before"), candidate=Path("after"),
                       receipt=tmp_path / "receipt", run=run)
    assert not any("up" in call for call in calls)


def test_named_volume_project_mapping_must_match_live_storage():
    from tools.installation_runtime_update import require_current_storage
    config = {"services": {"core": {"volumes": [{"type": "volume", "source": "data",
                                                "target": "/data"}]}},
              "volumes": {"data": {"name": "different-data"}}}
    current = {"Mounts": [{"Type": "volume", "Name": "actual-data", "Source": "/docker/actual",
                           "Destination": "/data", "RW": True}]}
    with pytest.raises(ValueError, match="running storage differs"):
        require_current_storage(config, "core", current)
    config["volumes"]["data"]["name"] = "actual-data"
    require_current_storage(config, "core", current)


def test_mount_order_is_not_identity_but_storage_changes_are():
    from tools.installation_runtime_update import same_mounts
    mounts = [{"Destination": "/data", "Source": "/volume/a", "RW": True},
              {"Destination": "/config", "Source": "/config/a", "RW": False}]
    assert same_mounts(mounts, list(reversed(mounts)))
    for field, value in [("Source", "/volume/b"), ("Destination", "/other"), ("RW", False)]:
        changed = [dict(m) for m in mounts]
        changed[0][field] = value
        assert not same_mounts(mounts, changed)
    assert not same_mounts(mounts, [*mounts, mounts[0]])


@pytest.mark.parametrize("initial_state", ["running", "exited", "created"])
def test_update_follows_same_process_without_elapsed_time_failure(tmp_path, initial_state):
    import json
    baseline = {"services": {"core": {"image": "sha256:" + "a" * 64}}}
    candidate = deepcopy(baseline)
    candidate["services"]["core"]["image"] = "sha256:" + "b" * 64
    dispatched = []

    def run(args, **kwargs):
        if "config" in args:
            if "--hash" in args:
                return "core current-hash"
            return json.dumps(candidate if "after" in args else baseline)
        if "ps" in args:
            if not dispatched and initial_state != "running" and "--all" not in args:
                return ""
            if dispatched:
                assert "--all" not in args
            return "current-container"
        if args[:3] == ["docker", "image", "inspect"]:
            return json.dumps([{"Id": args[-1], "Config": {}}])
        if args[:2] == ["docker", "inspect"]:
            return json.dumps([{
                "Config": {"Labels": {"com.docker.compose.project": "test",
                    "com.docker.compose.service": "core",
                    "com.docker.compose.config-hash": "current-hash"}},
                "Image": (candidate if dispatched else baseline)["services"]["core"]["image"],
                "Mounts": [],
                "State": {"Status": "running" if dispatched else initial_state},
            }])
        if "up" in args:
            assert kwargs["deadline"] is None
            assert "--wait" in args and "--wait-timeout" not in args
            dispatched.append(args)
            return "completed after an arbitrarily long healthy startup"
        pytest.fail("unexpected command")

    result = update_runtime(project="test", installed=Path("before"), candidate=Path("after"),
                            receipt=tmp_path / "receipt", run=run)
    assert result["status"] == "running_with_retained_mounts"
    assert len(dispatched) == 1
