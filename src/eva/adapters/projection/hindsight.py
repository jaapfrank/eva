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
import logging
import math
import os
from functools import lru_cache
from pathlib import Path
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from uuid import NAMESPACE_URL, UUID, uuid5
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Callable, Literal, Protocol

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
    HouseholdConsentReference,
    TelegramGroupConsentReference,
)
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256
from eva.contracts.memory import MemoryRecall, RecalledFact
from eva.contracts.planning import ConfirmedFact


_RECALL_QUERY_TOKEN_LIMIT = 500
_RECALL_ENCODING_URL = "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken"
_RECALL_ENCODING_SHA256 = "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"


@lru_cache(maxsize=1)
def _recall_tokenizer() -> Any:
    """Use the same verified local vocabulary cache as conversation admission."""
    import tiktoken

    cache_directory = os.environ.get("TIKTOKEN_CACHE_DIR") or str(
        Path(__file__).parent.parent / "model" / "tokenizer_data"
    )
    cache_path = Path(cache_directory) / hashlib.sha1(_RECALL_ENCODING_URL.encode()).hexdigest()
    try:
        if hashlib.sha256(cache_path.read_bytes()).hexdigest() != _RECALL_ENCODING_SHA256:
            raise ValueError("tokenizer data hash differs")
    except (OSError, ValueError) as error:
        raise HindsightProjectionProtocolError("recall tokenizer data unavailable or differs") from error
    os.environ["TIKTOKEN_CACHE_DIR"] = cache_directory
    return tiktoken.get_encoding("cl100k_base")


def _bounded_recall_query(text: str) -> str:
    """Fit Hindsight's query limit; leave the full task and anchor extraction intact.

    Keep both ends of a long search query. This is a retrieval approximation,
    not a replacement for the original user instruction or stored conversation.
    """
    if len(text.encode("utf-8")) <= _RECALL_QUERY_TOKEN_LIMIT:
        return text
    encoding = _recall_tokenizer()
    tokens = encoding.encode(text, disallowed_special=())
    if len(tokens) <= _RECALL_QUERY_TOKEN_LIMIT:
        return text
    width = (_RECALL_QUERY_TOKEN_LIMIT - 8) // 2
    while width:
        head = encoding.decode_bytes(tokens[:width]).decode("utf-8", errors="ignore")
        tail = encoding.decode_bytes(tokens[-width:]).decode("utf-8", errors="ignore")
        bounded = head + "\n…\n" + tail
        if len(encoding.encode(bounded, disallowed_special=())) <= _RECALL_QUERY_TOKEN_LIMIT:
            return bounded
        width -= 1
    raise HindsightProjectionProtocolError("recall query could not be bounded")


_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_LOGGER = logging.getLogger(__name__)

_PROJECTION_VERSION = "hindsight-0.9.1-v1"
_ANCHOR_TOKEN = re.compile(r"(?u)\b[^\W_][\w-]{5,63}\b")
# These Dutch grammatical/time words qualify by length, but do not identify a
# topic on their own. Keep them in the full question: expanding e.g. "gisteren"
# separately discards what happened and can promote unrelated older memories.
# Deliberately narrower than a general stopword list ("buiten" is a real topic).
# Explicit bound/planned concepts bypass this filter, including these words.
_IMPLICIT_ANCHOR_NON_TOPICS = frozenset({
    "vandaag", "gisteren", "eergisteren", "morgen", "overmorgen",
    "vanochtend", "vanmiddag", "vanavond", "vannacht", "destijds",
    "gedaan", "geweest", "geworden", "hebben", "hadden", "worden",
    "werden", "kunnen", "konden", "moeten", "moesten", "zullen",
    "zouden", "willen", "wilden", "wanneer", "waarom", "hoeveel",
    "welke", "iemand", "iedereen", "niemand", "jezelf", "mijzelf",
    "jijzelf", "onszelf", "zichzelf", "jullie", "eigenlijk", "misschien",
    "andere", "anders", "dezelfde", "hetzelfde", "allemaal",
    "daarmee", "daarover", "daarvoor", "daardoor", "hiermee", "hierover",
    "hiervoor", "hierdoor", "waarmee", "waarover", "waarvoor", "waardoor",
    "bedoel", "bedoelt", "bedoelde", "bedoelden", "bedoelen",
    "vertel", "vertelt", "vertelde", "vertelden", "vertellen",
    "denken", "dachten", "gedacht", "zeggen", "gezegd",
    "vragen", "vroegen", "gevraagd", "wachten", "wachtte", "wachtten",
    "begrijp", "begrijpt", "begreep", "begrepen", "begrijpen",
    "herinner", "herinnert", "herinnerde", "herinnerden", "herinneren",
})
_PLANNED_QUERY_MARKER = "Semantische zoekconcepten:"
_BOUND_QUERY_MARKER = "Gebonden retrieval-ankers:"
_SHA256 = re.compile(r"[0-9a-f]{64}")
_OBSERVATION_EVIDENCE_PREFIX = "observation-binding:"


def _bounded_memory_reference(reference: str) -> str:
    """Project long stored references into Identifier without losing their source.

    Audience/provenance checks must run on the original reference first. The
    caller retains it alongside the fact; this never rewrites stored memory.
    """
    if len(reference) <= 192:
        return reference
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:._/@+\-=]*", reference):
        raise HindsightProjectionProtocolError("Hindsight memory reference is invalid")
    return "hindsight-ref:sha256:" + hashlib.sha256(reference.encode("utf-8")).hexdigest()


class HindsightProjectionError(SemanticMemoryProviderError):
    """Base error for the replaceable Hindsight projection."""


class HindsightProjectionUnavailable(HindsightProjectionError):
    """The projection could not be reached before its deadline."""


class HindsightProjectionProtocolError(HindsightProjectionError):
    """The projection returned an invalid or contradictory response."""


class HindsightMemoryScopeError(HindsightProjectionError):
    """A retain or recall crossed its admitted person/install scope."""


class ActiveHouseholdConsentResolver(Protocol):
    def active_reference(
        self, *, principal_id: str, household_id: str, now: datetime
    ) -> HouseholdConsentReference | None: ...


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
    before_content_sha256: str | None = None
    after_content_sha256: str | None = None
    source_evidence_sha256: str | None = None
    source_fact_count: int = 0
    observation_error_type: str | None = None


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
        active_household_consent_resolver: ActiveHouseholdConsentResolver | None = None,
        private_alias_resolver: Callable[[RecallCurrentFacts], tuple[str, ...]] | None = None,
        private_source_resolver: Callable[[RecallCurrentFacts, tuple[tuple[str, str, str], ...]], tuple[dict[str, Any], ...]] | None = None,
    ) -> None:
        self._private_alias_resolver = private_alias_resolver
        self._private_source_resolver = private_source_resolver
        self._config = config
        self._base_url = config.base_url.rstrip("/")
        self._known_banks: set[str] = set()
        self._cache_lock = threading.Lock()
        self._fast_presence_cache: dict[str, tuple[float, PresenceMemory]] = {}
        self._fast_presence_generation = 0
        self._derivative_lock = threading.RLock()
        self._active_group_consent_resolver = active_group_consent_resolver
        self._active_household_consent_resolver = active_household_consent_resolver

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

    @staticmethod
    def _household_tag(household_id: str) -> str:
        digest = hashlib.sha256(household_id.encode("utf-8")).hexdigest()
        return f"eva-household-{digest[:32]}"

    def _resolve_active_household_reference(
        self, *, principal_id: str, household_id: str
    ) -> HouseholdConsentReference | None:
        resolver = self._active_household_consent_resolver
        if resolver is None:
            raise HindsightMemoryScopeError("household memory has no active mandate resolver")
        try:
            return resolver.active_reference(principal_id=principal_id, household_id=household_id,
                now=datetime.now(tz=timezone.utc).replace(microsecond=0))
        except Exception as error:
            raise HindsightMemoryScopeError("household memory mandate could not be resolved") from error

    def _household_admitted(self, audience: MemoryAudience) -> bool:
        assert audience.household_id is not None
        assert audience.household_consent_ref is not None
        return self._resolve_active_household_reference(
            principal_id=audience.principal_id, household_id=audience.household_id,
        ) == audience.household_consent_ref

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
        audience = canonical_sha256([
            query.tenant_id, query.installation_id,
            query.principal_id, query.audience.audience_id,
        ])
        request = canonical_sha256([
            query.query_text, query.requested_valid_at, query.query_purpose,
            list(query.allowed_privacy_classes), list(query.subject_ids),
            query.maximum_facts, query.scope,
        ])
        return f"{audience}:{request}"

    def _private_recall_principals(self, query: RecallCurrentFacts) -> tuple[str, ...]:
        """Resolve current private aliases; failed authority never widens recall."""
        if query.audience.kind != "private_person" or self._private_alias_resolver is None:
            return (query.principal_id,)
        try:
            aliases = self._private_alias_resolver(query)
        except Exception:
            return (query.principal_id,)
        return tuple(sorted({query.principal_id, *aliases}))

    def read_style_context(self, query: RecallCurrentFacts) -> str | None:
        """Read the same private style mirror without semantic history retrieval."""
        if not query.subject_ids or query.principal_id not in query.subject_ids:
            raise HindsightMemoryScopeError(
                "memory recall must include the admitted principal"
            )
        if query.audience.kind != "private_person":
            return None
        self._require_private_recall_audience(query)
        principals = self._private_recall_principals(query)
        bank_id = self.ensure_bank(query.tenant_id, query.installation_id)
        style = self._recall_style_mirror(bank_id, query.principal_id)
        if principals != self._private_recall_principals(query):
            return None
        return style

    def read_contact_context(self, query: RecallCurrentFacts) -> PresenceMemory:
        """Read current typed revisions without semantic history or a model call.

        The conversation service keeps its deferred-history marker. Source checks
        run on every turn, so a cold-path summary never delays a correction.
        """
        if not query.subject_ids or query.principal_id not in query.subject_ids:
            raise HindsightMemoryScopeError("memory recall must include the admitted principal")
        if query.audience.kind == "private_person":
            self._require_private_recall_audience(query)
        principals = self._private_recall_principals(query)
        # The native style mirror has no current PG source/revision proof. It
        # cannot serve as a fallback when a fresh correction is pending/erased.
        memory = self._with_private_sources(query, self._empty_presence(query))
        if principals != self._private_recall_principals(query):
            return self._empty_presence(query)
        return memory

    def recall_for_presence_fast(self, query: RecallCurrentFacts) -> PresenceMemory:
        """Supply relevant memory before answering, including the first turn.

        A missing or stale cache entry is not evidence that a person has no
        memories. Cache only the same scoped question and validity time.
        """
        if query.audience.kind != "private_person" or query.project_id is not None:
            return self.recall_for_presence(query)
        if (not query.subject_ids or query.principal_id not in query.subject_ids
                or "observation_binding_sha256" in query.scope):
            return self.recall_for_presence(query)
        principals = self._private_recall_principals(query)
        key = self._fast_presence_key(query) + ":" + canonical_sha256(list(principals))
        cached_memory = None
        with self._cache_lock:
            cached = self._fast_presence_cache.get(key)
            if cached is not None and time.monotonic() - cached[0] <= 30:
                cached_memory = cached[1]
            generation = self._fast_presence_generation
        if cached_memory is not None:
            # An erased/expired source or revoked binding must not survive in
            # a semantic-recall cache. Resolve exact source authority again.
            return self._with_private_sources(query, cached_memory)
        memory = self.recall_for_presence(query)
        if principals != self._private_recall_principals(query):
            return self._empty_presence(query)
        with self._cache_lock:
            if generation == self._fast_presence_generation:
                self._fast_presence_cache[key] = (time.monotonic(), memory)
                while len(self._fast_presence_cache) > 128:
                    self._fast_presence_cache.pop(next(iter(self._fast_presence_cache)))
        return memory

    def _with_private_sources(self, query: RecallCurrentFacts, memory: PresenceMemory) -> PresenceMemory:
        """Resolve selected sources and current typed learning on every cache hit."""
        from eva.application.human_understanding import (
            MAX_LEARNING_RECORDS, is_human_understanding_reference,
            validate_human_understanding_context,
        )
        # Only a source previously resolved under current host authority is
        # known here. A missing imported legacy reference is not deletion proof.
        known_source_references = set()
        for fact in memory.confirmed_facts:
            source = (fact.value.get("original_source") or fact.value.get("source_provenance")) if isinstance(fact.value, dict) else None
            if isinstance(source, dict) and all(isinstance(source.get(key), str)
                    for key in ("principal_id", "source_ref", "evidence_ref")):
                known_source_references.add((source["principal_id"], source["source_ref"], source["evidence_ref"]))
        facts = tuple(fact.model_copy(update={"value": {
            key: value for key, value in fact.value.items()
            if key not in {"original_source", "source_provenance", "learning_context", "observation_sources"}
        }}) if isinstance(fact.value, dict) else fact for fact in memory.confirmed_facts)
        primary_references, references, observation_references = [], [], {}
        for fact in facts:
            provenance = fact.value.get("reference_provenance", {}) if isinstance(fact.value, dict) else {}
            primary = (fact.subject, provenance.get("source_ref", fact.source_ref),
                       provenance.get("evidence_ref", fact.evidence_ref))
            primary_references.append(primary)
            if (fact.predicate == "memory.observation"
                    and not is_human_understanding_reference(primary[2])):
                items = fact.value.get("observation_source_refs", []) if isinstance(fact.value, dict) else []
                complete = tuple((item["principal_id"], item["source_ref"], item["evidence_ref"])
                    for item in items if isinstance(item, dict) and all(
                        isinstance(item.get(key), str) and 0 < len(item[key]) <= 1024
                        for key in ("principal_id", "source_ref", "evidence_ref"))) if isinstance(items, list) else ()
                if (not isinstance(items, list) or not 1 <= len(complete) <= 3
                        or len(complete) != len(items) or len(set(complete)) != len(complete)
                        or primary not in complete):
                    complete = ()
                observation_references[fact.fact_id] = complete
                references.extend(complete)
            else:
                references.append(primary)
        available = {}
        sources_resolved = False
        if self._private_source_resolver is not None and query.audience.kind == "private_person":
            try:
                sources = self._private_source_resolver(query, tuple(dict.fromkeys(references)))
                if len(sources) > 3 + MAX_LEARNING_RECORDS:
                    raise HindsightProjectionProtocolError("original and learning source count differs")
                principals = self._private_recall_principals(query)
                ordinary_sources = 0
                for item in sources:
                    reference = (item["principal_id"], item["source_ref"], item["evidence_ref"])
                    learning = is_human_understanding_reference(reference[2])
                    if not learning:
                        ordinary_sources += 1
                    compacted = item.get("raw_state") == "compacted"
                    if compacted:
                        if (item.get("text") is not None or item.get("truncated") is not False
                                or not isinstance(item.get("content_sha256"), str)
                                or not re.fullmatch(r"[0-9a-f]{64}", item["content_sha256"])):
                            raise HindsightProjectionProtocolError("compacted source provenance differs")
                        _canonical_timestamp(item.get("compacted_at"))
                    if (ordinary_sources > 3 or (reference not in references and not learning)
                            or reference[0] not in principals or item["project_id"] != query.project_id
                            or (not compacted and (not isinstance(item["text"], str)
                                or not item["text"] or len(item["text"]) > 4096))
                            or type(item["truncated"]) is not bool
                            or not isinstance(item["occurred_at"], str)):
                        raise HindsightProjectionProtocolError("original source scope differs")
                    _canonical_timestamp(item["occurred_at"])
                    available[reference] = item
                sources_resolved = True
            except Exception:
                _LOGGER.debug("Original private memory sources unavailable")
                available = {}
        # A different selected fact may already have consumed its primary source.
        # Derived observations still require every contribution, on every cache hit.
        observation_available = dict(available)
        enriched, excluded, used_learning, learning_records = [], dict(memory.recall.excluded_reasons), set(), set()

        def learning_value(reference, source):
            context = validate_human_understanding_context(
                None if source is None else source.get("learning_context"),
                source_ref=reference[1], evidence_ref=reference[2])
            selected_record = next(item["record"] for item in context["records"]
                if (item["source_ref"], item["evidence_ref"]) == reference[1:])
            if selected_record["recorded_at"] != source["occurred_at"]:
                raise ValueError("learning source time differs")
            if (source.get("raw_state") == "compacted"
                    and selected_record["source_text_sha256"] != source["content_sha256"]):
                raise ValueError("learning compacted source hash differs")
            head = context["records"][0]
            key = (reference[0], head["source_ref"], head["evidence_ref"])
            records = {(reference[0], item["source_ref"], item["evidence_ref"]) for item in context["records"]}
            if key in used_learning or len(learning_records | records) > MAX_LEARNING_RECORDS:
                raise ValueError("learning context duplicate or limit")
            used_learning.add(key)
            learning_records.update(records)
            return {"text":head["record"]["interpretation"], "semantic_trust":"model_inference_unconfirmed",
                "learning_context":context, "source_observed_at":head["record"]["recorded_at"]}

        for fact, reference in zip(facts, primary_references, strict=True):
            learning = is_human_understanding_reference(reference[2])
            if (query.audience.kind == "private_person" and sources_resolved
                    and not learning and fact.fact_id not in observation_references
                    and reference in known_source_references and reference not in observation_available):
                excluded[fact.fact_id] = "known_source_unavailable"
                continue
            if fact.fact_id in observation_references and query.audience.kind == "private_person":
                contributing = observation_references[fact.fact_id]
                if not contributing or any(item not in observation_available for item in contributing):
                    excluded[fact.fact_id] = "observation_source_unavailable"
                    continue
                # Shared observations use their existing audience/mandate checks;
                # private observations additionally require current PG sources.
                fact = fact.model_copy(update={"value": {**fact.value,
                    "observation_sources": [observation_available[item] for item in contributing]}})
                source = observation_available[reference]
            else:
                source = available.get(reference) if learning else available.pop(reference, None)
            if learning:
                try:
                    value = learning_value(reference, source)
                except (ValueError, TypeError, KeyError):
                    excluded[fact.fact_id] = "learning_source_unavailable"
                    continue
                # Consolidation text never replaces the exact provisional record.
                fact = fact.model_copy(update={"trust":"host_policy", "value":{**fact.value, **value}})
            if source is not None and isinstance(fact.value, dict):
                key = "source_provenance" if source.get("raw_state") == "compacted" else "original_source"
                fact = fact.model_copy(update={"value":{**fact.value, key: source}})
            enriched.append(fact)
        # These are current host-validated outbox sources, not additional semantic
        # facts or a second store. Exact typed references remain available even
        # when Hindsight selected only ordinary conversation facts or nothing.
        added = []
        for reference, source in sorted(available.items(), key=lambda row: (row[1]["occurred_at"], row[0]), reverse=True):
            if not is_human_understanding_reference(reference[2]) or len(enriched) >= query.maximum_facts:
                continue
            try:
                value = learning_value(reference, source)
            except (ValueError, TypeError, KeyError):
                continue
            fact_id = "learning-source:" + canonical_sha256(list(reference))
            enriched.append(ConfirmedFact(fact_id=fact_id, subject=reference[0], predicate="memory.observation",
                value={**value,"original_source":source}, source_ref=reference[1], evidence_ref=reference[2],
                observed_at=value["source_observed_at"], trust="host_policy"))
            added.append(RecalledFact(rank=1,fact_id=fact_id,source_event_ids=[reference[1]],
                trust="model_inference_unconfirmed",ranking_reason="current_scoped_learning_source"))
        ids = {fact.fact_id for fact in enriched}
        selected = [item.model_copy(update={"rank":rank}) for rank,item in enumerate(
            [item for item in memory.recall.selected if item.fact_id in ids] + added, start=1)]
        recall = memory.recall.model_copy(update={"selected":selected,"excluded_reasons":excluded,
            "admitted_fact_ids_sha256":canonical_sha256(sorted(ids))}) if added or len(enriched) != len(facts) else memory.recall
        return replace(memory, recall=recall, confirmed_facts=tuple(enriched),
            style_mirror=(None if self._private_source_resolver is not None
                and query.audience.kind == "private_person" else memory.style_mirror))

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
    def _recall_query_variants(
        query_text: str, *, query_purpose: str | None = None
    ) -> tuple[str, ...]:
        """Extract candidate topic anchors from the full query in parallel.

        Each outgoing variant is bounded separately at the HTTP boundary.

        Dense retrieval can bury a unique Dutch place, person, or device name under
        the rest of a natural sentence. The longest remaining topic words are sent
        as independent semantic queries and fused with the full-sentence result.
        Grammatical and conversational verbs remain in the full query. Anchors never encode
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

        # Broad private orientation asks for a relationship between needs,
        # interests and context, not each longest word independently. Reranker
        # scores from unrelated queries are not calibrated against one another.
        # Explicit host-bound/model-planned concepts above remain intentional.
        if query_purpose == "private_opportunity_review":
            return (query_text,)

        unique: dict[str, str] = {}
        for match in _ANCHOR_TOKEN.finditer(query_text):
            token = match.group(0)
            if token.casefold() in _IMPLICIT_ANCHOR_NON_TOPICS:
                continue
            unique.setdefault(token.casefold(), token)
        anchors = sorted(unique.values(), key=lambda value: -len(value))[:3]
        # A one-word query is already its own lexical anchor. Keep every
        # distinct variant without repeating identical semantic retrieval.
        return (query_text, *(anchor for anchor in anchors
                              if anchor.casefold() != query_text.casefold()))

    def _recall_response(
        self,
        bank_id: str,
        person_tag: str | tuple[str, ...],
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
                "tags": list(person_tag) if isinstance(person_tag, tuple) else [person_tag],
                "tags_match": "any_strict" if isinstance(person_tag, tuple) else "all_strict",
                "include": {
                    "entities": {"max_tokens": 256} if query.query_purpose == "owner_memory_view" else None,
                    "chunks": None,
                    # Hindsight observations intentionally omit document metadata.
                    # Source facts carry text as well as provenance. A 256-token
                    # per-observation envelope can omit most contributors and
                    # make a valid multi-source pattern unusable. These bounded
                    # internal receipts are not added separately to model context;
                    # the original-event, audience and context limits still apply.
                    "source_facts": {
                        "max_tokens": 8192,
                        "max_tokens_per_observation": 2048,
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
            copied.pop("_eva_source_metadata", None)
            copied.pop("_eva_observation_source_refs", None)
            copied["_eva_memory_project_id"] = memory_project_id
            copied["_eva_memory_observation_binding_sha256"] = (
                memory_observation_binding_sha256
            )
            # The response-wide flag means some map entries were omitted, not
            # that every observation is incomplete. Validate every referenced
            # source below; complete observations retain their own authority.
            if copied.get("type") == "observation":
                from eva.application.human_understanding import is_human_understanding_reference
                source_ids = copied.get("source_fact_ids", [])
                if not isinstance(source_ids, list) or not all(isinstance(value, str) for value in source_ids):
                    continue
                origins = [source.get("metadata", {}) if isinstance(source, dict) else {}
                           for source in (source_facts.get(source_id) for source_id in source_ids)] if isinstance(source_ids, list) else []
                learning = [metadata for metadata in origins if isinstance(metadata, dict)
                            and is_human_understanding_reference(metadata.get("evidence_ref"))]
                if learning and (len(learning) != len(source_ids) or len({
                        (metadata.get("source_ref"), metadata.get("evidence_ref"))
                        for metadata in learning}) != 1):
                    continue
                candidates = self._observation_source_candidates(copied, source_facts)
                if candidates is not None:
                    copied["_eva_source_metadata"] = max(candidates, key=lambda row: row[0])[1]
                    copied["_eva_observation_source_refs"] = [
                        {key: metadata[key] for key in ("principal_id", "source_ref", "evidence_ref")}
                        for _timestamp, metadata in candidates]
                elif learning:
                    continue
            enriched.append(copied)
        return enriched

    @staticmethod
    def _observation_source_metadata(
        observation: dict[str, Any], source_facts: dict[str, Any]
    ) -> dict[str, str] | None:
        candidates = HindsightMemoryEngine._observation_source_candidates(observation, source_facts)
        return max(candidates, key=lambda row: row[0])[1] if candidates else None

    @staticmethod
    def _observation_source_candidates(
        observation: dict[str, Any], source_facts: dict[str, Any]
    ) -> list[tuple[str, dict[str, str]]] | None:
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
                or any(not isinstance(metadata.get(key), str) or not 0 < len(metadata[key]) <= 1024
                       for key in ("principal_id", "source_ref", "evidence_ref"))
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
                metadata.get("audience_kind"),
                metadata.get("audience_id"),
                metadata.get("household_id"),
                metadata.get("group_chat_id"),
                metadata.get("consent_operation_id"),
                metadata.get("project_id"),
                metadata.get("observation_binding_sha256"),
            )
            for _timestamp, metadata in candidates
        }
        if len(authorities) != 1:
            return None
        for _timestamp, metadata in candidates:
            if metadata.get("audience_kind") == "household":
                household_id = metadata.get("household_id")
                conversation = metadata.get("conversation_id")
                prefix = f"household:{household_id}:conversation:"
                if (not isinstance(conversation, str) or not conversation.startswith(prefix)
                        or not conversation.removeprefix(prefix)
                        or not all(isinstance(metadata.get(key), str)
                                   and metadata[key].startswith(conversation + ":")
                                   for key in ("source_ref", "evidence_ref"))):
                    return None
        # Multiple extracted facts can share one original event. Retain every
        # distinct event, or reject the observation rather than trim its support.
        unique = {}
        for candidate in sorted(candidates, key=lambda row: row[0]):
            metadata = candidate[1]
            unique[tuple(metadata[key] for key in ("principal_id", "source_ref", "evidence_ref"))] = candidate
        return list(unique.values()) if len(unique) <= 3 else None

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        allowed_statuses: frozenset[int] = frozenset(),
    ) -> tuple[int, dict[str, Any]]:
        data = None
        if method == "POST" and path.endswith("/memories/recall") and body is not None:
            body = {**body, "query": _bounded_recall_query(body["query"])}
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
                    "Extract information the user actually states that remains useful later: "
                    "preferences, relationships, projects, corrections, commitments "
                    "and lived experiences, including explicitly time-bounded exceptions. "
                    "Preserve who, what and when, including the stated start, end and "
                    "conditions of an exception. Anchor relative dates to the source "
                    "timestamp; do not invent an unspecified duration. A temporary "
                    "exception does not replace a standing preference outside its scope. Treat later "
                    "explicit corrections as newer truth only within their stated scope. "
                    "Keep each fact short and atomic. A greeting, acknowledgement, "
                    "one-off request to use a tool, run a test, inspect a temporary "
                    "status or produce an answer is not enduring person knowledge. "
                    "Do not retain such instructions, proof IDs, intermediate tool "
                    "steps or completed administrative chatter as personal facts. "
                    "Still extract a genuine preference, correction, ongoing goal or "
                    "future commitment explicitly stated within a request. If no "
                    "later-useful information is stated, extract no facts. "
                    "A question, hypothetical or "
                    "benchmark scenario, quoted third-party claim, and assistant proposal "
                    "is not direct testimony; never turn it into a user preference or "
                    "fact unless the user explicitly asserts it."
                ),
                "observations_mission": (
                    "Synthesize durable person-specific knowledge, including evolving "
                    "communication preferences such as desired brevity, directness, "
                    "warmth, formality and formatting. Track contradictions over time. "
                    "Merge repeated statements about the same person and subject into "
                    "one concise current observation, updating existing observations "
                    "rather than paraphrasing them into duplicates. Preserve source "
                    "evidence and meaningful changes over time. A correction updates "
                    "current understanding; it must not become an additional competing "
                    "current preference. "
                    "Keep temporary exceptions and their dates distinct from enduring traits. "
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

        source_query = (
            "Schrijf een compacte stijlspiegel van maximaal 100 Nederlandse woorden "
            "voor Eva. Beschrijf alleen communicatievoorkeuren die deze persoon "
            "daadwerkelijk heeft uitgesproken: lengte, directheid, warmte, formaliteit, "
            "opmaak, vragen en feitelijke precisie. Onze instructies zijn geen "
            "uitspraken of wensen van de gebruiker. Verzin geen voorkeur om een "
            "vaste openingszin te vullen. Laat onbekende voorkeuren onbekend. "
            "Zonder onderbouwde communicatievoorkeuren schrijf je uitsluitend: "
            "Geen onderbouwde communicatievoorkeuren bekend. Neem alleen latere "
            "correcties op die deze communicatievoorkeuren wijzigen. Onderscheid "
            "tijdelijke uitzonderingen van blijvende voorkeuren. Behoud expliciete "
            "bovengrenzen en verboden exact: maak van geen genummerde lijsten "
            "niet dat lijsten niet verplicht zijn, en van maximaal niet ongeveer. "
            "Geen inhoudelijke "
            "interesses, vrijetijdskeuzes, projecten, huisfeiten of antwoordtekst; "
            "die blijven in het gewone geheugen. Geen kopjes of lijst."
        )

        def trigger_matches(value: object) -> bool:
            return isinstance(value, dict) and all(
                value.get(key) == expected for key, expected in trigger.items()
            )

        status, existing = self._request(
            "GET",
            f"/v1/default/banks/{bank_id}/mental-models/{mental_model_id}"
            "?detail=full",
            allowed_statuses=frozenset({404}),
        )
        before_content = None
        if status != 404:
            try:
                before_content = self._style_mirror_content(existing, mental_model_id)
            except HindsightProjectionProtocolError:
                # An invalid old derivative may still be repaired by this reflection.
                pass
        if status == 404:
            _, created = self._request(
                "POST",
                f"/v1/default/banks/{bank_id}/mental-models",
                {
                    "id": mental_model_id,
                    "max_tokens": 320,
                    "name": "Eva communication style mirror",
                    "source_query": source_query,
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
            if (not trigger_matches(existing["trigger"])
                    or existing.get("source_query") != source_query):
                _, updated = self._request(
                    "PATCH",
                    f"/v1/default/banks/{bank_id}/mental-models/{mental_model_id}",
                    {"trigger": trigger, "source_query": source_query},
                )
                if (updated.get("id") != mental_model_id
                        or not trigger_matches(updated.get("trigger"))
                        or updated.get("source_query") != source_query):
                    raise HindsightProjectionProtocolError(
                        "Hindsight did not apply the admitted reflection configuration"
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
        after_digest = source_digest = observation_error_type = None
        source_fact_count = 0
        try:
            _, final = self._request(
                "GET",
                f"/v1/default/banks/{bank_id}/mental-models/{mental_model_id}?detail=full",
            )
            after_content = self._style_mirror_content(final, mental_model_id)
            if after_content is not None:
                based_on = final["reflect_response"]["based_on"]
                source_digest = hashlib.sha256(json.dumps(
                    based_on, sort_keys=True, separators=(",", ":"),
                    ensure_ascii=False, allow_nan=False,
                ).encode("utf-8")).hexdigest()
                source_fact_count = sum(
                    len(items) for items in based_on.values() if isinstance(items, list)
                )
                after_digest = hashlib.sha256(after_content.encode("utf-8")).hexdigest()
        except Exception as error:
            # Reflection already settled: missing readback must not trigger another one.
            after_digest = source_digest = None
            source_fact_count = 0
            error_type = type(error)
            observation_error_type = hashlib.sha256(
                f"{error_type.__module__}.{error_type.__qualname__}".encode("utf-8")
            ).hexdigest()
        return BackgroundReflectionReceipt(
            bank_id, mental_model_id, disposition, operation_id,
            before_content_sha256=(None if before_content is None else
                                   hashlib.sha256(before_content.encode("utf-8")).hexdigest()),
            after_content_sha256=after_digest,
            source_evidence_sha256=source_digest,
            source_fact_count=source_fact_count,
            observation_error_type=observation_error_type,
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
        return self._style_mirror_content(response, mental_model_id)

    @staticmethod
    def _style_mirror_content(response: dict[str, Any], mental_model_id: str) -> str | None:
        """Return precisely the grounded, stripped content admitted to Presence."""
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

    def _conversation_item(
        self, document: ConversationMemoryDocument, *, _ensure_bank: bool = True
    ) -> tuple[str, dict[str, Any]]:
        if document.audience.kind == "private_person":
            audience_tag = self._person_tag(document.principal_id)
            audience_metadata: dict[str, str] = {}
        elif document.audience.kind == "household":
            if not self._household_admitted(document.audience):
                raise HindsightMemoryScopeError("household memory mandate is not active and exact")
            assert document.audience.household_id is not None
            assert document.audience.household_consent_ref is not None
            audience_tag = self._household_tag(document.audience.household_id)
            audience_metadata = {
                "audience_id": document.audience.audience_id,
                "audience_kind": "household",
                "household_id": document.audience.household_id,
                "consent_operation_id": document.audience.household_consent_ref.operation_id,
            }
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
        bank_id = (self.ensure_bank(document.tenant_id, partition) if _ensure_bank
                   else self._bank_id(document.tenant_id, partition))
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
                "selection_policy": "later-useful-knowledge-v2",
            },
            "tags": ["eva-memory", audience_tag],
            "observation_scopes": [[audience_tag]],
        }
        return bank_id, item

    def retain_conversations(
        self, documents: tuple[ConversationMemoryDocument, ...]
    ) -> tuple[MemoryRetentionReceipt, ...]:
        """Retain one bounded private import through Hindsight's native batch API.

        Every item keeps its own stable document, source, trust and person tags.
        No receipt is returned before the whole asynchronous batch completes.
        """
        if not documents or len(documents) > 64:
            raise HindsightMemoryScopeError("memory batch must contain 1..64 documents")
        scopes = {(d.tenant_id, d.installation_id, d.principal_id) for d in documents}
        if (len(scopes) != 1 or any(d.audience.kind != "private_person" for d in documents)
                or len({d.document_id for d in documents}) != len(documents)
                or sum(len(d.content.encode("utf-8")) for d in documents) > 1_048_576):
            raise HindsightMemoryScopeError("memory batch differs in private scope or exceeds its budget")
        prepared = tuple(self._conversation_item(document) for document in documents)
        if len({bank for bank, _ in prepared}) != 1:
            raise HindsightMemoryScopeError("memory batch spans different banks")
        bank_id = prepared[0][0]
        items = [item for _, item in prepared]
        _, response = self._request("POST", f"/v1/default/banks/{bank_id}/memories", {
            "items": items, "async": True,
            "operation_id": str(uuid5(NAMESPACE_URL, "EvaMemoryBatchRetain/v1:" + canonical_sha256([bank_id, items]))),
        })
        operation_id = response.get("operation_id")
        if (response.get("success") is not True or response.get("items_count") != len(documents)
                or response.get("async") is not True or not isinstance(operation_id, str)
                or not operation_id):
            raise HindsightProjectionProtocolError("Hindsight did not acknowledge the exact memory batch")
        self._wait_for_operation(bank_id, operation_id, retry_existing_failure=True)
        with self._cache_lock:
            self._fast_presence_generation += 1
            self._fast_presence_cache.clear()
        return tuple(MemoryRetentionReceipt(bank_id=bank_id, document_id=document.document_id,
                                            content_sha256=hashlib.sha256(document.content.encode("utf-8")).hexdigest())
                     for document in documents)

    def _prepare_dream_batch(
        self, documents: tuple[ConversationMemoryDocument, ...]
    ) -> tuple[str, dict[str, Any]]:
        """Select compact native knowledge from one immutable private dream batch.

        The named native strategy must already be provisioned and read back.
        It omits document/chunk source text; async operation payloads retain their
        existing native lifecycle. This never changes bank defaults or deletes
        source/derived memory. Eva's journal/outbox remains source authority.
        """
        strategy = "eva-ordinary-dream-v1"
        if not documents or len(documents) > 32:
            raise HindsightMemoryScopeError("dream batch must contain 1..32 documents")
        scopes = {
            (d.tenant_id, d.installation_id, d.principal_id, d.privacy_class,
             d.audience.audience_id)
            for d in documents
        }
        if (len(scopes) != 1 or len({d.occurred_at[:10] for d in documents}) != 1
                or any(d.audience.kind != "private_person" or d.project_id is not None
                       or d.trust != "direct_user"
                       or getattr(d, "retention_mode", None) != "dream"
                       or len(d.document_id) > 512
                       or d.document_id != f"{d.conversation_id}:exchange:{d.source_ref}"
                       for d in documents)
                or len({d.document_id for d in documents}) != len(documents)
                or sum(len(d.content.encode("utf-8")) for d in documents) > 262_144):
            raise HindsightMemoryScopeError("dream batch is not bounded ordinary private evidence")
        prepared = tuple(self._conversation_item(d, _ensure_bank=False) for d in sorted(
            documents, key=lambda d: d.document_id,
        ))
        if len({bank for bank, _ in prepared}) != 1:
            raise HindsightMemoryScopeError("dream batch spans different banks")
        bank_id = prepared[0][0]
        items = []
        for _, original in prepared:
            item = {**original, "strategy": strategy,
                    "metadata": {**original["metadata"], "retention_mode": "dream"}}
            items.append(item)
        if len(canonical_json_bytes(items)) > 262_144:
            raise HindsightMemoryScopeError("dream batch serialized items exceed 256 KiB")

        operation_id = str(uuid5(
            NAMESPACE_URL, "EvaDreamMemoryBatchRetain/v1:" + canonical_sha256([bank_id, items]),
        ))
        request = {"items": items, "async": True, "operation_id": operation_id}
        if len(canonical_json_bytes(request)) > 262_144:
            raise HindsightMemoryScopeError("dream batch serialized request exceeds 256 KiB")
        return bank_id, request

    def document_retention_address(self, document: ConversationMemoryDocument) -> tuple[str, str]:
        """Validate a native deletion address without retaining or creating a bank."""
        bank_id, item = self._conversation_item(document, _ensure_bank=False)
        return bank_id, item["document_id"]

    def dream_operation_identity(
        self, documents: tuple[ConversationMemoryDocument, ...]
    ) -> tuple[str, str]:
        """Derive the exact dispatch identity without HTTP or bank creation."""
        bank_id, request = self._prepare_dream_batch(documents)
        return bank_id, request["operation_id"]

    def _read_dream_operation(self, *, bank_id: str, operation_id: str) -> dict[str, Any]:
        if (not isinstance(bank_id, str)
                or not re.fullmatch(rf"{re.escape(self._config.bank_namespace)}-[0-9a-f]{{32}}", bank_id)
                or not isinstance(operation_id, str)):
            raise HindsightMemoryScopeError("dream operation identity is invalid")
        try:
            if str(UUID(operation_id)) != operation_id:
                raise ValueError("noncanonical operation UUID")
        except ValueError as error:
            raise HindsightMemoryScopeError("dream operation identity is invalid") from error
        status, response = self._request(
            "GET", f"/v1/default/banks/{bank_id}/operations/{operation_id}",
            allowed_statuses=frozenset({404}),
        )
        if status == 404:
            return {"operation_id": operation_id, "status": "not_found"}
        if response.get("operation_id") != operation_id:
            raise HindsightProjectionProtocolError("Hindsight dream operation identity differs")
        state = response.get("status")
        if state == "not_found":
            return response
        if state in {"pending", "processing"}:
            raise HindsightProjectionUnavailable("Hindsight dream operation is still unsettled")
        metadata = response.get("result_metadata")
        children = response.get("child_operations")
        if (state not in {"completed", "failed"} or not isinstance(metadata, dict)
                or metadata.get("is_parent") is not True
                or type(metadata.get("num_sub_batches")) is not int
                or metadata["num_sub_batches"] < 1 or not isinstance(children, list)
                or len(children) != metadata["num_sub_batches"]
                or any(not isinstance(child, dict) or child.get("status") not in {"completed", "failed"}
                       or not isinstance(child.get("operation_id"), str)
                       or not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}",
                                           child["operation_id"]) for child in children)
                or len({child["operation_id"] for child in children}) != len(children)
                or (state == "completed" and any(child["status"] != "completed" for child in children))):
            # Native cancellation only marks the parent; children may still run.
            # Missing children cannot be used as proof that remote writes ended.
            raise HindsightProjectionProtocolError("Hindsight dream operation has no settled child proof")
        return response

    def settle_dream_operation(self, *, bank_id: str, operation_id: str) -> str:
        """Read only this recorded operation; refuse erasure while it can write.

        The caller holds the existing cross-worker retention/erasure guard so a
        not-found read cannot race a later POST. Never retry or cancel here.
        """
        return self._read_dream_operation(bank_id=bank_id, operation_id=operation_id)["status"]

    def _dream_document_count(
        self, bank_id: str, item: dict[str, Any], *, allow_absent: bool
    ) -> int | None:
        encoded = urllib.parse.quote(item["document_id"], safe="")
        status, record = self._request(
            "GET", f"/v1/default/banks/{bank_id}/documents/{encoded}",
            allowed_statuses=frozenset({404}) if allow_absent else frozenset(),
        )
        if status == 404 and allow_absent:
            return None
        digest = hashlib.sha256(item["content"].encode("utf-8")).hexdigest()
        count = record.get("memory_unit_count")
        if (record.get("id") != item["document_id"] or record.get("bank_id") != bank_id
                or record.get("content_hash") != digest
                or record.get("document_metadata") != item["metadata"]
                or not isinstance(record.get("tags"), list)
                or not all(isinstance(tag, str) for tag in record["tags"])
                or sorted(record["tags"]) != sorted(item["tags"])
                or "original_text" not in record or record["original_text"] is not None
                or type(count) is not int or count < 0):
            raise HindsightProjectionProtocolError("Hindsight dream document evidence differs")
        return count

    def _dream_receipts(
        self, documents: tuple[ConversationMemoryDocument, ...], bank_id: str,
        request: dict[str, Any], completed: dict[str, Any],
    ) -> tuple[MemoryRetentionReceipt, ...]:
        with self._cache_lock:
            self._fast_presence_generation += 1
            self._fast_presence_cache.clear()
        metadata = completed.get("result_metadata")
        if (not isinstance(metadata, dict)
                or type(metadata.get("unit_ids_count")) is not int
                or metadata["unit_ids_count"] < 0
                or type(metadata.get("extraction_errors_count")) is not int
                or metadata["extraction_errors_count"] != 0):
            raise HindsightProjectionProtocolError("Hindsight dream batch has no clean measured outcome")
        counts = {item["document_id"]: self._dream_document_count(bank_id, item, allow_absent=False)
                  for item in request["items"]}
        if sum(counts.values()) != metadata["unit_ids_count"]:
            raise HindsightProjectionProtocolError("Hindsight dream document counts differ from batch outcome")
        return tuple(MemoryRetentionReceipt(
            bank_id=bank_id, document_id=d.document_id,
            content_sha256=hashlib.sha256(d.content.encode("utf-8")).hexdigest(),
            disposition="not_selected" if counts[d.document_id] == 0 else "retained",
            selected_fact_count=counts[d.document_id], operation_id=request["operation_id"],
        ) for d in documents)

    def read_dream_operation_receipts(
        self, documents: tuple[ConversationMemoryDocument, ...], *, bank_id: str, operation_id: str
    ) -> tuple[MemoryRetentionReceipt, ...]:
        """Recover completed batch receipts with GET only, before erasing any member."""
        expected_bank, request = self._prepare_dream_batch(documents)
        if bank_id != expected_bank or operation_id != request["operation_id"]:
            raise HindsightMemoryScopeError("dream receipt operation differs from exact membership")
        completed = self._read_dream_operation(bank_id=bank_id, operation_id=operation_id)
        if completed["status"] != "completed":
            raise HindsightProjectionProtocolError("Hindsight dream operation has no completed receipts")
        return self._dream_receipts(documents, bank_id, request, completed)

    def _ensure_dream_strategy(self, bank_id: str) -> None:
        """Provision only our named strategy, preserving bank defaults and other entries."""
        strategy = "eva-ordinary-dream-v1"
        expected = {"store_document_text": False}
        # The cold worker also holds its cross-process native-stage guard. No model
        # or untrusted source may choose a strategy or mutate bank configuration.
        with self._cache_lock:
            _, configuration = self._request("GET", f"/v1/default/banks/{bank_id}/config")
            resolved = configuration.get("config")
            if configuration.get("bank_id") != bank_id or not isinstance(resolved, dict):
                raise HindsightProjectionProtocolError("Hindsight dream bank configuration differs")
            strategies = resolved.get("retain_strategies")
            if strategies is None:
                strategies = {}
            if not isinstance(strategies, dict):
                raise HindsightProjectionProtocolError("Hindsight named strategies are malformed")
            if strategy in strategies:
                entry = strategies[strategy]
                if (not isinstance(entry, dict) or set(entry) != {"store_document_text"}
                        or entry["store_document_text"] is not False):
                    raise HindsightProjectionProtocolError("Hindsight dream strategy differs")
                return
            existing = dict(strategies)
            previous_default = canonical_sha256(["store_document_text" in resolved,
                                                  resolved.get("store_document_text")])
            self._request("PATCH", f"/v1/default/banks/{bank_id}/config",
                {"updates": {"retain_strategies": {**existing, strategy: expected}}})
            _, checked = self._request("GET", f"/v1/default/banks/{bank_id}/config")
            actual = checked.get("config")
            actual_strategies = actual.get("retain_strategies") if isinstance(actual, dict) else None
            if (checked.get("bank_id") != bank_id or not isinstance(actual_strategies, dict)
                    or not isinstance(actual_strategies.get(strategy), dict)
                    or set(actual_strategies[strategy]) != {"store_document_text"}
                    or actual_strategies[strategy]["store_document_text"] is not False
                    or canonical_sha256(["store_document_text" in actual,
                                         actual.get("store_document_text")]) != previous_default
                    or any(key not in actual_strategies
                           or canonical_sha256(actual_strategies[key]) != canonical_sha256(value)
                           for key, value in existing.items())):
                raise HindsightProjectionProtocolError("Hindsight dream strategy readback differs")

    def retain_dream_conversations(
        self, documents: tuple[ConversationMemoryDocument, ...]
    ) -> tuple[MemoryRetentionReceipt, ...]:
        """Dispatch one immutable batch using the previously persistable identity."""
        strategy = "eva-ordinary-dream-v1"
        bank_id, request = self._prepare_dream_batch(documents)
        if self.ensure_bank(documents[0].tenant_id, documents[0].installation_id) != bank_id:
            raise HindsightMemoryScopeError("dream bank identity differs")
        for item in request["items"]:
            self._dream_document_count(bank_id, item, allow_absent=True)
        self._ensure_dream_strategy(bank_id)
        _, response = self._request("POST", f"/v1/default/banks/{bank_id}/memories", request)
        if (response.get("success") is not True or response.get("bank_id") != bank_id
                or type(response.get("items_count")) is not int
                or response["items_count"] != len(documents) or response.get("async") is not True
                or response.get("operation_id") != request["operation_id"]):
            raise HindsightProjectionProtocolError("Hindsight did not acknowledge the exact dream batch")
        completed = self._wait_for_operation(bank_id, request["operation_id"], retry_existing_failure=True)
        return self._dream_receipts(documents, bank_id, request, completed)

    def retain_conversation(
        self, document: ConversationMemoryDocument
    ) -> MemoryRetentionReceipt:
        """Upsert one source using Hindsight's native replace/idempotency model."""
        bank_id, item = self._conversation_item(document)
        expected_operation_id = str(uuid5(
            NAMESPACE_URL, "EvaMemoryRetain/v2:" + canonical_sha256([bank_id, item]),
        ))
        _, response = self._request(
            "POST",
            f"/v1/default/banks/{bank_id}/memories",
            {
                "items": [item],
                "async": True,
                # Hindsight replays an existing operation before examining its
                # content. Bind retries to the complete source revision, not
                # just the document ID, or an edit silently retains old memory.
                "operation_id": expected_operation_id,
            },
        )
        operation_id = response.get("operation_id")
        if (
            response.get("success") is not True
            or response.get("items_count") != 1
            or response.get("async") is not True
            or operation_id != expected_operation_id
        ):
            raise HindsightProjectionProtocolError(
                "Hindsight did not acknowledge exactly one memory document"
        )
        completed = self._wait_for_operation(bank_id, operation_id, retry_existing_failure=True)
        metadata = completed.get("result_metadata")
        selected_count = None
        extraction_errors = None
        if isinstance(metadata, dict):
            for key in ("unit_ids_count", "extraction_errors_count"):
                value = metadata.get(key)
                if value is not None and (type(value) is not int or value < 0):
                    raise HindsightProjectionProtocolError("Hindsight selection counts are invalid")
            selected_count = metadata.get("unit_ids_count")
            extraction_errors = metadata.get("extraction_errors_count")
        # Only ordinary immutable user exchanges may be classified as unselected.
        # Typed learning/work records remain source evidence even with zero facts.
        ordinary_exchange = (
            document.audience.kind == "private_person"
            and document.trust == "direct_user"
            and document.document_id == f"{document.conversation_id}:exchange:{document.source_ref}"
        )
        not_selected = ordinary_exchange and selected_count == 0 and extraction_errors == 0
        with self._cache_lock:
            # A completed retain may correct a previously recalled preference.
            # Drop this audience's old snapshot and fence any earlier read still
            # in flight; unrelated warm snapshots remain available.
            self._fast_presence_generation += 1
            audience_key = canonical_sha256([
                document.tenant_id, document.installation_id,
                document.principal_id, document.audience.audience_id,
            ]) + ":"
            # Linked private identities may have cached the same source under
            # either actor. Retention is infrequent; invalidate the bounded cache
            # rather than retaining a second, potentially stale authority map.
            if self._private_alias_resolver is not None and document.audience.kind == "private_person":
                self._fast_presence_cache.clear()
            for key in tuple(self._fast_presence_cache):
                if key.startswith(audience_key):
                    self._fast_presence_cache.pop(key, None)
        return MemoryRetentionReceipt(
            bank_id=bank_id,
            document_id=document.document_id,
            content_sha256=hashlib.sha256(document.content.encode("utf-8")).hexdigest(),
            disposition="not_selected" if not_selected else "retained",
            selected_fact_count=selected_count,
            operation_id=operation_id,
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

    def _wait_for_operation(
        self, bank_id: str, operation_id: str, *, retry_existing_failure: bool = False
    ) -> dict[str, Any]:
        deadline = time.monotonic() + self._config.timeout_seconds
        first_poll = True
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
                return response
            if status == "failed" and first_poll and retry_existing_failure:
                # Reposting an idempotent retain only replays its failed operation.
                # Resume that exact operation once; never revive cancellations or
                # restart work already observed pending/processing in this attempt.
                retry_status, retry = self._request(
                    "POST",
                    f"/v1/default/banks/{bank_id}/operations/{operation_id}/retry",
                    allowed_statuses=frozenset({409}),
                )
                if retry_status != 409 and (
                    retry.get("success") is not True
                    or retry.get("operation_id") != operation_id
                ):
                    raise HindsightProjectionProtocolError(
                        "Hindsight did not acknowledge the exact operation retry"
                    )
                first_poll = False
                continue
            first_poll = False
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
        private_principals = self._private_recall_principals(query)
        group_recall = query.audience.kind == "telegram_group"
        household_recall = query.audience.kind == "household"
        if household_recall:
            if not self._household_admitted(query.audience):
                return self._empty_presence(query)
            assert query.audience.household_id is not None
            audience_tag = self._household_tag(query.audience.household_id)
        elif group_recall:
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
            audience_tag = (tuple(self._person_tag(p) for p in private_principals)
                            if len(private_principals) > 1
                            else self._person_tag(query.principal_id))
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
        variants = self._recall_query_variants(
            query.query_text, query_purpose=query.query_purpose
        )
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

        # Native reranker scores are query-relative, not calibrated relevance
        # probabilities. A conversational paraphrase can rank the correct memory
        # first while every score falls below our normal noise floor. Preserve
        # only that best candidate in this case; source, audience and privacy
        # checks below still apply, and the reply model decides whether to use it.
        # An observation without source authority is discarded below. It
        # cannot occupy the one low-score fallback and suppress an admissible
        # source fact. All existing scope, time and privacy checks still apply.
        best_candidate_id = next(
            (item["id"] for item in results
             if (isinstance(item.get("_eva_source_metadata"), dict)
                 if item.get("type") == "observation"
                 else not (group_recall or household_recall) or bool(item.get("metadata")))),
            None,
        )

        selected: list[RecalledFact] = []
        confirmed: list[ConfirmedFact] = []
        excluded_reasons: dict[str, str] = {}
        seen: set[str] = set()
        admitted_text_chars = 0
        admitted_text_char_limit = self._config.recall_max_tokens * 4
        for raw in results:
            memory_id = raw.get("id")
            text = raw.get("text")
            memory_type = raw.get("type") or "world"
            source_metadata = raw.get("_eva_source_metadata")
            if memory_type == "observation" and not isinstance(source_metadata, dict):
                continue
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
            if (group_recall or household_recall) and not metadata:
                # Some historical source facts have no authority metadata at all.
                # They are unavailable memory, not evidence for another audience.
                # Never infer their owner from a retrieval tag or let them occupy
                # the low-score fallback ahead of a verifiable source. Explicit
                # contradictory/partial authority still fails closed below.
                excluded_reasons[f"hindsight:{memory_id}"] = "missing_shared_source_authority"
                continue
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
            if final_score < score_floor and (
                memory_id != best_candidate_id or final_score == 0
            ):
                continue
            fact_id = f"hindsight:{memory_id}"
            if (not group_recall and not household_recall
                    and len(private_principals) > 1
                    and not metadata.get("principal_id")
                    and metadata.get("audience_kind") in {None, "private_person"}
                    and metadata.get("audience_id") in {None, query.audience.audience_id}):
                # Linked private banks require explicit source authority. An
                # unproven legacy candidate is unavailable memory, not a reason
                # to prevent the entire conversation. Never infer its owner.
                excluded_reasons[fact_id] = "missing_private_source_authority"
                continue
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
            elif household_recall:
                assert query.audience.household_id is not None
                required = {"audience_id": query.audience.audience_id,
                            "audience_kind": "household",
                            "household_id": query.audience.household_id}
                if any(metadata.get(key) != value for key, value in required.items()):
                    raise HindsightMemoryScopeError("Hindsight returned memory for another household audience")
                conversation = metadata.get("conversation_id")
                prefix = f"household:{query.audience.household_id}:conversation:"
                if (not isinstance(conversation, str) or not conversation.startswith(prefix)
                        or not conversation.removeprefix(prefix)
                        or not all(isinstance(metadata.get(key), str)
                                   and metadata[key].startswith(conversation + ":")
                                   for key in ("source_ref", "evidence_ref"))):
                    raise HindsightMemoryScopeError("Hindsight household memory has cross-scope provenance")
                try:
                    retained = HouseholdConsentReference(
                        principal_id=subject_id, household_id=query.audience.household_id,
                        operation_id=metadata.get("consent_operation_id"),
                    )
                except ValueError as error:
                    raise HindsightMemoryScopeError("Hindsight household memory lacks exact mandate") from error
                if self._resolve_active_household_reference(
                    principal_id=retained.principal_id, household_id=retained.household_id,
                ) != retained:
                    continue
            elif metadata.get("audience_kind") not in {None, "private_person"}:
                raise HindsightMemoryScopeError("Hindsight returned shared memory for a private audience")
            elif subject_id not in {*query.subject_ids, *private_principals}:
                raise HindsightMemoryScopeError(
                    "Hindsight returned memory for another subject"
                )
            if not group_recall and not household_recall and len(private_principals) > 1:
                # Original identities and exact private Telegram provenance stay intact.
                if (metadata.get("principal_id") not in private_principals
                        or metadata.get("audience_id") not in {None, f"person:{subject_id}"}
                        or (subject_id.startswith("telegram:")
                            and metadata.get("conversation_id") != subject_id)):
                    raise HindsightMemoryScopeError("private alias source authority differs")
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
                if memory_type == "observation"
                else metadata.get("trust", "host_policy")
            )
            if trust not in {
                "direct_user",
                "verified_external",
                "host_policy",
                "model_inference_unconfirmed",
                "legacy_unverified",
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
            original_references = {"source_ref": source_ref, "evidence_ref": evidence_ref}
            source_ref = _bounded_memory_reference(source_ref)
            evidence_ref = _bounded_memory_reference(evidence_ref)
            reference_provenance = {
                key: original for key, original in original_references.items()
                if original != {"source_ref": source_ref, "evidence_ref": evidence_ref}[key]
            }
            observation_refs = raw.get("_eva_observation_source_refs", []) if memory_type == "observation" else []
            rank = len(selected) + 1
            selected.append(
                RecalledFact(
                    rank=rank,
                    fact_id=fact_id,
                    source_event_ids=list(dict.fromkeys(_bounded_memory_reference(item["source_ref"])
                        for item in observation_refs)) if observation_refs else [source_ref],
                    trust=(
                        "confirmed_direct_user"
                        if trust == "direct_user"
                        else (
                            "verified_external"
                            if trust == "verified_external"
                            else (
                                "legacy_unverified"
                                if trust == "legacy_unverified"
                                else "model_inference_unconfirmed"
                            )
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
                        # Event time and recall time are not the date on which
                        # the person made a relative-date statement.
                        "source_observed_at": (
                            _canonical_timestamp(raw["mentioned_at"])
                            if raw.get("mentioned_at") else None
                        ),
                        "text": text,
                        **({"mentioned_names": [name for name in (raw["entities"] if isinstance(raw.get("entities"), list) else [])
                            if isinstance(name, str) and 0 < len(name) <= 120][:32]}
                           if query.query_purpose == "owner_memory_view" else {}),
                        **({"observation_source_refs": observation_refs} if observation_refs else {}),
                        **({"reference_provenance": reference_provenance}
                           if reference_provenance else {}),
                    },
                    source_ref=source_ref,
                    evidence_ref=evidence_ref,
                    observed_at=observed_at,
                    trust=(
                        "host_policy"
                        if trust in {"model_inference_unconfirmed", "legacy_unverified"}
                        else trust
                    ),
                )
            )
            if len(confirmed) >= query.maximum_facts:
                break

        if household_recall and not self._household_admitted(query.audience):
            return self._empty_presence(query)
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
            excluded_reasons=excluded_reasons,
            projection_version=_PROJECTION_VERSION,
            scope_sha256=canonical_sha256(query.scope),
            created_at=query.requested_valid_at,
        )
        memory = PresenceMemory(
            recall=recall,
            confirmed_facts=tuple(confirmed),
            style_mirror=(
                None
                if group_recall or household_recall
                else self._recall_style_mirror(global_bank_id, query.principal_id)
            ),
        )

        if private_principals != self._private_recall_principals(query):
            return self._empty_presence(query)
        # Preserve retrieval-versus-selection evidence without copying memory
        # text, queries, principals, source references or provider diagnostics.
        try:
            _LOGGER.info("memory_recall_selection %s", json.dumps({
                "recall_sha256": canonical_sha256(query.recall_id),
                "scope_sha256": canonical_sha256({
                    "tenant": query.tenant_id, "installation": query.installation_id,
                    "principal": query.principal_id, "audience": query.audience.audience_id,
                    "scope": query.scope,
                }),
                "query_sha256": canonical_sha256(query.query_text),
                "candidates": [{
                    "fact_sha256": canonical_sha256("hindsight:" + item["id"]),
                    "memory_type": (item.get("type") if isinstance(item.get("type"), str) and item.get("type") in
                                    {"world", "experience", "observation"} else "unknown"),
                    "final_score": item["scores"]["final"],
                    "source_metadata_present": isinstance(
                        item.get("_eva_source_metadata") if item.get("type") == "observation"
                        else item.get("metadata"), dict),
                } for item in results],
                "selected": [canonical_sha256(fact_id) for fact_id in fact_ids],
                "explicit_exclusions": {
                    canonical_sha256(fact_id): reason
                    for fact_id, reason in excluded_reasons.items()
                },
            }, separators=(",", ":")))
        except Exception:
            # Diagnostics must never change a successful recall or expose errors.
            pass
        return self._with_private_sources(query, memory)

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
