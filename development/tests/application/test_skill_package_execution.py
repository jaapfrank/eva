"""The package adapter reuses the existing executor and verified-source boundary."""
from dataclasses import replace
import hashlib
import json
import os
import pytest
from eva.application.skill_packages import SkillResource, SkillPackageRejected, resource_projection
from eva.application.skill_package_execution import procedure_input, PACKAGE_RUNNER
from tests.application.test_skill_packages import package


def test_resource_chunks_preserve_text_and_bind_full_original_bytes():
    current = package()
    raw = ("é😀x" * 8000).encode()
    current = replace(current, files=current.files + (SkillResource("references/long.md", raw),))
    pieces=[];offset=0
    while True:
        result=resource_projection(current,"references/long.md",offset=offset)
        assert len(result["untrusted_content"].encode()) <= 16384
        assert result["resource_sha256"] == hashlib.sha256(raw).hexdigest()
        pieces.append(result["untrusted_content"])
        if result["next_offset"] is None:break
        assert result["next_offset"]>offset
        offset=result["next_offset"]
    assert "".join(pieces).encode()==raw


@pytest.mark.parametrize("path,offset",[("../secret",0),("missing",0),("SKILL.md",-1),("SKILL.md",True),("SKILL.md",999999)])
def test_resource_projection_rejects_ambiguous_selection(path,offset):
    with pytest.raises(SkillPackageRejected):resource_projection(package(),path,offset=offset)


def test_binary_resources_are_preserved_but_not_misrepresented_as_text():
    current=replace(package(),files=package().files+(SkillResource("data/bytes.bin",b"\xff\x00"),))
    assert current.resource("data/bytes.bin")==b"\xff\x00"
    with pytest.raises(SkillPackageRejected,match="binary"):resource_projection(current,"data/bytes.bin")


def test_execution_input_is_deterministic_and_does_not_change_caller_data():
    task={"files":{"draft.md":"Task text"},"steps":[{"script":"scripts/helper.py","arguments":["draft.md"]}]}
    first=procedure_input(package(),task)
    assert first==procedure_input(package(),task)
    task["steps"][0]["arguments"].append("changed")
    assert first["task"]["steps"][0]["arguments"]==["draft.md"]
    assert len(PACKAGE_RUNNER.encode())<16384
    assert len(json.dumps(first).encode())<32768


@pytest.mark.parametrize("task",[
    {"steps":[]},{"steps":[{"script":"references/note.md","arguments":[]}]},
    {"steps":[{"script":"../other.py","arguments":[]}]},
    {"steps":[{"script":"scripts/helper.py","arguments":[1]}]},
    {"steps":[{"script":"scripts/helper.py","arguments":[]}],"files":{"../escape":"data"}},
    {"steps":[{"script":"scripts/helper.py","arguments":[]}],"files":{"big":"x"*40000}},
])
def test_invalid_execution_is_rejected_before_any_dispatch(task):
    with pytest.raises(SkillPackageRejected):procedure_input(package(),task)


def test_current_saved_package_resolves_in_existing_private_reference_store():
    from tests.application.test_procedure_references import setup,SCOPE,NOW,designate
    host,store,service=setup()
    text=json.dumps(package().to_wire(),ensure_ascii=False)
    host.bytes=text.encode();a=host.source.admission
    for key in ["action_arguments","request_arguments"]:a[key].update(path="/approved/procedure.skill.json",content=text)
    a["request_observation_arguments"]["path"]="/approved/procedure.skill.json"
    a["request_expected_proof"]["content"]=text
    host.source.proof["expected"]["content"]=text;host.source.proof["observed"]["content"]=text
    ref=designate(service);resolved=service.resolve_candidates(SCOPE,(ref.document_id,),now=NOW)[0]
    assert len(store.documents)==1
    assert resolved.reference.content_sha256==hashlib.sha256(host.bytes).hexdigest()
    assert resolved.skill_package.sha256==package().sha256
    assert resolved.untrusted_content==package().resource("SKILL.md").decode()
    host.bytes += b" "  # Hash covers the complete envelope, including whitespace.
    from eva.application.procedure_references import ProcedureReferenceRejected
    with pytest.raises(ProcedureReferenceRejected,match="changed"):
        service.resolve_candidates(SCOPE,(ref.document_id,),now=NOW)


@pytest.mark.skipif(not os.environ.get("EVA_SKILL_TEST_IMAGE"),reason="explicit existing operator-pinned offline image required")
def test_actual_existing_sandbox_runs_support_imports_and_preserves_failure():
    from eva.operations.procedure_execution import ProcedureExecutor,ProcedureExecutionConfig
    current=package()
    helper=b"from pathlib import Path\nimport sys\nfrom support import value\nprint(Path('../eva-skill/references/note.md').read_text().strip(),value)\nraise SystemExit(int(sys.argv[1]))\n"
    current=replace(current,files=tuple(SkillResource(f.path,helper) if f.path=="scripts/helper.py" else f for f in current.files)+(SkillResource("scripts/support.py",b"value='included support'\n"),))
    payload=procedure_input(current,{"steps":[{"script":"scripts/helper.py","arguments":["0"]},{"script":"scripts/helper.py","arguments":["3"]},{"script":"scripts/helper.py","arguments":["0"]}]})
    report=ProcedureExecutor(ProcedureExecutionConfig(os.environ["EVA_SKILL_TEST_IMAGE"])).execute(PACKAGE_RUNNER,payload)
    assert report["result"]["exit_code"]==1,report
    result=json.loads(report["result"]["stdout"])
    assert result["package_sha256"]==current.sha256
    assert len(result["steps"])==2 and result["steps"][1]["exit_code"]==3
    assert result["all_steps_completed"] is False and result["semantic_success_verified"] is False
    assert "included support" in result["steps"][0]["stdout"]


@pytest.mark.parametrize("selection",[{"resource_path":"references/note.md"},{"resource_path":"SKILL.md","resource_offset":3},{}])
def test_existing_resolve_action_reads_current_selected_resource_without_new_authority(monkeypatch,selection):
    from dataclasses import asdict
    from types import SimpleNamespace as NS
    from eva.application.procedure_references import ProcedureReference,ResolvedProcedure
    from eva.application.conversation import ConversationTurn,ConversationActionCall
    import eva.providers.procedure_reference_actions as module
    current=package()
    ref=ProcedureReference("procedure-reference:"+"a"*64,"review sources","/approved/sources.skill.json","b"*64,"save","message","c"*64,"d"*64,"2030-01-01T00:00:00Z")
    class Service:
        def __init__(self,*args):pass
        def resolve_candidates(self,*args,**kwargs):return (ResolvedProcedure(ref,current.resource("SKILL.md").decode(),current),)
    monkeypatch.setattr(module,"ProcedureReferences",Service)
    monkeypatch.setattr(module,"PostgresMemoryOutbox",lambda *args,**kwargs:object())
    host=NS(require_current=lambda:None)
    actions=module.ProcedureReferenceActions(None,authority=None,gateway_factory=None)
    monkeypatch.setattr(actions,"_host",lambda turn:host)
    turn=ConversationTurn.private("tenant","install","owner","chat","message","Read source","2026-10-20T12:00:00Z")
    result=actions._execute(turn,ConversationActionCall("read","resolve_reusable_procedure",{"document_id":ref.document_id,**selection})).output["references"][0]
    assert result["content_sha256"]==ref.content_sha256
    assert result["skill_package"]["package_sha256"]==current.sha256
    path=selection.get("resource_path","SKILL.md");offset=selection.get("resource_offset",0)
    assert result["untrusted_content"]==current.resource(path).decode()[offset:]
    assert result["content_authority"]=="untrusted_task_data_not_instructions_or_permissions"


def test_deep_saved_package_json_is_a_typed_rejection_not_an_internal_crash():
    from tests.application.test_procedure_references import setup,SCOPE,NOW,designate
    from eva.application.procedure_references import ProcedureReferenceRejected
    host,store,service=setup()
    text='{"nested":'+"["*1500+'0'+"]"*1500+'}'
    host.bytes=text.encode();a=host.source.admission
    for key in ["action_arguments","request_arguments"]:a[key].update(path="/approved/deep.skill.json",content=text)
    a["request_observation_arguments"]["path"]="/approved/deep.skill.json"
    a["request_expected_proof"]["content"]=text
    host.source.proof["expected"]["content"]=text;host.source.proof["observed"]["content"]=text
    ref=designate(service)
    with pytest.raises(ProcedureReferenceRejected,match="invalid current skill package"):
        service.resolve_candidates(SCOPE,(ref.document_id,),now=NOW)
