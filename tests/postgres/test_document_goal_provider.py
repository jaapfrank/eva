"""Document admission and isolated real gateway/broker integration evidence."""

from dataclasses import replace
import asyncio
import json
import hashlib
import os
from pathlib import Path
import subprocess
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from eva.adapters.credentials.runtime_files import read_runtime_secret_file
from eva.adapters.postgres.durable_goals import PostgresDurableGoalStore
from eva.application.conversation import ConversationConflict
from eva.application.durable_goals import DurableGoalError, DurableGoalWorker
from eva.providers.document_goal_plan import DocumentGoalPlan
from eva.providers.document_goals import DocumentGoalAuthority, DocumentGoalPorts
from eva.providers.mcp_owner_authority import McpOwnerAuthority
from tests.postgres.test_durable_goal_store import spec
from tests.postgres.test_operational_timeline import timeline_engine  # noqa: F401


def test_real_owner_authority_is_fresh_and_plan_binding_survives_recreation(
    timeline_engine, private_tmp_path,
):
    mandate_file = private_tmp_path / "mandate.json"
    plan_file = private_tmp_path / "plan.json"
    mandate = {
        "contract": "OwnerConnectorMandate/v1", "mandate_id": "document-plan-authority",
        "tenant_id": "document-test", "installation_id": "isolated",
        "owner_principal_id": "owner", "issued_at": "2020-01-01T00:00:00Z",
        "authorization_source": "isolated operator fixture", "enabled": True,
        "public_https": False, "local_networks": [],
    }
    mandate_file.write_text(json.dumps(mandate))
    mandate_file.chmod(0o600)
    authority = McpOwnerAuthority(timeline_engine, str(mandate_file))
    goal = replace(spec("document-admission"), owner_id="owner", followup_instruction="Use draft")
    plan = DocumentGoalPlan(
        goal.goal_id, goal.sha256, "owner", str(authority.scope.tenant_id), str(authority.scope.installation_id),
        authority.configuration_sha256, "b" * 64, "/proof-state/draft.txt",
        "/proof-state/final.txt", "Draft", "Final: ", "\n",
    )
    plan_file.write_text(json.dumps(plan.to_payload()))
    plan_file.chmod(0o600)

    def build():
        owner = McpOwnerAuthority(timeline_engine, str(mandate_file))
        store = PostgresDurableGoalStore(
            timeline_engine, tenant_id=mandate["tenant_id"], installation_id=mandate["installation_id"]
        )
        store.admit(goal)
        guard = DocumentGoalAuthority(
            store=store, authority=owner, tool_configuration_sha256="b" * 64,
            read_plan=lambda: read_runtime_secret_file(str(plan_file), setting_name="document test plan"),
        )
        return store, guard

    store, guard = build()
    assert guard.authorize_effect(goal) == plan
    recreated_store, recreated = build()
    assert recreated_store.document_plan_binding(goal) == (plan.sha256, authority.configuration_sha256)
    assert recreated.authorize_effect(goal) == plan
    mandate_file.write_text(json.dumps({**mandate, "enabled": False}))
    with pytest.raises(ConversationConflict, match="stopped"):
        recreated.authorize_effect(goal)
    assert recreated.authorize_readback(goal) == plan
    assert len([e for e in store._events(goal) if e.event_type == "GOAL_DOCUMENT_PLAN_BOUND"]) == 1
    plan_file.write_text(json.dumps(replace(plan, first_content="Changed").to_payload()))
    with pytest.raises(DurableGoalError, match="changed"):
        recreated.authorize_effect(goal)
    # This fixture invokes no provider, model or conversation ingress.


def test_stop_after_catalog_wait_is_checked_before_actual_adapter_dispatch(timeline_engine):
    """A controlled adapter pause tests the guard, not the product primitive."""
    goal = replace(spec("document-stop-at-dispatch"), followup_instruction="Use draft")
    store = PostgresDurableGoalStore(timeline_engine, tenant_id="stop-test", installation_id="isolated")
    store.admit(goal)
    from eva.adapters.postgres.operational_timeline import external_timeline_scope
    scope = external_timeline_scope("stop-test", "isolated", "test scope")
    plan = DocumentGoalPlan(
        goal.goal_id, goal.sha256, goal.owner_id, str(scope.tenant_id), str(scope.installation_id),
        "a" * 64, "b" * 64, "/proof-state/draft.txt", "/proof-state/final.txt",
        "Draft", "Final: ", "\n",
    )
    authority = SimpleNamespace(
        owner_principal_id=goal.owner_id, scope=scope, configuration_sha256="a" * 64,
        authorize_current=lambda **kwargs: None,
    )
    guard = DocumentGoalAuthority(
        store=store, authority=authority, read_plan=lambda: json.dumps(plan.to_payload()),
        tool_configuration_sha256="b" * 64,
    )
    dispatches = []

    class Adapter:
        async def verify_catalog(self):
            store.cancel(goal.goal_id, goal.owner_id, "Stop during catalog wait", goal.created_at)

        async def execute(self, request):
            dispatches.append(request)
            raise AssertionError("cancelled document was dispatched")

    tool = SimpleNamespace(
        operation_kind="effect", configuration_sha256="b" * 64,
        execute_tool_name="workspace_create_bound", observe_tool_name="workspace_observe_bound",
        proof_bindings=tuple(SimpleNamespace(argument_name=name, observed_name=name) for name in
                             ("operation_id", "request_binding_sha256", "path", "content")),
    )
    ports = DocumentGoalPorts(goal=goal, store=store, guard=guard, timeline=store._timeline,
                              adapter=Adapter(), spec=tool)
    worker = DurableGoalWorker(store, ports, ports, ports)
    with pytest.raises(DurableGoalError):
        asyncio.run(worker.on_wake(goal))
    assert dispatches == []
    assert store.state(goal.goal_id, goal.owner_id).status == "cancelled"
    assert asyncio.run(worker.on_wake(goal)).status == "cancelled"


def test_actual_gateway_broker_recovers_effect_and_stops_second_project(
    timeline_engine, private_tmp_path,
):
    """Uses the retained, isolated product gateway/translate/SDK/native stack."""
    from eva.adapters.connectors.mcp_gateway import mcp_gateway_catalog_binding
    from eva.providers.mcp_gateway import parse_mcp_gateway_catalog
    from eva.contracts.canonical_json import canonical_sha256

    stack_file = os.environ.get("EVA2_DOCUMENT_PRODUCT_STACK")
    if not stack_file:
        pytest.fail("explicit isolated EVA2_DOCUMENT_PRODUCT_STACK is required")
    stack = json.loads(Path(stack_file).read_text())
    assert stack["names"]["network"].startswith("eva-atomic-gateway-")
    inspect = json.loads(subprocess.check_output([
        "sudo", "-n", "docker", "inspect", stack["names"]["gateway"]
    ], text=True))[0]
    assert not inspect["HostConfig"]["PortBindings"]
    address = inspect["NetworkSettings"]["Networks"][stack["names"]["network"]]["IPAddress"]
    gateway_url = "http://" + address + ":4444"
    now = datetime.now(UTC).replace(microsecond=0)
    tenant, installation, owner = "document-product", "isolated", "owner"
    dsn = timeline_engine.url.render_as_string(hide_password=False)
    assert "eva2_living_disposable_" in dsn
    summaries = []

    def material(instance):
        assert instance["provider"].startswith("eva-atomic-gateway-")
        code = (
            "import json,hashlib;from pathlib import Path;"
            "print(json.dumps({str(p):hashlib.sha256(p.read_bytes()).hexdigest() "
            "for root in ['/workspace','/journal'] for p in Path(root).iterdir() if p.is_file()}))"
        )
        return json.loads(subprocess.check_output([
            "sudo", "-n", "docker", "exec", instance["provider"], "python3", "-c", code
        ], text=True))

    for label in ("happy", "stop"):
        instance = stack["instances"][label]
        assert material(instance) == {}, "prepared provider must have untouched genesis"
        rows = instance["selectedTools"]
        execute, observe = rows["workspace_create_bound"], rows["workspace_observe_bound"]
        schema = execute["inputSchema"]
        names = list(schema["properties"])
        tool = {
            "eva_name": "create_document_bound", "description": "Bound document provider",
            "completion_guidance": "Only report verified created receipts",
            "input_schema": schema, "operation_kind": "effect",
            "execute_catalog_name": execute["name"], "execute_tool_name": execute["name"],
            "observe_catalog_name": observe["name"], "observe_tool_name": observe["name"],
            "execute_catalog_sha256": canonical_sha256(mcp_gateway_catalog_binding(execute, contract="EvaMcpGatewayProvider/v3")),
            "observe_catalog_sha256": canonical_sha256(mcp_gateway_catalog_binding(observe, contract="EvaMcpGatewayProvider/v3")),
            "execute_argument_bindings": [{"argument_name": n, "provider_name": n} for n in names],
            "observe_argument_bindings": [{"argument_name": n, "provider_name": n} for n in names],
            "proof_bindings": [{"argument_name": n, "observed_name": n} for n in
                               ("operation_id", "request_binding_sha256", "path", "content")],
        }
        catalog = json.dumps({"contract": "EvaMcpGatewayProvider/v3", "tools": [tool]})
        selected = parse_mcp_gateway_catalog(catalog)[0]
        goal = replace(
            spec("product-" + label), owner_id=owner, created_at=now,
            wake=replace(spec().wake, deadline=now + timedelta(hours=1)),
            pivot_instruction=None, suitability_field=None, unsuitable_value=None,
            followup_instruction="Use the exact first document",
        )
        folder = private_tmp_path / label
        folder.mkdir(mode=0o700)
        mandate_file, catalog_file, plan_file = (folder / n for n in ("mandate.json", "catalog.json", "plan.json"))
        mandate = {
            "contract": "OwnerConnectorMandate/v1", "mandate_id": "product-mandate",
            "tenant_id": tenant, "installation_id": installation, "owner_principal_id": owner,
            "issued_at": "2020-01-01T00:00:00Z", "authorization_source": "isolated product operator",
            "enabled": True, "public_https": False, "local_networks": ["192.168.0.0/16"],
        }
        mandate_file.write_text(json.dumps(mandate)); mandate_file.chmod(0o600)
        authority = McpOwnerAuthority(timeline_engine, str(mandate_file))
        plan = DocumentGoalPlan(
            goal.goal_id, goal.sha256, owner, str(authority.scope.tenant_id), str(authority.scope.installation_id),
            authority.configuration_sha256, selected.configuration_sha256,
            "/proof-state/draft.txt", "/proof-state/final.txt", "Draft: café 21", "Final from [", "]\n",
        )
        catalog_file.write_text(catalog); catalog_file.chmod(0o600)
        plan_file.write_text(json.dumps(plan.to_payload())); plan_file.chmod(0o600)
        store = PostgresDurableGoalStore(timeline_engine, tenant_id=tenant, installation_id=installation)
        store.admit(goal)
        env = dict(os.environ, EVA_MCP_GATEWAY_URL=gateway_url,
                   EVA_MCP_GATEWAY_TOKEN_FILE=stack["tokenFile"], EVA_MCP_GATEWAY_CATALOG_FILE=str(catalog_file),
                   EVA_MCP_GATEWAY_ALLOW_PLAIN_HTTP="true",
                   EVA_MCP_GATEWAY_PRINCIPAL_SHA256_ALLOWLIST=hashlib.sha256(owner.encode()).hexdigest(),
                   EVA_MCP_AUTONOMY_MANDATE_FILE=str(mandate_file))
        env.pop("EVA_MCP_MANAGED_ENDPOINTS_FILE", None)
        child = '''
import asyncio,json,os,sys
import sqlalchemy as sa
from eva.adapters.postgres.durable_goals import PostgresDurableGoalStore
from eva.application.durable_goals import DurableGoalWorker
from eva.providers.document_goals import build_document_goal_ports
dsn,goal_id,plan_file,mode,audit=sys.argv[1:]
engine=sa.create_engine(dsn)
store=PostgresDurableGoalStore(engine,tenant_id="document-product",installation_id="isolated")
goal=store.state(goal_id,"owner").spec
ports=build_document_goal_ports(engine=engine,store=store,goal=goal,plan_file=plan_file,tenant_id="document-product",installation_id="isolated")
execute=ports.adapter.execute
async def dispatch(request):
    with open(audit,"a") as log:
        log.write(json.dumps({"operation_id":request.operation_id})+"\\n");log.flush();os.fsync(log.fileno())
    result=await execute(request)
    if mode=="after-provider": os._exit(72)
    return result
ports.adapter.execute=dispatch
record=store.record_effect
def checkpoint(*args):
    result=record(*args)
    if mode=="after-checkpoint": os._exit(71)
    return result
store.record_effect=checkpoint
state=asyncio.run(DurableGoalWorker(store,ports,ports,ports).on_wake(goal))
print(state.status)
engine.dispose()
'''
        audit = folder / "dispatch-audit.jsonl"

        def run(mode="normal"):
            source = Path(__file__).resolve().parents[2] / "src"
            socket = timeline_engine.url.query["host"]
            command = [
                "sudo", "-n", "docker", "run", "--rm", "--read-only",
                "--network", stack["names"]["network"], "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges", "--user", f"{os.getuid()}:{os.getgid()}",
                "--tmpfs", "/tmp:rw,nosuid,nodev,size=32m",
                "--mount", f"type=bind,src={source},dst=/opt/eva/src,readonly",
                "--mount", f"type=bind,src={private_tmp_path},dst={private_tmp_path}",
                "--mount", f"type=bind,src={socket},dst={socket}",
                "--mount", f"type=bind,src={stack['tokenFile']},dst={stack['tokenFile']},readonly",
                "-e", "PYTHONPATH=/opt/eva/src", "-e", "PYTHONDONTWRITEBYTECODE=1",
            ]
            for key, value in env.items():
                if key.startswith("EVA_MCP_"):
                    command += ["-e", f"{key}={value}"]
            command += [
                "--entrypoint", "/usr/local/bin/python",
                "sha256:832983322ad7d71611225749d618e828d9dd4676f8a24711f09e0ac349db1fcd",
                "-c", child, dsn, goal.goal_id, str(plan_file), mode, str(audit),
            ]
            result = subprocess.run(command, capture_output=True, text=True, timeout=60)
            assert result.returncode == {"after-provider": 72, "after-checkpoint": 71}.get(mode, 0), result.stdout + result.stderr

        run("after-provider" if label == "happy" else "after-checkpoint")
        midway = store.state(goal.goal_id, goal.owner_id)
        assert midway.status == "waiting"
        assert "/workspace/draft.txt" in material(instance)
        if label == "happy":
            assert midway.first_artifact is None  # Native receipt exists before core effect registration.
            run()
            final = store.state(goal.goal_id, goal.owner_id)
            assert final.status == "completed" and final.completed_steps == 2
            assert final.artifact.content == "Final from [Draft: café 21]\n"
            assert final.delivery_status == "accepted"
            before = material(instance)
            assert before["/workspace/final.txt"] == hashlib.sha256(final.artifact.content.encode()).hexdigest()
            # Replay the terminal worker directly; factory need not admit new work.
            from eva.application.durable_goals import DurableGoalWorker
            assert asyncio.run(DurableGoalWorker(store, None, None, None).on_wake(goal)).status == "completed"
            assert material(instance) == before
            expected_dispatches = 2
        else:
            assert midway.first_artifact.content == "Draft: café 21"
            store.cancel(goal.goal_id, goal.owner_id, "Stop after first step", now)
            before = material(instance)
            assert asyncio.run(DurableGoalWorker(store, None, None, None).on_wake(goal)).status == "cancelled"
            assert material(instance) == before
            assert "/workspace/final.txt" not in before
            expected_dispatches = 1
        calls = [json.loads(line) for line in audit.read_text().splitlines()]
        assert len(calls) == expected_dispatches
        assert len({call["operation_id"] for call in calls}) == expected_dispatches
        summaries.append({"case": label, "goal_sha256": goal.sha256, "plan_sha256": plan.sha256,
                          "provider_image": stack["providerImage"], "dispatches": len(calls),
                          "material_sha256": material(instance)})
    destination = os.environ.get("EVA2_DOCUMENT_PRODUCT_RESULT")
    if destination:
        Path(destination).write_text(json.dumps(summaries, indent=2) + "\n")
