"""Real PG save/source/publication/reuse wiring; synthetic gain is not AIDE evidence."""
import asyncio
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import os
import pytest
from eva.application.conversation import ConversationActionCall, ConversationTurn
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.conversation_actions import PostgresConversationActions
from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
from eva.providers.procedure_reference_actions import ProcedureReferenceActions
from eva.providers.mcp_owner_authority import McpOwnerAuthority
from eva.application.skill_packages import AgentSkillPackage, SkillResource
from eva.application.component_improvements import prepare_component_release, bind_selected_release
from eva.application.component_development import compare_development_evaluations
from eva.application.procedure_references import ProcedureReferenceRejected
from eva.operations.saved_artifact_application import publication_plan, apply_in_core, receipt_confirms_application
from eva.operations.procedure_execution import ProcedureExecutor, ProcedureExecutionConfig
from eva.contracts.canonical_json import canonical_json_bytes
from tests.postgres.test_procedure_reference_host import workspace_gateway
from tests.postgres.test_people_memory_overview import people_memory_engine
from tests.application.test_skill_packages import package
from tests.application.test_saved_component_improvements import proposal
from tests.application.test_component_development import entry

NOW = "2026-10-20T12:00:00Z"
UNTIL = "2027-10-20T12:00:00Z"


def test_saved_package_resource_improvement_publication_and_other_task_reuse(people_memory_engine, tmp_path):
    engine = people_memory_engine
    mandate = {"contract":"OwnerConnectorMandate/v1", "mandate_id":"improvement-package",
        "tenant_id":"improvement-package", "installation_id":"home", "owner_principal_id":"owner",
        "issued_at":"2020-01-01T00:00:00Z", "authorization_source":"explicit isolated test grant",
        "enabled":True, "public_https":True, "local_networks":[]}
    config = tmp_path / "mandate.json"; config.write_text(json.dumps(mandate)); config.chmod(0o600)
    authority = McpOwnerAuthority(engine, str(config))
    turn = ConversationTurn.private("improvement-package", "home", "owner", "first", "package-source",
        "Keep a reusable skill.", NOW)
    journal = PostgresConversationJournal(engine, authority.scope); journal.admit(turn)
    original = package()
    # Resource > the old 16KiB publication ceiling, complete envelope < existing 64KiB save ceiling.
    script = b"VALUE = 1\n" + b"# preserved context\n" * 1000 + b"print(VALUE)\n"
    original = replace(original, files=tuple(SkillResource(f.path, script) if f.path=="scripts/helper.py" else f
        for f in original.files))
    raw = canonical_json_bytes(original.to_wire()).decode()
    executor = ProcedureExecutor(ProcedureExecutionConfig(os.environ["EVA_SKILL_TEST_IMAGE"]))
    with workspace_gateway("save_workspace_document") as (factory, handler):
        gateway = factory(); asyncio.run(gateway.verify_catalog())
        external = PostgresExternalConversationTools(engine, gateway.planner(), gateway,
            authorize_turn=authority.authorize_turn)
        procedures = ProcedureReferenceActions(engine, authority=authority, gateway_factory=factory,
            clock=lambda:NOW, executor=executor)
        route = PostgresConversationActions(engine, external_tools=(external,procedures))
        saved = external.execute(turn, ConversationActionCall("save-source", "save_workspace_document",
            {"path":"/proof-state/source.skill.json", "content":raw}))
        assert saved.output["status"] == "verified"
        ref = route.execute_step(turn, ConversationActionCall("designate-source", "designate_reusable_procedure",
            {"document_path":"/proof-state/source.skill.json", "use_when":"show the task value",
             "retention_until":UNTIL}), step_index=1).output["reference"]
        later = replace(turn, conversation_id="improve", message_id="improve-source", text="Improve this saved skill.")
        journal.admit(later)
        resolved = procedures.resolve_improvement_source(later, ref["document_id"], resource_path="scripts/helper.py")
        assert resolved["baseline_package_content"] == raw
        assert resolved["baseline_content"].encode() == script
        assert resolved["source_target"]["content_sha256"] == hashlib.sha256(script).hexdigest()
        assert resolved["source_target"]["skill_package_binding"]["package_content_sha256"] == hashlib.sha256(raw.encode()).hexdigest()
        with pytest.raises(ProcedureReferenceRejected):
            procedures.resolve_improvement_source(later, ref["document_id"])
        draft = proposal(script, resolved["source_target"], resolved["path"]); draft["draft_step"] = 1
        report = entry(2, hits=3)["report"]
        report.update(relative_path=resolved["path"], baseline_sha256=draft["source_sha256"],
            candidate_sha256=draft["candidate_sha256"], source_target=deepcopy(resolved["source_target"]), evaluation_step=2)
        selection = compare_development_evaluations([{"draft_step":1,"evaluation_step":2,"report":report}]); selection["selection_step"] = 3
        selected = bind_selected_release(prepare_component_release(draft, report), selection)
        plan = publication_plan(selected)
        result = apply_in_core(plan, selected["candidate_content"], "synthetic-reviewed-work",
            engine=engine, procedures=procedures, tools=external, source_turn=later, check_ready=lambda _:"ready")
        assert receipt_confirms_application(result, plan, "synthetic-reviewed-work")
        assert handler.documents["/proof-state/source.skill.json"] == raw
        changed_raw = handler.documents[plan["destination_path"]]
        changed = AgentSkillPackage.from_wire(json.loads(changed_raw))
        for file in original.files:
            if file.path != "scripts/helper.py": assert changed.resource(file.path)==file.content
        assert changed.manifest()["origin"] == original.manifest()["origin"]
        assert changed.resource("scripts/helper.py") == script.replace(b"VALUE = 1",b"VALUE = 2")
        use_turn = replace(later, conversation_id="another-task", message_id="later-reuse",text="Use the saved method.")
        journal.admit(use_turn)
        new_ref = result["reference"]["document_id"]
        reread = procedures.resolve_improvement_source(use_turn, new_ref, resource_path="scripts/helper.py")
        assert reread["baseline_package_content"] == changed_raw
        assert reread["source_target"]["skill_package_binding"]["package_manifest"]["derivation"]["parent_package_sha256"]==original.sha256
        run = route.execute_step(use_turn, ConversationActionCall("use-improved", "run_reusable_procedure",
            {"document_id":new_ref,"input_json":json.dumps({"files":{},"steps":[{"script":"scripts/helper.py","arguments":[]}],"outputs":[]})}), step_index=1)
        assert run.output["status"]=="execution_completed", run.output
        assert json.loads(run.output["report"]["result"]["stdout"])["steps"][0]["stdout"]=="2\n"
        assert run.output["semantic_success_verified"] is False
