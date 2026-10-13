from dataclasses import replace
import json
import stat
import pytest
from sqlalchemy.engine import make_url

from eva.contracts.canonical_json import canonical_sha256
from eva.operations.installer_runtime_inputs import InstallerRuntimeInputs, RuntimeInputReceipt, RuntimeSecretInput
from eva.operations.model_credentials import build_access_lease
from tools.installer_runtime_materialization import materialize_installer_runtime_files, NewDatabaseTarget
from tests.operations.test_installer_runtime_budget import _workflow, _values
from tests.operations.test_installer_installation_plan import _preflight, _profile, _capabilities
from eva.operations.installer_installation_plan import build_installer_installation_plan
from eva.adapters.objects.identity_verifier import load_current_identity_pack
from eva.operations.installer_secret_store import InstallerSecretStore


@pytest.fixture
def bound(tmp_path):
    workflow = _workflow(tmp_path)
    staged = workflow.stage(**_values())
    workflow.confirm(expected_budget_sha256=staged.budget_sha256)
    secret_path = tmp_path/"secrets"; secret_path.mkdir(mode=0o700)
    store = InstallerSecretStore(secret_path)
    authority = tmp_path/"authority.json"
    authority.write_text(json.dumps({"access_token":"private-access", "account_id":"account", "refresh_token":"private-refresh"}))
    authority.chmod(0o600)
    lease = build_access_lease(authority)
    store.store(slot_id="model_provider",secret=lease)
    budget = workflow.status()
    plan = build_installer_installation_plan(preflight=_preflight(), identity=load_current_identity_pack(),
        profile=_profile(),capabilities=_capabilities(budget_sha256=budget.budget_sha256),
        secrets=store.status(),budgets=budget)
    binding = tuple((x["slot_id"],x["reference"],x["generation"]) for x in plan["inputs"]["secret_bindings"])
    inputs = InstallerRuntimeInputs(RuntimeInputReceipt(plan["plan_sha256"],"b"*64,"c"*64,"d"*64,(),binding),
                                   (RuntimeSecretInput("model_provider",lease),))
    return dict(runtime_inputs=inputs,current_plan=plan,budget_workflow=workflow,
        target_root=tmp_path/"target",tenant_id="tenant:test",installation_id="installation:test",
        database_targets=(NewDatabaseTarget("new-db","eva","postgres","db-password","db-dsn","postgresql+psycopg"),),
        model_authority_file=authority)


def test_materializes_real_confirmed_budget_and_new_database_credentials(bound):
    receipt = materialize_installer_runtime_files(**bound)
    root = bound["target_root"]
    assert receipt["status"] == "materialized"
    dsn = make_url((root/"db-dsn").read_text())
    assert dsn.host == "new-db" and dsn.database == "eva"
    assert dsn.password == (root/"db-password").read_text()
    assert len(dsn.password) >= 48
    assert json.loads((root/"runtime-budget.json").read_text())["source_budget_sha256"] == bound["budget_workflow"].status().budget_sha256
    assert b"private-refresh" not in (root/"model-access-lease.json").read_bytes()
    assert b"private-refresh" not in (root/"hindsight-auth.json").read_bytes()
    assert "private-access" not in repr(receipt) and dsn.password not in repr(receipt)
    assert all(stat.S_IMODE(p.stat().st_mode)==0o600 for p in root.iterdir())
    assert stat.S_IMODE(root.stat().st_mode)==0o700
    before = (root/"db-password").read_bytes()
    with pytest.raises(FileExistsError): materialize_installer_runtime_files(**bound)
    assert (root/"db-password").read_bytes() == before


def test_access_only_input_is_not_promoted_to_refresh_authority(bound):
    bound["model_authority_file"] = None
    result = materialize_installer_runtime_files(**bound)
    assert result["model_authority_reused"] is False
    assert "explicit_model_authority_for_refresh_lifecycle" in result["pending"]
    assert not (bound["target_root"]/"hindsight-auth.json").exists()
    assert (bound["target_root"]/"provider-model_provider.secret").read_bytes() == bound["runtime_inputs"].secrets[0].content


@pytest.mark.parametrize("change",["plan","budget","model","filename"])
def test_changed_or_unsafe_inputs_fail_before_creating_root(bound,change):
    if change=="plan": bound["current_plan"]["inputs"]["profile"]["revision"] += 1
    elif change=="budget":
        plan = bound["current_plan"]
        plan["inputs"]["budgets"]["revision"] += 1
        plan["plan_sha256"] = canonical_sha256({k:v for k,v in plan.items() if k != "plan_sha256"})
        bound["runtime_inputs"] = replace(bound["runtime_inputs"],
            receipt=replace(bound["runtime_inputs"].receipt,plan_sha256=plan["plan_sha256"]))
    elif change=="model": bound["model_authority_file"].write_text('{"access_token":"changed","account_id":"account"}')
    else: bound["database_targets"]=(NewDatabaseTarget("new-db","eva","postgres","../password","dsn","postgresql"),)
    with pytest.raises(ValueError): materialize_installer_runtime_files(**bound)
    assert not bound["target_root"].exists()


def test_partial_file_failure_keeps_private_receipt_and_existing_outputs(bound, monkeypatch):
    import tools.installer_runtime_materialization as module
    original = module.write_private_once
    def fail_password(path, data):
        if path.name == "db-password":
            raise OSError("controlled write failure")
        return original(path, data)
    monkeypatch.setattr(module, "write_private_once", fail_password)
    with pytest.raises(module.RuntimeMaterializationError) as caught:
        module.materialize_installer_runtime_files(**bound)
    root = bound["target_root"]
    assert caught.value.receipt["status"] == "incomplete"
    assert (root/"runtime-budget.json").exists()
    assert (root/"provider-model_provider.secret").exists()
    assert json.loads((root/"materialization-receipt.json").read_text()) == caught.value.receipt
    assert "private-access" not in str(caught.value)
