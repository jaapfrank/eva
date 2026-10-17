from types import SimpleNamespace
import pytest
from eva.operations.installer_capability_workflow import InstallerCapabilityWorkflow
from eva.operations.installer_connector_selection import confirm_connectors,load_connector_scope,connector_scope


def test_scope_is_explicit_preserved_and_can_be_changed(tmp_path):
    caps=tmp_path/'caps';caps.mkdir(mode=0o700);evidence=tmp_path/'development/evidence';evidence.mkdir(mode=0o700, parents=True)
    flow=InstallerCapabilityWorkflow(caps);verifier=SimpleNamespace(capability_workflow=flow,evidence_workspace=evidence)
    with pytest.raises(ValueError):confirm_connectors(verifier,public_https=True,local_networks=[])
    flow.select(step_id='adaptive_connector',wanted=True)
    digest=confirm_connectors(verifier,public_https=True,local_networks=[])
    plan={'capabilities':[{'step_id':'adaptive_connector','state':'ready','evidence_sha256':digest}]}
    assert load_connector_scope(plan,verifier)=={'public_https':True,'local_networks':[]}
    changed=confirm_connectors(verifier,public_https=False,local_networks=['192.168.10.0/24'])
    assert changed!=digest and flow.status().step('adaptive_connector').evidence_sha256==changed


@pytest.mark.parametrize('networks',[[],['0.0.0.0/0'],['127.0.0.0/8'],['169.254.0.0/16'],['8.8.8.0/24'],['192.168.1.1/24'],[123]])
def test_unknown_or_excessive_scope_rejected(networks):
    with pytest.raises(ValueError):connector_scope(False,networks)


def test_confirmed_scope_can_be_skipped_through_ordinary_api(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from eva.adapters.api.installer_capability_api import register_installer_capability_routes,INSTALLER_CAPABILITY_SCRIPT
    caps=tmp_path/'caps';caps.mkdir(mode=0o700);evidence=tmp_path/'development/evidence';evidence.mkdir(mode=0o700, parents=True)
    flow=InstallerCapabilityWorkflow(caps)
    verifier=SimpleNamespace(capability_workflow=flow,evidence_workspace=evidence)
    flow.select(step_id='adaptive_connector',wanted=True)
    confirm_connectors(verifier,public_https=True,local_networks=[])
    app=FastAPI();register_installer_capability_routes(app,workflow=flow,enabled=True,
        require_read=lambda r:None,require_write=lambda r:None,security_headers={})
    response=TestClient(app).post('/internal/v1/onboarding/capabilities/select',json={'step_id':'adaptive_connector','wanted':False})
    assert response.status_code==200 and response.json()['state']=='skipped'
    assert flow.status().step('adaptive_connector').state=='skipped'
    assert "step.step_id === 'adaptive_connector' && !wanted" in INSTALLER_CAPABILITY_SCRIPT
    flow.select(step_id='model_provider',wanted=True)
    flow.record_verification(step_id='model_provider',outcome='ready',evidence_sha256='a'*64)
    response=TestClient(app).post('/internal/v1/onboarding/capabilities/select',json={'step_id':'model_provider','wanted':False})
    assert response.status_code==409


def test_connector_receipts_preserve_bytes_and_reject_symlink(tmp_path):
    import hashlib
    from eva.contracts.canonical_json import canonical_json_bytes
    from eva.operations.installer_connector_selection import _read, _save
    workspace = tmp_path / "evidence"
    workspace.mkdir(mode=0o700)
    verifier = SimpleNamespace(evidence_workspace=workspace)
    payload = {"contract": "EvaInstallerConnectorScope/v1", "scope": {
        "public_https": True, "local_networks": []}, "installed": False, "provider_access_verified": False}
    raw = canonical_json_bytes(payload)
    digest = hashlib.sha256(raw).hexdigest()
    assert _save(verifier, payload) == digest
    assert _save(verifier, payload) == digest
    assert (workspace / (digest + ".json")).read_bytes() == raw
    assert _read(verifier, digest) == payload
    (workspace / (digest + ".json")).unlink()
    original = tmp_path / "retained.json"
    original.write_bytes(raw)
    original.chmod(0o600)
    (workspace / (digest + ".json")).symlink_to(original)
    with pytest.raises(Exception, match="private regular file"):
        _save(verifier, payload)
    with pytest.raises(Exception, match="private regular file"):
        _read(verifier, digest)
