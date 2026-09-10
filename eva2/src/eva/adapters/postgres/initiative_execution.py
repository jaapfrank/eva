"""Current zero-new-cost authority over the existing native work journal."""
from __future__ import annotations

from datetime import UTC, datetime
import json
import re

import sqlalchemy as sa
from eva.adapters.connectors.mcp_gateway import _validate_schema_value

from eva.adapters.credentials.runtime_files import read_runtime_secret_file
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
        _validate_schema_value(value, schema, "initiative arguments")
        if isinstance(value, str) and "pattern" in schema:
            return re.fullmatch(schema["pattern"], value) is not None
        if isinstance(value, dict):
            return all(_matches_profile(v, schema["properties"][k]) for k, v in value.items())
        if isinstance(value, list):
            return all(_matches_profile(v, schema["items"]) for v in value)
        return True
    except (ConversationConflict, TypeError, ValueError):
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
    def __init__(self, owner_store, mandate_file: str):
        self.owner = owner_store
        self.path = mandate_file
        self.timeline = PostgresOperationalTimeline(owner_store._engine, owner_store._scope)
        self.thread = "initiative-mandate:" + str(owner_store._owner)

    def _read(self):
        try:
            data = json.loads(read_runtime_secret_file(self.path,
                setting_name="EVA_INITIATIVE_MANDATE_FILE"))
        except (OSError, ValueError, RuntimeError) as error:
            raise InitiativeMandateDenied("initiative mandate file unavailable") from error
        fields = {"contract", "mandate_id", "owner_principal_id", "enabled",
                  "authorization_source", "capabilities", "tenant_scope", "installation_scope"}
        if (not isinstance(data, dict) or set(data) != fields
                or data["contract"] != "EvaCostlessInitiativeMandate/v1"
                or data["owner_principal_id"] != str(self.owner._owner)
                or data["tenant_scope"] != str(self.owner._scope.tenant_id)
                or data["installation_scope"] != str(self.owner._scope.installation_id)
                or type(data["enabled"]) is not bool
                or not all(isinstance(data[k], str) and data[k].strip()
                           for k in ("mandate_id", "authorization_source"))
                or not isinstance(data["capabilities"], list)):
            raise InitiativeMandateDenied("initiative mandate is invalid")
        for cap in data["capabilities"]:
            required = {"action_name", "configuration_sha256", "new_cost_eur_cents",
                        "new_obligation", "data_scope", "communication_scope",
                        "cost_evidence", "execute_schema", "observe_schema", "tool_name", "observation_tool_name"}
            if (not isinstance(cap, dict) or set(cap) != required
                    or type(cap["new_cost_eur_cents"]) is not int
                    or cap["new_cost_eur_cents"] != 0 or cap["new_obligation"] is not False
                    or cap["data_scope"] not in {"owner_private_local", "public_no_private_input"}
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
                _check_profile_schema(schema)
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

    def bind(self, claim):
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
        try:
            self.timeline.create_thread(thread_id=thread, checkpoint_id=thread + ":bound",
                occurred_at=datetime.now(UTC).replace(microsecond=0), data=data)
        except CheckpointConflict:
            if self.timeline.events(thread)[0].payload["data"] != data:
                raise InitiativeMandateDenied("native execution binding differs") from None
        guard = NativeInitiativeGuard(self, claim, turn, digest, thread)
        guard.check(turn)
        return guard, turn


class NativeInitiativeGuard:
    def __init__(self, authority, claim, turn, mandate_sha, thread):
        self.authority, self.claim, self.turn = authority, claim, turn
        self.mandate_sha, self.thread = mandate_sha, thread

    def check(self, turn):
        if turn != self.turn:
            raise InitiativeMandateDenied("initiative execution turn differs")
        _, digest = self.authority.policy()
        if digest != self.mandate_sha:
            raise InitiativeMandateDenied("initiative mandate changed")
        owner = self.authority.owner
        with owner._engine.begin() as conn:
            owner._authorize(conn, datetime.now(UTC).replace(microsecond=0))
            row = conn.execute(owner._query().where(
                operational_checkpoints.c.thread_id == self.claim.thread_id
            ).with_for_update(of=operational_checkpoints)).one_or_none()
            if (row is None or row.revision != self.claim.revision
                    or row.phase != "exploration_claimed" or row.status != "waiting_for_proof"
                    or row.adoption["data"].get("execution_mode") != "autonomous_task"):
                raise InitiativeMandateDenied("initiative stopped, superseded or no longer claimed")

    def action_allowed(self, name):
        self.check(self.turn)
        policy, _ = self.authority.policy()
        # Only provider actions have pinned profiles. Internal self-grants, recursion,
        # shell execution and unprofiled research never inherit an owner user command.
        return any(c["action_name"] == name for c in policy["capabilities"])

    def authorize_request(self, request, *, phase):
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
            intent = conn.execute(owner._query().where(c.thread_id == self.claim.thread_id)
                .with_for_update(of=operational_checkpoints)).one_or_none()
            if (intent is None or intent.revision != self.claim.revision
                    or intent.phase != "exploration_claimed"
                    or intent.status != "waiting_for_proof"):
                raise InitiativeMandateDenied("initiative stopped before dispatch; previous effects may remain unverified")
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
                "Beschikbare kosteloze capabilities en hostgrenzen op de uitgewerkte providerinvoer (gebruik het gewone toolschema voor je toolargumenten): " + json.dumps([
                    {"action": c["action_name"], "arguments": self._schema(c["execute_schema"])}
                    for c in policy["capabilities"]], ensure_ascii=False))
