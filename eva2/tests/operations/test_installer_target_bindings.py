from copy import deepcopy
from pathlib import Path
import json
import hashlib
import pytest
import yaml

from eva.contracts.canonical_json import canonical_sha256
from tools.managed_b_render import RenderedBManifest
from tools.installer_runtime_materialization import NewDatabaseTarget, materialize_installer_runtime_files
from tools.installer_target_bindings import bind_installer_target_graph
from tests.operations.test_installer_runtime_materialization import bound


@pytest.fixture
def setup(bound):
    bound["database_targets"] = (
        NewDatabaseTarget("postgres","eva","postgres","db-password","db-dsn","postgresql+psycopg"),
        NewDatabaseTarget("hindsight-postgres","hindsight","postgres","memory-password","memory-dsn","postgresql"))
    material = materialize_installer_runtime_files(**bound)
    path=Path(__file__).resolve().parents[2]/"deploy/living-core/managed-b.compose.yml"
    graph=yaml.safe_load(path.read_text())
    graph["networks"]={"default":{"name":"old-network"}}
    for name, service in graph["services"].items():
        service["networks"]={"default":{}}
        service["container_name"]="old-"+name
    graph["services"]["postgres"]["environment"].update(POSTGRES_DB="eva",POSTGRES_USER="postgres")
    graph["services"]["hindsight-postgres"]["environment"].update(POSTGRES_DB="hindsight",POSTGRES_USER="postgres")
    render=RenderedBManifest("a"*64,"test","b"*64,"old",canonical_sha256(graph),tuple(graph["services"]))
    return dict(render_receipt=render,rendered_graph=graph,namespace="new-bind-target",
        materialization_receipt=material,expected_plan_sha256=material["plan_sha256"],
        materialized_bind_root=bound["target_root"])


def fill_remaining(setup, pending):
    root=setup["materialized_bind_root"]
    extras={}
    for index,item in enumerate(pending):
        service,destination=item.split(":",1)
        target=root/("approved-extra-"+str(index))
        if destination in {"/run/eva/codex-owner", "/var/lib/eva/restore-authority"}:
            target.mkdir(mode=0o700)
        else:
            target.write_text("explicit-approved-input");target.chmod(0o600)
        extras[(service,destination)]=target
    return extras


def test_actual_maintained_mounts_bound_without_changing_commands(setup):
    original=deepcopy(setup["rendered_graph"])
    first=bind_installer_target_graph(**setup)
    assert first.graph is None
    assert "core:/var/lib/eva/restore-authority" not in first.pending
    assert not list((setup["materialized_bind_root"] / "restore-authority").iterdir())
    assert "model-access-lease:/run/eva/codex-owner" in first.pending
    completed=bind_installer_target_graph(**setup,additional_bindings=fill_remaining(setup,first.pending))
    assert completed.graph is not None and completed.pending==()
    assert completed.runtime_readiness_verified is False
    assert "runtime_reader_ownership" in completed.pending_runtime_checks
    for service in completed.graph["services"].values():
        env = service.get("environment", {})
        if env.get("EVA_DATABASE_DSN_FILE") == "/run/secrets/database_dsn":
            mount = next(m for m in service["volumes"] if m["target"] == "/run/secrets/database_dsn")
            assert Path(mount["source"]).name == "db-dsn"
    memory_mount = next(m for m in completed.graph["services"]["hindsight"]["volumes"]
                        if m["target"] == "/run/secrets/hindsight_database_dsn")
    assert Path(memory_mount["source"]).name == "memory-dsn"
    for name,service in completed.graph["services"].items():
        for field in ("command","entrypoint","healthcheck"):
            assert service.get(field)==original["services"][name].get(field)
        for mount in service.get("volumes",[]):
            if mount["type"]=="bind":
                assert Path(mount["source"]).is_relative_to(setup["materialized_bind_root"])
    model_mount=next(m for m in completed.graph["services"]["core"]["volumes"] if m["target"]=="/run/eva/model-lease")
    assert (Path(model_mount["source"])/"auth.json").exists()
    assert setup["rendered_graph"]==original


@pytest.mark.parametrize("change",["hash","db_host","plan","dsn_password"])
def test_changed_materialization_or_database_relation_is_rejected(setup,change):
    if change=="hash":
        Path(setup["materialization_receipt"]["files"][0]["path"]).write_text("changed")
    elif change=="db_host":
        setup["materialization_receipt"]["databases"][0]["service_hostname"]="old-production"
    elif change=="plan":
        setup["expected_plan_sha256"]="0"*64
    else:
        item=next(i for i in setup["materialization_receipt"]["files"] if i["role"]=="database_dsn:postgres")
        path=Path(item["path"]);path.write_text("postgresql+psycopg://postgres:wrong@postgres:5432/eva")
        item["sha256"]=hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError): bind_installer_target_graph(**setup)


def test_unresolved_writable_source_is_never_silently_reused(setup,tmp_path):
    first=bind_installer_target_graph(**setup)
    assert first.graph is None
    extras=fill_remaining(setup,first.pending)
    extras[("core","/var/lib/eva/restore-authority")]=tmp_path
    with pytest.raises(ValueError,match="source storage"):
        bind_installer_target_graph(**setup,additional_bindings=extras)


def test_skipped_providers_do_not_require_tokens_or_ingress(setup, bound):
    before = deepcopy(setup["rendered_graph"])
    candidate = bind_installer_target_graph(**setup, current_plan=bound["current_plan"])
    assert not any("telegram" in item or "home_assistant" in item for item in candidate.pending)
    completed = bind_installer_target_graph(**setup, current_plan=bound["current_plan"],
        additional_bindings=fill_remaining(setup, candidate.pending))
    assert completed.graph is not None
    assert "telegram-ingress" not in completed.graph["services"]
    assert "core" in completed.graph["services"]
    for service in completed.graph["services"].values():
        assert not any(key.startswith(("EVA_TELEGRAM_", "EVA_HOME_ASSISTANT_"))
                       for key in service.get("environment", {}))
    assert setup["rendered_graph"] == before


def test_email_provider_secret_binds_existing_readonly_core_mount(setup):
    from dataclasses import replace
    root = setup['materialized_bind_root']
    secret = root / 'provider-email.secret'
    secret.write_bytes(b'synthetic-email-api-secret')
    secret.chmod(0o600)
    setup['materialization_receipt']['files'].append({'role': 'provider:email',
        'path': str(secret), 'sha256': hashlib.sha256(secret.read_bytes()).hexdigest()})
    graph = setup['rendered_graph']
    graph['services']['core']['environment'].update(EVA_AGENTMAIL_INBOX_ID='owner@example.invalid',
        EVA_AGENTMAIL_API_KEY_FILE='/run/secrets/agentmail_api_key')
    graph['services']['core']['volumes'].append({'type': 'bind', 'source': '/placeholder/email',
        'target': '/run/secrets/agentmail_api_key', 'read_only': True, 'bind': {'create_host_path': False}})
    setup['render_receipt'] = replace(setup['render_receipt'], rendered_sha256=canonical_sha256(graph))
    first = bind_installer_target_graph(**setup)
    assert 'core:/run/secrets/agentmail_api_key' not in first.pending
    completed = bind_installer_target_graph(**setup, additional_bindings=fill_remaining(setup, first.pending))
    assert completed.graph is not None
    mount = next(m for m in completed.graph['services']['core']['volumes']
                 if m['target'] == '/run/secrets/agentmail_api_key')
    assert mount['source'] == str(secret) and mount['read_only'] is True
    assert secret.read_bytes() == b'synthetic-email-api-secret'
