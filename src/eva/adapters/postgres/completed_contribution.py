"""Register a model-selected, independently read private contribution atomically."""
from copy import deepcopy
import hashlib
import sqlalchemy as sa

from eva.application.conversation import ConversationActionCall, ConversationActionReceipt, ConversationConflict
from eva.adapters.postgres.initiative_adoption import (
    PostgresOwnerConversationInitiativeAdopter, adopt_private_initiative,
)
from eva.adapters.postgres.operational_timeline import PostgresOperationalTimeline, transition_timeline_thread, operational_checkpoints
from eva.contracts.canonical_json import canonical_sha256


class CompletedContributionActions:
    action_names = ("register_own_contribution",)
    supports_indexed_steps = True

    def __init__(self, authority_loader, owner_resolver, artifact_resolver,
                 authorize, dispatch, clock, *, authorize_locked=None,
                 execution_namespace="ordinary-conversation", spec_binding=None):
        if not isinstance(execution_namespace, str) or not execution_namespace.strip():
            raise ValueError("contribution execution namespace is required")
        self._adopter = PostgresOwnerConversationInitiativeAdopter(authority_loader, owner_resolver)
        self._artifact_resolver = artifact_resolver
        self._authorize, self._dispatch, self._clock = authorize, dispatch, clock
        self._authorize_locked = authorize_locked
        self._namespace = execution_namespace
        self._spec_binding = deepcopy(spec_binding)
        canonical_sha256(self._spec_binding)

    def with_execution_guard(self, *, authorize, dispatch, clock, authorize_locked,
                             execution_namespace, spec_binding):
        """Bind the same host capability to one genuinely admitted worker attempt."""
        return type(self)(self._adopter._authority_loader, self._adopter._owner_resolver,
            self._artifact_resolver, authorize, dispatch, clock,
            authorize_locked=authorize_locked, execution_namespace=execution_namespace,
            spec_binding=spec_binding)

    def _authority(self, turn):
        self._authorize(turn)
        return self._private_authority(turn)

    def _private_authority(self, turn):
        """Validate private authority without reopening the locked worker row."""
        from eva.application.initiative_execution import require_initiative_context
        if turn.audience.kind != "private_person" or turn.conversation_id.startswith("initiative-task:"):
            raise ConversationConflict("contribution requires an ordinary private source")
        if require_initiative_context(turn) is not None:
            raise ConversationConflict("recursive initiative registration is unavailable")
        return self._adopter.validate(turn)

    def available_for(self, turn):
        if turn.audience.kind != "private_person" or turn.conversation_id.startswith("initiative-task:"):
            return False
        try:
            self._authority(turn)
        except (ConversationConflict, OSError, ValueError):
            return False
        except RuntimeError as error:
            if str(error) == "initiative owner binding is not current":
                return False
            raise
        return True

    def active_context(self, turn):
        if not self.available_for(turn):
            return ()
        return ()

    def _id(self, turn, step_index):
        if type(step_index) is not int or step_index < 0:
            raise ConversationConflict("contribution step is invalid")
        return "personal-interest:" + canonical_sha256({
            "contract": "CompletedContribution/v1", "tenant": turn.tenant_id,
            "installation": turn.installation_id, "principal": turn.principal_id,
            "conversation": turn.conversation_id, "message": turn.message_id,
            "namespace": self._namespace, "step": step_index, "spec_binding": self._spec_binding})

    def _proof(self, turn, call):
        args = call.arguments
        if (call.name not in self.action_names or set(args) != {
                "title", "motivation", "source_work_id", "artifact_path"}
                or any(not isinstance(v, str) or not v.strip() for v in args.values())
                or len(args["title"]) > 160 or len(args["motivation"]) > 800):
            raise ConversationConflict("contribution arguments are invalid")
        proof = self._artifact_resolver(turn, args["source_work_id"], args["artifact_path"])
        content = proof.get("content")
        if (not isinstance(content, str) or not content.strip()
                or len(content.encode("utf-8")) > 131072
                or hashlib.sha256(content.encode("utf-8")).hexdigest() != proof.get("content_sha256")
                or not isinstance(proof.get("source_provenance"), dict)
                or not proof["source_provenance"]):
            raise ConversationConflict("contribution artifact proof differs")
        canonical_sha256(proof["source_provenance"])
        return proof

    @staticmethod
    def _receipt(call, intent, digest):
        return ConversationActionReceipt(call, {"status": "verified", "operation_result": {
            "status": "registered", "intent_id": intent,
            "content_sha256": digest, "deliveryPending": True}})

    def pending(self, turn, *, step_index=0):
        return self.pending_step(turn, step_index=step_index)

    def pending_step(self, turn, *, step_index):
        if not self.available_for(turn):
            return None
        authority, _ = self._authority(turn)
        events = PostgresOperationalTimeline(authority.owner._engine, authority.owner._scope).events(
            "intent:" + self._id(turn, step_index))
        if not events:
            return None
        data = events[0].payload["data"]
        call = ConversationActionCall(data["action_call_id"], self.action_names[0], data["action_arguments"])
        return self._execute(turn, call, step_index=step_index, replay_only=True)

    def execute(self, turn, call, *, step_index=0):
        return self.execute_step(turn, call, step_index=step_index)

    def execute_step(self, turn, call, *, step_index):
        return self._execute(turn, call, step_index=step_index, replay_only=False)

    def _execute(self, turn, call, *, step_index, replay_only):
        authority, canonical = self._authority(turn)
        owner = authority.owner
        proof = self._proof(turn, call)
        intent = self._id(turn, step_index)
        data = {"intent_id": intent, "principal_id": canonical,
            "source_principal_id": turn.principal_id, "source_conversation_id": turn.conversation_id,
            "source_message_id": turn.message_id, "action_call_id": call.call_id,
            "action_arguments": deepcopy(call.arguments), "step_index": step_index,
            "execution_namespace": self._namespace, "spec_binding": self._spec_binding,
            "title": call.arguments["title"], "motivation": call.arguments["motivation"],
            "next_exploration": "Completed artifact; no further execution requested.",
            "origin": "eva_initiative", "execution_mode": "completed_contribution",
            "adoption_source": "ordinary_delegated_contribution", "unprompted": False,
            "content_sha256": proof["content_sha256"],
            "source_provenance": proof["source_provenance"]}
        now = self._clock()
        with owner._engine.begin() as conn:
            def revalidate():
                from eva.application.cancellation import check_cancellation
                check_cancellation()
                current, resolved = self._private_authority(turn)
                if current.owner._scope != owner._scope or resolved != canonical:
                    raise ConversationConflict("contribution authority changed")
                if self._authorize_locked is not None:
                    self._authorize_locked(conn)
                # The work guard may switch RLS scope; owner validation restores
                # the external timeline scope before adoption queries.
                current.owner._authorize(conn, self._clock())
            revalidate()
            # The shared owner lock serializes replay, duplicate and pending checks.
            outcome = adopt_private_initiative(conn, owner._scope, data, now=now, revalidate=revalidate)
            if outcome["status"] == "not_admitted":
                raise ConversationConflict("contribution admission refused: " + outcome["reason"])
            if outcome["status"] == "adopted":
                if replay_only:
                    raise ConversationConflict("contribution replay disappeared")
                # This callback must only check local signal/source/deadline and
                # counters. It must not open another connection to the locked work.
                self._dispatch(turn)
                revalidate()
                transition_timeline_thread(conn, owner._scope, thread_id="intent:" + intent,
                    expected_revision=1, checkpoint_id="intent:" + intent + ":completed-contribution",
                    phase="initiative_completed", status="completed",
                    event_type="PERSONAL_INITIATIVE_COMPLETED", occurred_at=now,
                    data={"intent_id": intent, "origin": "eva_initiative",
                          "content": proof["content"], "content_sha256": proof["content_sha256"],
                          "filename": "initiatief-resultaat.md", "provider": "eva:stored-contribution",
                          "source_work_id": call.arguments["source_work_id"],
                          "source_provenance": proof["source_provenance"],
                          "conversation_id": turn.conversation_id, "message_id": turn.message_id,
                          "action_call_id": call.call_id})
            else:
                state = conn.execute(sa.select(operational_checkpoints.c.phase).where(
                    operational_checkpoints.c.tenant_id == owner._scope.tenant_id,
                    operational_checkpoints.c.installation_id == owner._scope.installation_id,
                    operational_checkpoints.c.thread_id == "intent:" + intent)).scalar_one()
                if state != "initiative_completed":
                    raise ConversationConflict("contribution is no longer completed")
                events = PostgresOperationalTimeline(owner._engine, owner._scope).events("intent:" + intent)
                completions = [event for event in events if event.event_type == "PERSONAL_INITIATIVE_COMPLETED"]
                if (len(completions) != 1
                        or completions[0].payload["data"]["content_sha256"] != proof["content_sha256"]):
                    raise ConversationConflict("contribution completion replay differs")
        return self._receipt(call, intent, proof["content_sha256"])
