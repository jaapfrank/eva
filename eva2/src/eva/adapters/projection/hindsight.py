"""Hindsight-backed, rebuildable recall projection for Eva memory.

Hindsight ranks candidates; PostgreSQL remains authoritative.  A caller asks
Hindsight for candidate fact IDs before opening the PostgreSQL transaction,
then passes the returned query to :class:`PostgresMemoryRepository`, which
rechecks currentness, scope, validity, privacy, retention and provenance.
"""

from __future__ import annotations

import hashlib
import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from typing import Any, Literal

from eva.application.memory import RecallCurrentFacts, RememberFact
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256


_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_PROJECTION_VERSION = "hindsight-0.9.1-v1"


class HindsightProjectionError(RuntimeError):
    """Base error for the replaceable Hindsight projection."""


class HindsightProjectionUnavailable(HindsightProjectionError):
    """The projection could not be reached before its deadline."""


class HindsightProjectionProtocolError(HindsightProjectionError):
    """The projection returned an invalid or contradictory response."""


@dataclass(frozen=True, slots=True)
class HindsightProjectionConfig:
    base_url: str
    bank_namespace: str = "eva2"
    extraction_mode: Literal["concise", "verbose", "verbatim"] = "concise"
    timeout_seconds: float = 120.0
    maximum_candidates: int = 128

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


@dataclass(frozen=True, slots=True)
class HindsightProjectionReceipt:
    bank_id: str
    fact_id: str
    document_id: str
    disposition: str


@dataclass(frozen=True, slots=True)
class ProjectedRecall:
    query: RecallCurrentFacts
    backend: str
    fallback_reason: str | None = None


class HindsightMemoryProjection:
    """Small HTTP adapter around a self-hosted Hindsight memory bank."""

    def __init__(self, config: HindsightProjectionConfig) -> None:
        self._config = config
        self._base_url = config.base_url.rstrip("/")

    def _bank_id(self, tenant_id: str, installation_id: str) -> str:
        raw = canonical_json_bytes(
            [self._config.bank_namespace, tenant_id, installation_id]
        )
        digest = hashlib.sha256(raw).hexdigest()
        return f"{self._config.bank_namespace}-{digest[:32]}"

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

    def ensure_bank(self, tenant_id: str, installation_id: str) -> str:
        bank_id = self._bank_id(tenant_id, installation_id)
        _, response = self._request(
            "PUT",
            f"/v1/default/banks/{bank_id}",
            {
                "retain_extraction_mode": self._config.extraction_mode,
                "retain_mission": (
                    "Onthoud duurzame voorkeuren, relaties, projecten, correcties en "
                    "ervaringen van Eva. Behoud wie, wat, wanneer en de Nederlandse context."
                ),
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
        return bank_id

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


__all__ = [
    "HindsightMemoryProjection",
    "HindsightProjectionConfig",
    "HindsightProjectionError",
    "HindsightProjectionProtocolError",
    "HindsightProjectionReceipt",
    "HindsightProjectionUnavailable",
    "ProjectedRecall",
]
