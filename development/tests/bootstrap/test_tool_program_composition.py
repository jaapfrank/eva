"""Configured program capability and exact prior read profile, without network."""
from dataclasses import replace
import json
from eva.bootstrap.delegated_digital_composition import build_digital_profile, digital_tools
from eva.contracts.delegated_digital_work import INITIATIVE_LOCAL_CAPABILITIES, HOUSEHOLD_DIGITAL_CAPABILITIES
from tests.bootstrap.test_delegated_digital_composition import configuration


def test_program_is_bound_only_when_existing_executor_is_configured(tmp_path, monkeypatch):
    settings,bundle=configuration()
    monkeypatch.delenv("EVA_PROCEDURE_EXECUTION_CLIENT_FILE",raising=False)
    assert "run_tool_program" not in build_digital_profile(settings,bundle).capability_ids
    path=tmp_path/"client.json";path.write_text(json.dumps({"url":"http://existing-executor.invalid","token_file":str(tmp_path/"token"),"image_id":"sha256:"+"a"*64,"timeout_seconds":60}))
    monkeypatch.setenv("EVA_PROCEDURE_EXECUTION_CLIENT_FILE",str(path))
    current=build_digital_profile(settings,bundle)
    assert "run_tool_program" in current.capability_ids
    assert "run_tool_program" in current.grant().capability_ids
    assert "run_tool_program" in INITIATIVE_LOCAL_CAPABILITIES
    assert "run_tool_program" not in HOUSEHOLD_DIGITAL_CAPABILITIES
    assert {"application/tool_program.py","adapters/postgres/tool_program_actions.py"} <= set(dict(current.execution_source_sha256))
    previous=replace(current,capability_ids=tuple(n for n in current.capability_ids if n!="run_tool_program"))
    assert current.accepts_grant(previous.grant())
    assert current.accepts_prior_profile(previous.grant(),previous.release.worker_profile_release_sha256)
    assert current.release.worker_profile_release_sha256!=previous.release.worker_profile_release_sha256
