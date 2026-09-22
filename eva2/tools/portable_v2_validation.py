"""Validate post-freeze task records without creating execution authority."""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from uuid import UUID

from eva.adapters.postgres.delegated_work import PostgresDelegatedWorkStore
from eva.adapters.postgres.operational_timeline import (
    _event_from_row, _state_from_event, external_timeline_scope,
)
from eva.contracts.canonical_json import canonical_sha256
from tools.portable_restore import decode_column_preserving_record
from tools.portable_v2_profile import ALL_SPECS, CURRENT_SPECS, DEPENDENCY_SPECS, ATTENTION_SPEC


def typed_row(record, spec):
    row = decode_column_preserving_record(record, table=spec["table"], table_specs=ALL_SPECS)
    for mapping in spec["transform_spec"]["propertyMappings"]:
        name, encoding = mapping["portableProperty"], mapping["encoding"]
        value = row[name]
        if value is None:
            continue
        if encoding == "uuidLowercase":
            row[name] = UUID(value)
        elif encoding == "sha256LowercaseHex":
            row[name] = bytes.fromhex(value)
        elif encoding == "utcWholeSeconds":
            row[name] = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return row


def timeline_scopes(canonical_tenant, canonical_installation, runtime_identity):
    """An operator-selected runtime identity has deterministic scope provenance."""
    if (not isinstance(runtime_identity, dict)
            or set(runtime_identity) != {"tenant_id", "installation_id"}
            or any(not isinstance(value, str) or not value.strip()
                   or len(value) > 240 for value in runtime_identity.values())):
        raise ValueError("current archive runtime identity is invalid")
    scope = external_timeline_scope(runtime_identity["tenant_id"],
        runtime_identity["installation_id"], "current archive runtime identity missing")
    return {(canonical_tenant, canonical_installation),
            (str(scope.tenant_id), str(scope.installation_id))}


def validate_identity_extension(records, base_identity, *, tenant_id):
    """Current-only identities must be precisely the task dependency closure."""
    owners, required_bindings = set(), set()
    for record in records["eva_core.delegated_work_runs"]:
        row = record["row"]
        owners.add(row["owner_principal_id"])
        required_bindings.add(row["principal_binding_id"])
    for record in records["eva_core.operational_events"]:
        if record["row"]["event_type"] == "DOCUMENT_SLOT_CLAIMED":
            data = record["row"]["payload"]["data"]
            owners.add(data["plan"]["owner_principal_id"])
            binding = data.get("return_binding")
            if binding:
                owners.add(binding["owner_principal_id"])
                required_bindings.add(binding["principal_binding_id"])
    for spec in DEPENDENCY_SPECS:
        table = spec["table"]
        field = "principal_id" if table == "eva_core.principals" else "principal_binding_id"
        required = owners if field == "principal_id" else required_bindings
        actual = set()
        for record in records[table]:
            row = decode_column_preserving_record(record, table=table, table_specs=ALL_SPECS)
            if row["tenant_id"] != tenant_id:
                raise ValueError("current identity tenant differs")
            if row[field] in actual:
                raise ValueError("duplicate identity dependency")
            actual.add(row[field])
        if actual != base_identity[table] | required:
            raise ValueError("current identity dependency closure is not exact")
    from tools.postgres_portable_export import validate_principal_binding_evidence_scope
    validate_principal_binding_evidence_scope(records)


def validate_current_records(records, *, tenant_id, installation_id, runtime_identity, created_at):
    datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    scopes = timeline_scopes(tenant_id, installation_id, runtime_identity)
    principals = {row["row"]["principal_id"] for row in records["eva_core.principals"]}
    bindings = {row["row"]["principal_binding_id"]: row["row"]["principal_id"]
                for row in records["eva_core.principal_bindings"]}
    last = {}
    events_by_key = {}
    document_claims = []
    for record in records["eva_core.operational_events"]:
        raw = record["row"]
        if (raw["tenant_id"], raw["installation_id"]) not in scopes:
            raise ValueError("operational event scope has no runtime provenance")
        event = _event_from_row(SimpleNamespace(**typed_row(record, CURRENT_SPECS[0])))
        if event.payload_sha256 != canonical_sha256(event.payload):
            raise ValueError("operational event payload hash differs")
        state = _state_from_event(event)
        key = (raw["tenant_id"], raw["installation_id"], event.thread_id)
        previous = last.get(key)
        if event.sequence != (1 if previous is None else previous.revision + 1):
            raise ValueError("operational sequence is not contiguous")
        if state.updated_at != event.occurred_at:
            raise ValueError("operational event time differs from checkpoint")
        last[key] = state
        events_by_key.setdefault(key, []).append(event)
        if event.event_type == "DOCUMENT_SLOT_CLAIMED":
            document_claims.append(event)
    for record in records["eva_core.attention_items"]:
        raw = decode_column_preserving_record(record, table=ATTENTION_SPEC["table"], table_specs=ALL_SPECS)
        if (raw["tenant_id"], raw["installation_id"]) != (tenant_id, installation_id):
            raise ValueError("attention installation differs")
    for event in document_claims:
        import json
        from eva.providers.document_goal_plan import DocumentGoalPlan, PreparedDocumentGoal
        data = event.payload["data"]
        plan = DocumentGoalPlan.from_json(json.dumps(data["plan"]))
        definition = PreparedDocumentGoal.from_json(json.dumps(data["definition"]), plan)
        if plan.owner_principal_id not in principals:
            raise ValueError("document owner dependency missing")
        if event.thread_id != "document-slot:" + canonical_sha256({"slot_id": data["slot_id"]}):
            raise ValueError("document slot identity differs")
        if event.operation_id != data["task_id"] or plan.goal_id != "document-task:" + data["task_id"]:
            raise ValueError("document task identity differs")
        source = data["source"]
        if (source["tenant_id"], source["installation_id"]) != (
                runtime_identity["tenant_id"], runtime_identity["installation_id"]):
            raise ValueError("document source runtime provenance differs")
        source_scope = external_timeline_scope(source["tenant_id"], source["installation_id"], "missing source")
        ingress = events_by_key.get((str(source_scope.tenant_id), str(source_scope.installation_id),
                                    "conversation:" + source["message_id"]))
        if (not ingress or ingress[0].event_type != "INGRESS_COMMITTED"
                or any(ingress[0].payload["data"].get(key) != source[key]
                       for key in ("principal_id", "conversation_id", "message_id"))):
            raise ValueError("document source ingress dependency missing")
        goal_thread = "goal:" + canonical_sha256({"goal_id": plan.goal_id, "owner_id": plan.owner_principal_id})
        goal_key = (str(source_scope.tenant_id), str(source_scope.installation_id), goal_thread)
        goal_events = events_by_key.get(goal_key)
        if (not goal_events or goal_events[0].event_type != "GOAL_ADMITTED"
                or goal_events[0].payload["data"] != definition.goal.to_payload()):
            raise ValueError("current archive requires matching admitted document work")
        binding = data.get("return_binding")
        if binding is not None and (binding["owner_principal_id"] != plan.owner_principal_id
                or bindings.get(binding["principal_binding_id"]) != plan.owner_principal_id):
            raise ValueError("document private return owner dependency missing")
    for record in records["eva_core.delegated_work_runs"]:
        raw = record["row"]
        if (raw["tenant_id"], raw["installation_id"]) != (tenant_id, installation_id):
            raise ValueError("delegated work installation differs")
        if (raw["owner_principal_id"] not in principals
                or bindings.get(raw["principal_binding_id"]) != raw["owner_principal_id"]):
            raise ValueError("delegated work owner dependency missing")
        # This existing validator reads no database and validates all spec/result
        # bindings, output digest, usage, stop state and private-context contracts.
        PostgresDelegatedWorkStore._validated(None, None,
            SimpleNamespace(**typed_row(record, CURRENT_SPECS[1])))
    return last
