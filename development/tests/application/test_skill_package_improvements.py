"""Synthetic source/publication bindings; not quality or real AIDE gain evidence."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import pytest
from eva.application.skill_packages import SkillResource,AgentSkillPackage,replace_python_resource,derived_manifest
from eva.contracts.canonical_json import canonical_json_bytes,canonical_sha256
from eva.contracts.component_target import validate_saved_artifact_target,saved_artifact_virtual_path
from eva.operations.saved_artifact_application import publication_plan,materialize_candidate_artifact,validate_candidate_artifact,receipt_confirms_application
from tests.application.test_skill_packages import package
from tests.operations.test_saved_artifact_final_assessment import saved_source,proposal,entry
from eva.application.component_improvements import prepare_component_release,bind_selected_release
from eva.application.component_development import compare_development_evaluations


def fixture():
    raw,target,_=saved_source();current=package()
    current=replace(current,files=tuple(SkillResource(f.path,raw) if f.path=='scripts/helper.py' else f for f in current.files))
    wire=canonical_json_bytes(current.to_wire()).decode()
    target.update(artifact_path='/proof-state/example.skill.json',skill_package_binding={
        'package_content_sha256':hashlib.sha256(wire.encode()).hexdigest(),
        'package_manifest':current.manifest(),'resource_path':'scripts/helper.py'})
    path=saved_artifact_virtual_path(target)
    draft=proposal(raw,target,path);draft['draft_step']=1
    report=entry(2,hits=3)['report'];report.update(relative_path=path,baseline_sha256=draft['source_sha256'],candidate_sha256=draft['candidate_sha256'],source_target=deepcopy(target),evaluation_step=2)
    choice=compare_development_evaluations([{'draft_step':1,'evaluation_step':2,'report':report}]);choice['selection_step']=3
    selected=bind_selected_release(prepare_component_release(draft,report),choice)
    return current,wire,target,selected


def test_resource_target_binds_package_envelope_manifest_resource_and_virtual_path():
    current,wire,target,selected=fixture()
    assert validate_saved_artifact_target(target,relative_path=selected['path'],baseline_sha256=target['content_sha256'])==target
    assert hashlib.sha256(b'scripts/helper.py').hexdigest() in selected['path']
    assert target['content_sha256']!=target['skill_package_binding']['package_content_sha256']
    assert target['skill_package_binding']['package_manifest']==current.manifest()


@pytest.mark.parametrize('change',['resource','resource_digest','raw_digest','manifest_extra','outside','null_binding'])
def test_malformed_resource_binding_does_not_become_a_saved_source(change):
    _,_,target,_=fixture();target=deepcopy(target)
    binding=target['skill_package_binding']
    if change=='resource':binding['resource_path']='scripts/missing.py'
    elif change=='resource_digest':target['content_sha256']='a'*64
    elif change=='raw_digest':binding['package_content_sha256']='invalid'
    elif change=='manifest_extra':binding['package_manifest']['execute']='host shell'
    elif change=='outside':binding['resource_path']='../helper.py'
    else:target['skill_package_binding']=None
    with pytest.raises(ValueError):validate_saved_artifact_target(target)


def test_new_complete_version_preserves_original_files_license_origin_and_lineage():
    current,wire,target,selected=fixture();plan=publication_plan(selected)
    new=materialize_candidate_artifact(plan,selected['candidate_content'],{'baseline_package_content':wire})
    variant=AgentSkillPackage.from_wire(json.loads(new))
    assert variant.sha256==plan['skill_package']['candidate_package_sha256']
    assert variant.sha256!=current.sha256
    assert variant.manifest()['origin']==current.manifest()['origin']
    assert variant.manifest()['derivation']['parent_package_sha256']==current.sha256
    for resource in current.files:
        if resource.path!='scripts/helper.py':assert variant.resource(resource.path)==resource.content
    assert current.resource('scripts/helper.py')==selected['baseline_content'].encode()
    assert variant.resource('scripts/helper.py')==selected['candidate_content'].encode()
    assert AgentSkillPackage.from_wire(current.to_wire()).sha256==current.sha256
    assert plan['destination_path'].endswith('.skill.json')
    assert validate_candidate_artifact(plan,selected['candidate_content'],new)


@pytest.mark.parametrize('change',['support','license','origin','metadata','script'])
def test_reviewed_write_cannot_replace_unreviewed_parts(change):
    current,wire,target,selected=fixture();plan=publication_plan(selected)
    content=materialize_candidate_artifact(plan,selected['candidate_content'],{'baseline_package_content':wire})
    variant=AgentSkillPackage.from_wire(json.loads(content))
    if change in {'support','license','metadata','script'}:
        name={'support':'references/note.md','license':'LICENSE','metadata':'SKILL.md','script':'scripts/helper.py'}[change]
        new=variant.resource(name)+b' changed'
        variant=replace(variant,files=tuple(SkillResource(f.path,new) if f.path==name else f for f in variant.files))
    else:variant=replace(variant,revision='b'*40)
    assert not validate_candidate_artifact(plan,selected['candidate_content'],json.dumps(variant.to_wire()))


def test_changed_complete_baseline_is_not_silently_rebased():
    _,wire,target,selected=fixture();plan=publication_plan(selected)
    with pytest.raises(ValueError,match='source changed'):
        materialize_candidate_artifact(plan,selected['candidate_content'],{'baseline_package_content':wire+' '})


def test_application_receipt_binds_full_new_artifact_separately_from_improved_script():
    _,wire,target,selected=fixture();plan=publication_plan(selected)
    new=materialize_candidate_artifact(plan,selected['candidate_content'],{'baseline_package_content':wire})
    digest=hashlib.sha256(new.encode()).hexdigest();receipt_hash='d'*64
    ref={'document_id':'procedure-reference:'+canonical_sha256({'scope':target['scope'],'receipt':receipt_hash}),'path':plan['destination_path'],'content_sha256':digest,'receipt_sha256':receipt_hash,'retention_until':target['retention_until']}
    receipt={'contract':'EvaSavedArtifactApplication/v1','status':'saved_and_designated','task_id':'task','publication':plan,'reference':ref,'old_source_unchanged':True,'candidate_artifact_sha256':digest,'skill_package_sha256':plan['skill_package']['candidate_package_sha256']}
    assert receipt_confirms_application(receipt,plan,'task')
    receipt['candidate_artifact_sha256']=selected['candidate_sha256']
    assert not receipt_confirms_application(receipt,plan,'task')
