"""Explicit local readback must not grant unrelated connector dispatch."""
from dataclasses import replace
from datetime import timedelta
import json
import pytest
from eva.adapters.postgres.initiative_execution import InitiativeMandateDenied
from eva.application.orchestration import ToolRequest
from tests.postgres.test_native_initiative_digital_work import native_digital, admitted, started
from tests.postgres.test_owner_initiatives import costless_native, NOW


def test_new_grant_can_admit_local_reader_without_admitting_remote_effect(native_digital):
    f = native_digital
    # The host profile already offers its connector reader, but policy must
    # explicitly admit it before a new initiative may receive the capability.
    profile = replace(f.profile, capability_ids=(*f.profile.capability_ids, "use_connector_tool"))
    f.policy["digital_work"]["worker_profile_release_sha256"] = profile.release.worker_profile_release_sha256
    f.path.write_text(json.dumps(f.policy))
    assert "use_connector_tool" not in f.auth.digital_grant(profile).capability_ids
    old_digest = f.auth.policy()[1]
    f.policy["digital_work"]["capability_ids"].append("use_connector_tool")
    f.path.write_text(json.dumps(f.policy))
    from eva.contracts.canonical_json import canonical_sha256
    new_digest = canonical_sha256({k: v for k, v in f.policy.items() if k != "enabled"})
    f.auth.admit_revision(old_digest, new_digest)
    f.service.digital_profile = profile
    receipt, work = admitted(f)
    call = started(replace_namespace(f, profile), work)
    guard, turn = f.auth.bind_work(work.spec)
    assert guard.action_allowed("use_connector_tool")
    request = ToolRequest("fake-remote", "fake-effect", "fake-checkpoint", "unadmitted_remote_send",
        {"recipient": "outside", "content": "private"}, {},
        tool_configuration_sha256="f" * 64)
    with pytest.raises(InitiativeMandateDenied, match="outside initiative mandate"):
        guard.authorize_request(request, phase="execute")
    assert work.spec.grant.capability_ids.count("use_connector_tool") == 1


def replace_namespace(f, profile):
    from types import SimpleNamespace
    return SimpleNamespace(**{**vars(f), "profile": profile})
