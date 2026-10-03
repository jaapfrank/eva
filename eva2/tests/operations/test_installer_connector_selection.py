from types import SimpleNamespace
import pytest
from eva.operations.installer_capability_workflow import InstallerCapabilityWorkflow
from eva.operations.installer_connector_selection import confirm_connectors,load_connector_scope,connector_scope


def test_scope_is_explicit_preserved_and_can_be_changed(tmp_path):
    caps=tmp_path/'caps';caps.mkdir(mode=0o700);evidence=tmp_path/'evidence';evidence.mkdir(mode=0o700)
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
    caps=tmp_path/'caps';caps.mkdir(mode=0o700);evidence=tmp_path/'evidence';evidence.mkdir(mode=0o700)
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
