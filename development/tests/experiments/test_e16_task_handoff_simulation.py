"""Step 1 blueprint simulation only; NOT the installed Eva task handler.

No Eva imports, PostgreSQL, files, network, models, workers or retry scheduler.
The fake journal serializes admission and can be copied across handler restarts.
This illustrates a proposed effect state; it does not prove production SQL locks,
provider idempotency, source authority, STOP or E1.6 live acceptance.

Not executed when authored. Run only after the owner's explicit Go.
"""
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
from threading import Lock
from typing import Literal

import pytest


Status = Literal["blocked", "in_flight", "confirmed", "unknown"]


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"))
                  .encode()).hexdigest()


@dataclass(frozen=True)
class Intent:
    tenant_id: str
    installation_id: str
    principal_id: str
    intent_id: str
    source_event_id: str
    source_revision: str
    action: str
    target: str
    content_sha256: str

    @property
    def effect_key(self):
        # Transport callback IDs and reconnect counters are intentionally absent.
        # The revision must identify an actual source version, not a retry number.
        return digest({name: getattr(self, name) for name in (
            "tenant_id", "installation_id", "principal_id", "intent_id",
            "source_event_id", "source_revision", "action", "target")})


@dataclass(frozen=True)
class EffectState:
    effect_key: str
    effect_status: Status
    verification_ref: str | None
    content_sha256: str
    blocked_reason: str | None = None


class FakeJournal:
    """In-memory stand-in for an atomic journal, never a PostgreSQL substitute."""

    def __init__(self, saved=None):
        self.rows = deepcopy(saved) if saved is not None else {}
        self.lock = Lock()

    def checkpoint(self):
        with self.lock:
            return deepcopy(self.rows)


class FakeProvider:
    def __init__(self, outcome="confirmed"):
        self.outcome = outcome
        self.calls = 0
        self.effects = []
        self.receipts = {}

    def perform(self, intent):
        self.calls += 1
        if self.outcome == "timeout_before_effect":
            raise TimeoutError("request outcome not observable by caller")
        self.effects.append(intent.effect_key)
        if self.outcome == "timeout_after_effect":
            raise TimeoutError("effect happened but response was lost")
        ref = "fake-provider-receipt:" + intent.effect_key
        self.receipts[ref] = (intent.effect_key, intent.content_sha256)
        return ref

    def verifies(self, ref, intent):
        # Authoritative readback is simulated; generated prose is not a receipt.
        return self.receipts.get(ref) == (intent.effect_key, intent.content_sha256)


class SimulatedTaskHandler:
    def __init__(self, journal, provider):
        self.journal, self.provider = journal, provider

    def on_callback(self, intent, *, delivery_id, missing_assets=()):
        if not delivery_id:
            raise ValueError("transport delivery identity is required")
        key = intent.effect_key
        with self.journal.lock:
            prior = self.journal.rows.get(key)
            if prior is not None:
                if prior.content_sha256 != intent.content_sha256:
                    raise ValueError("same logical effect has changed content")
                # A duplicate is a status read in ALL states, including blocked.
                # Adding assets or reconnecting never grants an automatic retry.
                return prior
            if missing_assets:
                blocked = EffectState(key, "blocked", None,
                                      intent.content_sha256, "missing_assets")
                self.journal.rows[key] = blocked
                return blocked
            # Record before crossing the external boundary. A crash after this
            # checkpoint leaves in_flight; a new callback must not dispatch again.
            self.journal.rows[key] = EffectState(
                key, "in_flight", None, intent.content_sha256)

        try:
            ref = self.provider.perform(intent)
            if self.provider.verifies(ref, intent):
                result = EffectState(key, "confirmed", ref, intent.content_sha256)
            else:
                result = EffectState(key, "unknown", None,
                                     intent.content_sha256, "unverified_response")
        except Exception:
            # After dispatch, timeout or any other unproven failure is uncertain.
            # Do not claim failure-before-effect or grant retry from an exception.
            result = EffectState(key, "unknown", None,
                                 intent.content_sha256, "external_outcome_unknown")

        with self.journal.lock:
            self.journal.rows[key] = result
        return result


@pytest.fixture
def intent():
    return Intent(
        tenant_id="example-tenant", installation_id="example-installation",
        principal_id="example-owner", intent_id="example-intent",
        source_event_id="example-source-event", source_revision="source-version-1",
        action="save_document", target="example-document",
        content_sha256=digest("example authorized content"))


def restored_handler(journal, provider):
    # Simulated restart retains the journal, but no handler-local deduplication.
    return SimulatedTaskHandler(FakeJournal(journal.checkpoint()), provider)


def test_missing_assets_block_before_dispatch_and_reconnect_does_not_retry(intent):
    journal, provider = FakeJournal(), FakeProvider()
    handler = SimulatedTaskHandler(journal, provider)
    state = handler.on_callback(
        intent, delivery_id="delivery-1", missing_assets=("required-input.txt",))
    assert state.effect_key == intent.effect_key
    assert state.effect_status == "blocked"
    assert state.blocked_reason == "missing_assets"
    assert state.verification_ref is None
    # Even if the asset later becomes available, a callback is not an explicit,
    # currently-authorized blocked-task resumption. That belongs to Step 2.
    replay = restored_handler(journal, provider).on_callback(
        intent, delivery_id="delivery-after-reconnect", missing_assets=())
    assert replay == state
    assert provider.calls == 0
    assert provider.effects == []


def test_duplicate_source_callback_retains_confirmed_effect_and_receipt(intent):
    journal, provider = FakeJournal(), FakeProvider()
    handler = SimulatedTaskHandler(journal, provider)
    state = handler.on_callback(intent, delivery_id="delivery-1")
    assert state.effect_status == "confirmed"
    assert state.verification_ref is not None
    replay = restored_handler(journal, provider).on_callback(
        intent, delivery_id="different-transport-id-after-reconnect")
    assert replay == state
    assert provider.calls == 1
    assert provider.effects == [intent.effect_key]


@pytest.mark.parametrize("outcome", ["timeout_before_effect", "timeout_after_effect"])
def test_timeout_is_unknown_and_reconnect_never_redispatches(intent, outcome):
    journal, provider = FakeJournal(), FakeProvider(outcome)
    state = SimulatedTaskHandler(journal, provider).on_callback(
        intent, delivery_id="delivery-1")
    assert state.effect_status == "unknown"
    assert state.verification_ref is None
    replay = restored_handler(journal, provider).on_callback(
        intent, delivery_id="delivery-after-timeout-and-reconnect")
    assert replay == state
    assert provider.calls == 1
    # Same caller-visible state whether the external action happened or not.
    assert provider.effects == ([intent.effect_key]
                                if outcome == "timeout_after_effect" else [])


def test_restart_with_in_flight_record_does_not_assume_failure_or_redispatch(intent):
    journal, provider = FakeJournal(), FakeProvider()
    journal.rows[intent.effect_key] = EffectState(
        intent.effect_key, "in_flight", None, intent.content_sha256)
    state = restored_handler(journal, provider).on_callback(
        intent, delivery_id="delivery-after-crash")
    assert state.effect_status == "in_flight"
    assert state.verification_ref is None
    assert provider.calls == 0
