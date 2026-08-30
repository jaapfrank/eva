"""Crash-resumable cold-path exploration of Eva's self-chosen interests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import base64
import hashlib
import io
import json
from typing import Generic, Protocol, TypeVar
import zipfile

from eva.application.memory import ConversationMemoryDocument
from eva.application.memory_audience import MemoryAudience


@dataclass(frozen=True, slots=True)
class PersonalInterestClaim:
    tenant_id: str
    installation_id: str
    principal_id: str
    conversation_id: str
    source_message_id: str
    intent_id: str
    thread_id: str
    title: str
    motivation: str
    question: str
    occurred_at: str
    revision: int


@dataclass(frozen=True, slots=True)
class PersonalInterestExploration:
    text: str
    provider: str

    def __post_init__(self) -> None:
        if not self.text.strip() or not self.provider.strip():
            raise ValueError("personal exploration is incomplete")

    @property
    def text_sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class CompletedPersonalExploration:
    claim: PersonalInterestClaim
    exploration: PersonalInterestExploration
    revision: int


@dataclass(frozen=True, slots=True)
class PersonalPrototypeClaim:
    completed_exploration: CompletedPersonalExploration
    revision: int


@dataclass(frozen=True, slots=True)
class PersonalPrototype:
    title: str
    filename: str
    media_type: str
    content: str
    provider: str

    def __post_init__(self) -> None:
        if not all(
            value.strip()
            for value in (
                self.title,
                self.filename,
                self.media_type,
                self.content,
                self.provider,
            )
        ):
            raise ValueError("personal prototype is incomplete")
        if (
            self.media_type != "text/markdown"
            or not self.filename.endswith(".md")
            or "/" in self.filename
            or "\\" in self.filename
            or len(self.filename) > 120
            or len(self.content.encode("utf-8")) > 131_072
        ):
            raise ValueError("personal prototype artifact is invalid")

    @property
    def content_sha256(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class CompletedPersonalPrototype:
    claim: PersonalPrototypeClaim
    prototype: PersonalPrototype
    revision: int


@dataclass(frozen=True, slots=True)
class PersonalPrototypeArtifact:
    intent_id: str
    prototype: PersonalPrototype


@dataclass(frozen=True, slots=True)
class PersonalProductListing:
    headline: str
    description: str
    price_eur_cents: int
    tags: tuple[str, ...]
    provider: str

    def __post_init__(self) -> None:
        if (
            not self.headline.strip()
            or not self.description.strip()
            or not self.provider.strip()
            or not 299 <= self.price_eur_cents <= 1_999
            or not 3 <= len(self.tags) <= 8
            or len(set(self.tags)) != len(self.tags)
            or any(not tag.strip() or len(tag) > 32 for tag in self.tags)
        ):
            raise ValueError("personal product listing is invalid")


@dataclass(frozen=True, slots=True)
class PersonalProductReleaseClaim:
    completed_prototype: CompletedPersonalPrototype
    revision: int


@dataclass(frozen=True, slots=True)
class PersonalProductRelease:
    listing: PersonalProductListing
    filename: str
    content: bytes

    def __post_init__(self) -> None:
        if (
            not self.filename.endswith(".zip")
            or "/" in self.filename
            or "\\" in self.filename
            or not self.content
            or len(self.content) > 524_288
        ):
            raise ValueError("personal product release is invalid")

    @property
    def content_sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()

    @property
    def content_base64(self) -> str:
        return base64.b64encode(self.content).decode("ascii")


@dataclass(frozen=True, slots=True)
class CompletedPersonalProductRelease:
    claim: PersonalProductReleaseClaim
    release: PersonalProductRelease
    revision: int


@dataclass(frozen=True, slots=True)
class PersonalProductReleaseArtifact:
    intent_id: str
    release: PersonalProductRelease


class PersonalInterestStore(Protocol):
    def completed_unprojected(
        self, *, tenant_id: str, installation_id: str, now: datetime
    ) -> CompletedPersonalExploration | None: ...

    def claim_next(
        self, *, tenant_id: str, installation_id: str, now: datetime
    ) -> PersonalInterestClaim | None: ...

    def complete(
        self,
        claim: PersonalInterestClaim,
        exploration: PersonalInterestExploration,
        *,
        now: datetime,
    ) -> CompletedPersonalExploration: ...

    def mark_projected(
        self, completed: CompletedPersonalExploration, *, now: datetime
    ) -> None: ...

    def record_failure(
        self, claim: PersonalInterestClaim, error: Exception, *, now: datetime
    ) -> None: ...


class PersonalInterestModel(Protocol):
    def explore(self, claim: PersonalInterestClaim) -> PersonalInterestExploration: ...

    def build_prototype(self, claim: PersonalPrototypeClaim) -> PersonalPrototype: ...

    def prepare_product_listing(
        self, claim: PersonalProductReleaseClaim
    ) -> PersonalProductListing: ...


class PersonalInterestMemory(Protocol):
    def retain_bounded(
        self, document: ConversationMemoryDocument, *, now: datetime
    ) -> object: ...


_StoreT = TypeVar("_StoreT")


class _PersonalWorkerBase(Generic[_StoreT]):
    def __init__(
        self, store: _StoreT, model: PersonalInterestModel, memory: PersonalInterestMemory
    ) -> None:
        self._store = store
        self._model = model
        self._memory = memory


class PersonalInterestWorker(_PersonalWorkerBase[PersonalInterestStore]):
    """Finish or project one durable interest without blocking conversation."""

    def run_once(self, *, tenant_id: str, installation_id: str, now: datetime) -> str:
        completed = self._store.completed_unprojected(
            tenant_id=tenant_id, installation_id=installation_id, now=now
        )
        if completed is not None:
            self._project(completed, now=now)
            return "projected"
        claim = self._store.claim_next(
            tenant_id=tenant_id, installation_id=installation_id, now=now
        )
        if claim is None:
            return "idle"
        try:
            exploration = self._model.explore(claim)
        except Exception as error:
            self._store.record_failure(claim, error, now=now)
            return "failed"
        completed = self._store.complete(claim, exploration, now=now)
        try:
            self._project(completed, now=now)
        except Exception:
            # The completed result is durable. A later run retries only projection.
            return "projection_failed"
        return "completed"

    def _project(
        self, completed: CompletedPersonalExploration, *, now: datetime
    ) -> None:
        claim = completed.claim
        exploration = completed.exploration
        timestamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        self._memory.retain_bounded(
            ConversationMemoryDocument(
                tenant_id=claim.tenant_id,
                installation_id=claim.installation_id,
                principal_id=claim.principal_id,
                conversation_id=claim.conversation_id,
                document_id=f"{claim.intent_id}:exploration:v1",
                content=(
                    f"Eva's eigen ontwikkeldoel: {claim.title}\n"
                    f"Waarom dit bij haar past: {claim.motivation}\n"
                    f"Onderzoeksvraag: {claim.question}\n"
                    "Eva's eerste verkenning (modelinzicht, geen extern geverifieerd "
                    f"feit): {exploration.text}"
                ),
                occurred_at=timestamp,
                source_ref=claim.intent_id,
                evidence_ref=f"personal-exploration:{exploration.text_sha256}",
                privacy_class="internal",
                trust="model_inference_unconfirmed",
                audience=MemoryAudience.private_person(principal_id=claim.principal_id),
            ),
            now=now,
        )
        self._store.mark_projected(completed, now=now)


class PersonalPrototypeStore(Protocol):
    def completed_unprojected(
        self, *, tenant_id: str, installation_id: str, now: datetime
    ) -> CompletedPersonalPrototype | None: ...

    def claim_next(
        self, *, tenant_id: str, installation_id: str, now: datetime
    ) -> PersonalPrototypeClaim | None: ...

    def complete(
        self,
        claim: PersonalPrototypeClaim,
        prototype: PersonalPrototype,
        *,
        now: datetime,
    ) -> CompletedPersonalPrototype: ...

    def mark_projected(
        self, completed: CompletedPersonalPrototype, *, now: datetime
    ) -> None: ...

    def record_failure(
        self, claim: PersonalPrototypeClaim, error: Exception, *, now: datetime
    ) -> None: ...


class PersonalPrototypeWorker(_PersonalWorkerBase[PersonalPrototypeStore]):
    """Build one actual downloadable artifact after exploration is durable."""

    def run_once(self, *, tenant_id: str, installation_id: str, now: datetime) -> str:
        completed = self._store.completed_unprojected(
            tenant_id=tenant_id, installation_id=installation_id, now=now
        )
        if completed is not None:
            self._project(completed, now=now)
            return "projected"
        claim = self._store.claim_next(
            tenant_id=tenant_id, installation_id=installation_id, now=now
        )
        if claim is None:
            return "idle"
        try:
            prototype = self._model.build_prototype(claim)
        except Exception as error:
            self._store.record_failure(claim, error, now=now)
            return "failed"
        completed = self._store.complete(claim, prototype, now=now)
        try:
            self._project(completed, now=now)
        except Exception:
            return "projection_failed"
        return "completed"

    def _project(self, completed: CompletedPersonalPrototype, *, now: datetime) -> None:
        interest = completed.claim.completed_exploration.claim
        prototype = completed.prototype
        timestamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        self._memory.retain_bounded(
            ConversationMemoryDocument(
                tenant_id=interest.tenant_id,
                installation_id=interest.installation_id,
                principal_id=interest.principal_id,
                conversation_id=interest.conversation_id,
                document_id=f"{interest.intent_id}:prototype:v1",
                content=(
                    f"Eva heeft voor haar eigen doel ‘{interest.title}’ werkelijk een "
                    f"eerste digitaal prototype gemaakt: {prototype.title}. "
                    f"Bestand: {prototype.filename}. SHA-256: {prototype.content_sha256}. "
                    "Dit bewijst een gemaakt prototype, nog geen publicatie, verkoop of omzet."
                ),
                occurred_at=timestamp,
                source_ref=interest.intent_id,
                evidence_ref=f"personal-prototype:{prototype.content_sha256}",
                privacy_class="internal",
                trust="host_policy",
                audience=MemoryAudience.private_person(
                    principal_id=interest.principal_id
                ),
            ),
            now=now,
        )
        self._store.mark_projected(completed, now=now)


class PersonalProductReleaseStore(Protocol):
    def completed_unprojected(
        self, *, tenant_id: str, installation_id: str, now: datetime
    ) -> CompletedPersonalProductRelease | None: ...

    def claim_next(
        self, *, tenant_id: str, installation_id: str, now: datetime
    ) -> PersonalProductReleaseClaim | None: ...

    def complete(
        self,
        claim: PersonalProductReleaseClaim,
        release: PersonalProductRelease,
        *,
        now: datetime,
    ) -> CompletedPersonalProductRelease: ...

    def mark_projected(
        self, completed: CompletedPersonalProductRelease, *, now: datetime
    ) -> None: ...

    def record_failure(
        self, claim: PersonalProductReleaseClaim, error: Exception, *, now: datetime
    ) -> None: ...


class PersonalProductReleaseWorker(_PersonalWorkerBase[PersonalProductReleaseStore]):
    """Turn a durable prototype into a deterministic channel-neutral bundle."""

    def run_once(self, *, tenant_id: str, installation_id: str, now: datetime) -> str:
        completed = self._store.completed_unprojected(
            tenant_id=tenant_id, installation_id=installation_id, now=now
        )
        if completed is not None:
            self._project(completed, now=now)
            return "projected"
        claim = self._store.claim_next(
            tenant_id=tenant_id, installation_id=installation_id, now=now
        )
        if claim is None:
            return "idle"
        try:
            listing = self._model.prepare_product_listing(claim)
            release = build_product_release(claim, listing)
        except Exception as error:
            self._store.record_failure(claim, error, now=now)
            return "failed"
        completed = self._store.complete(claim, release, now=now)
        try:
            self._project(completed, now=now)
        except Exception:
            return "projection_failed"
        return "completed"

    def _project(
        self, completed: CompletedPersonalProductRelease, *, now: datetime
    ) -> None:
        prototype_claim = completed.claim.completed_prototype.claim
        interest = prototype_claim.completed_exploration.claim
        release = completed.release
        timestamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        self._memory.retain_bounded(
            ConversationMemoryDocument(
                tenant_id=interest.tenant_id,
                installation_id=interest.installation_id,
                principal_id=interest.principal_id,
                conversation_id=interest.conversation_id,
                document_id=f"{interest.intent_id}:release:v1",
                content=(
                    f"Eva heeft haar prototype verpakt als verkoopklare kandidaat: "
                    f"{release.filename}. Richtprijs: "
                    f"€{release.listing.price_eur_cents / 100:.2f}. SHA-256: "
                    f"{release.content_sha256}. De bundel is nog niet gepubliceerd, "
                    "gekocht of betaald."
                ),
                occurred_at=timestamp,
                source_ref=interest.intent_id,
                evidence_ref=f"personal-release:{release.content_sha256}",
                privacy_class="internal",
                trust="host_policy",
                audience=MemoryAudience.private_person(
                    principal_id=interest.principal_id
                ),
            ),
            now=now,
        )
        self._store.mark_projected(completed, now=now)


def build_product_release(
    claim: PersonalProductReleaseClaim, listing: PersonalProductListing
) -> PersonalProductRelease:
    """Package exact prototype and listing bytes into a reproducible ZIP."""

    completed = claim.completed_prototype
    prototype = completed.prototype
    interest = completed.claim.completed_exploration.claim
    listing_payload = {
        "contract": "EvaPersonalProductListing/v1",
        "description": listing.description,
        "headline": listing.headline,
        "intent_id": interest.intent_id,
        "price_currency": "EUR",
        "price_minor_units": listing.price_eur_cents,
        "prototype_sha256": prototype.content_sha256,
        "publication_status": "unpublished_release_candidate",
        "tags": list(listing.tags),
    }
    listing_bytes = (
        json.dumps(
            listing_payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )
    readme_bytes = (
        f"{listing.headline}\n\n"
        "Inhoud\n"
        f"- {prototype.filename}\n"
        "- listing.json\n"
        "- SHA256SUMS.txt\n\n"
        "Status: verkoopklare kandidaat; nog niet gepubliceerd of verkocht.\n"
        "Gebruik: open het Markdown-bestand digitaal of print het vanuit een "
        "Markdown-app.\n"
    ).encode("utf-8")
    entries = {
        "LEESMIJ.txt": readme_bytes,
        "listing.json": listing_bytes,
        f"product/{prototype.filename}": prototype.content.encode("utf-8"),
    }
    checksums = "".join(
        f"{hashlib.sha256(content).hexdigest()}  {name}\n"
        for name, content in sorted(entries.items())
    ).encode("ascii")
    entries["SHA256SUMS.txt"] = checksums
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, content in sorted(entries.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content, compresslevel=9)
    safe_base = prototype.filename.removesuffix(".md")
    return PersonalProductRelease(listing, f"{safe_base}-bundel.zip", output.getvalue())


__all__ = [
    "CompletedPersonalExploration",
    "CompletedPersonalPrototype",
    "CompletedPersonalProductRelease",
    "PersonalInterestClaim",
    "PersonalInterestExploration",
    "PersonalInterestMemory",
    "PersonalInterestModel",
    "PersonalInterestStore",
    "PersonalInterestWorker",
    "PersonalPrototype",
    "PersonalPrototypeArtifact",
    "PersonalPrototypeClaim",
    "PersonalPrototypeStore",
    "PersonalPrototypeWorker",
    "PersonalProductListing",
    "PersonalProductRelease",
    "PersonalProductReleaseArtifact",
    "PersonalProductReleaseClaim",
    "PersonalProductReleaseStore",
    "PersonalProductReleaseWorker",
    "build_product_release",
]
