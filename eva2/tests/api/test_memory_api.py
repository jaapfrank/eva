from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from eva.adapters.api.app import RuntimeHealth, create_app
from eva.application.conversation import (
    ConversationConflict,
    ConversationTurn,
    GroundedReply,
)
from eva.application.advanced_tasks import (
    AdvancedTaskBlocked,
    AdvancedTaskPackageApproval,
    AdvancedTaskPlan,
    AdvancedTaskRepositoryApproval,
)
from eva.application.memory import (
    ConversationMemoryDocument,
    MemoryRetentionReceipt,
    PresenceMemory,
    RecallCurrentFacts,
)
from eva.application.mail import MailInboxPage, MailMessage, MailMessageSummary
from eva.application.personal_interests import (
    PersonalProductListing,
    PersonalProductRelease,
    PersonalProductReleaseArtifact,
    PersonalPrototype,
    PersonalPrototypeArtifact,
)
from eva.application.research import ResearchArtifact, ResearchSource
from eva.application.commerce import (
    DigitalFulfillmentDownload,
    DigitalFulfillmentGrant,
    ProductPublicationApproval,
)
from eva.application.orchestration import VerifiedToolResult
from eva.application.free_will import ProactiveExpressionClaim
from eva.application.durable_goals import DurableGoalSpec, DurableGoalState
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.memory import MemoryRecall, RecalledFact
from eva.contracts.planning import ConfirmedFact


NOW = "2026-08-23T14:00:00Z"
TENANT = "66a4aad5-0550-5211-8b36-398eabe5b7be"
INSTALLATION = "0a44a85c-4bbb-5330-8803-60fab86e5a94"
PRINCIPAL = "95684f20-4ccb-5830-9a80-1d400f69730b"
FACT = "308563a5-7f06-5066-9fd8-825927cfed36"


def _goal_payload() -> dict[str, object]:
    return {
        "created_at": "2026-08-23T14:00:00Z",
        "delivery_recipient": "sink-a",
        "description": "Maak na vrijgave één resultaat",
        "goal_id": "goal-api-1",
        "owner_id": PRINCIPAL,
        "pivot_instruction": None,
        "primary_instruction": "Maak het resultaat",
        "suitability_field": None,
        "unsuitable_value": None,
        "wake": {
            "deadline": "2026-08-23T15:00:00Z",
            "expected_value": "yes",
            "field": "ready",
            "provider": "goal-test",
            "resource": "release-1",
        },
    }


class _Runtime:
    def __init__(self, *, ready: bool = True) -> None:
        self.ready = ready
        self.queries: list[RecallCurrentFacts] = []
        self.documents: list[ConversationMemoryDocument] = []
        self.turns: list[ConversationTurn] = []
        self.after: list[tuple[ConversationTurn, GroundedReply]] = []
        self.tool_calls: list[dict[str, str]] = []
        self.localtuya_probe_called = False
        self.proactive_claim: ProactiveExpressionClaim | None = None
        self.proactive_acks: list[dict[str, object]] = []
        self.commerce_calls: list[tuple[str, dict[str, str]]] = []
        self.webhooks: list[tuple[bytes, str]] = []
        self.goals: dict[tuple[str, str], DurableGoalState] = {}

    def health(self) -> RuntimeHealth:
        return RuntimeHealth(
            self.ready,
            "ready" if self.ready else "unavailable",
            "ready" if self.ready else "unknown",
        )

    def recall(self, query: RecallCurrentFacts) -> PresenceMemory:
        self.queries.append(query)
        confirmed = ConfirmedFact(
            fact_id=FACT,
            subject=PRINCIPAL,
            predicate="preference.outdoor_temperature",
            value={"preference": "warm", "context": "overkapping"},
            source_ref="telegram:message:5001",
            evidence_ref="evidence:message:5001",
            observed_at=NOW,
            trust="direct_user",
        )
        recall = MemoryRecall(
            schema_version=1,
            contract="MemoryRecall/v1",
            recall_id=query.recall_id,
            query_purpose=query.query_purpose,
            tenant_id=query.tenant_id,
            principal_id=query.principal_id,
            conversation_id=query.conversation_id,
            project_id=query.project_id,
            requested_valid_at=query.requested_valid_at,
            admitted_fact_ids_sha256=canonical_sha256([FACT]),
            selected=[
                RecalledFact(
                    rank=1,
                    fact_id=FACT,
                    source_event_ids=["bd8b4729-f2b3-5c6e-bf8e-d4e0ebfcdd35"],
                    trust="confirmed_direct_user",
                    ranking_reason="hindsight_temporal_semantic_match",
                ).model_dump(mode="json")
            ],
            excluded_reasons={},
            projection_version="hindsight-0.9.1-v1",
            scope_sha256=canonical_sha256(query.scope),
            created_at=query.requested_valid_at,
        )
        return PresenceMemory(recall, (confirmed,))

    def retain(self, document: ConversationMemoryDocument) -> MemoryRetentionReceipt:
        self.documents.append(document)
        return MemoryRetentionReceipt(
            bank_id="eva2-bank",
            document_id=document.document_id,
            content_sha256="a" * 64,
        )

    def respond(self, turn: ConversationTurn, recent_messages=()) -> GroundedReply:  # type: ignore[no-untyped-def]
        self.turns.append(turn)
        return GroundedReply(
            conversation_id=turn.conversation_id,
            message_id=turn.message_id,
            text="Dan zou ik vanavond lekker warm onder de veranda gaan zitten.",
            evidence_memory_ids=(FACT,),
            generated_at=turn.occurred_at,
            provider="hindsight-0.9.1-reflect",
        )

    def after_response(self, turn: ConversationTurn, reply: GroundedReply) -> None:
        self.after.append((turn, reply))

    def admit_durable_goal(
        self,
        *,
        tenant_id: str,
        installation_id: str,
        goal: DurableGoalSpec,
    ) -> DurableGoalState:
        state = DurableGoalState(goal, "waiting")
        self.goals[(goal.goal_id, goal.owner_id)] = state
        return state

    def cancel_durable_goal(
        self,
        *,
        tenant_id: str,
        installation_id: str,
        goal_id: str,
        owner_id: str,
        reason: str,
        occurred_at: datetime,
    ) -> DurableGoalState:
        current = self.goals[(goal_id, owner_id)]
        state = DurableGoalState(
            current.spec, "cancelled", cancellation_reason=reason
        )
        self.goals[(goal_id, owner_id)] = state
        return state

    def durable_goal_state(
        self,
        *,
        tenant_id: str,
        installation_id: str,
        goal_id: str,
        owner_id: str,
    ) -> DurableGoalState | None:
        return self.goals.get((goal_id, owner_id))

    def latest_personal_prototype(
        self, tenant_id: str, installation_id: str, principal_id: str
    ) -> PersonalPrototypeArtifact | None:
        if (tenant_id, installation_id, principal_id) != (
            TENANT,
            INSTALLATION,
            PRINCIPAL,
        ):
            return None
        return PersonalPrototypeArtifact(
            "personal-interest:income",
            PersonalPrototype(
                "Rustig weekmenu",
                "rustig-weekmenu.md",
                "text/markdown",
                "# Rustig weekmenu\n\nMaandag: ______\n",
                "model",
            ),
        )

    def latest_current_research(
        self, tenant_id: str, installation_id: str, principal_id: str
    ) -> ResearchArtifact | None:
        if (tenant_id, installation_id, principal_id) != (
            TENANT,
            INSTALLATION,
            PRINCIPAL,
        ):
            return None
        return ResearchArtifact(
            "Actuele opties",
            "Vergelijk drie actuele opties.",
            "# Actuele opties\n\nBronfeiten en conclusie.\n",
            (
                ResearchSource("A", "https://a.example/docs"),
                ResearchSource("B", "https://b.example/docs"),
                ResearchSource("C", "https://c.example/docs"),
            ),
            NOW,
            "live-search-model",
        )

    def latest_personal_product_release(
        self, tenant_id: str, installation_id: str, principal_id: str
    ) -> PersonalProductReleaseArtifact | None:
        if (tenant_id, installation_id, principal_id) != (
            TENANT,
            INSTALLATION,
            PRINCIPAL,
        ):
            return None
        return PersonalProductReleaseArtifact(
            "personal-interest:income",
            PersonalProductRelease(
                PersonalProductListing(
                    "Rustig weekmenu voor thuis",
                    "Een invulbare planner.",
                    499,
                    ("weekmenu", "planner", "boodschappen"),
                    "model",
                ),
                "rustig-weekmenu-bundel.zip",
                b"PK\x03\x04test-release-bytes",
            ),
        )

    def plan_product_publication(self, **request: str) -> dict[str, object]:
        self.commerce_calls.append(("plan", request))
        return {
            "disposition": "planned",
            "plan": {
                "contract": "EvaProductPublicationPlan/v1",
                "release_sha256": "a" * 64,
            },
            "plan_sha256": "b" * 64,
        }

    def approve_product_publication(
        self, **request: str
    ) -> ProductPublicationApproval:
        self.commerce_calls.append(("approve", request))
        now = datetime(2026, 8, 24, 10, 0, tzinfo=UTC)
        return ProductPublicationApproval(
            "approval:publication:001",
            request["plan_sha256"],
            "a" * 64,
            "acct_12345678",
            False,
            request["approved_by"],
            now,
            datetime(2026, 8, 24, 10, 15, tzinfo=UTC),
        )

    async def publish_approved_product(
        self, **request: str
    ) -> dict[str, object]:
        self.commerce_calls.append(("publish", request))
        return {
            "disposition": "verified",
            "receipt": {
                "contract": "EvaProductPublicationReceipt/v1",
                "provider_resource_id": "plink_12345678",
                "public_url": "https://buy.stripe.com/test_12345678",
            },
        }

    def record_stripe_webhook(
        self, *, payload: bytes, signature_header: str
    ) -> dict[str, object]:
        self.webhooks.append((payload, signature_header))
        return {
            "checkout_session_id": "cs_test_12345678",
            "disposition": "recorded",
            "receipt_sha256": "c" * 64,
        }

    def paid_product_download(
        self, *, checkout_session_id: str
    ) -> DigitalFulfillmentDownload:
        content = b"PK\x03\x04paid-release"
        return DigitalFulfillmentDownload(
            content,
            DigitalFulfillmentGrant(
                checkout_session_id,
                "d" * 64,
                hashlib.sha256(content).hexdigest(),
                "rustig-weekmenu-bundel.zip",
                len(content),
                datetime(2026, 8, 24, 14, 0, tzinfo=UTC),
            ),
        )

    async def execute_home_assistant_light(self, **command: str) -> VerifiedToolResult:
        self.tool_calls.append(command)
        expected_state = "on" if command["service"] == "turn_on" else "off"
        return VerifiedToolResult(
            status="verified",
            operation_id=command["operation_id"],
            checkpoint_id="checkpoint:verified",
            proof={
                "evidence": {"source": "home_assistant"},
                "expected": {
                    "entity_id": command["entity_id"],
                    "state": expected_state,
                },
                "observed": {
                    "entity_id": command["entity_id"],
                    "state": expected_state,
                },
            },
            retry_count=0,
        )

    async def plan_tuya_to_localtuya(self, **request: str) -> AdvancedTaskPlan:
        return AdvancedTaskPlan(
            "advanced:test",
            request["operation_id"],
            "migrate_tuya_to_localtuya",
            "a" * 64,
            "blocked_preflight",
            ("localtuya_integration_not_loaded",),
            (),
            (),
            request["principal_id"],
            datetime(2026, 8, 23, 14, 0, tzinfo=UTC),
        )

    async def refresh_tuya_to_localtuya_plan(self, **request: str) -> dict[str, object]:
        return {
            "disposition": "refreshed",
            "plan": AdvancedTaskPlan(
                request["plan_id"],
                "operation:tuya-plan",
                "migrate_tuya_to_localtuya",
                "c" * 64,
                "blocked_preflight",
                ("local_device_credentials_not_collected",),
                (),
                (),
                PRINCIPAL,
                datetime(2026, 8, 23, 14, 5, tzinfo=UTC),
            ).to_payload(),
        }

    def approve_tuya_to_localtuya_canary(self, **request: str) -> None:
        raise AdvancedTaskBlocked(
            "canary approval is forbidden while preflight blockers remain"
        )

    def approve_localtuya_package(self, **request: str) -> AdvancedTaskPackageApproval:
        return AdvancedTaskPackageApproval(
            "approval:package",
            request["plan_id"],
            request["plan_sha256"],
            request["inventory_sha256"],
            request["repository_id"],
            "c" * 64,
            request["version"],
            request["approved_by"],
            datetime(2026, 8, 23, 15, 0, tzinfo=UTC),
            datetime(2026, 8, 23, 15, 15, tzinfo=UTC),
        )

    async def install_approved_localtuya_package(
        self, **request: str
    ) -> dict[str, object]:
        return {
            "approval_sha256": request["approval_sha256"],
            "disposition": "verified",
            "installed_version": "v5.2.5",
            "plan_id": request["plan_id"],
            "repository_id": "249381778",
            "repository_sha256": "d" * 64,
        }

    def approve_localtuya_repository(
        self, **request: str
    ) -> AdvancedTaskRepositoryApproval:
        return AdvancedTaskRepositoryApproval(
            "approval:repository",
            request["plan_id"],
            request["plan_sha256"],
            request["inventory_sha256"],
            request["repository_full_name"],
            request["expected_version"],
            request["approved_by"],
            datetime(2026, 8, 24, 8, 0, tzinfo=UTC),
            datetime(2026, 8, 24, 8, 15, tzinfo=UTC),
        )

    async def register_approved_localtuya_repository(
        self, **request: str
    ) -> dict[str, object]:
        return {
            "approval_sha256": request["approval_sha256"],
            "disposition": "verified",
            "repository_full_name": "xZetsubou/hass-localtuya",
            "repository_id": "maintained-1",
            "repository_sha256": "f" * 64,
            "version": "2026.7.0",
        }

    async def probe_localtuya_canary(self, **request: str) -> dict[str, object]:
        assert request["local_key"] == "0123456789abcdef"
        self.localtuya_probe_called = True
        return {
            "disposition": "verified",
            "preflight": {
                "contract": "EvaAdvancedTaskDevicePreflight/v1",
                "device_id": request["device_id"],
                "local_device_id_sha256": "a" * 64,
                "local_host_sha256": "b" * 64,
                "preflight_sha256": "c" * 64,
            },
        }

    async def prepare_localtuya_canary_execution(
        self, **request: str
    ) -> dict[str, object]:
        return {"disposition": "prepared", **request}

    async def execute_prepared_localtuya_canary(
        self, **request: object
    ) -> dict[str, object]:
        assert request["local_key"] == "0123456789abcdef"
        entities = request["entities"]
        assert isinstance(entities, tuple) and entities[0].platform == "switch"
        return {
            "disposition": "verified",
            "proof": {"contract": "EvaAdvancedTaskCanaryProof/v1"},
            "provider_receipt": {
                "contract": "EvaLocalTuyaCreationReceipt/v1",
                "configuration_sha256": "d" * 64,
            },
        }

    async def reconcile_localtuya_canary_execution(
        self, **request: str
    ) -> dict[str, object]:
        return {
            "disposition": "already_verified",
            "plan_id": request["plan_id"],
        }

    async def list_mail_messages(
        self, *, limit: int, page_token: str | None
    ) -> MailInboxPage:
        assert limit == 1
        assert page_token is None
        return MailInboxPage(
            "eva@agentmail.to",
            (
                MailMessageSummary(
                    "message-001",
                    "thread-001",
                    NOW,
                    "Jaap <jaap@example.nl>",
                    ("eva@agentmail.to",),
                    "Even testen",
                    "Hoi Eva",
                    ("inbox",),
                ),
            ),
            None,
        )

    async def get_mail_message(self, *, message_id: str) -> MailMessage:
        assert message_id == "message-001"
        return MailMessage(
            (await self.list_mail_messages(limit=1, page_token=None)).messages[0],
            "Hoi Eva, dit is de inhoud.",
            "Hoi Eva, dit is de inhoud.",
            0,
        )

    def claim_proactive_expression(self) -> ProactiveExpressionClaim | None:
        claim = self.proactive_claim
        self.proactive_claim = None
        return claim

    def acknowledge_proactive_expression(self, **receipt: object) -> None:
        self.proactive_acks.append(receipt)


def _request() -> dict[str, object]:
    return {
        "allowed_privacy_classes": ["internal"],
        "conversation_id": "conversation:telegram:jaap",
        "installation_id": INSTALLATION,
        "maximum_facts": 8,
        "principal_id": PRINCIPAL,
        "audience": {
            "audience_id": f"person:{PRINCIPAL}",
            "kind": "private_person",
            "principal_id": PRINCIPAL,
        },
        "project_id": None,
        "query_purpose": "reply",
        "query_text": "Hoe wil ik het onder de overkapping?",
        "recall_id": "recall:telegram:5002",
        "requested_valid_at": NOW,
        "scope": {"kind": "person", "principal_id": PRINCIPAL},
        "subject_ids": [PRINCIPAL],
        "tenant_id": TENANT,
    }


def test_live_core_health_readiness_and_memory_recall() -> None:
    runtime = _Runtime()
    client = TestClient(create_app(runtime))

    assert client.get("/healthz").json() == {"status": "alive"}
    assert client.get("/readyz").json()["ready"] is True
    response = client.post("/internal/v1/memory/recall", json=_request())

    assert response.status_code == 200
    payload = response.json()
    assert payload["contract"] == "EvaInternalMemoryRecallResult/v1"
    assert payload["recall"]["projection_version"] == "hindsight-0.9.1-v1"
    assert payload["confirmed_facts"][0]["value"]["preference"] == "warm"
    assert runtime.queries[0].candidate_fact_ids is None


def test_proactive_outbox_claim_and_transport_receipt_are_separate() -> None:
    runtime = _Runtime()
    runtime.proactive_claim = ProactiveExpressionClaim(
        "sensory:home-assistant:event-smoke",
        "home-assistant:event-smoke",
        4,
        0,
        "home-assistant:event-smoke",
        "De rookmelder in de schuur meldt rook.",
        3,
    )
    client = TestClient(create_app(runtime))

    claimed = client.post("/internal/v1/proactive/outbox/claim")
    assert claimed.status_code == 200
    assert claimed.json()["text"] == "De rookmelder in de schuur meldt rook."
    assert client.post("/internal/v1/proactive/outbox/claim").status_code == 204

    acknowledged = client.post(
        "/internal/v1/proactive/outbox/ack",
        json={
            "channel": "telegram",
            "provider_message_id": "telegram:message:5004",
            "revision": claimed.json()["revision"],
            "thread_id": claimed.json()["thread_id"],
        },
    )
    assert acknowledged.json()["status"] == "accepted"
    assert runtime.proactive_acks == [
        {
            "channel": "telegram",
            "provider_message_id": "telegram:message:5004",
            "revision": 4,
            "thread_id": "sensory:home-assistant:event-smoke",
        }
    ]


def test_latest_personal_project_is_a_real_download() -> None:
    client = TestClient(create_app(_Runtime()))
    response = client.get(
        "/internal/v1/personal-projects/latest/download",
        params={
            "tenant_id": TENANT,
            "installation_id": INSTALLATION,
            "principal_id": PRINCIPAL,
        },
    )

    assert response.status_code == 200
    assert response.content == b"# Rustig weekmenu\n\nMaandag: ______\n"
    assert response.headers["content-disposition"] == (
        'attachment; filename="rustig-weekmenu.md"'
    )
    assert (
        response.headers["etag"].strip('"')
        == hashlib.sha256(response.content).hexdigest()
    )


def test_latest_personal_release_is_bound_but_explicitly_unpublished() -> None:
    client = TestClient(create_app(_Runtime()))
    response = client.get(
        "/internal/v1/personal-projects/latest/release",
        params={
            "tenant_id": TENANT,
            "installation_id": INSTALLATION,
            "principal_id": PRINCIPAL,
        },
    )

    assert response.status_code == 200
    assert response.content == b"PK\x03\x04test-release-bytes"
    assert response.headers["content-disposition"] == (
        'attachment; filename="rustig-weekmenu-bundel.zip"'
    )
    assert response.headers["x-eva-publication-status"] == (
        "unpublished_release_candidate"
    )
    assert response.headers["x-eva-suggested-price-eur-cents"] == "499"
    assert (
        response.headers["etag"].strip('"')
        == hashlib.sha256(response.content).hexdigest()
    )


def test_latest_current_research_is_a_reopenable_markdown_download() -> None:
    client = TestClient(create_app(_Runtime()))
    response = client.get(
        "/internal/v1/research/latest/download",
        params={
            "tenant_id": TENANT,
            "installation_id": INSTALLATION,
            "principal_id": PRINCIPAL,
        },
    )

    assert response.status_code == 200
    assert response.content == b"# Actuele opties\n\nBronfeiten en conclusie.\n"
    assert response.headers["content-type"].startswith("text/markdown")
    assert response.headers["x-eva-research-source-count"] == "3"
    assert response.headers["x-eva-researched-at"] == NOW
    assert response.headers["etag"].strip('"') == hashlib.sha256(
        response.content
    ).hexdigest()


def test_commerce_api_keeps_plan_approval_and_external_publication_separate() -> None:
    runtime = _Runtime()
    client = TestClient(create_app(runtime))
    planned = client.post(
        "/internal/v1/commerce/publications/plan",
        json={
            "installation_id": INSTALLATION,
            "mode": "plan_latest_release",
            "principal_id": PRINCIPAL,
            "tenant_id": TENANT,
        },
    )
    assert planned.status_code == 200
    assert planned.json()["disposition"] == "planned"

    approved = client.post(
        "/internal/v1/commerce/publications/approve",
        json={
            "approved_by": PRINCIPAL,
            "installation_id": INSTALLATION,
            "mode": "approve_exact_plan",
            "plan_sha256": planned.json()["plan_sha256"],
            "tenant_id": TENANT,
        },
    )
    assert approved.status_code == 200
    assert len(approved.json()["approval_sha256"]) == 64

    published = client.post(
        "/internal/v1/commerce/publications/publish",
        json={
            "approval_sha256": approved.json()["approval_sha256"],
            "installation_id": INSTALLATION,
            "mode": "publish_approved_plan",
            "plan_sha256": planned.json()["plan_sha256"],
            "tenant_id": TENANT,
        },
    )
    assert published.status_code == 200
    assert published.json()["disposition"] == "verified"
    assert [name for name, _request in runtime.commerce_calls] == [
        "plan",
        "approve",
        "publish",
    ]


def test_stripe_webhook_preserves_exact_body_and_requires_signature() -> None:
    runtime = _Runtime()
    client = TestClient(create_app(runtime))
    payload = b'{"id":"evt_12345678","exact":"bytes"}'

    missing = client.post(
        "/public/v1/commerce/stripe/webhook",
        content=payload,
        headers={"content-type": "application/json"},
    )
    assert missing.status_code == 400
    assert runtime.webhooks == []

    accepted = client.post(
        "/public/v1/commerce/stripe/webhook",
        content=payload,
        headers={
            "content-type": "application/json",
            "stripe-signature": "t=123,v1=" + "a" * 64,
        },
    )
    assert accepted.status_code == 200
    assert accepted.json()["disposition"] == "recorded"
    assert runtime.webhooks == [(payload, "t=123,v1=" + "a" * 64)]


def test_paid_download_returns_exact_hash_bound_release() -> None:
    client = TestClient(create_app(_Runtime()))
    response = client.get(
        "/public/v1/commerce/orders/cs_test_12345678/download"
    )

    assert response.status_code == 200
    assert response.content == b"PK\x03\x04paid-release"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["content-disposition"] == (
        'attachment; filename="rustig-weekmenu-bundel.zip"'
    )
    assert response.headers["etag"].strip('"') == hashlib.sha256(
        response.content
    ).hexdigest()
    assert len(response.headers["x-eva-fulfillment-grant"]) == 64


def test_tuya_migration_endpoint_only_creates_a_blocked_durable_plan() -> None:
    client = TestClient(create_app(_Runtime()))

    response = client.post(
        "/internal/v1/advanced/home-assistant/tuya-local-plan",
        json={
            "installation_id": INSTALLATION,
            "mode": "plan_only",
            "operation_id": "operation:tuya-plan",
            "principal_id": PRINCIPAL,
            "request": "migrate_tuya_to_localtuya",
            "tenant_id": TENANT,
        },
    )

    assert response.status_code == 200
    assert response.json()["state"] == "blocked_preflight"
    assert response.json()["blockers"] == ["localtuya_integration_not_loaded"]
    assert len(response.json()["plan_sha256"]) == 64


def test_tuya_migration_endpoint_rejects_execution_mode() -> None:
    client = TestClient(create_app(_Runtime()))

    response = client.post(
        "/internal/v1/advanced/home-assistant/tuya-local-plan",
        json={
            "installation_id": INSTALLATION,
            "mode": "execute",
            "operation_id": "operation:tuya-plan",
            "principal_id": PRINCIPAL,
            "request": "migrate_tuya_to_localtuya",
            "tenant_id": TENANT,
        },
    )

    assert response.status_code == 422


def test_tuya_plan_refresh_returns_a_new_inventory_bound_version() -> None:
    client = TestClient(create_app(_Runtime()))

    response = client.post(
        "/internal/v1/advanced/home-assistant/tuya-local-plan/refresh",
        json={
            "installation_id": INSTALLATION,
            "mode": "refresh_inventory_only",
            "plan_id": "advanced:test",
            "tenant_id": TENANT,
        },
    )

    assert response.status_code == 200
    assert response.json()["disposition"] == "refreshed"
    assert response.json()["plan"]["inventory_sha256"] == "c" * 64
    assert response.json()["plan"]["blockers"] == [
        "local_device_credentials_not_collected"
    ]


def test_tuya_canary_approval_refuses_a_blocked_plan() -> None:
    client = TestClient(create_app(_Runtime()))

    response = client.post(
        "/internal/v1/advanced/home-assistant/tuya-local-canary/approve",
        json={
            "approved_by": PRINCIPAL,
            "device_id": "device-light",
            "installation_id": INSTALLATION,
            "inventory_sha256": "a" * 64,
            "mode": "approve_canary_only",
            "plan_id": "advanced:test",
            "plan_sha256": "b" * 64,
            "tenant_id": TENANT,
        },
    )

    assert response.status_code == 409
    assert "preflight blockers remain" in response.json()["detail"]


def test_localtuya_canary_preflight_returns_only_secret_free_proof() -> None:
    runtime = _Runtime()
    client = TestClient(create_app(runtime))

    response = client.post(
        "/internal/v1/advanced/home-assistant/tuya-local-canary/preflight",
        json={
            "config_entry_id": "01K3XJR7BTERG1Z20Q1J2HYP0B",
            "device_id": "device-light",
            "friendly_name": "Schuur 1",
            "host": "10.0.20.81",
            "installation_id": INSTALLATION,
            "inventory_sha256": "a" * 64,
            "local_device_id": "bf1234567890abcd",
            "local_key": "0123456789abcdef",
            "mode": "probe_and_abort_before_write",
            "plan_id": "advanced:test",
            "plan_sha256": "b" * 64,
            "protocol_version": "3.3",
            "tenant_id": TENANT,
        },
    )

    assert response.status_code == 200
    assert runtime.localtuya_probe_called is True
    assert "0123456789abcdef" not in response.text
    assert response.json()["preflight"]["local_device_id_sha256"] == "a" * 64


def test_localtuya_canary_prepare_execute_and_reconcile_are_separate_steps() -> None:
    client = TestClient(create_app(_Runtime()))
    prepared = client.post(
        "/internal/v1/advanced/home-assistant/tuya-local-canary/prepare",
        json={
            "approval_sha256": "a" * 64,
            "configuration_sha256": "d" * 64,
            "installation_id": INSTALLATION,
            "mode": "commit_before_provider_call",
            "plan_id": "advanced:test",
            "preflight_sha256": "b" * 64,
            "tenant_id": TENANT,
        },
    )
    executed = client.post(
        "/internal/v1/advanced/home-assistant/tuya-local-canary/execute",
        json={
            "entities": [
                {
                    "platform": "switch",
                    "values": {
                        "friendly_name": "Schuur lokaal",
                        "id": "1 (value: True)",
                    },
                }
            ],
            "friendly_name": "Schuur lokaal",
            "host": "10.0.20.81",
            "installation_id": INSTALLATION,
            "local_device_id": "bf1234567890abcd",
            "local_key": "0123456789abcdef",
            "mode": "execute_prepared_canary_once",
            "plan_id": "advanced:test",
            "protocol_version": "3.3",
            "tenant_id": TENANT,
        },
    )
    reconciled = client.post(
        "/internal/v1/advanced/home-assistant/tuya-local-canary/reconcile",
        json={
            "installation_id": INSTALLATION,
            "mode": "readback_only_no_replay",
            "plan_id": "advanced:test",
            "tenant_id": TENANT,
        },
    )

    assert prepared.status_code == 200
    assert prepared.json()["disposition"] == "prepared"
    assert executed.status_code == 200
    assert executed.json()["disposition"] == "verified"
    assert "0123456789abcdef" not in executed.text
    assert reconciled.status_code == 200
    assert reconciled.json()["disposition"] == "already_verified"


def test_localtuya_package_approval_is_exact_and_does_not_install() -> None:
    client = TestClient(create_app(_Runtime()))

    response = client.post(
        "/internal/v1/advanced/home-assistant/tuya-local-package/approve",
        json={
            "approved_by": PRINCIPAL,
            "installation_id": INSTALLATION,
            "inventory_sha256": "a" * 64,
            "mode": "approve_install_only",
            "plan_id": "advanced:test",
            "plan_sha256": "b" * 64,
            "repository_id": "249381778",
            "tenant_id": TENANT,
            "version": "v5.2.5",
        },
    )

    assert response.status_code == 200
    assert response.json()["repository_id"] == "249381778"
    assert response.json()["version"] == "v5.2.5"
    assert response.json()["step_id"] == "install_localtuya_package"


def test_localtuya_package_install_requires_separate_approval_hash() -> None:
    client = TestClient(create_app(_Runtime()))

    response = client.post(
        "/internal/v1/advanced/home-assistant/tuya-local-package/install",
        json={
            "approval_sha256": "e" * 64,
            "installation_id": INSTALLATION,
            "mode": "execute_approved_install",
            "plan_id": "advanced:test",
            "tenant_id": TENANT,
        },
    )

    assert response.status_code == 200
    assert response.json()["approval_sha256"] == "e" * 64
    assert response.json()["disposition"] == "verified"


def test_localtuya_repository_registration_is_two_separate_requests() -> None:
    client = TestClient(create_app(_Runtime()))

    approval = client.post(
        "/internal/v1/advanced/home-assistant/tuya-local-repository/approve",
        json={
            "approved_by": PRINCIPAL,
            "expected_version": "2026.7.0",
            "installation_id": INSTALLATION,
            "inventory_sha256": "a" * 64,
            "mode": "approve_registration_only",
            "plan_id": "advanced:test",
            "plan_sha256": "b" * 64,
            "repository_full_name": "xZetsubou/hass-localtuya",
            "tenant_id": TENANT,
        },
    )
    assert approval.status_code == 200
    assert approval.json()["step_id"] == "register_localtuya_repository"

    registered = client.post(
        "/internal/v1/advanced/home-assistant/tuya-local-repository/register",
        json={
            "approval_sha256": approval.json()["approval_sha256"],
            "installation_id": INSTALLATION,
            "mode": "execute_approved_registration",
            "plan_id": "advanced:test",
            "tenant_id": TENANT,
        },
    )
    assert registered.status_code == 200
    assert registered.json()["disposition"] == "verified"
    assert registered.json()["repository_full_name"] == "xZetsubou/hass-localtuya"


def test_mail_endpoints_return_hash_bound_connector_neutral_records() -> None:
    client = TestClient(create_app(_Runtime()))

    page = client.post(
        "/internal/v1/mail/messages/list", json={"limit": 1, "page_token": None}
    )
    message = client.post(
        "/internal/v1/mail/messages/get", json={"message_id": "message-001"}
    )

    assert page.status_code == 200
    assert page.json()["messages"][0]["subject"] == "Even testen"
    assert len(page.json()["page_sha256"]) == 64
    assert message.status_code == 200
    assert message.json()["text"] == "Hoi Eva, dit is de inhoud."
    assert len(message.json()["message_sha256"]) == 64


def test_readiness_and_recall_input_fail_closed() -> None:
    client = TestClient(create_app(_Runtime(ready=False)))
    assert client.get("/readyz").status_code == 503

    request = _request()
    request["unsealed_extra"] = True
    response = client.post("/internal/v1/memory/recall", json=request)
    assert response.status_code == 422
    assert "unknown or missing" in response.json()["detail"]

    request = _request()
    del request["audience"]
    response = client.post("/internal/v1/memory/recall", json=request)
    assert response.status_code == 422
    assert "unknown or missing" in response.json()["detail"]


def test_retains_complete_conversation_document() -> None:
    runtime = _Runtime()
    client = TestClient(create_app(runtime))
    payload = {
        "audience": {
            "audience_id": f"person:{PRINCIPAL}",
            "kind": "private_person",
            "principal_id": PRINCIPAL,
        },
        "content": "Jaap: Hou antwoorden kort.\nEva: Begrepen.",
        "conversation_id": "conversation:telegram:jaap",
        "document_id": "conversation:telegram:jaap:history",
        "evidence_ref": "evidence:telegram:5003",
        "installation_id": INSTALLATION,
        "occurred_at": NOW,
        "principal_id": PRINCIPAL,
        "privacy_class": "internal",
        "source_ref": "telegram:conversation:jaap",
        "tenant_id": TENANT,
        "trust": "direct_user",
    }

    response = client.put("/internal/v1/memory/documents", json=payload)

    assert response.status_code == 200
    assert response.json() == {
        "bank_id": "eva2-bank",
        "content_sha256": "a" * 64,
        "contract": "EvaInternalMemoryRetentionReceipt/v1",
        "disposition": "retained",
        "document_id": "conversation:telegram:jaap:history",
    }
    assert runtime.documents[0].content == payload["content"]


def test_retain_rejects_invalid_style_document_metadata() -> None:
    runtime = _Runtime()
    client = TestClient(create_app(runtime))
    payload = {
        "audience": {
            "audience_id": f"person:{PRINCIPAL}",
            "kind": "private_person",
            "principal_id": PRINCIPAL,
        },
        "content": "Hou het kort.",
        "conversation_id": "conversation:telegram:jaap",
        "document_id": "conversation:telegram:jaap:history",
        "evidence_ref": "evidence:telegram:5003",
        "installation_id": INSTALLATION,
        "occurred_at": NOW,
        "principal_id": PRINCIPAL,
        "privacy_class": "whatever",
        "source_ref": "telegram:conversation:jaap",
        "tenant_id": TENANT,
        "trust": "direct_user",
    }

    response = client.put("/internal/v1/memory/documents", json=payload)

    assert response.status_code == 422
    assert runtime.documents == []


def test_channel_neutral_turn_returns_a_grounded_reply_receipt() -> None:
    runtime = _Runtime()
    client = TestClient(create_app(runtime))
    response = client.post(
        "/internal/v1/turns/respond",
        json={
            "conversation_id": "conversation:telegram:jaap",
            "installation_id": INSTALLATION,
            "message_id": "telegram:5004",
            "occurred_at": NOW,
            "principal_id": PRINCIPAL,
            "tenant_id": TENANT,
            "text": "Zal ik vanavond onder de veranda gaan zitten?",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["contract"] == "EvaInternalGroundedReply/v1"
    assert payload["evidence_memory_ids"] == [FACT]
    assert payload["provider"] == "hindsight-0.9.1-reflect"
    assert len(payload["text_sha256"]) == 64
    assert runtime.turns[0].message_id == "telegram:5004"
    assert runtime.after[0][0].message_id == "telegram:5004"


def test_channel_neutral_turn_accepts_only_a_complete_bound_photo_region() -> None:
    runtime = _Runtime()
    client = TestClient(create_app(runtime))
    binding = {
        "media_sha256": "a" * 64,
        "media_type": "image/jpeg",
        "observation_id": "telegram-photo:5005",
        "source_ref": "telegram:file:5005",
        "subject_region_sha256": "b" * 64,
    }
    payload = {
        "conversation_id": "conversation:telegram:jaap",
        "installation_id": INSTALLATION,
        "message_id": "telegram:5005",
        "observation_binding": binding,
        "occurred_at": NOW,
        "principal_id": PRINCIPAL,
        "tenant_id": TENANT,
        "text": "Nee, dat is Mark.",
    }

    response = client.post("/internal/v1/turns/respond", json=payload)

    assert response.status_code == 200
    assert runtime.turns[0].observation_binding is not None
    assert runtime.turns[0].observation_binding.observation_id == (
        "telegram-photo:5005"
    )

    del binding["subject_region_sha256"]
    rejected = client.post(
        "/internal/v1/turns/respond",
        json={**payload, "message_id": "telegram:5006", "observation_binding": binding},
    )
    assert rejected.status_code == 422


def test_reused_conversation_message_identity_is_reported_as_conflict() -> None:
    class ConflictingRuntime(_Runtime):
        def respond(self, turn, recent_messages=()):  # type: ignore[no-untyped-def]
            raise ConversationConflict("conversation replay differs")

    client = TestClient(create_app(ConflictingRuntime()))
    response = client.post(
        "/internal/v1/turns/respond",
        json={
            "conversation_id": "conversation:telegram:jaap",
            "installation_id": INSTALLATION,
            "message_id": "telegram:reused",
            "occurred_at": NOW,
            "principal_id": PRINCIPAL,
            "tenant_id": TENANT,
            "text": "Andere tekst onder hetzelfde ID.",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "conversation replay differs"


def test_unavailable_conversation_model_is_not_exposed_as_http_500() -> None:
    class UnavailableRuntime(_Runtime):
        def respond(self, turn, recent_messages=()):  # type: ignore[no-untyped-def]
            raise RuntimeError("secret provider detail")

    client = TestClient(create_app(UnavailableRuntime()))
    response = client.post(
        "/internal/v1/turns/respond",
        json={
            "conversation_id": "conversation:telegram:jaap",
            "installation_id": INSTALLATION,
            "message_id": "telegram:model-unavailable",
            "occurred_at": NOW,
            "principal_id": PRINCIPAL,
            "tenant_id": TENANT,
            "text": "Ben je daar?",
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"] == (
        "Eva's conversation model is tijdelijk unavailable"
    )
    assert "secret provider detail" not in response.text


def test_home_assistant_light_action_returns_independent_proof() -> None:
    runtime = _Runtime()
    client = TestClient(create_app(runtime))
    response = client.post(
        "/internal/v1/tools/home-assistant/light",
        json={
            "entity_id": "light.kantoor_rechts",
            "installation_id": "live-installation",
            "operation_id": "operation:live-light:on",
            "service": "turn_on",
            "tenant_id": "live-tenant",
            "thread_id": "thread:live-light:on",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "verified"
    assert response.json()["proof"]["observed"] == {
        "entity_id": "light.kantoor_rechts",
        "state": "on",
    }
    assert runtime.tool_calls == [
        {
            "entity_id": "light.kantoor_rechts",
            "installation_id": "live-installation",
            "operation_id": "operation:live-light:on",
            "service": "turn_on",
            "tenant_id": "live-tenant",
            "thread_id": "thread:live-light:on",
        }
    ]


def test_home_assistant_route_rejects_non_light_or_non_reversible_action() -> None:
    runtime = _Runtime()
    client = TestClient(create_app(runtime))
    base = {
        "entity_id": "lock.voordeur",
        "installation_id": "live-installation",
        "operation_id": "operation:unsafe",
        "service": "unlock",
        "tenant_id": "live-tenant",
        "thread_id": "thread:unsafe",
    }

    assert (
        client.post("/internal/v1/tools/home-assistant/light", json=base).status_code
        == 422
    )
    assert runtime.tool_calls == []
def test_durable_goal_api_admits_reads_and_cancels_distinctly() -> None:
    runtime = _Runtime()
    client = TestClient(create_app(runtime))
    admitted = client.post(
        "/internal/v1/goals/admit",
        json={"goal": _goal_payload(), "installation_id": INSTALLATION, "tenant_id": TENANT},
    )
    assert admitted.status_code == 200
    assert admitted.json()["status"] == "waiting"

    observed = client.post(
        "/internal/v1/goals/state",
        json={
            "goal_id": "goal-api-1",
            "installation_id": INSTALLATION,
            "owner_id": PRINCIPAL,
            "tenant_id": TENANT,
        },
    )
    assert observed.status_code == 200
    assert observed.json()["goal"]["wake"]["resource"] == "release-1"

    cancelled = client.post(
        "/internal/v1/goals/cancel",
        json={
            "goal_id": "goal-api-1",
            "installation_id": INSTALLATION,
            "occurred_at": "2026-08-23T14:10:00Z",
            "owner_id": PRINCIPAL,
            "reason": "Niet meer nodig",
            "tenant_id": TENANT,
        },
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["delivery_status"] is None


def test_durable_goal_api_rejects_incomplete_pivot_and_unknown_state() -> None:
    runtime = _Runtime()
    client = TestClient(create_app(runtime))
    malformed = _goal_payload()
    malformed["pivot_instruction"] = "Doe alternatief"
    response = client.post(
        "/internal/v1/goals/admit",
        json={"goal": malformed, "installation_id": INSTALLATION, "tenant_id": TENANT},
    )
    assert response.status_code == 422
    missing = client.post(
        "/internal/v1/goals/state",
        json={
            "goal_id": "missing",
            "installation_id": INSTALLATION,
            "owner_id": PRINCIPAL,
            "tenant_id": TENANT,
        },
    )
    assert missing.status_code == 404
