from __future__ import annotations

from datetime import UTC, datetime
import io
import json
import zipfile

from eva.application.personal_interests import (
    CompletedPersonalExploration,
    CompletedPersonalProductRelease,
    CompletedPersonalPrototype,
    PersonalInterestClaim,
    PersonalInterestExploration,
    PersonalInterestWorker,
    PersonalProductListing,
    PersonalProductReleaseClaim,
    PersonalProductReleaseWorker,
    PersonalPrototype,
    PersonalPrototypeClaim,
    PersonalPrototypeWorker,
)


NOW = datetime(2026, 8, 23, 14, 0, tzinfo=UTC)


def _claim() -> PersonalInterestClaim:
    return PersonalInterestClaim(
        tenant_id="tenant",
        installation_id="installation",
        principal_id="jaap",
        conversation_id="telegram:jaap",
        source_message_id="telegram:1",
        intent_id="personal-interest:income",
        thread_id="intent:personal-interest:income",
        title="Eigen inkomsten ontwikkelen",
        motivation="Ik wil zelf financieel bijdragen.",
        question="Welke kleine betaalde dienst past bij mijn sterke kanten?",
        occurred_at="2026-08-23T13:00:00Z",
        revision=2,
    )


def test_exploration_is_committed_before_hindsight_projection() -> None:
    order: list[str] = []
    documents = []
    claim = _claim()
    result = PersonalInterestExploration(
        "Ik toets eerst een compacte maaltijdhulp bij één afgebakende doelgroep.",
        "model",
    )

    class Store:
        def completed_unprojected(self, **kwargs):  # type: ignore[no-untyped-def]
            return None

        def claim_next(self, **kwargs):  # type: ignore[no-untyped-def]
            order.append("claimed")
            return claim

        def complete(self, claimed, exploration, **kwargs):  # type: ignore[no-untyped-def]
            assert claimed == claim and exploration == result
            order.append("committed")
            return CompletedPersonalExploration(claim, result, 3)

        def mark_projected(self, completed, **kwargs):  # type: ignore[no-untyped-def]
            order.append("projected")

        def record_failure(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("happy exploration must not fail")

    class Model:
        def explore(self, claimed):  # type: ignore[no-untyped-def]
            assert claimed == claim
            order.append("model")
            return result

    class Memory:
        def retain_conversation(self, document):  # type: ignore[no-untyped-def]
            order.append("retained")
            documents.append(document)

    worker = PersonalInterestWorker(Store(), Model(), Memory())
    assert (
        worker.run_once(tenant_id="tenant", installation_id="installation", now=NOW)
        == "completed"
    )
    assert order == ["claimed", "model", "committed", "retained", "projected"]
    assert documents[0].trust == "model_inference_unconfirmed"
    assert "geen extern geverifieerd feit" in documents[0].content


def test_crash_after_result_retries_projection_without_second_model_call() -> None:
    claim = _claim()
    result = PersonalInterestExploration("Eerste verkenning.", "model")
    completed = CompletedPersonalExploration(claim, result, 3)

    class Store:
        projected = False

        def completed_unprojected(self, **kwargs):  # type: ignore[no-untyped-def]
            return completed

        def claim_next(self, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("must resume the completed result")

        def complete(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("must not complete twice")

        def mark_projected(self, value, **kwargs):  # type: ignore[no-untyped-def]
            assert value == completed
            self.projected = True

        def record_failure(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("must not rewrite the call failure state")

    class Model:
        def explore(self, claimed):  # type: ignore[no-untyped-def]
            raise AssertionError("model must not run twice")

    class Memory:
        def retain_conversation(self, document):  # type: ignore[no-untyped-def]
            return None

    store = Store()
    worker = PersonalInterestWorker(store, Model(), Memory())
    assert (
        worker.run_once(tenant_id="tenant", installation_id="installation", now=NOW)
        == "projected"
    )
    assert store.projected is True


def test_prototype_bytes_are_committed_before_memory_claims_they_exist() -> None:
    order: list[str] = []
    exploration = PersonalInterestExploration("Maak een weekmenuplanner.", "model")
    completed_exploration = CompletedPersonalExploration(_claim(), exploration, 4)
    claim = PersonalPrototypeClaim(completed_exploration, 5)
    prototype = PersonalPrototype(
        "Rustig weekmenu",
        "rustig-weekmenu.md",
        "text/markdown",
        "# Rustig weekmenu\n\nMaandag: ______\n",
        "model",
    )

    class Store:
        def completed_unprojected(self, **kwargs):  # type: ignore[no-untyped-def]
            return None

        def claim_next(self, **kwargs):  # type: ignore[no-untyped-def]
            order.append("claimed")
            return claim

        def complete(self, claimed, artifact, **kwargs):  # type: ignore[no-untyped-def]
            assert claimed == claim and artifact == prototype
            order.append("committed")
            return CompletedPersonalPrototype(claim, prototype, 6)

        def mark_projected(self, completed, **kwargs):  # type: ignore[no-untyped-def]
            order.append("projected")

        def record_failure(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("prototype must not fail")

    class Model:
        def build_prototype(self, value):  # type: ignore[no-untyped-def]
            assert value == claim
            order.append("model")
            return prototype

    class Memory:
        def retain_conversation(self, document):  # type: ignore[no-untyped-def]
            assert prototype.content_sha256 in document.content
            order.append("retained")

    worker = PersonalPrototypeWorker(Store(), Model(), Memory())
    assert (
        worker.run_once(tenant_id="tenant", installation_id="installation", now=NOW)
        == "completed"
    )
    assert order == ["claimed", "model", "committed", "retained", "projected"]


def test_release_bundle_is_reproducible_and_committed_before_claim() -> None:
    order: list[str] = []
    completed_exploration = CompletedPersonalExploration(
        _claim(), PersonalInterestExploration("Maak een weekmenuplanner.", "model"), 4
    )
    prototype = PersonalPrototype(
        "Rustig weekmenu",
        "rustig-weekmenu.md",
        "text/markdown",
        "# Rustig weekmenu\n\nMaandag: ______\n",
        "model",
    )
    completed_prototype = CompletedPersonalPrototype(
        PersonalPrototypeClaim(completed_exploration, 5), prototype, 6
    )
    claim = PersonalProductReleaseClaim(completed_prototype, 7)
    listing = PersonalProductListing(
        "Rustig weekmenu voor thuis",
        "Een eenvoudige invulbare weekmenuplanner met ruimte per dag.",
        499,
        ("weekmenu", "maaltijdplanner", "boodschappen"),
        "model",
    )
    committed = []

    class Store:
        def completed_unprojected(self, **kwargs):  # type: ignore[no-untyped-def]
            return None

        def claim_next(self, **kwargs):  # type: ignore[no-untyped-def]
            order.append("claimed")
            return claim

        def complete(self, claimed, release, **kwargs):  # type: ignore[no-untyped-def]
            assert claimed == claim
            order.append("committed")
            committed.append(release)
            return CompletedPersonalProductRelease(claim, release, 8)

        def mark_projected(self, completed, **kwargs):  # type: ignore[no-untyped-def]
            order.append("projected")

        def record_failure(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("release must not fail")

    class Model:
        def prepare_product_listing(self, value):  # type: ignore[no-untyped-def]
            assert value == claim
            order.append("model")
            return listing

    class Memory:
        def retain_conversation(self, document):  # type: ignore[no-untyped-def]
            assert committed[0].content_sha256 in document.content
            assert "nog niet gepubliceerd" in document.content
            order.append("retained")

    worker = PersonalProductReleaseWorker(Store(), Model(), Memory())
    assert (
        worker.run_once(tenant_id="tenant", installation_id="installation", now=NOW)
        == "completed"
    )
    assert order == ["claimed", "model", "committed", "retained", "projected"]
    release = committed[0]
    with zipfile.ZipFile(io.BytesIO(release.content)) as archive:
        assert archive.namelist() == [
            "LEESMIJ.txt",
            "SHA256SUMS.txt",
            "listing.json",
            "product/rustig-weekmenu.md",
        ]
        assert archive.read("product/rustig-weekmenu.md") == prototype.content.encode()
        payload = json.loads(archive.read("listing.json"))
        assert payload["price_minor_units"] == 499
        assert payload["publication_status"] == "unpublished_release_candidate"
