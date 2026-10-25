"""Host-resolved new component scope cannot be chosen or rebased by a candidate."""
from copy import deepcopy
import hashlib

import pytest

from eva.contracts.canonical_json import canonical_sha256
from eva.operations.reviewed_component_application import _save
from eva.operations.runtime_repair_target import RuntimeRepairTargets, public_repair_target, repairable_source


@pytest.fixture
def target(tmp_path):
    source = b'def product_behavior():\n    return 1\n'
    digest = hashlib.sha256(source).hexdigest()
    binding = {'contract': 'EvaCurrentRepairBinding/v1', 'incident_id': 'self-repair:'+'a'*64,
        'incident_event_sha256': 'b'*64, 'scope_sha256': 'c'*64, 'mandate_sha256': 'd'*64,
        'source_sha256': 'e'*64, 'intent_id': 'personal-interest:'+'f'*64,
        'observation': {'error_type_sha256': 'a'*64, 'frames': [
            {'path': 'application/new_component.py', 'function': 'product_behavior:2',
             'source_sha256': digest}]}}
    installation = {service: {'image': 'sha256:'+str(i)*64, 'configuration_sha256': str(i+2)*64}
        for i,service in enumerate(('core','delegated-work-worker'),1)}
    criteria = tmp_path/'sealed-criteria.json';_save(criteria, {'original_behavior': 'fixed before candidate'}, initial=True)
    calls=[]
    def prepare(scope):
        assert 'candidate' not in str(scope)
        calls.append(deepcopy(scope))
        return {'path':str(criteria),'sha256':hashlib.sha256(criteria.read_bytes()).hexdigest()}
    registry = RuntimeRepairTargets(tmp_path/'receipts',binding=lambda incident:deepcopy(binding),
        installation=lambda:deepcopy(installation),source=lambda image,path:source,criteria=prepare)
    return registry,binding,installation,source,calls,criteria


def test_new_component_is_bound_once_to_actual_sources_and_independent_criteria(target):
    registry,binding,_,_,calls,criteria=target
    saved=registry.prepare(binding['incident_id'])
    assert set(saved['sources'])=={'application/new_component.py'}
    assert registry.prepare(binding['incident_id'])==saved and len(calls)==1
    public=public_repair_target(saved)
    assert str(criteria) not in str(public)
    assert 'fixed before candidate' not in str(public)
    assert public['installed'] is False
    assert registry.current(saved['target_id'])==saved


@pytest.mark.parametrize('change',['mandate','stop','image','configuration','criteria'])
def test_changed_authority_runtime_or_sealed_criteria_invalidates_target(target,change):
    registry,binding,installation,_,_,criteria=target
    saved=registry.prepare(binding['incident_id'])
    if change=='mandate':binding['mandate_sha256']='9'*64
    elif change=='stop':registry.binding=lambda _:(_ for _ in ()).throw(ValueError('stopped'))
    elif change=='image':installation['core']['image']='sha256:'+'9'*64
    elif change=='configuration':installation['core']['configuration_sha256']='9'*64
    else:criteria.write_text('{}')
    with pytest.raises(ValueError):registry.current(saved['target_id'])


def test_peer_source_mismatch_is_not_overwritten_or_silently_rebased(target):
    registry,binding,installation,source,calls,_=target
    registry.source=lambda image,path:source if image==installation['core']['image'] else source+b'# Foreign work\n'
    with pytest.raises(ValueError,match='no currently repairable source'):
        registry.prepare(binding['incident_id'])
    assert calls==[]


def test_permission_change_during_criteria_preparation_does_not_publish_target(target):
    registry,binding,_,_,_,_=target
    prepare=registry.criteria
    def changed(scope):
        value=prepare(scope);binding['mandate_sha256']='9'*64;return value
    registry.criteria=changed
    with pytest.raises(ValueError,match='changed during criteria'):
        registry.prepare(binding['incident_id'])
    assert list(registry.root.iterdir())==[]


@pytest.mark.parametrize('path',['../private.py','/private.py','application//a.py',
    'operations/new_evaluator.py','contracts/new_permission.py',
    'adapters/credentials/key.py','adapters/postgres/initiative_execution.py',
    'bootstrap/self_repair.py','adapters/postgres/self_repair.py'])
def test_direct_authority_and_path_escape_cannot_become_repair_targets(path):
    assert repairable_source(path) is False


def test_request_cannot_substitute_another_incident(target):
    registry,_,_,_,calls,_=target
    with pytest.raises(ValueError,match='another repair incident'):
        registry.prepare('self-repair:'+'9'*64)
    assert calls==[]


def test_unregistered_new_component_receives_only_its_actual_candidate_contract(target):
    from eva.operations.runtime_repair_target import repair_builder_handoff
    from eva.operations.runtime_repair_evaluation import validate_repair_candidate
    registry,binding,_,source,calls,criteria=target
    actual=registry.resolve(binding['incident_id'])
    handoff=repair_builder_handoff(actual)
    assert handoff['target_id']==actual['target_id']
    assert handoff['candidate_file']=='/workspace/repair/candidate.json'
    options=handoff['candidate_schema']['properties']['files']['items']['anyOf']
    assert len(options)==1
    fields=options[0]['properties'];path='application/new_component.py'
    assert fields['path']['const']==path
    assert fields['baseline_sha256']['const']==hashlib.sha256(source).hexdigest()
    assert str(criteria) not in str(handoff) and 'fixed before candidate' not in str(handoff)
    # This component had no suite registration; its real host-selected source is
    # sufficient to describe a proposal. The original validator remains authority.
    changed=source.replace(b'return 1',b'return 2')
    candidate=dict(contract='EvaRuntimeRepairCandidate/v1',target_id=actual['target_id'],diagnosis='Fixture transport only',
        files=[dict(path=path,baseline_sha256=hashlib.sha256(source).hexdigest(),
            candidate_sha256=hashlib.sha256(changed).hexdigest(),candidate_content=changed.decode())])
    assert validate_repair_candidate(actual,candidate)==candidate
    assert not handoff['installed'] and not handoff['is_execution_permission']
    assert calls==[]  # Describing a build contract does not create/use criteria.


@pytest.mark.parametrize("size", [283749, 524288])
def test_observed_module_uses_existing_total_budget_through_source_and_candidate_handoff(target, tmp_path, size):
    import json
    from types import SimpleNamespace
    from eva.operations.runtime_repair_target import repair_builder_handoff
    from eva.operations.runtime_repair_artifacts import RuntimeRepairArtifacts
    from eva.operations.runtime_repair_evaluation import validate_repair_candidate

    registry, binding, _, _, _, _ = target
    source = b"value = 1\n#" + b"x" * (size - 11)
    assert len(source) == size
    digest = hashlib.sha256(source).hexdigest()
    binding["observation"]["frames"][0]["source_sha256"] = digest
    registry.source = lambda *_: source
    resolved = registry.resolve(binding["incident_id"])
    handoff = repair_builder_handoff(resolved)
    schema = handoff["candidate_schema"]["properties"]["files"]["items"]["anyOf"][0]
    assert schema["properties"]["candidate_content"]["maxLength"] >= size
    programming = SimpleNamespace(config=SimpleNamespace(workspace_root=tmp_path / "workspaces"))
    artifacts = RuntimeRepairArtifacts(programming)
    metadata = {"workspace_key": "a" * 64, "role": None, "state": "succeeded"}
    artifacts.seed_sources(metadata, resolved, source=registry.source)
    repair = programming.config.workspace_root / metadata["workspace_key"] / "repair"
    assert (repair / "source/eva/application/new_component.py").read_bytes() == source
    changed = source.replace(b"value = 1", b"value = 2", 1)
    candidate = {"contract": "EvaRuntimeRepairCandidate/v1", "target_id": resolved["target_id"],
        "diagnosis": "Synthetic source transport regression only; no autonomous repair claim.",
        "files": [{"path": "application/new_component.py", "baseline_sha256": digest,
            "candidate_sha256": hashlib.sha256(changed).hexdigest(), "candidate_content": changed.decode()}]}
    (repair / "candidate.json").write_text(json.dumps(candidate))
    assert validate_repair_candidate(resolved, artifacts.read(metadata, "candidate.json")) == candidate


def test_per_module_and_combined_source_bytes_stay_within_original_total_budget(target):
    from eva.operations.runtime_repair_evaluation import validate_repair_candidate
    registry, binding, _, _, calls, _ = target
    source = b"#" + b"x" * 524288
    binding["observation"]["frames"][0]["source_sha256"] = hashlib.sha256(source).hexdigest()
    registry.source = lambda *_: source
    with pytest.raises(ValueError, match="bounded installed repair source"):
        registry.resolve(binding["incident_id"])
    assert calls == []
    # Each file fits; their combined source must still fit the unchanged512KiB budget.
    source = b"#" + b"x" * 283748
    digest = hashlib.sha256(source).hexdigest()
    binding["observation"]["frames"] = [{"path": "application/" + name + ".py",
        "function": "example:1", "source_sha256": digest} for name in ("one", "two")]
    registry.source = lambda *_: source
    with pytest.raises(ValueError, match="scope exceeds bound"):
        registry.resolve(binding["incident_id"])
    target_info = {"target_id": "a" * 64, "sources": {f["path"]: digest for f in binding["observation"]["frames"]}}
    changed = source + b"\n"
    entries = [{"path": path, "baseline_sha256": digest,
        "candidate_sha256": hashlib.sha256(changed).hexdigest(), "candidate_content": changed.decode()}
        for path in target_info["sources"]]
    candidate = {"contract": "EvaRuntimeRepairCandidate/v1", "target_id": "a" * 64,
        "diagnosis": "Synthetic total-bound regression", "files": entries}
    with pytest.raises(ValueError, match="candidate exceeds source bound"):
        validate_repair_candidate(target_info, candidate)
