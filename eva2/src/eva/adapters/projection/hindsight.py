"""Thin HTTP adapter for Eva's self-hosted Hindsight memory engine.

Hindsight owns semantic memory: it extracts, consolidates and recalls memories
from stable conversation documents.  Eva keeps operational conversations and
effect receipts in its own database, but does not maintain a second semantic
fact store on this runtime path.

The older ``project_current``/``recall_candidates`` methods remain temporarily
available for frozen migration evidence.  New runtime code uses
``retain_conversation`` and ``recall_for_presence`` directly.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from uuid import NAMESPACE_URL, uuid5
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Literal, Protocol

from eva.application.memory import (
    ConversationMemoryDocument,
    MemoryRetentionReceipt,
    PresenceMemory,
    RecallCurrentFacts,
    RememberFact,
    SemanticMemoryProviderError,
)
from eva.application.memory_audience import (
    MemoryAudience,
    TelegramGroupConsentReference,
)
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256
from eva.contracts.memory import MemoryRecall, RecalledFact
from eva.contracts.planning import ConfirmedFact


_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_PROJECTION_VERSION = "hindsight-0.9.1-v1"
_ANCHOR_TOKEN = re.compile(r"(?u)\b[^\W_][\w-]{5,63}\b")
_PLANNED_QUERY_MARKER = "Semantische zoekconcepten:"
_BOUND_QUERY_MARKER = "Gebonden retrieval-ankers:"
_SHA256 = re.compile(r"[0-9a-f]{64}")
_OBSERVATION_EVIDENCE_PREFIX = "observation-binding:"


class HindsightProjectionError(SemanticMemoryProviderError):
    """Base error for the replaceable Hindsight projection."""


class HindsightProjectionUnavailable(HindsightProjectionError):
    """The projection could not be reached before its deadline."""


class HindsightProjectionProtocolError(HindsightProjectionError):
    """The projection returned an invalid or contradictory response."""


class HindsightMemoryScopeError(HindsightProjectionError):
    """A retain or recall crossed its admitted person/install scope."""


class ActiveGroupConsentResolver(Protocol):
    """The durable, current source of group-memory permission."""

    def active_reference(
        self, *, principal_id: str, group_chat_id: str, now: datetime
    ) -> TelegramGroupConsentReference | None: ...


@dataclass(frozen=True, slots=True)
class HindsightProjectionConfig:
    base_url: str
    bank_namespace: str = "eva2"
    extraction_mode: Literal["concise", "verbose", "verbatim"] = "concise"
    timeout_seconds: float = 120.0
    maximum_candidates: int = 128
    recall_budget: Literal["low", "mid", "high"] = "low"
    recall_max_tokens: int = 2048
    recall_node_budget: int = 40
    minimum_final_score: float = 0.05
    exact_binding_minimum_final_score: float = 0.02

    def __post_init__(self) -> None:
        parsed = urllib.parse.urlsplit(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Hindsight base URL must be absolute HTTP(S)")
        if parsed.query or parsed.fragment or parsed.username or parsed.password:
            raise ValueError(
                "Hindsight base URL may not contain credentials or a query"
            )
        if not self.bank_namespace or len(self.bank_namespace) > 32:
            raise ValueError("Hindsight bank namespace is invalid")
        if not 0 < self.timeout_seconds <= 600:
            raise ValueError("Hindsight timeout must be between 0 and 600 seconds")
        if not 1 <= self.maximum_candidates <= 256:
            raise ValueError("Hindsight candidate limit must be between 1 and 256")
        if self.recall_budget not in {"low", "mid", "high"}:
            raise ValueError("Hindsight recall budget is invalid")
        if not 2_000 <= self.recall_max_tokens <= 4_000:
            raise ValueError("Hindsight recall token budget must be 2000..4000")
        if not 20 <= self.recall_node_budget <= 100:
            raise ValueError("Hindsight recall node budget must be 20..100")
        if not 0.0 < self.minimum_final_score <= 1.5:
            raise ValueError("Hindsight recall score floor must be within 0..1.5")
        if not (
            0.0 < self.exact_binding_minimum_final_score <= self.minimum_final_score
        ):
            raise ValueError(
                "Hindsight exact-binding score floor must be within the general floor"
            )


@dataclass(frozen=True, slots=True)
class HindsightProjectionReceipt:
    bank_id: str
    fact_id: str
    document_id: str
    disposition: str


@dataclass(frozen=True, slots=True)
class BackgroundReflectionReceipt:
    bank_id: str
    mental_model_id: str
    disposition: Literal["created", "refreshed"]
    operation_id: str | None


@dataclass(frozen=True, slots=True)
class ProjectedRecall:
    query: RecallCurrentFacts
    backend: str
    fallback_reason: str | None = None


def _canonical_timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise HindsightProjectionProtocolError(
            "Hindsight returned an invalid memory timestamp"
        ) from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class HindsightMemoryEngine:
    """Small HTTP adapter around a self-hosted Hindsight memory bank."""

    def __init__(
        self,
        config: HindsightProjectionConfig,
        *,
        active_group_consent_resolver: ActiveGroupConsentResolver | None = None,
    ) -> None:
        self._config = config
        self._base_url = config.base_url.rstrip("/")
        self._known_banks: set[str] = set()
        self._cache_lock = threading.Lock()
        self._fast_presence_cache: dict[str, tuple[float, PresenceMemory]] = {}
        self._fast_presence_inflight: set[str] = set()
        self._fast_presence_slots = threading.BoundedSemaphore(4)
        self._fast_presence_generation = 0
        self._derivative_lock = threading.RLock()
        self._active_group_consent_resolver = active_group_consent_resolver

    def _bank_id(self, tenant_id: str, installation_id: str) -> str:
        raw = canonical_json_bytes(
            [self._config.bank_namespace, tenant_id, installation_id]
        )
        digest = hashlib.sha256(raw).hexdigest()
        return f"{self._config.bank_namespace}-{digest[:32]}"

    @staticmethod
    def _person_tag(principal_id: str) -> str:
        digest = hashlib.sha256(principal_id.encode("utf-8")).hexdigest()
        return f"eva-person-{digest[:32]}"

    @staticmethod
    def _group_tag(group_chat_id: str) -> str:
        digest = hashlib.sha256(group_chat_id.encode("utf-8")).hexdigest()
        return f"eva-telegram-group-{digest[:32]}"

    def _require_active_group_audience(
        self, audience: MemoryAudience
    ) -> TelegramGroupConsentReference:
        """Resolve the live grant and bind it to the durable document audience."""

        if audience.kind != "telegram_group":
            raise HindsightMemoryScopeError("memory audience is not a Telegram group")
        if self._active_group_consent_resolver is None:
            raise HindsightMemoryScopeError(
                "Telegram group memory has no active consent resolver"
            )
        assert audience.group_chat_id is not None
        assert audience.consent_ref is not None
        active = self._resolve_active_group_reference(
            principal_id=audience.principal_id,
            group_chat_id=audience.group_chat_id,
        )
        if active != audience.consent_ref:
            raise HindsightMemoryScopeError(
                "Telegram group memory consent is not active and exact"
            )
        return active

    def _resolve_active_group_reference(
        self, *, principal_id: str, group_chat_id: str
    ) -> TelegramGroupConsentReference | None:
        if self._active_group_consent_resolver is None:
            raise HindsightMemoryScopeError(
                "Telegram group memory has no active consent resolver"
            )
        now = datetime.now(tz=timezone.utc).replace(microsecond=0)
        try:
            return self._active_group_consent_resolver.active_reference(
                principal_id=principal_id,
                group_chat_id=group_chat_id,
                now=now,
            )
        except Exception as error:
            raise HindsightMemoryScopeError(
                "Telegram group memory consent could not be resolved"
            ) from error

    @staticmethod
    def _empty_presence(query: RecallCurrentFacts) -> PresenceMemory:
        return PresenceMemory(
            recall=MemoryRecall(
                schema_version=1,
                contract="MemoryRecall/v1",
                recall_id=query.recall_id,
                query_purpose=query.query_purpose,
                tenant_id=query.tenant_id,
                principal_id=query.principal_id,
                conversation_id=query.conversation_id,
                project_id=query.project_id,
                requested_valid_at=query.requested_valid_at,
                admitted_fact_ids_sha256=canonical_sha256([]),
                selected=[],
                excluded_reasons={},
                projection_version=_PROJECTION_VERSION,
                scope_sha256=canonical_sha256(query.scope),
                created_at=query.requested_valid_at,
            ),
            confirmed_facts=(),
            style_mirror=None,
        )

    @staticmethod
    def _fast_presence_key(query: RecallCurrentFacts) -> str:
        return canonical_sha256(
            [
                query.tenant_id,
                query.installation_id,
                query.principal_id,
                query.audience.audience_id,
            ]
        )

    def _refresh_fast_presence(
        self, key: str, query: RecallCurrentFacts, generation: int
    ) -> None:
        try:
            memory = self.recall_for_presence(query)
            with self._cache_lock:
                if generation == self._fast_presence_generation:
                    self._fast_presence_cache[key] = (time.monotonic(), memory)
                    while len(self._fast_presence_cache) > 128:
                        self._fast_presence_cache.pop(
                            next(iter(self._fast_presence_cache))
                        )
        except HindsightProjectionError:
            pass
        finally:
            with self._cache_lock:
                self._fast_presence_inflight.discard(key)
            self._fast_presence_slots.release()

    def recall_for_presence_fast(self, query: RecallCurrentFacts) -> PresenceMemory:
        """Return scoped warm context and refresh Hindsight outside first content."""

        if query.audience.kind != "private_person" or query.project_id is not None:
            return self.recall_for_presence(query)
        if (
            not query.subject_ids
            or query.principal_id not in query.subject_ids
            or "observation_binding_sha256" in query.scope
        ):
            return self.recall_for_presence(query)
        key = self._fast_presence_key(query)
        now = time.monotonic()
        start_refresh = False
        generation = 0
        with self._cache_lock:
            cached = self._fast_presence_cache.get(key)
            if cached is not None and now - cached[0] <= 30:
                return cached[1]
            if (
                key not in self._fast_presence_inflight
                and self._fast_presence_slots.acquire(blocking=False)
            ):
                self._fast_presence_inflight.add(key)
                start_refresh = True
                generation = self._fast_presence_generation
        if start_refresh:
            threading.Thread(
                target=self._refresh_fast_presence,
                args=(key, query, generation),
                name="eva-hindsight-prefetch",
                daemon=True,
            ).start()
        return cached[1] if cached is not None else self._empty_presence(query)

    @staticmethod
    def _project_partition(installation_id: str, project_id: str) -> str:
        digest = hashlib.sha256(project_id.encode("utf-8")).hexdigest()
        return f"{installation_id}:eva-project:{digest[:32]}"

    @staticmethod
    def _observation_partition(
        installation_id: str, observation_binding_sha256: str
    ) -> str:
        if not _SHA256.fullmatch(observation_binding_sha256):
            raise HindsightMemoryScopeError("observation memory binding is invalid")
        return f"{installation_id}:eva-observation:{observation_binding_sha256[:32]}"

    @staticmethod
    def _style_mirror_id(principal_id: str) -> str:
        digest = hashlib.sha256(principal_id.encode("utf-8")).hexdigest()
        return HindsightMemoryEngine._style_mirror_id_from_sha256(digest)

    @staticmethod
    def _style_mirror_id_from_sha256(principal_sha256: str) -> str:
        if not _SHA256.fullmatch(principal_sha256):
            raise HindsightMemoryScopeError(
                "Hindsight derivative principal binding is invalid"
            )
        return f"eva-style-mirror-v1-{principal_sha256[:16]}"

    @staticmethod
    def _recall_query_variants(query_text: str) -> tuple[str, ...]:
        """Keep the full meaning while rescuing rare lexical anchors in parallel.

        Dense retrieval can bury a unique Dutch place, person, or device name under
        the rest of a natural sentence. The longest words are sent as independent
        semantic queries and fused with the full-sentence result. They never encode
        a scenario or an answer and therefore remain connector- and persona-neutral.
        """

        if _BOUND_QUERY_MARKER in query_text:
            bound = query_text.rsplit(_BOUND_QUERY_MARKER, 1)[1]
            variants = [query_text]
            seen = {query_text.casefold()}
            for part in re.split(r"[,;\n]+", bound):
                anchor = part.strip(" .:-")
                key = anchor.casefold()
                if not anchor or key in seen:
                    continue
                seen.add(key)
                variants.append(anchor)
                if len(variants) >= 5:
                    break
            return tuple(variants)

        if _PLANNED_QUERY_MARKER in query_text:
            planned = query_text.split(_PLANNED_QUERY_MARKER, 1)[1]
            variants = [query_text]
            seen = {query_text.casefold()}
            for part in re.split(r"[,;\n]+", planned):
                concept = part.strip(" .:-")
                key = concept.casefold()
                if not concept or key in seen:
                    continue
                seen.add(key)
                variants.append(concept)
                if len(variants) >= 7:
                    break
            return tuple(variants)

        unique: dict[str, str] = {}
        for match in _ANCHOR_TOKEN.finditer(query_text):
            token = match.group(0)
            unique.setdefault(token.casefold(), token)
        anchors = sorted(unique.values(), key=lambda value: -len(value))[:3]
        return (query_text, *anchors)

    def _recall_response(
        self,
        bank_id: str,
        person_tag: str,
        query: RecallCurrentFacts,
        query_text: str,
        memory_project_id: str | None,
        memory_observation_binding_sha256: str | None,
    ) -> list[dict[str, Any]]:
        _, response = self._request(
            "POST",
            f"/v1/default/banks/{bank_id}/memories/recall",
            {
                "query": query_text,
                "types": ["world", "experience", "observation"],
                "prefer_observations": True,
                "budget": self._config.recall_budget,
                "max_tokens": self._config.recall_max_tokens,
                "query_timestamp": query.requested_valid_at,
                "tags": [person_tag],
                "tags_match": "all_strict",
                "include": {
                    "entities": None,
                    "chunks": None,
                    # Hindsight observations intentionally omit document metadata.
                    # A small bounded source-fact envelope lets Eva preserve the
                    # original authority and timestamp without adding those facts
                    # separately to the model context.
                    "source_facts": {
                        "max_tokens": 512,
                        "max_tokens_per_observation": 256,
                    },
                },
            },
        )
        results = response.get("results")
        if not isinstance(results, list):
            raise HindsightProjectionProtocolError(
                "Hindsight recall response has no result list"
            )
        if not all(isinstance(item, dict) for item in results):
            raise HindsightProjectionProtocolError(
                "Hindsight recall result is not an object"
            )
        source_facts = response.get("source_facts", {})
        if not isinstance(source_facts, dict):
            raise HindsightProjectionProtocolError(
                "Hindsight recall source facts are invalid"
            )
        enriched: list[dict[str, Any]] = []
        for item in results:
            copied = dict(item)
            copied["_eva_memory_project_id"] = memory_project_id
            copied["_eva_memory_observation_binding_sha256"] = (
                memory_observation_binding_sha256
            )
            if (
                response.get("source_facts_truncated") is not True
                and copied.get("type") == "observation"
            ):
                source_metadata = self._observation_source_metadata(
                    copied, source_facts
                )
                if source_metadata is not None:
                    copied["_eva_source_metadata"] = source_metadata
            enriched.append(copied)
        return enriched

    @staticmethod
    def _observation_source_metadata(
        observation: dict[str, Any], source_facts: dict[str, Any]
    ) -> dict[str, str] | None:
        source_ids = observation.get("source_fact_ids")
        if (
            not isinstance(source_ids, list)
            or not source_ids
            or not all(isinstance(value, str) and value for value in source_ids)
        ):
            return None
        sources = [source_facts.get(source_id) for source_id in source_ids]
        if not all(isinstance(source, dict) for source in sources):
            return None
        candidates: list[tuple[str, dict[str, str]]] = []
        for source in sources:
            assert isinstance(source, dict)
            metadata = source.get("metadata")
            timestamp = (
                source.get("mentioned_at")
                or source.get("occurred_start")
                or observation.get("mentioned_at")
            )
            if (
                not isinstance(metadata, dict)
                or not isinstance(timestamp, str)
                or any(
                    not isinstance(key, str) or not isinstance(value, str)
                    for key, value in metadata.items()
                )
                or metadata.get("trust")
                not in {
                    "direct_user",
                    "verified_external",
                    "host_policy",
                    "model_inference_unconfirmed",
                }
            ):
                return None
            candidates.append((_canonical_timestamp(timestamp), dict(metadata)))
        authorities = {
            (
                metadata.get("principal_id"),
                metadata.get("privacy_class"),
                metadata.get("trust"),
            )
            for _timestamp, metadata in candidates
        }
        if len(authorities) != 1:
            return None
        return max(candidates, key=lambda item: item[0])[1]

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        allowed_statuses: frozenset[int] = frozenset(),
    ) -> tuple[int, dict[str, Any]]:
        data = None
        if body is not None:
            data = json.dumps(
                body,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        request = urllib.request.Request(
            f"{self._base_url}{path}",
            data=data,
            method=method,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            response = urllib.request.urlopen(
                request, timeout=self._config.timeout_seconds
            )
        except urllib.error.HTTPError as error:
            if error.code not in allowed_statuses:
                detail = error.read(2048).decode("utf-8", errors="replace")
                raise HindsightProjectionProtocolError(
                    f"Hindsight {method} {path} returned HTTP {error.code}: {detail}"
                ) from error
            response = error
        except (TimeoutError, socket.timeout, urllib.error.URLError) as error:
            raise HindsightProjectionUnavailable(
                f"Hindsight {method} {path} is unavailable"
            ) from error
        try:
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
        finally:
            response.close()
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise HindsightProjectionProtocolError("Hindsight response is too large")
        try:
            decoded = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise HindsightProjectionProtocolError(
                "Hindsight response is not valid UTF-8 JSON"
            ) from error
        if not isinstance(decoded, dict):
            raise HindsightProjectionProtocolError(
                "Hindsight response must be a JSON object"
            )
        return response.status, decoded

    def health(self) -> dict[str, Any]:
        _, response = self._request("GET", "/health")
        if response.get("status") != "healthy":
            raise HindsightProjectionProtocolError("Hindsight is not healthy")
        return response

    def ensure_bank(self, tenant_id: str, installation_id: str) -> str:
        bank_id = self._bank_id(tenant_id, installation_id)
        with self._cache_lock:
            if bank_id in self._known_banks:
                return bank_id
        _, response = self._request(
            "PUT",
            f"/v1/default/banks/{bank_id}",
            {
                "retain_extraction_mode": self._config.extraction_mode,
                "retain_mission": (
                    "Extract only durable information that the user actually states: "
                    "preferences, relationships, projects, corrections, commitments "
                    "and lived experiences. Preserve who, what and when. Treat later "
                    "explicit corrections as newer truth. A question, hypothetical or "
                    "benchmark scenario, quoted third-party claim, and assistant proposal "
                    "is not direct testimony; never turn it into a user preference or "
                    "fact unless the user explicitly asserts it."
                ),
                "observations_mission": (
                    "Synthesize durable person-specific knowledge, including evolving "
                    "communication preferences such as desired brevity, directness, "
                    "warmth, formality and formatting. Track contradictions over time. "
                    "Describe style preferences; never recommend copying spelling errors, "
                    "broken grammar or transient emotion."
                ),
                "reflect_mission": (
                    "Help Eva understand the person accurately and empathetically while "
                    "preserving Eva's own identity and truthful behavior."
                ),
                "disposition_skepticism": 3,
                "disposition_literalism": 2,
                "disposition_empathy": 4,
                "enable_observations": True,
                "enable_temporal_retrieval": True,
                "enable_graph_retrieval": True,
            },
        )
        returned_id = response.get("bank_id") or response.get("id")
        if returned_id is not None and returned_id != bank_id:
            raise HindsightProjectionProtocolError(
                "Hindsight created a different memory bank"
            )
        self._request(
            "PATCH",
            f"/v1/default/banks/{bank_id}/config",
            {
                "updates": {
                    "recall_budget_fixed_low": self._config.recall_node_budget,
                    "recall_budget_fixed_mid": self._config.recall_node_budget,
                    "recall_budget_fixed_high": self._config.recall_node_budget,
                    "recall_budget_function": "fixed",
                }
            },
        )
        with self._cache_lock:
            self._known_banks.add(bank_id)
        return bank_id

    def maintain_person_scope(
        self, tenant_id: str, installation_id: str, principal_id: str
    ) -> BackgroundReflectionReceipt:
        """Run one explicitly admitted reflection without an unmanaged scheduler."""

        with self._derivative_lock:
            return self._maintain_person_scope(tenant_id, installation_id, principal_id)

    def _maintain_person_scope(
        self, tenant_id: str, installation_id: str, principal_id: str
    ) -> BackgroundReflectionReceipt:
        """Perform the serialized Hindsight reflection operation."""

        bank_id = self.ensure_bank(tenant_id, installation_id)
        person_tag = self._person_tag(principal_id)
        mental_model_id = self._style_mirror_id(principal_id)
        trigger = {
            "exclude_mental_models": True,
            "fact_types": ["world", "experience", "observation"],
            "include_chunks": False,
            "keep_trace": True,
            "mode": "delta",
            "recall_chunks_max_tokens": 512,
            "recall_max_tokens": self._config.recall_max_tokens,
            "refresh_after_consolidation": False,
            "refresh_cron": None,
            "tags_match": "all_strict",
        }

        def trigger_matches(value: object) -> bool:
            return isinstance(value, dict) and all(
                value.get(key) == expected for key, expected in trigger.items()
            )

        status, existing = self._request(
            "GET",
            f"/v1/default/banks/{bank_id}/mental-models/{mental_model_id}"
            "?detail=metadata",
            allowed_statuses=frozenset({404}),
        )
        if status == 404:
            _, created = self._request(
                "POST",
                f"/v1/default/banks/{bank_id}/mental-models",
                {
                    "id": mental_model_id,
                    "max_tokens": 320,
                    "name": "Eva communication style mirror",
                    "source_query": (
                        "Schrijf alleen een compacte stijlspiegel van maximaal 100 "
                        "Nederlandse woorden voor Eva. Begin met 'De gebruiker wil' en "
                        "schrijf nooit dat Eva deze voorkeuren heeft. Beschrijf de laatst "
                        "geldende, "
                        "duurzame voorkeuren van deze persoon voor lengte, directheid, "
                        "warmte, formaliteit, opmaak, vragen en feitelijke precisie. "
                        "Noem onzekerheid of een latere correctie. Geen kopjes, lijst, "
                        "inhoudelijke projecten, huisfeiten of antwoordtekst."
                    ),
                    "tags": [person_tag],
                    "trigger": trigger,
                },
            )
            operation_id = created.get("operation_id")
            if not isinstance(operation_id, str) or not operation_id:
                raise HindsightProjectionProtocolError(
                    "Hindsight did not queue background reflection"
                )
            disposition: Literal["created", "refreshed"] = "created"
        else:
            if existing.get("id") != mental_model_id or not isinstance(
                existing.get("trigger"), dict
            ):
                raise HindsightProjectionProtocolError(
                    "Hindsight mental model metadata is invalid"
                )
            if not trigger_matches(existing["trigger"]):
                _, updated = self._request(
                    "PATCH",
                    f"/v1/default/banks/{bank_id}/mental-models/{mental_model_id}",
                    {"trigger": trigger},
                )
                if updated.get("id") != mental_model_id or not trigger_matches(
                    updated.get("trigger")
                ):
                    raise HindsightProjectionProtocolError(
                        "Hindsight did not remove the unmanaged reflection schedule"
                    )
            _, refreshed = self._request(
                "POST",
                f"/v1/default/banks/{bank_id}/mental-models/{mental_model_id}/refresh",
            )
            operation_id = refreshed.get("operation_id")
            if not isinstance(operation_id, str) or not operation_id:
                raise HindsightProjectionProtocolError(
                    "Hindsight did not queue the admitted background reflection"
                )
            disposition = "refreshed"
        assert operation_id is not None
        self._wait_for_operation(bank_id, operation_id)
        return BackgroundReflectionReceipt(
            bank_id, mental_model_id, disposition, operation_id
        )

    def _recall_style_mirror(self, bank_id: str, principal_id: str) -> str | None:
        mental_model_id = self._style_mirror_id(principal_id)
        status, response = self._request(
            "GET",
            f"/v1/default/banks/{bank_id}/mental-models/{mental_model_id}?detail=full",
            allowed_statuses=frozenset({404}),
        )
        if status == 404:
            return None
        if response.get("id") != mental_model_id:
            raise HindsightProjectionProtocolError(
                "Hindsight style mirror identity differs"
            )
        content = response.get("content")
        reflected = response.get("reflect_response")
        based_on = reflected.get("based_on") if isinstance(reflected, dict) else None
        if not isinstance(based_on, dict) or not any(
            isinstance(items, list) and items for items in based_on.values()
        ):
            return None
        if (
            not isinstance(content, str)
            or not content.strip()
            or len(content.encode("utf-8")) > 4_096
        ):
            raise HindsightProjectionProtocolError(
                "Hindsight style mirror content is invalid"
            )
        return content.strip()

    def retain_conversation(
        self, document: ConversationMemoryDocument
    ) -> MemoryRetentionReceipt:
        """Upsert a complete conversation document into Hindsight.

        Reusing ``document_id`` is Hindsight's native replace/idempotency model.
        This adapter does not pre-extract or persist a parallel Eva fact graph.
        """

        if document.audience.kind == "private_person":
            audience_tag = self._person_tag(document.principal_id)
            audience_metadata: dict[str, str] = {}
        else:
            consent_ref = self._require_active_group_audience(document.audience)
            assert document.audience.group_chat_id is not None
            group_prefix = f"telegram:{document.audience.group_chat_id}:"
            if (
                document.conversation_id
                != f"telegram:{document.audience.group_chat_id}"
                or not document.document_id.startswith(group_prefix)
                or not document.source_ref.startswith(group_prefix)
                or not document.evidence_ref.startswith(group_prefix)
            ):
                raise HindsightMemoryScopeError(
                    "Telegram group memory document is not bound to its group"
                )
            audience_tag = self._group_tag(document.audience.group_chat_id)
            audience_metadata = {
                "audience_id": document.audience.audience_id,
                "audience_kind": document.audience.kind,
                "consent_operation_id": consent_ref.operation_id,
                "group_chat_id": document.audience.group_chat_id,
            }
        observation_binding_sha256: str | None = None
        if document.evidence_ref.startswith(_OBSERVATION_EVIDENCE_PREFIX):
            observation_binding_sha256 = document.evidence_ref.removeprefix(
                _OBSERVATION_EVIDENCE_PREFIX
            )
            if not _SHA256.fullmatch(observation_binding_sha256):
                raise HindsightMemoryScopeError(
                    "observation memory evidence binding is invalid"
                )
        partition = (
            self._observation_partition(
                document.installation_id, observation_binding_sha256
            )
            if observation_binding_sha256 is not None
            else (
                document.installation_id
                if document.project_id is None
                else self._project_partition(
                    document.installation_id, document.project_id
                )
            )
        )
        bank_id = self.ensure_bank(document.tenant_id, partition)
        item = {
            "content": document.content,
            "timestamp": document.occurred_at,
            "context": "Eva conversation and experience memory",
            "document_id": document.document_id,
            "update_mode": "replace",
            "metadata": {
                "conversation_id": document.conversation_id,
                **audience_metadata,
                "evidence_ref": document.evidence_ref,
                "installation_id": document.installation_id,
                "principal_id": document.principal_id,
                **(
                    {}
                    if observation_binding_sha256 is None
                    else {"observation_binding_sha256": observation_binding_sha256}
                ),
                **(
                    {}
                    if document.project_id is None
                    else {"project_id": document.project_id}
                ),
                "privacy_class": document.privacy_class,
                **(
                    {}
                    if document.retention_until is None
                    else {"retention_until": document.retention_until}
                ),
                "source_ref": document.source_ref,
                "trust": document.trust,
            },
            "tags": ["eva-memory", audience_tag],
            "observation_scopes": [[audience_tag]],
        }
        _, response = self._request(
            "POST",
            f"/v1/default/banks/{bank_id}/memories",
            {
                "items": [item],
                "async": True,
                # Hindsight replays an existing operation before examining its
                # content. Bind retries to the complete source revision, not
                # just the document ID, or an edit silently retains old memory.
                "operation_id": str(uuid5(
                    NAMESPACE_URL,
                    "EvaMemoryRetain/v2:" + canonical_sha256([bank_id, item]),
                )),
            },
        )
        operation_id = response.get("operation_id")
        if (
            response.get("success") is not True
            or response.get("items_count") != 1
            or response.get("async") is not True
            or not isinstance(operation_id, str)
            or not operation_id
        ):
            raise HindsightProjectionProtocolError(
                "Hindsight did not acknowledge exactly one memory document"
        )
        self._wait_for_operation(bank_id, operation_id)
        with self._cache_lock:
            # A completed retain may correct a previously recalled preference.
            # Drop this audience's old snapshot and fence any earlier read still
            # in flight; unrelated warm snapshots remain available.
            self._fast_presence_generation += 1
            self._fast_presence_cache.pop(canonical_sha256([
                document.tenant_id,
                document.installation_id,
                document.principal_id,
                document.audience.audience_id,
            ]), None)
        return MemoryRetentionReceipt(
            bank_id=bank_id,
            document_id=document.document_id,
            content_sha256=hashlib.sha256(document.content.encode("utf-8")).hexdigest(),
        )

    def remove_conversation_document(
        self, *, bank_id: str, document_id: str, principal_sha256: str
    ) -> None:
        """Remove one source and its person-derived style model, replay safely."""

        if not re.fullmatch(
            rf"{re.escape(self._config.bank_namespace)}-[0-9a-f]{{32}}", bank_id
        ):
            raise HindsightMemoryScopeError(
                "Hindsight removal bank is outside Eva's namespace"
            )
        if not document_id.strip() or len(document_id) > 1_024:
            raise HindsightMemoryScopeError("Hindsight removal document is invalid")
        mental_model_id = self._style_mirror_id_from_sha256(principal_sha256)
        with self._derivative_lock:
            encoded = urllib.parse.quote(document_id, safe="")
            status, response = self._request(
                "DELETE",
                f"/v1/default/banks/{bank_id}/documents/{encoded}",
                allowed_statuses=frozenset({404}),
            )
            if status != 404 and response.get("success") is not True:
                raise HindsightProjectionProtocolError(
                    "Hindsight did not acknowledge conversation-memory removal"
                )
            status, response = self._request(
                "DELETE",
                f"/v1/default/banks/{bank_id}/mental-models/{mental_model_id}",
                allowed_statuses=frozenset({404}),
            )
            if status != 404 and response.get("status") != "deleted":
                raise HindsightProjectionProtocolError(
                    "Hindsight did not acknowledge derived-style removal"
                )
            with self._cache_lock:
                self._fast_presence_generation += 1
                self._fast_presence_cache.clear()

    def _wait_for_operation(self, bank_id: str, operation_id: str) -> None:
        deadline = time.monotonic() + self._config.timeout_seconds
        while True:
            _, response = self._request(
                "GET",
                f"/v1/default/banks/{bank_id}/operations/{operation_id}",
            )
            if response.get("operation_id") != operation_id:
                raise HindsightProjectionProtocolError(
                    "Hindsight operation identity differs"
                )
            status = response.get("status")
            if status == "completed":
                return
            if status in {"cancelled", "failed"}:
                raise HindsightProjectionError(
                    f"Hindsight retain operation ended as {status}"
                )
            if status not in {"pending", "processing"}:
                raise HindsightProjectionProtocolError(
                    "Hindsight operation status is invalid"
                )
            if time.monotonic() >= deadline:
                raise HindsightProjectionUnavailable(
                    "Hindsight retain operation exceeded its deadline"
                )
            time.sleep(0.25)

    def recall_for_presence(self, query: RecallCurrentFacts) -> PresenceMemory:
        """Recall semantic memories directly from Hindsight for model Presence."""

        if not query.subject_ids or query.principal_id not in query.subject_ids:
            raise HindsightMemoryScopeError(
                "memory recall must include the admitted principal"
            )
        group_recall = query.audience.kind == "telegram_group"
        if group_recall:
            assert query.audience.group_chat_id is not None
            assert query.audience.consent_ref is not None
            if (
                query.conversation_id != f"telegram:{query.audience.group_chat_id}"
                or query.project_id is not None
                or "observation_binding_sha256" in query.scope
            ):
                raise HindsightMemoryScopeError(
                    "Telegram group recall is not bound to its group"
                )
            active = self._resolve_active_group_reference(
                principal_id=query.principal_id,
                group_chat_id=query.audience.group_chat_id,
            )
            if active != query.audience.consent_ref:
                return self._empty_presence(query)
            audience_tag = self._group_tag(query.audience.group_chat_id)
        else:
            self._require_private_recall_audience(query)
            audience_tag = self._person_tag(query.principal_id)
        observation_binding_sha256 = query.scope.get("observation_binding_sha256")
        if observation_binding_sha256 is not None and (
            not isinstance(observation_binding_sha256, str)
            or not _SHA256.fullmatch(observation_binding_sha256)
        ):
            raise HindsightMemoryScopeError("observation recall binding is invalid")
        global_bank_id = self.ensure_bank(query.tenant_id, query.installation_id)
        bank_ids: list[tuple[str, str | None, str | None]] = [
            (global_bank_id, None, None)
        ]
        if query.project_id is not None:
            bank_ids.append(
                (
                    self.ensure_bank(
                        query.tenant_id,
                        self._project_partition(
                            query.installation_id, query.project_id
                        ),
                    ),
                    query.project_id,
                    None,
                )
            )
        if isinstance(observation_binding_sha256, str):
            bank_ids.append(
                (
                    self.ensure_bank(
                        query.tenant_id,
                        self._observation_partition(
                            query.installation_id, observation_binding_sha256
                        ),
                    ),
                    query.project_id,
                    observation_binding_sha256,
                )
            )
        variants = self._recall_query_variants(query.query_text)
        requests = [
            (text, bank_id, project_id, binding_sha256)
            for text in variants
            for bank_id, project_id, binding_sha256 in bank_ids
        ]
        with ThreadPoolExecutor(max_workers=len(requests)) as executor:
            batches = tuple(
                executor.map(
                    lambda request: self._recall_response(
                        request[1],
                        audience_tag,
                        query,
                        request[0],
                        request[2],
                        request[3],
                    ),
                    requests,
                )
            )

        # Use the strongest score seen across the full sentence and its neutral
        # lexical anchors. This preserves Hindsight's own ranking while preventing
        # one unique topic word from disappearing in a long conversational phrase.
        fused: dict[str, dict[str, Any]] = {}
        for batch in batches:
            for raw in batch:
                memory_id = raw.get("id")
                scores = raw.get("scores")
                final_score = scores.get("final") if isinstance(scores, dict) else None
                if (
                    not isinstance(memory_id, str)
                    or not memory_id
                    or isinstance(final_score, bool)
                    or not isinstance(final_score, (int, float))
                    or not math.isfinite(final_score)
                    or final_score < 0
                ):
                    raise HindsightProjectionProtocolError(
                        "Hindsight returned an invalid semantic memory"
                    )
                previous = fused.get(memory_id)
                previous_scores = None if previous is None else previous.get("scores")
                previous_final = (
                    previous_scores.get("final")
                    if isinstance(previous_scores, dict)
                    else None
                )
                if not isinstance(previous_final, (int, float)) or (
                    isinstance(final_score, (int, float))
                    and final_score > previous_final
                ):
                    fused[memory_id] = raw
        results = sorted(
            fused.values(),
            key=lambda item: (
                item.get("scores", {}).get("final", -1)
                if isinstance(item.get("scores"), dict)
                else -1
            ),
            reverse=True,
        )

        selected: list[RecalledFact] = []
        confirmed: list[ConfirmedFact] = []
        seen: set[str] = set()
        admitted_text_chars = 0
        admitted_text_char_limit = self._config.recall_max_tokens * 4
        for raw in results:
            memory_id = raw.get("id")
            text = raw.get("text")
            memory_type = raw.get("type") or "world"
            source_metadata = raw.get("_eva_source_metadata")
            metadata = (
                source_metadata
                if isinstance(source_metadata, dict)
                else raw.get("metadata") or {}
            )
            scores = raw.get("scores")
            final_score = scores.get("final") if isinstance(scores, dict) else None
            if (
                not isinstance(memory_id, str)
                or not memory_id
                or not isinstance(text, str)
                or not text.strip()
                or memory_type not in {"world", "experience", "observation"}
                or not isinstance(metadata, dict)
                or isinstance(final_score, bool)
                or not isinstance(final_score, (int, float))
                or not math.isfinite(final_score)
                or final_score < 0
                or any(
                    not isinstance(key, str) or not isinstance(value, str)
                    for key, value in metadata.items()
                )
            ):
                raise HindsightProjectionProtocolError(
                    "Hindsight returned an invalid semantic memory"
                )
            score_floor = self._config.minimum_final_score
            if (
                observation_binding_sha256 is not None
                and raw.get("_eva_memory_observation_binding_sha256")
                == observation_binding_sha256
            ):
                # The observation bank is already isolated by the exact media and
                # subject-region digest. Hindsight's cross-domain reranker assigns
                # short proper-name queries lower absolute scores, so this bank can
                # safely use a narrower floor without admitting weak matches from
                # the person's global or project memories.
                score_floor = self._config.exact_binding_minimum_final_score
            if final_score < score_floor:
                continue
            fact_id = f"hindsight:{memory_id}"
            if fact_id in seen:
                continue
            if admitted_text_chars + len(text) > admitted_text_char_limit:
                continue
            seen.add(fact_id)
            admitted_text_chars += len(text)
            subject_id = metadata.get("principal_id", query.principal_id)
            if group_recall:
                assert query.audience.group_chat_id is not None
                required_group_metadata = {
                    "audience_id": query.audience.audience_id,
                    "audience_kind": "telegram_group",
                    "conversation_id": query.conversation_id,
                    "group_chat_id": query.audience.group_chat_id,
                }
                if any(
                    metadata.get(key) != value
                    for key, value in required_group_metadata.items()
                ):
                    raise HindsightMemoryScopeError(
                        "Hindsight returned memory for another audience"
                    )
                group_prefix = f"telegram:{query.audience.group_chat_id}:"
                if not all(
                    isinstance(metadata.get(key), str)
                    and metadata[key].startswith(group_prefix)
                    for key in ("source_ref", "evidence_ref")
                ):
                    raise HindsightMemoryScopeError(
                        "Hindsight group memory has cross-scope provenance"
                    )
                consent_operation_id = metadata.get("consent_operation_id")
                if not isinstance(subject_id, str) or not isinstance(
                    consent_operation_id, str
                ):
                    raise HindsightMemoryScopeError(
                        "Hindsight group memory lacks consent metadata"
                    )
                try:
                    retained_reference = TelegramGroupConsentReference(
                        principal_id=subject_id,
                        group_chat_id=query.audience.group_chat_id,
                        operation_id=consent_operation_id,
                    )
                except ValueError as error:
                    raise HindsightMemoryScopeError(
                        "Hindsight group memory has invalid consent metadata"
                    ) from error
                if (
                    self._resolve_active_group_reference(
                        principal_id=subject_id,
                        group_chat_id=query.audience.group_chat_id,
                    )
                    != retained_reference
                ):
                    continue
            elif subject_id not in query.subject_ids:
                raise HindsightMemoryScopeError(
                    "Hindsight returned memory for another subject"
                )
            privacy_class = metadata.get("privacy_class", "internal")
            returned_project_id = metadata.get(
                "project_id", raw.get("_eva_memory_project_id")
            )
            if (
                returned_project_id is not None
                and returned_project_id != query.project_id
            ):
                continue
            returned_observation_binding = metadata.get(
                "observation_binding_sha256",
                raw.get("_eva_memory_observation_binding_sha256"),
            )
            if returned_observation_binding is not None and (
                returned_observation_binding != observation_binding_sha256
            ):
                continue
            if privacy_class not in query.allowed_privacy_classes:
                continue
            # Observations are Hindsight's derived conclusions, even when their
            # source was a direct message. They inform Eva but never become a
            # confirmed user fact merely through consolidation.
            trust = (
                "model_inference_unconfirmed"
                if memory_type == "observation" and source_metadata is None
                else metadata.get("trust", "host_policy")
            )
            if trust not in {
                "direct_user",
                "verified_external",
                "host_policy",
                "model_inference_unconfirmed",
            }:
                raise HindsightProjectionProtocolError(
                    "Hindsight returned memory with invalid trust"
                )
            observed_at_raw = (
                raw.get("mentioned_at")
                or raw.get("occurred_start")
                or query.requested_valid_at
            )
            if not isinstance(observed_at_raw, str):
                raise HindsightProjectionProtocolError(
                    "Hindsight memory timestamp is not text"
                )
            observed_at = _canonical_timestamp(observed_at_raw)
            source_ref = metadata.get("source_ref") or raw.get("document_id")
            evidence_ref = metadata.get("evidence_ref") or fact_id
            if not isinstance(source_ref, str) or not source_ref:
                source_ref = fact_id
            if not isinstance(evidence_ref, str) or not evidence_ref:
                evidence_ref = fact_id
            rank = len(selected) + 1
            selected.append(
                RecalledFact(
                    rank=rank,
                    fact_id=fact_id,
                    source_event_ids=[source_ref],
                    trust=(
                        "confirmed_direct_user"
                        if trust == "direct_user"
                        else (
                            "verified_external"
                            if trust == "verified_external"
                            else "model_inference_unconfirmed"
                        )
                    ),
                    ranking_reason="hindsight_temporal_semantic_match",
                )
            )
            confirmed.append(
                ConfirmedFact(
                    fact_id=fact_id,
                    subject=subject_id,
                    predicate=f"memory.{memory_type}",
                    value={
                        "context": raw.get("context"),
                        "memory_type": memory_type,
                        "semantic_trust": trust,
                        "text": text,
                    },
                    source_ref=source_ref,
                    evidence_ref=evidence_ref,
                    observed_at=observed_at,
                    trust=(
                        "host_policy"
                        if trust == "model_inference_unconfirmed"
                        else trust
                    ),
                )
            )
            if len(confirmed) >= query.maximum_facts:
                break

        fact_ids = [item.fact_id for item in selected]
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
            admitted_fact_ids_sha256=canonical_sha256(sorted(fact_ids)),
            selected=[item.model_dump(mode="json") for item in selected],
            excluded_reasons={},
            projection_version=_PROJECTION_VERSION,
            scope_sha256=canonical_sha256(query.scope),
            created_at=query.requested_valid_at,
        )
        return PresenceMemory(
            recall=recall,
            confirmed_facts=tuple(confirmed),
            style_mirror=(
                None
                if group_recall
                else self._recall_style_mirror(global_bank_id, query.principal_id)
            ),
        )

    @staticmethod
    def _require_private_recall_audience(query: RecallCurrentFacts) -> None:
        """Block group reads until their own tag and consent resolver exist."""

        if query.audience.kind != "private_person":
            raise HindsightMemoryScopeError(
                "Telegram group memory recall is not wired yet"
            )

    @staticmethod
    def _content(command: RememberFact) -> str:
        fact = command.fact
        value = canonical_json_bytes(fact.value).decode("utf-8")
        return (
            "Canoniek Eva-geheugenfeit.\n"
            f"Onderwerp: {fact.subject_id}\n"
            f"Eigenschap: {fact.predicate}\n"
            f"Waarde: {value}\n"
            f"Bron: {command.source_event.source_ref}"
        )

    def project_current(
        self,
        command: RememberFact,
        *,
        previous_fact_id: str | None = None,
    ) -> HindsightProjectionReceipt:
        """Project a fact only after its PostgreSQL commit has succeeded."""

        bank_id = self.ensure_bank(command.fact.tenant_id, command.installation_id)
        item = {
            "content": self._content(command),
            "timestamp": command.fact.observed_at,
            "context": "Afgeleide projectie van een canoniek, brongebonden Eva-geheugenfeit.",
            "document_id": command.fact.fact_id,
            "metadata": {
                "fact_id": command.fact.fact_id,
                "subject_id": command.fact.subject_id,
                "predicate": command.fact.predicate,
                "source_event_id": command.source_event.source_event_id,
                "source_ref": command.source_event.source_ref,
                "scope_sha256": canonical_sha256(command.scope),
                "trust": command.fact.trust,
            },
            "tags": [
                "eva-current",
                f"trust:{command.fact.trust}",
                f"predicate:{command.fact.predicate}",
            ],
        }
        _, response = self._request(
            "POST",
            f"/v1/default/banks/{bank_id}/memories",
            {"items": [item], "async": False},
        )
        if response.get("success") is not True or response.get("items_count") != 1:
            raise HindsightProjectionProtocolError(
                "Hindsight did not acknowledge exactly one projected fact"
            )
        if previous_fact_id and previous_fact_id != command.fact.fact_id:
            self.remove_fact(
                tenant_id=command.fact.tenant_id,
                installation_id=command.installation_id,
                fact_id=previous_fact_id,
            )
        return HindsightProjectionReceipt(
            bank_id=bank_id,
            fact_id=command.fact.fact_id,
            document_id=command.fact.fact_id,
            disposition="projected",
        )

    def remove_fact(
        self, *, tenant_id: str, installation_id: str, fact_id: str
    ) -> None:
        bank_id = self._bank_id(tenant_id, installation_id)
        encoded = urllib.parse.quote(fact_id, safe="")
        status, response = self._request(
            "DELETE",
            f"/v1/default/banks/{bank_id}/documents/{encoded}",
            allowed_statuses=frozenset({404}),
        )
        if status != 404 and response.get("success") is not True:
            raise HindsightProjectionProtocolError(
                "Hindsight did not acknowledge projected-fact removal"
            )

    def recall_candidates(self, query: RecallCurrentFacts) -> ProjectedRecall:
        self._require_private_recall_audience(query)
        bank_id = self._bank_id(query.tenant_id, query.installation_id)
        _, response = self._request(
            "POST",
            f"/v1/default/banks/{bank_id}/memories/recall",
            {
                "query": query.query_text,
                "budget": "low",
                "max_tokens": 1024,
                "tags": ["eva-current"],
                "tags_match": "all_strict",
                "include": {"entities": None, "chunks": None, "source_facts": None},
            },
        )
        results = response.get("results")
        if not isinstance(results, list):
            raise HindsightProjectionProtocolError(
                "Hindsight recall response has no result list"
            )
        fact_ids: list[str] = []
        for result in results:
            if not isinstance(result, dict):
                raise HindsightProjectionProtocolError(
                    "Hindsight recall result is not an object"
                )
            metadata = result.get("metadata") or {}
            if not isinstance(metadata, dict):
                raise HindsightProjectionProtocolError(
                    "Hindsight recall metadata is not an object"
                )
            fact_id = metadata.get("fact_id") or result.get("document_id")
            if fact_id is None:
                continue
            if not isinstance(fact_id, str) or not fact_id or len(fact_id) > 512:
                raise HindsightProjectionProtocolError(
                    "Hindsight returned an invalid fact ID"
                )
            if fact_id not in fact_ids:
                fact_ids.append(fact_id)
            if len(fact_ids) >= self._config.maximum_candidates:
                break
        ranked_query = replace(
            query,
            candidate_fact_ids=tuple(fact_ids),
            projection_version=_PROJECTION_VERSION,
        )
        return ProjectedRecall(ranked_query, backend=_PROJECTION_VERSION)

    def recall_or_sql_fallback(self, query: RecallCurrentFacts) -> ProjectedRecall:
        """Use SQL recall only for an actual Hindsight availability failure."""

        try:
            return self.recall_candidates(query)
        except HindsightProjectionUnavailable:
            return ProjectedRecall(
                replace(
                    query,
                    candidate_fact_ids=None,
                    projection_version="postgres-simple-v1",
                ),
                backend="postgres-simple-v1",
                fallback_reason="hindsight_unavailable",
            )


# Compatibility name for frozen evidence and older imports. New code should use
# HindsightMemoryEngine so the runtime role is unambiguous.
HindsightMemoryProjection = HindsightMemoryEngine


__all__ = [
    "HindsightMemoryEngine",
    "HindsightMemoryScopeError",
    "HindsightMemoryProjection",
    "HindsightProjectionConfig",
    "HindsightProjectionError",
    "HindsightProjectionProtocolError",
    "HindsightProjectionReceipt",
    "HindsightProjectionUnavailable",
    "ProjectedRecall",
]
