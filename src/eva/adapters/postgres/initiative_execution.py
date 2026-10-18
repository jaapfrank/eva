"""Current zero-new-cost authority over the existing native work journal."""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
import json
import re

import sqlalchemy as sa
from eva.application.schema_validation import validate_schema_value

from eva.adapters.postgres.operational_timeline import (
    CheckpointConflict, PostgresOperationalTimeline, TimelineNotFound,
    operational_checkpoints, operational_events, transition_timeline_thread,
)
from eva.application.conversation import ConversationConflict, ConversationTurn
from eva.application.memory_audience import MemoryAudience
from eva.contracts.canonical_json import canonical_sha256


def _check_profile_schema(schema):
    """Closed subset already understood by Eva's provider argument validator."""
    common = {"type", "const", "enum"}
    by_type = {"object": {"properties", "required", "additionalProperties"},
               "array": {"items", "minItems", "maxItems"},
               "string": {"minLength", "maxLength", "pattern"},
               "integer": {"minimum", "maximum"}, "number": {"minimum", "maximum"},
               "boolean": set(), "null": set()}
    kind = schema.get("type") if isinstance(schema, dict) else None
    if kind not in by_type or set(schema) - common - by_type[kind]:
        raise InitiativeMandateDenied("unsupported capability schema constraint")
    if kind == "object":
        if (schema.get("additionalProperties") is not False
                or not isinstance(schema.get("properties"), dict)
                or not isinstance(schema.get("required"), list)
                or not all(isinstance(k, str) and k in schema["properties"] for k in schema["required"])):
            raise InitiativeMandateDenied("capability object boundary is invalid")
        for child in schema["properties"].values(): _check_profile_schema(child)
    elif kind == "array":
        _check_profile_schema(schema.get("items"))
    if "pattern" in schema:
        try:
            re.compile(schema["pattern"])
        except (TypeError, re.error) as error:
            raise InitiativeMandateDenied("capability path pattern is invalid") from error


def _matches_profile(value, schema):
    try:
        validate_schema_value(value, schema, "initiative arguments")
        if isinstance(value, str) and "pattern" in schema:
            return re.fullmatch(schema["pattern"], value) is not None
        if isinstance(value, dict):
            return all(_matches_profile(v, schema["properties"][k]) for k, v in value.items())
        if isinstance(value, list):
            return all(_matches_profile(v, schema["items"]) for v in value)
        return True
    except (ConversationConflict, TypeError, ValueError):
        return False


def _public_input_is_host_selected(schema):
    """Public routes accept only host-listed values, never free private text."""
    if "const" in schema or "enum" in schema:
        return True
    if schema.get("type") == "object":
        return all(_public_input_is_host_selected(v) for v in schema["properties"].values())
    if schema.get("type") == "array":
        return "maxItems" in schema and _public_input_is_host_selected(schema["items"])
    return False


class InitiativeMandateDenied(ConversationConflict):
    """No execution permission; keep a concrete blocker instead of guessing."""


def execution_turn(claim):
    return ConversationTurn(
        tenant_id=claim.tenant_id, installation_id=claim.installation_id,
        principal_id=claim.principal_id,
        conversation_id="initiative-task:" + claim.intent_id.removeprefix("personal-interest:"),
        message_id="initiative-execute:" + claim.intent_id.removeprefix("personal-interest:"),
        text=("Eigen initiatief van Eva (geen gebruikersopdracht): " + claim.title
              + "\nAanleiding: " + claim.motivation + "\nGekozen uitkomst: " + claim.question),
        occurred_at=claim.occurred_at,
        audience=MemoryAudience.private_person(principal_id=claim.principal_id),
    )


class PostgresInitiativeAuthority:
    def __init__(self, owner_store, read_mandate: Callable[[], str]):
        if not callable(read_mandate):
            raise TypeError("initiative mandate reader is required")
        self.owner = owner_store
        self._read_mandate = read_mandate
        self.timeline = PostgresOperationalTimeline(owner_store._engine, owner_store._scope)
        self.thread = "initiative-mandate:" + str(owner_store._owner)

    def _read(self):
        try:
            data = json.loads(self._read_mandate())
        except (OSError, ValueError, RuntimeError) as error:
            raise InitiativeMandateDenied("initiative mandate file unavailable") from error
        fields = {"contract", "mandate_id", "owner_principal_id", "enabled",
                  "authorization_source", "capabilities", "tenant_scope", "installation_scope"}
        if (not isinstance(data, dict) or not fields <= set(data)
                or set(data) - fields - {"digital_work", "private_contact"}
                or data["contract"] != "EvaCostlessInitiativeMandate/v1"
                or data["owner_principal_id"] != str(self.owner._owner)
                or data["tenant_scope"] != str(self.owner._scope.tenant_id)
                or data["installation_scope"] != str(self.owner._scope.installation_id)
                or type(data["enabled"]) is not bool
                or not all(isinstance(data[k], str) and data[k].strip()
                           for k in ("mandate_id", "authorization_source"))
                or not isinstance(data["capabilities"], list)):
            raise InitiativeMandateDenied("initiative mandate is invalid")
        if "private_contact" in data:
            contact = data["private_contact"]
            if (not isinstance(contact, dict)
                    or set(contact) != {"enabled", "channel", "owner_principal_id"}
                    or type(contact["enabled"]) is not bool
                    or contact["channel"] != "telegram"
                    or contact["owner_principal_id"] != str(self.owner._owner)):
                raise InitiativeMandateDenied("private contact requires the current private owner")
        digital = data.get("digital_work")
        if digital is not None:
            from eva.contracts.delegated_digital_work import INITIATIVE_LOCAL_CAPABILITIES
            if (not isinstance(digital, dict)
                    or set(digital) != {"worker_profile_release_sha256", "capability_ids"}
                    or not isinstance(digital["worker_profile_release_sha256"], str)
                    or re.fullmatch(r"[0-9a-f]{64}", digital["worker_profile_release_sha256"]) is None
                    or not isinstance(digital["capability_ids"], list)
                    or not digital["capability_ids"]
                    or any(not isinstance(name, str) or name not in INITIATIVE_LOCAL_CAPABILITIES
                           for name in digital["capability_ids"])
                    or len(set(digital["capability_ids"])) != len(digital["capability_ids"])):
                raise InitiativeMandateDenied("digital initiative requires an explicit local host profile")
        for cap in data["capabilities"]:
            required = {"action_name", "configuration_sha256", "new_cost_eur_cents",
                        "new_obligation", "data_scope", "communication_scope",
                        "cost_evidence", "execute_schema", "observe_schema", "tool_name", "observation_tool_name"}
            if (not isinstance(cap, dict) or set(cap) != required
                    or type(cap["new_cost_eur_cents"]) is not int
                    or cap["new_cost_eur_cents"] != 0 or cap["new_obligation"] is not False
                    or cap["data_scope"] not in {"owner_private_local", "public_no_private_input", "public_research_minimized"}
                    or cap["communication_scope"] != "none"
                    or not isinstance(cap["cost_evidence"], str) or not cap["cost_evidence"].strip()
                    or not isinstance(cap["action_name"], str) or not cap["action_name"].strip()
                    or not isinstance(cap["configuration_sha256"], str)
                    or len(cap["configuration_sha256"]) != 64
                    or any(c not in "0123456789abcdef" for c in cap["configuration_sha256"])):
                raise InitiativeMandateDenied("capability lacks a known zero-cost authority profile")
            for key in ("execute_schema", "observe_schema"):
                schema = cap[key]
                # No schema retrieval or permissive argument envelope from provider text.
                if (not isinstance(schema, dict) or schema.get("type") != "object"
                        or schema.get("additionalProperties") is not False
                        or '"$ref"' in json.dumps(schema)):
                    raise InitiativeMandateDenied("capability argument boundary is invalid")
                if cap["data_scope"] == "public_research_minimized" and (
                        cap["action_name"] != "research_current_options"
                        or cap["tool_name"] != "research.public_sources"
                        or cap["observation_tool_name"] != "research.public_sources"
                        or schema.get("properties", {}).get("public_review") != {"type": "string", "const": "public_unlinked"}
                        or "public_review" not in schema.get("required", ())):
                    raise InitiativeMandateDenied("minimized public scope is only for reviewed research input")
                _check_profile_schema(schema)
                if (cap["data_scope"] == "public_no_private_input"
                        and not _public_input_is_host_selected(schema)):
                    raise InitiativeMandateDenied("public capability requires host-selected input values")
        return data

    def policy(self):
        data = self._read()
        digest = canonical_sha256({k: v for k, v in data.items() if k != "enabled"})
        now = datetime.now(UTC).replace(microsecond=0)
        with self.owner._engine.begin() as conn:
            self.owner._authorize(conn, now)
        try:
            state = self.timeline.load(self.thread)
        except TimelineNotFound:
            try:
                state = self.timeline.create_thread(thread_id=self.thread,
                    checkpoint_id=self.thread + ":admitted", occurred_at=now,
                    data={"mandate_sha256": digest, "authorization_source": data["authorization_source"]})
            except CheckpointConflict:
                state = self.timeline.load(self.thread)
        if state.phase == "initiative_mandate_revoked":
            raise InitiativeMandateDenied("initiative mandate was durably revoked")
        latest = self.timeline.events(self.thread)[-1].payload["data"]
        if latest.get("mandate_sha256") != digest:
            raise InitiativeMandateDenied("initiative mandate revision changed; owner admission required")
        if not data["enabled"]:
            self.revoke()
            raise InitiativeMandateDenied("initiative mandate revoked")
        if state.phase == "initiative_mandate_revoked":
            raise InitiativeMandateDenied("initiative mandate was durably revoked")
        return data, digest

    def _admitted_digest(self, conn):
        c = operational_events.c
        row = conn.execute(sa.select(c.payload, c.payload_sha256).where(
            c.tenant_id == self.owner._scope.tenant_id,
            c.installation_id == self.owner._scope.installation_id,
            c.thread_id == self.thread).order_by(c.sequence.desc()).limit(1)).one()
        if canonical_sha256(row.payload) != bytes(row.payload_sha256).hex():
            raise InitiativeMandateDenied("admitted mandate evidence hash differs")
        return row.payload["data"].get("mandate_sha256")

    def admit_revision(self, expected_sha256, candidate_sha256):
        """Owner-only CAS over the host file; never accepts model-supplied policy."""
        from eva.application.initiative_execution import current_initiative
        if current_initiative() is not None:
            raise InitiativeMandateDenied("initiative cannot revise its own mandate")
        if any(not isinstance(v, str) or re.fullmatch(r"[0-9a-f]{64}", v) is None
               for v in (expected_sha256, candidate_sha256)):
            raise ValueError("mandate revision requires two SHA256 digests")
        data = self._read()
        digest = canonical_sha256({k: v for k, v in data.items() if k != "enabled"})
        if not data["enabled"] or digest != candidate_sha256:
            raise InitiativeMandateDenied("candidate mandate does not match enabled host file")
        now = datetime.now(UTC).replace(microsecond=0)
        with self.owner._engine.begin() as conn:
            self.owner._authorize(conn, now)
            c = operational_checkpoints.c
            row = conn.execute(sa.select(operational_checkpoints).where(
                c.tenant_id == self.owner._scope.tenant_id,
                c.installation_id == self.owner._scope.installation_id,
                c.thread_id == self.thread).with_for_update()).one_or_none()
            if row is None or row.phase == "initiative_mandate_revoked":
                raise InitiativeMandateDenied("unadmitted or revoked mandate cannot be revised")
            current = self._admitted_digest(conn)
            if current == digest:
                return {"status": "unchanged", "mandate_sha256": digest}
            if current != expected_sha256:
                raise InitiativeMandateDenied("previous mandate revision differs")
            transition_timeline_thread(conn, self.owner._scope, thread_id=self.thread,
                expected_revision=row.revision,
                checkpoint_id=self.thread + f":revision:{row.revision + 1}",
                phase="initiative_mandate_admitted", status="active",
                event_type="INITIATIVE_MANDATE_REVISED", occurred_at=now,
                data={"previous_mandate_sha256": current, "mandate_sha256": digest,
                      "authorization_source": data["authorization_source"],
                      "owner_principal_id": str(self.owner._owner)})
        return {"status": "revised", "mandate_sha256": digest}

    def revoke(self):
        now = datetime.now(UTC).replace(microsecond=0)
        with self.owner._engine.begin() as conn:
            self.owner._authorize(conn, now)
            c = operational_checkpoints.c
            row = conn.execute(sa.select(operational_checkpoints).where(
                c.tenant_id == self.owner._scope.tenant_id,
                c.installation_id == self.owner._scope.installation_id,
                c.thread_id == self.thread).with_for_update()).one_or_none()
            if row is None:
                # Materialize the owner-authorized policy before revoking it.
                data = self._read()
                digest = canonical_sha256({k: v for k, v in data.items() if k != "enabled"})
                from eva.adapters.postgres.operational_timeline import insert_initial_timeline_thread
                insert_initial_timeline_thread(conn, self.owner._scope, thread_id=self.thread,
                    checkpoint_id=self.thread + ":revoked", occurred_at=now,
                    phase="initiative_mandate_revoked", status="completed",
                    event_type="INITIATIVE_MANDATE_REVOKED", data={"mandate_sha256": digest})
            elif row.phase != "initiative_mandate_revoked":
                transition_timeline_thread(conn, self.owner._scope, thread_id=self.thread,
                    expected_revision=row.revision,
                    checkpoint_id=self.thread + f":revoked:{row.revision + 1}",
                    phase="initiative_mandate_revoked", status="completed",
                    event_type="INITIATIVE_MANDATE_REVOKED", occurred_at=now,
                    data={"owner_principal_id": str(self.owner._owner)})
        return {"status": "revoked", "new_actions_allowed": False}

    def digital_grant(self, profile):
        from eva.contracts.delegated_digital_work import DelegatedDigitalWorkGrant
        policy, _ = self.policy()
        digital = policy.get("digital_work")
        if (digital is None or digital["worker_profile_release_sha256"]
                != profile.release.worker_profile_release_sha256):
            raise InitiativeMandateDenied("digital initiative host profile is not admitted")
        permitted = set(digital["capability_ids"]) | {c["action_name"] for c in policy["capabilities"]}
        grant = profile.grant().to_payload()
        grant["capability_ids"] = [name for name in grant["capability_ids"] if name in permitted]
        if not grant["capability_ids"]:
            raise InitiativeMandateDenied("digital initiative has no installed admitted capability")
        return DelegatedDigitalWorkGrant.model_validate(grant)

    def bind_work(self, spec):
        from eva.adapters.postgres.conversation_actions import PostgresPersonalInterestStore
        from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
        from eva.contracts.delegated_digital_work import DelegatedInitiativeDigitalWorkSpec
        if not isinstance(spec, DelegatedInitiativeDigitalWorkSpec):
            raise InitiativeMandateDenied("native digital contract is required")
        origin = spec.origin
        owner = self.owner
        if (spec.owner_principal_id != str(owner._owner)
                or spec.principal_binding_id != str(owner._binding)):
            raise InitiativeMandateDenied("native digital owner binding differs")
        claim = PostgresPersonalInterestStore._claim_from_events(owner._tenant_id,
            owner._installation_id, self.timeline.events(origin.thread_id), origin.claim_revision)
        guard, turn = self.bind(claim, work_spec=spec)
        expected = {key: getattr(turn, key) for key in (
            "tenant_id", "installation_id", "principal_id", "conversation_id", "message_id")}
        expected["audience"] = {"kind": "private_person", "principal_id": turn.principal_id}
        if (spec.source.model_dump(mode="json") != expected
                or canonical_sha256(PostgresConversationJournal._ingress_data(turn)) != spec.source_ingress_sha256
                or origin.intent_id != claim.intent_id or origin.mandate_sha256 != guard.mandate_sha):
            raise InitiativeMandateDenied("native digital source differs from its durable initiative")
        policy, _ = self.policy()
        digital = policy.get("digital_work")
        if (digital is None or digital["worker_profile_release_sha256"] != spec.worker_profile_release_sha256
                or not set(spec.grant.capability_ids).issubset(
                    set(digital["capability_ids"]) | {c["action_name"] for c in policy["capabilities"]})):
            raise InitiativeMandateDenied("native digital capability binding differs")
        return guard, turn

    @staticmethod
    def _work_admission(events, spec):
        expected_type = ("PERSONAL_INITIATIVE_WORK_ADMITTED" if spec.generation == 1
                         else "PERSONAL_INITIATIVE_WORK_RESUMED")
        for event in events:
            if event.event_type != expected_type:
                continue
            data = event.payload.get("data", {})
            if (canonical_sha256(event.payload) != event.payload_sha256
                    or data.get("intent_id") != spec.origin.intent_id
                    or data.get("work_id") != spec.work_id or data.get("spec_sha256") != spec.sha256
                    or data.get("mandate_sha256") != spec.origin.mandate_sha256):
                continue
            if spec.generation == 1:
                if event.sequence == spec.origin.claim_revision + 1:
                    return event
            elif (event.sequence > spec.origin.claim_revision + 1
                    and data.get("generation") == spec.generation
                    and data.get("previous_work_id") == spec.parent_task_id
                    and data.get("source_ingress_sha256") == spec.source_ingress_sha256
                    and data.get("recovery_kind") == "explicit_private_owner_native_resume"
                    and all(isinstance(data.get(key), str)
                            and re.fullmatch(r"[0-9a-f]{64}", data[key]) is not None
                            for key in ("previous_spec_sha256", "recovery_ingress_sha256"))):
                return event
        return None

    def bind_resume_work(self, previous, spec, *, now, _connection=None):
        """Admission-only guard; resume never invents another native source claim."""
        from eva.adapters.postgres.conversation_actions import PostgresPersonalInterestStore
        from eva.contracts.delegated_digital_work import DelegatedInitiativeDigitalWorkSpec
        if (type(previous.spec) is not DelegatedInitiativeDigitalWorkSpec
                or type(spec) is not DelegatedInitiativeDigitalWorkSpec
                or previous.state != "failed" or previous.stop_requested_at is not None
                or previous.lease_owner is not None or previous.retention_until <= now
                or spec.origin != previous.spec.origin or spec.source != previous.spec.source
                or spec.source_ingress_sha256 != previous.spec.source_ingress_sha256
                or spec.execution_id != previous.spec.execution_id or spec.grant != previous.spec.grant):
            raise InitiativeMandateDenied("native resume requires its retained known failed attempt")
        self._retained_work_source(previous, now=now, _connection=_connection)
        claim = PostgresPersonalInterestStore._claim_from_events(self.owner._tenant_id,
            self.owner._installation_id, self.timeline.events(spec.origin.thread_id),
            spec.origin.claim_revision)
        return self.bind(claim, work_spec=spec, resume_work=previous, _connection=_connection)

    def work_memory_source(self, work, *, now):
        """Read-only outcome-memory compatibility; no new execution authority."""
        return self._retained_work_source(work, now=now)

    def _retained_work_source(self, work, *, now, _connection=None):
        """Read a retained outcome's original intent; never grant another action."""
        from eva.adapters.postgres.conversation_actions import PostgresPersonalInterestStore
        from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
        from eva.contracts.delegated_digital_work import DelegatedInitiativeDigitalWorkSpec

        spec, owner = work.spec, self.owner
        if (type(spec) is not DelegatedInitiativeDigitalWorkSpec
                or spec.owner_principal_id != str(owner._owner)
                or spec.principal_binding_id != str(owner._binding)
                or work.retention_until <= now):
            raise InitiativeMandateDenied("initiative memory owner or retention differs")
        try:
            from contextlib import nullcontext
            with (owner._engine.begin() if _connection is None else nullcontext(_connection)) as conn:
                owner._authorize(conn, now)
                row = conn.execute(owner._query().where(
                    operational_checkpoints.c.thread_id == spec.origin.thread_id)
                    .with_for_update(of=operational_checkpoints)).one_or_none()
                if row is None:
                    raise InitiativeMandateDenied("initiative memory source is absent")
                events = self.timeline.events(spec.origin.thread_id)
        except (RuntimeError, TimelineNotFound) as error:
            raise InitiativeMandateDenied("initiative memory source is unavailable") from error
        claim = PostgresPersonalInterestStore._claim_from_events(
            owner._tenant_id, owner._installation_id, events, spec.origin.claim_revision)
        turn = execution_turn(claim)
        expected = {key: getattr(turn, key) for key in (
            "tenant_id", "installation_id", "principal_id", "conversation_id", "message_id")}
        expected["audience"] = {"kind": "private_person", "principal_id": turn.principal_id}
        admission = self._work_admission(events, spec)
        bound = {} if admission is None else admission.payload.get("data", {})
        if (claim.principal_id != str(owner._owner) or claim.intent_id != spec.origin.intent_id
                or spec.source.model_dump(mode="json") != expected
                or canonical_sha256(PostgresConversationJournal._ingress_data(turn))
                    != spec.source_ingress_sha256
                or bound.get("work_id") != spec.work_id
                or bound.get("spec_sha256") != spec.sha256
                or bound.get("mandate_sha256") != spec.origin.mandate_sha256):
            raise InitiativeMandateDenied("initiative memory source binding differs")
        if work.state in {"succeeded", "verified"}:
            completion = next((event for event in reversed(events)
                if event.event_type == "PERSONAL_INITIATIVE_COMPLETED"), None)
            completed = {} if completion is None else completion.payload.get("data", {})
            if (row.phase != "initiative_completed" or row.status != "completed" or work.result is None
                    or completed.get("work_id") != spec.work_id
                    or completed.get("spec_sha256") != spec.sha256
                    or completed.get("result_sha256") != canonical_sha256(work.result.to_payload())):
                raise InitiativeMandateDenied("initiative memory lacks its completed parent")
        return turn

    def _completed_work(self, conn, work_id, *, now):
        """Retained typed queue/result/parent provenance, without old execution rights."""
        from eva.adapters.postgres.delegated_work import PostgresDelegatedWorkStore
        from eva.contracts.delegated_digital_work import DelegatedInitiativeDigitalWorkSpec
        owner = self.owner
        owner._authorize(conn, now)
        store = PostgresDelegatedWorkStore(owner._engine, owner._authority)
        # This owner transaction intentionally crosses the two existing exact
        # scopes; the generic store setter correctly refuses implicit rebinding.
        PostgresOperationalTimeline(owner._engine, owner._authority)._scope(conn)
        work = store._load_terminal_locked(conn, work_id=work_id,
            missing_message="native procedure work is absent")
        spec = work.spec
        if (type(spec) is not DelegatedInitiativeDigitalWorkSpec
                or spec.owner_principal_id != str(owner._owner)
                or spec.principal_binding_id != str(owner._binding)
                or work.state not in {"succeeded", "verified"} or work.result is None
                or work.stop_requested_at is not None or work.lease_owner is not None
                or work.retention_until <= now):
            raise InitiativeMandateDenied("native procedure work is not retained completed work")
        turn = self._retained_work_source(work, now=now, _connection=conn)
        return work, turn

    def retained_procedure_source(self, *, source=None, scope=None, message=None, now):
        """Exact historical source lookup; never reconstruct an owner INGRESS."""
        from eva.adapters.postgres.current_catalog import delegated_work_runs as work
        if source is not None:
            fields = {"tenant_id", "installation_id", "principal_id", "conversation_id", "message_id", "audience"}
            if (not isinstance(source, dict) or set(source) != fields
                    or source["audience"] != {"kind": "private_person", "principal_id": str(self.owner._owner)}):
                raise InitiativeMandateDenied("native procedure source is not private owner data")
            identity = {key: source[key] for key in fields - {"audience"}}
        elif scope is not None and isinstance(message, str) and message:
            identity = {key: getattr(scope, key) for key in ("tenant_id", "installation_id", "principal_id")}
            identity["message_id"] = message
        else:
            raise InitiativeMandateDenied("exact native source identity is required")
        if any(identity[key] != expected for key, expected in (
                ("tenant_id", self.owner._tenant_id), ("installation_id", self.owner._installation_id),
                ("principal_id", str(self.owner._owner)))):
            raise InitiativeMandateDenied("native procedure source scope differs")
        with self.owner._engine.begin() as conn:
            self.owner._authorize(conn, now)
            PostgresOperationalTimeline(self.owner._engine, self.owner._authority)._scope(conn)
            ids = conn.execute(sa.select(work.c.work_id).where(
                work.c.spec_payload["contract"].astext == "DelegatedInitiativeDigitalWork/v1",
                work.c.state.in_(("succeeded", "verified")),
                *(work.c.spec_payload["source"][key].astext == value for key, value in identity.items()))
                .limit(2)).scalars().all()
            if len(ids) != 1:
                raise InitiativeMandateDenied("native procedure source is absent or ambiguous")
            _, turn = self._completed_work(conn, ids[0], now=now)
            if scope is not None and turn.project_id != scope.project_id:
                raise InitiativeMandateDenied("native procedure project differs")
            return turn

    def reviewed_work_current(self, work_id, *, now):
        """Eligibility only; a completed queue row alone grants no new dispatch."""
        policy, digest = self.policy()
        with self.owner._engine.begin() as conn:
            work, turn = self._completed_work(conn, work_id, now=now)
        spec = work.spec
        digital = policy.get("digital_work")
        if (digest != spec.origin.mandate_sha256 or digital is None
                or digital["worker_profile_release_sha256"] != spec.worker_profile_release_sha256
                or not set(spec.grant.capability_ids).issubset(
                    set(digital["capability_ids"]) | {cap["action_name"] for cap in policy["capabilities"]})):
            raise InitiativeMandateDenied("completed native work mandate or grant changed")
        from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
        PostgresConversationJournal(self.owner._engine, self.owner._scope).ensure_running(turn)
        return work, turn

    def bind_reviewed_saved_application(self, work_id, publication, candidate_content):
        """Host-only binding for one independently accepted saved-source publication."""
        from eva.adapters.postgres.conversation_actions import PostgresPersonalInterestStore
        from eva.operations.component_evaluation import validate_saved_artifact_target
        from pathlib import PurePosixPath
        import hashlib
        now = datetime.now(UTC).replace(microsecond=0)
        work, turn = self.reviewed_work_current(work_id, now=now)
        target = validate_saved_artifact_target(publication["source_target"],
            baseline_sha256=publication["baseline_sha256"])
        actual_scope = {key: getattr(turn, key) for key in
                        ("tenant_id", "installation_id", "principal_id", "project_id")}
        candidate_sha = hashlib.sha256(candidate_content.encode()).hexdigest()
        expected_path = str(PurePosixPath(target["artifact_path"]).with_name(
            PurePosixPath(target["artifact_path"]).stem + ".improved-" + candidate_sha + ".py"))
        if (publication.get("contract") != "EvaSavedArtifactPublication/v1"
                or re.fullmatch(r"[0-9a-f]{64}", publication.get("review_id", "")) is None
                or target["scope"] != actual_scope
                or not 0 < len(candidate_content.encode()) <= 16384
                or publication.get("candidate_sha256") != candidate_sha
                or publication.get("destination_path") != expected_path
                or datetime.fromisoformat(target["retention_until"].replace("Z", "+00:00")) <= now
                or "apply_reviewed_component_improvement" not in work.spec.grant.capability_ids):
            raise InitiativeMandateDenied("reviewed native publication binding differs")
        claim = PostgresPersonalInterestStore._claim_from_events(self.owner._tenant_id,
            self.owner._installation_id, self.timeline.events(work.spec.origin.thread_id),
            work.spec.origin.claim_revision)
        thread = "initiative-execution:" + claim.intent_id.removeprefix("personal-interest:")
        # This is the original native execution thread; never create a terminal mandate.
        self.timeline.load(thread)
        expected = {"intent_id": claim.intent_id, "origin": "eva_initiative",
            "mandate_sha256": work.spec.origin.mandate_sha256, "turn_sha256": canonical_sha256({
                "principal_id": turn.principal_id, "conversation_id": turn.conversation_id,
                "message_id": turn.message_id, "text": turn.text, "occurred_at": turn.occurred_at})}
        if self.timeline.events(thread)[0].payload.get("data") != expected:
            raise InitiativeMandateDenied("original native execution binding differs")
        guard = ReviewedNativeSavedArtifactGuard(self, claim, turn, work.spec.origin.mandate_sha256,
            thread, work=work, publication=publication, candidate_content=candidate_content)
        guard.check(turn)
        return guard, turn

    def settle_work(self, conn, spec, *, state, result, usage, now, guard=None):
        """Persist the real outcome on its original parent in the queue transaction."""
        owner = self.owner
        self.timeline._scope(conn)
        c, e = operational_checkpoints.c, operational_events.c
        row = conn.execute(owner._query().where(c.thread_id == spec.origin.thread_id)
            .with_for_update(of=operational_checkpoints)).one_or_none()
        if row is None:
            raise InitiativeMandateDenied("native parent is absent")
        if row.phase == "personal_stopped":
            return  # Stop wins; a late result cannot manufacture completion.
        admission = self._work_admission(self.timeline.events(spec.origin.thread_id), spec)
        if (row.phase != "initiative_work_waiting" or row.status != "waiting_for_proof"
                or admission is None or row.revision != admission.sequence):
            raise InitiativeMandateDenied("native parent outcome no longer matches")
        bound = conn.execute(sa.select(e.payload, e.payload_sha256).where(
            e.tenant_id == owner._scope.tenant_id, e.installation_id == owner._scope.installation_id,
            e.thread_id == row.thread_id, e.sequence == row.revision)).one()
        if (canonical_sha256(bound.payload) != bytes(bound.payload_sha256).hex()
                or bound.payload["data"].get("spec_sha256") != spec.sha256
                or bound.payload["data"].get("work_id") != spec.work_id):
            raise InitiativeMandateDenied("native parent work receipt differs")
        complete = state == "succeeded" and result is not None and guard is not None
        if complete:
            try:
                # Includes current owner, stop/revision and admitted mandate lock.
                guard.check_locked(conn)
            except (InitiativeMandateDenied, RuntimeError):
                complete = False
        data = {"intent_id": spec.origin.intent_id, "origin": "eva_initiative",
            "work_id": spec.work_id, "spec_sha256": spec.sha256, "work_state": state}
        if complete:
            data.update(content=result.output, content_sha256=result.output_sha256,
                filename="initiatief-resultaat.md", provider="eva:delegated-digital-work",
                conversation_id=spec.source.conversation_id, message_id=spec.source.message_id,
                call_id=result.call_id, result_sha256=canonical_sha256(result.to_payload()),
                delegated_usage=usage.to_payload(), semantic_verification=False)
        else:
            data.update(reason="Digitale uitvoering niet bevestigd onder het actuele initiatiefmandaat.",
                delegated_usage=None if usage is None else usage.to_payload())
        transition_timeline_thread(conn, owner._scope, thread_id=row.thread_id,
            expected_revision=row.revision, checkpoint_id=(row.thread_id + ":digital-outcome"
                if spec.generation == 1 else row.thread_id + ":digital-outcome:" + spec.work_id),
            phase="initiative_completed" if complete else "initiative_blocked",
            status="completed" if complete else "needs_help",
            event_type="PERSONAL_INITIATIVE_COMPLETED" if complete else "PERSONAL_INITIATIVE_BLOCKED",
            occurred_at=now, data=data)

    def bind(self, claim, *, work_spec=None, resume_work=None, _connection=None):
        policy, digest = self.policy()
        from eva.adapters.postgres.conversation_actions import PostgresPersonalInterestStore
        durable = PostgresPersonalInterestStore._claim_from_events(
            claim.tenant_id, claim.installation_id, self.timeline.events(claim.thread_id), claim.revision)
        if durable != claim:
            raise InitiativeMandateDenied("initiative claim differs from its durable native source")
        turn = execution_turn(claim)
        thread = "initiative-execution:" + claim.intent_id.removeprefix("personal-interest:")
        data = {"intent_id": claim.intent_id, "origin": "eva_initiative",
                "mandate_sha256": digest, "turn_sha256": canonical_sha256({
                    "principal_id": turn.principal_id, "conversation_id": turn.conversation_id,
                    "message_id": turn.message_id, "text": turn.text, "occurred_at": turn.occurred_at})}
        if resume_work is not None:
            existing = self.timeline.events(thread)
            if not existing or existing[0].payload.get("data") != data:
                raise InitiativeMandateDenied("native resume lacks its original execution binding")
        else:
            try:
                self.timeline.create_thread(thread_id=thread, checkpoint_id=thread + ":bound",
                    occurred_at=datetime.now(UTC).replace(microsecond=0), data=data)
            except CheckpointConflict:
                if self.timeline.events(thread)[0].payload["data"] != data:
                    raise InitiativeMandateDenied("native execution binding differs") from None
        guard = NativeInitiativeGuard(self, claim, turn, digest, thread,
            work_spec=work_spec, resume_work=resume_work)
        if _connection is None:
            guard.check(turn)
        else:
            guard.check_locked(_connection)
        return guard, turn


class NativeInitiativeGuard:
    def __init__(self, authority, claim, turn, mandate_sha, thread, *, work_spec=None, resume_work=None):
        self.authority, self.claim, self.turn = authority, claim, turn
        self.mandate_sha, self.thread = mandate_sha, thread
        self.work_spec = work_spec
        self.resume_work = resume_work

    def check(self, turn):
        if turn != self.turn:
            raise InitiativeMandateDenied("initiative execution turn differs")
        _, digest = self.authority.policy()
        if digest != self.mandate_sha:
            raise InitiativeMandateDenied("initiative mandate changed")
        owner = self.authority.owner
        with owner._engine.begin() as conn:
            self.check_locked(conn)

    def check_locked(self, conn):
        """Same owner rows serialize native tool admission, handoff and stop."""
        policy = self.authority._read()
        if (not policy["enabled"] or canonical_sha256({k: v for k, v in policy.items()
                if k != "enabled"}) != self.mandate_sha):
            raise InitiativeMandateDenied("initiative host mandate changed before dispatch")
        owner = self.authority.owner
        owner._authorize(conn, datetime.now(UTC).replace(microsecond=0))
        c = operational_checkpoints.c
        mandate = conn.execute(sa.select(operational_checkpoints).where(
            c.tenant_id == owner._scope.tenant_id, c.installation_id == owner._scope.installation_id,
            c.thread_id == self.authority.thread).with_for_update()).one()
        if (mandate.phase == "initiative_mandate_revoked"
                or self.authority._admitted_digest(conn) != self.mandate_sha):
            raise InitiativeMandateDenied("initiative mandate revoked or revised before dispatch")
        row = conn.execute(owner._query().where(c.thread_id == self.claim.thread_id)
            .with_for_update(of=operational_checkpoints)).one_or_none()
        if self.resume_work is not None:
            previous = self.resume_work
            events = self.authority.timeline.events(self.claim.thread_id)
            outcome = events[-1] if events else None
            data = {} if outcome is None else outcome.payload.get("data", {})
            if (row is None or row.phase != "initiative_blocked" or row.status != "needs_help"
                    or outcome is None or outcome.sequence != row.revision
                    or outcome.event_type != "PERSONAL_INITIATIVE_BLOCKED"
                    or data.get("work_id") != previous.spec.work_id
                    or data.get("spec_sha256") != previous.spec.sha256
                    or data.get("work_state") != "failed"
                    or data.get("delegated_usage") != (None if previous.usage is None else previous.usage.to_payload())
                    or self.work_spec.origin.mandate_sha256 != self.mandate_sha):
                raise InitiativeMandateDenied("native failure was stopped, superseded or is uncertain")
            return row
        admission = (None if self.work_spec is None else self.authority._work_admission(
            self.authority.timeline.events(self.claim.thread_id), self.work_spec))
        revision = self.claim.revision if self.work_spec is None else (
            None if admission is None else admission.sequence)
        phase = "initiative_work_waiting" if self.work_spec is not None else "exploration_claimed"
        if (row is None or row.revision != revision or row.phase != phase
                or row.status != "waiting_for_proof"
                or row.adoption["data"].get("execution_mode") != "autonomous_task"):
            raise InitiativeMandateDenied("initiative stopped, superseded or no longer claimed before dispatch")
        return row

    def action_allowed(self, name):
        if self.resume_work is not None:
            return False  # Recovery admission is never tool execution authority.
        self.check(self.turn)
        policy, _ = self.authority.policy()
        # Local work requires the separately admitted host profile; never infer
        # internal privileges from an initiative's text or an owner-shaped ingress.
        if self.work_spec is not None:
            return name in self.work_spec.grant.capability_ids
        return any(c["action_name"] == name for c in policy["capabilities"])

    def authorize_request(self, request, *, phase):
        if self.resume_work is not None:
            raise InitiativeMandateDenied("native recovery admission cannot dispatch tools")
        self.check(self.turn)
        policy, _ = self.authority.policy()
        caps = [c for c in policy["capabilities"]
                if c["configuration_sha256"] == request.tool_configuration_sha256
                and c["tool_name"] == request.tool_name
                and c["observation_tool_name"] == request.observation_tool_name]
        arguments = request.arguments if phase == "execute" else request.observation_arguments
        if arguments is None:
            arguments = request.arguments
        if not any(_matches_profile(arguments, self._schema(c[phase + "_schema"])) for c in caps):
            raise InitiativeMandateDenied("tool cost, data destination or arguments outside initiative mandate")
        # Stop and revoke serialize on these same owner-controlled rows. An
        # effect admitted before stop may finish; after stop no new dispatch or
        # network readback is allowed. Cached journal receipts remain readable.
        now = datetime.now(UTC).replace(microsecond=0)
        owner = self.authority.owner
        with owner._engine.begin() as conn:
            owner._authorize(conn, now)
            c = operational_checkpoints.c
            scope_filter = (c.tenant_id == owner._scope.tenant_id,
                            c.installation_id == owner._scope.installation_id)
            mandate = conn.execute(sa.select(operational_checkpoints).where(
                *scope_filter, c.thread_id == self.authority.thread).with_for_update()).one()
            if mandate.phase == "initiative_mandate_revoked":
                raise InitiativeMandateDenied("initiative mandate revoked before dispatch")
            if self.authority._admitted_digest(conn) != self.mandate_sha:
                raise InitiativeMandateDenied("initiative mandate revised before dispatch")
            self.check_locked(conn)
            state = conn.execute(sa.select(operational_checkpoints).where(
                *scope_filter, c.thread_id == self.thread).with_for_update()).one()
            transition_timeline_thread(conn, owner._scope, thread_id=self.thread,
                expected_revision=state.revision,
                checkpoint_id=self.thread + f":dispatch:{state.revision + 1}",
                phase="initiative_dispatch_admitted", status="active",
                event_type="INITIATIVE_DISPATCH_ADMITTED", occurred_at=now,
                operation_id=request.operation_id,
                data={"phase": phase, "operation_id": request.operation_id,
                      "configuration_sha256": request.tool_configuration_sha256,
                      "arguments_sha256": canonical_sha256(arguments),
                      "mandate_sha256": self.mandate_sha})

    def _schema(self, schema):
        # Host-owned per-initiative destination template; the model cannot choose
        # another run's namespace or overwrite an already completed contribution.
        key = self.claim.intent_id.removeprefix("personal-interest:")
        return json.loads(json.dumps(schema).replace("{initiative_key}", key))

    def instructions(self):
        policy, _ = self.authority.policy()
        return ("Dit is Eva's eigen initiatief, geen verzoek of toestemming van de eigenaar. "
                "Kies en voltooi de nuttige uitkomst met de beschikbare toegelaten tools. "
                "Geen verplichte documentvorm. Claim alleen wat resultaat en toolbewijs dragen. "
                "Geen nieuwe kosten, verplichtingen, identificatie of communicatie naar derden. "
                "Onbekende kosten of ontbrekende mogelijkheden zijn een concrete beperking, geen toestemming. "
                "Bestaand modelverbruik blijft begrensd. Maak geen herhaling van afgerond of gestopt werk. "
                "Voor algemeen publiek onderzoek: abstraheer de behoefte, verwijder persoonlijke "
                "en indirect identificerende details, beoordeel de minimale publieke vraag en "
                "gebruik public_review=public_unlinked alleen als dat oordeel positief is. "
                "Twijfel is geen toestemming. Stuur nooit privéaanleiding of geheugenfragmenten mee. "
                "Beschikbare kosteloze capabilities en hostgrenzen op de uitgewerkte providerinvoer (gebruik het gewone toolschema voor je toolargumenten): " + json.dumps([
                    {"action": c["action_name"], "arguments": self._schema(c["execute_schema"])}
                    for c in policy["capabilities"]], ensure_ascii=False))

class ReviewedNativeSavedArtifactGuard(NativeInitiativeGuard):
    """No general terminal execution: exact accepted save/designation/readback only."""
    def __init__(self, authority, claim, turn, mandate_sha, thread, *, work, publication, candidate_content):
        super().__init__(authority, claim, turn, mandate_sha, thread, work_spec=work.spec)
        self.publication = json.loads(json.dumps(publication))
        self.candidate_content = candidate_content
        self.result_sha256 = canonical_sha256(work.result.to_payload())
        self._designation = None

    def check_locked(self, conn):
        owner = self.authority.owner
        now = datetime.now(UTC).replace(microsecond=0)
        policy = self.authority._read()
        if not policy["enabled"] or canonical_sha256({k: v for k, v in policy.items() if k != "enabled"}) != self.mandate_sha:
            raise InitiativeMandateDenied("reviewed native mandate changed before dispatch")
        owner._authorize(conn, now)
        c = operational_checkpoints.c
        mandate = conn.execute(sa.select(operational_checkpoints).where(
            c.tenant_id == owner._scope.tenant_id, c.installation_id == owner._scope.installation_id,
            c.thread_id == self.authority.thread).with_for_update()).one()
        if mandate.phase == "initiative_mandate_revoked" or self.authority._admitted_digest(conn) != self.mandate_sha:
            raise InitiativeMandateDenied("reviewed native mandate revoked or revised")
        work, turn = self.authority._completed_work(conn, self.work_spec.work_id, now=now)
        if (work.spec != self.work_spec or turn != self.turn
                or canonical_sha256(work.result.to_payload()) != self.result_sha256
                or datetime.fromisoformat(self.publication["source_target"]["retention_until"].replace("Z", "+00:00")) <= now):
            raise InitiativeMandateDenied("reviewed native result or retention changed")
        from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
        PostgresConversationJournal(owner._engine, owner._scope).ensure_running(turn)
        self._check_target_source(now)
        return conn.execute(owner._query().where(c.thread_id == self.claim.thread_id)).one()

    def _check_target_source(self, now):
        from eva.adapters.postgres.memory_outbox import PostgresMemoryOutbox
        target = self.publication["source_target"]
        scope = target["scope"]
        store = PostgresMemoryOutbox(self.authority.owner._engine, tenant_id=scope["tenant_id"],
                                    installation_id=scope["installation_id"])
        doc = store.resolve_source(target["procedure_reference_id"],
            principal_id=scope["principal_id"], project_id=scope["project_id"])
        if doc is None:
            raise InitiativeMandateDenied("reviewed native original reference was erased")
        wire = json.loads(doc.content)
        expected = {"document_id": target["procedure_reference_id"], "path": target["artifact_path"],
            **{key: target[key] for key in ("content_sha256", "receipt_sha256", "source_call_id",
                "source_message_id", "tool_configuration_sha256", "retention_until")}}
        if (wire.get("contract") != "ProcedureReference/v1"
                or any(wire.get(key) != value for key, value in expected.items())
                or doc.source_ref != target["source_message_id"]
                or datetime.fromisoformat(doc.retention_until.replace("Z", "+00:00")) <= now):
            raise InitiativeMandateDenied("reviewed native original reference changed or expired")

    def action_allowed(self, name):
        self.check(self.turn)
        return name in {"save_workspace_document", "designate_reusable_procedure", "resolve_reusable_procedure"}

    def authorize_reference(self, document_id):
        self.check(self.turn)
        if document_id != self.publication["source_target"]["procedure_reference_id"]:
            raise InitiativeMandateDenied("reviewed native reference differs")

    def bind_designation(self, arguments):
        self.check(self.turn)
        if (set(arguments) != {"document_path", "use_when", "retention_until"}
                or arguments["document_path"] != self.publication["destination_path"]
                or arguments["retention_until"] != self.publication["source_target"]["retention_until"]
                or not isinstance(arguments["use_when"], str) or not arguments["use_when"].strip()
                or len(arguments["use_when"]) > 1000
                or (self._designation is not None and self._designation != arguments)):
            raise InitiativeMandateDenied("reviewed native designation differs")
        self._designation = dict(arguments)

    def authorize_procedure(self, call):
        self.check(self.turn)
        if call.name == "designate_reusable_procedure" and self._designation is not None and call.arguments == self._designation:
            return
        if call.name == "resolve_reusable_procedure" and call.arguments == {
                "document_id": self.publication["source_target"]["procedure_reference_id"]}:
            return
        raise InitiativeMandateDenied("reviewed native procedure action differs")

    def authorize_request(self, request, *, phase):
        import hashlib
        target = self.publication["source_target"]
        arguments = request.arguments
        path, content = arguments.get("path"), arguments.get("content")
        allowed = {target["artifact_path"]: target["content_sha256"],
                   self.publication["destination_path"]: self.publication["candidate_sha256"]}
        if (phase not in {"execute", "observe"}
                or request.tool_configuration_sha256 != target["tool_configuration_sha256"]
                or path not in allowed or not isinstance(content, str)
                or hashlib.sha256(content.encode()).hexdigest() != allowed[path]
                or (phase == "execute" and (path != self.publication["destination_path"]
                    or content != self.candidate_content))
                or (request.observation_arguments is not None
                    and request.observation_arguments != {"path": path})):
            raise InitiativeMandateDenied("reviewed native request differs from the accepted package")
        super().authorize_request(request, phase=phase)

    def instructions(self):
        return "Host-only exact reviewed saved-artifact application; no model execution."
