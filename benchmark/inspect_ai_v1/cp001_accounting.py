"""Offline native accounting admission. Exports are observations, never authority.

No network, credentials, database discovery or model execution. Current native
interfaces cannot attest complete sample accounting or server-wide limits.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import inspect
import json
import threading
from contextlib import contextmanager
from pathlib import Path

PARTICIPANTS = ("eva2", "eva1-bottie")
AGGREGATE_PARTICIPANTS = (*PARTICIPANTS, "openclaw", "hermes", "agent_zero")
USAGE_FIELDS = ("input_tokens", "output_tokens", "model_attempts", "tool_calls")
LIMIT_FIELDS = (
    "model",
    "reasoning_effort",
    "input_tokens",
    "output_tokens",
    "model_attempts",
    "tool_calls",
    "deadline_ms",
)
SOURCES = {
    "eva2": (
        "src/eva/application/conversation.py",
        "src/eva/adapters/postgres/conversation_journal.py",
        "src/eva/adapters/model/pydantic_conversation.py",
        "src/eva/adapters/model/openai_codex_provider.py",
        "src/eva/contracts/runtime_budget.py",
    ),
    "eva1-bottie": (
        "src/brain/llm.js",
        "src/mind/agent.js",
        "src/web/httpingress.js",
        "src/web/server.js",
    ),
}


def obj(value):
    return value if isinstance(value, dict) else {}


def count(value):
    return value if type(value) is int and value >= 0 else None


def name(value):
    return value if isinstance(value, str) and value.strip() else None


def normalize(participant: str, export: dict) -> dict:
    """Read native field names without treating absent values as zero.

    provider_response is a raw response.completed response object, provider_request
    is its wire body. Neither export alone proves request binding/completeness.
    Bottie timeline meta is lossy diagnostic data, not raw measured usage.
    """
    if participant not in PARTICIPANTS or not isinstance(export, dict):
        raise ValueError("invalid participant export")
    response = obj(export.get("provider_response"))
    usage = obj(response.get("usage"))
    raw = {
        k: count(usage.get(k))
        for k in ("input_tokens", "output_tokens", "total_tokens")
    }
    coherent = (
        all(v is not None for v in raw.values())
        and raw["total_tokens"] == raw["input_tokens"] + raw["output_tokens"]
    )
    wire = obj(export.get("provider_request"))
    effort = name(obj(wire.get("reasoning")).get("effort"))
    if effort in ("auto", "default"):
        effort = None
    output_limit = count(wire.get("max_output_tokens"))
    observed = {
        "model": name(wire.get("model")),
        "reasoning_effort": effort,
        "input_tokens": None,
        "output_tokens": output_limit or None,
        "model_attempts": None,
        "tool_calls": None,
        "deadline_ms": None,
    }
    conversation = None
    diagnostic = None
    configured = None
    if participant == "eva2":
        # Reuse the native identity/hash and ConversationModelUsage reader.
        import cp001_native

        body, journal = obj(export.get("request")), obj(export.get("journal_reply"))
        required = ("message_id", "conversation_id", "principal_id")
        if all(name(body.get(k)) for k in required) and name(journal.get("text")):
            readback = cp001_native.independent_readback(
                participant, body, journal["text"], journal
            )
            conversation = readback["conversation_usage"]
            if conversation is not None and (
                conversation["model_attempts"] < 1 or not name(conversation["provider"])
            ):
                conversation = None
        budget = obj(export.get("runtime_resource_budget"))
        configured = {
            k: count(budget.get("max_" + k)) for k in ("input_tokens", "output_tokens")
        }
    else:
        meta = obj(export.get("timeline_meta"))
        diagnostic = {
            k: count(meta.get(v))
            for k, v in {
                "input_tokens": "inputTokens",
                "output_tokens": "outputTokens",
                "model_attempts": "requestAttempts",
            }.items()
        }
    blockers = []
    for field, value in raw.items():
        if value is None:
            blockers.append("provider_usage_missing_or_invalid:" + field)
    if not coherent and all(v is not None for v in raw.values()):
        blockers.append("provider_usage_incoherent")
    for field, value in observed.items():
        if value is None:
            blockers.append("observed_limit_unknown:" + field)
    # These are unsupported native attestations, not caller-set booleans.
    blockers += [
        "export_provenance_and_sample_binding_unverified",
        "complete_helper_memory_retry_accounting_unavailable",
        "server_sample_budget_enforcement_unattested",
        "server_cancellation_and_quiescence_unattested",
    ]
    return {
        "raw_provider_usage": raw,
        "raw_provider_usage_coherent": coherent,
        "conversation_usage": conversation,
        "timeline_diagnostic": diagnostic,
        "configured_resource_tokens": configured,
        "observed_provider_limits": observed,
        "total_usage": {k: None for k in USAGE_FIELDS},
        "server_sample_limits": {k: None for k in LIMIT_FIELDS},
        "money": None,
        "admission": "unknown",
        "blockers": blockers,
    }



class SampleStopped(BaseException):
    """Leave a native retry/repair loop when sample control rejects further work."""


class PersistentAggregateLedger:
    """Comparison-owned durable admission over the original frozen envelopes.

    Reserve each complete sample envelope before dispatch. Reservations remain
    across failure/restart; unused sample allowance never funds another sample.
    Observed usage is separate from these ceilings. This does not impose a
    provider-side output limit or turn missing usage into zero.
    """
    DIMENSIONS = ("providerAttempts", "providerInputTokens", "providerOutputTokens",
                  "wallSeconds", "toolDispatches", "speechRequests")

    def __init__(self, path, plan, binding, participant):
        self.path, self.participant = Path(path), participant
        self.families = {x["family"]: x for x in plan["families"]}
        self.total = plan["aggregateLimits"]
        self.binding = {"binding": binding, "families": plan["families"],
                        "aggregateLimits": self.total}
        self.lock = threading.RLock()
        declared_order = plan.get("executionOrder", {}).get("epochParticipantOrder")
        eligible = PARTICIPANTS if declared_order is None else (
            set(AGGREGATE_PARTICIPANTS).intersection(
                name for epoch in declared_order for name in epoch))
        if participant not in eligible or not binding or not self.path.is_absolute():
            raise ValueError("explicit participant and frozen comparison binding required")
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self._connection() as db:
            db.execute("CREATE TABLE IF NOT EXISTS metadata (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS samples (participant TEXT, sample_id TEXT, family TEXT, "
                       "value TEXT NOT NULL, revision INTEGER NOT NULL, PRIMARY KEY(participant,sample_id))")
            encoded = self._json(self.binding)
            db.execute("INSERT OR IGNORE INTO metadata VALUES (1,?)", (encoded,))
            if db.execute("SELECT value FROM metadata WHERE id=1").fetchone()[0] != encoded:
                raise ValueError("aggregate comparison binding changed")

    @staticmethod
    def _json(value):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))

    @contextmanager
    def _connection(self):
        # Only the host authority uses SQLite. Native IPC workers also import
        # SampleStopped, including images without the SQLite shared library.
        import sqlite3
        db = sqlite3.connect(self.path, timeout=30)
        try:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            with db:
                yield db
        finally:
            db.close()

    def _rows(self, db):
        return [(family, json.loads(value)) for family, value in db.execute(
            "SELECT family,value FROM samples WHERE participant=?", (self.participant,))]

    @staticmethod
    def _unresolved(row):
        return (row.get("usage_unknown") or row.get("envelope_exceeded") or
                (row["kind"] == "active" and any(x["status"] != "completed"
                    for x in row["report"]["attempts"])))

    def _admit(self, db, family, limits):
        expected = self.families[family]["perSampleLimits"]
        if any(limits.get(k) != expected[k] for k in self.DIMENSIONS):
            raise ValueError("sample limits differ from frozen original plan")
        rows = self._rows(db)
        if any(self._unresolved(row) for _, row in rows):
            raise SampleStopped("historical aggregate usage is unknown or exceeded")
        same = [row for name, row in rows if name == family]
        f = self.families[family]
        if (len(same) >= f["cases"] * f["epochs"]
                or len(rows) >= self.total["samplesPerParticipant"]):
            raise SampleStopped("original aggregate sample count reached")
        for selected, cap in ((same, f["aggregateLimitsPerParticipant"]),
                              ([row for _, row in rows], self.total["perParticipant"])):
            if any(sum(row["reservation"][k] for row in selected) + expected[k] > cap[k]
                   for k in self.DIMENSIONS):
                raise SampleStopped("original aggregate envelope reached")

    def seed_history(self, sample_id, family, observed, evidence_sha256):
        """Import independently attested accounting, never answer/oracle content."""
        required = ("provider_attempts", "input_tokens", "output_tokens")
        pre_dispatch_zero = (observed.get("provider_dispatch_not_reached") is True
                             and all(type(observed.get(k)) is int and observed[k] == 0 for k in required)
                             and observed.get("raw_usage_measurement") == "UNKNOWN")
        if (any(count(observed.get(k)) is None for k in required)
                or not (observed.get("provider_usage_complete") is True or pre_dispatch_zero)
                or len(evidence_sha256) != 64):
            raise SampleStopped("historical provider usage needs complete attestation")
        limits = self.families[family]["perSampleLimits"]
        value = {"kind": "historical", "reservation": limits, "observed": observed,
                 "evidence_sha256": evidence_sha256, "usage_unknown": False,
                 "envelope_exceeded": any(observed[k] > limits[cap] for k, cap in
                    zip(required, self.DIMENSIONS[:3]))}
        with self.lock, self._connection() as db:
            prior = db.execute("SELECT family,value FROM samples WHERE participant=? AND sample_id=?",
                               (self.participant, sample_id)).fetchone()
            if prior:
                if prior != (family, self._json(value)):
                    raise ValueError("historical aggregate seed changed")
                return
            self._admit(db, family, limits)
            db.execute("INSERT INTO samples VALUES (?,?,?,?,0)",
                       (self.participant, sample_id, family, self._json(value)))

    def open_sample(self, sample_id, family, limits, profiles):
        identity = {"limits": limits, "profiles": {k: list(v) for k, v in profiles.items()}}
        with self.lock, self._connection() as db:
            prior = db.execute("SELECT family,value,revision FROM samples WHERE participant=? AND sample_id=?",
                               (self.participant, sample_id)).fetchone()
            if prior:
                row = json.loads(prior[1])
                if prior[0] != family or row.get("kind") != "active" or row["identity"] != identity:
                    raise ValueError("aggregate sample identity changed or historical sample reused")
                report = row["report"]
                if any(x["status"] != "completed" for x in report["attempts"]):
                    raise SampleStopped("resumed sample has unresolved provider usage")
                return report, prior[2]
            self._admit(db, family, limits)
            row = {"kind": "active", "reservation": limits, "identity": identity,
                   "report": {"attempts": [], "stop": None, "stop_requested": False},
                   "usage_unknown": False, "envelope_exceeded": False}
            db.execute("INSERT INTO samples VALUES (?,?,?,?,0)",
                       (self.participant, sample_id, family, self._json(row)))
            return row["report"], 0

    def save(self, sample_id, revision, report):
        with self.lock, self._connection() as db:
            prior = db.execute("SELECT value,revision FROM samples WHERE participant=? AND sample_id=?",
                               (self.participant, sample_id)).fetchone()
            if not prior or prior[1] != revision:
                raise SampleStopped("stale aggregate sample writer rejected")
            row = json.loads(prior[0])
            if len(report["attempts"]) > len(row["report"]["attempts"]):
                others = db.execute("SELECT value FROM samples WHERE participant=? AND sample_id<>?",
                                    (self.participant, sample_id))
                if (row["usage_unknown"] or row["envelope_exceeded"]
                        or any(self._unresolved(json.loads(value)) for (value,) in others)):
                    raise SampleStopped("aggregate has unresolved or exceeded historical usage")
            known = [x["usage"] for x in report["attempts"] if x["usage"] is not None]
            row.update(report=report,
                usage_unknown=any(x["status"] not in ("pending", "completed") for x in report["attempts"]),
                envelope_exceeded=(len(report["attempts"]) > row["reservation"]["providerAttempts"]
                    or sum(x["input_tokens"] for x in known) > row["reservation"]["providerInputTokens"]
                    or sum(x["output_tokens"] for x in known) > row["reservation"]["providerOutputTokens"]))
            db.execute("UPDATE samples SET value=?,revision=revision+1 WHERE participant=? AND sample_id=?",
                       (self._json(row), self.participant, sample_id))
            return revision + 1

    def report(self):
        with self.lock, self._connection() as db:
            rows = self._rows(db)
            attempts = inputs = outputs = 0
            for _, row in rows:
                if row["kind"] == "historical":
                    value = row["observed"]
                    attempts += value["provider_attempts"]
                    inputs += value["input_tokens"]; outputs += value["output_tokens"]
                else:
                    values = row["report"]["attempts"]
                    attempts += len(values)
                    inputs += sum(x["usage"]["input_tokens"] for x in values if x["usage"] is not None)
                    outputs += sum(x["usage"]["output_tokens"] for x in values if x["usage"] is not None)
            return {"participant": self.participant, "admitted_samples": len(rows),
                    "reserved_original_envelopes": {k: sum(row["reservation"][k] for _, row in rows)
                                                    for k in self.DIMENSIONS},
                    "known_observed": {"provider_attempts": attempts, "input_tokens": inputs,
                                       "output_tokens": outputs},
                    "historical_pre_dispatch_zero_samples": sum(row["kind"] == "historical" and
                        row["observed"].get("provider_dispatch_not_reached") is True for _, row in rows),
                    "usage_unknown": any(row["usage_unknown"] or (row["kind"] == "active" and
                        any(x["status"] == "pending" for x in row["report"]["attempts"])) for _, row in rows),
                    "envelope_exceeded": any(row["envelope_exceeded"] for _, row in rows),
                    "meaning": "Original ceiling reservations; observations and attested pre-dispatch zero "
                               "are separate from ceilings. Historical raw SDK UNKNOWN remains retained."}


class ProviderSampleLedger:
    """Observe every bound SDK attempt, including helpers and incomplete calls.

    This is a comparison-side control, not a product quota or live admission.
    Completed SDK usage can be checked only after return; do not claim a provider
    output cap. Callers must bind all native provider entrypoints before inference.
    """

    def __init__(self, sample_id, limits, profiles, *, aggregate=None, family=None):
        self.sample_id = sample_id
        self.limits = dict(limits)
        self.profiles = dict(profiles)
        self.attempts = []
        self.stopped = None
        self.async_attempts = {}
        self.stop_requested = False
        self.cancel_errors = []
        self.lock = threading.RLock()
        self.aggregate, self.aggregate_revision = aggregate, None
        if aggregate is not None:
            retained, self.aggregate_revision = aggregate.open_sample(sample_id, family, self.limits, self.profiles)
            self.attempts = retained["attempts"]
            self.stopped = retained["stop"]
            self.stop_requested = retained["stop_requested"]

    def _persist(self):
        if self.aggregate is not None:
            self.aggregate_revision = self.aggregate.save(self.sample_id, self.aggregate_revision,
                {"attempts": [dict(x) for x in self.attempts], "stop": self.stopped,
                 "stop_requested": self.stop_requested})

    def begin(self, role, model, effort):
        with self.lock:
            if self.stopped:
                raise SampleStopped(self.stopped)
            if (model, effort) != self.profiles.get(role):
                self.stopped = "wrong native model/effort or unbound role"
                self._persist()
                raise SampleStopped(self.stopped)
            if len(self.attempts) >= self.limits["providerAttempts"]:
                self.stopped = "declared sample attempts reached"
                self._persist()
                raise SampleStopped(self.stopped)
            self.attempts.append({"id": len(self.attempts), "sample_id": self.sample_id,
                "role": role, "model": model, "reasoning_effort": effort,
                "status": "pending", "usage": None})
            try:
                self._persist()
            except SampleStopped:
                self.attempts.pop()
                self.stopped = "aggregate provider admission refused"
                raise
            return len(self.attempts) - 1

    def track_async_attempt(self, attempt):
        """Bind the admitted SDK call to its actual task, across native threads."""
        loop, task = asyncio.get_running_loop(), asyncio.current_task()
        if task is None:
            raise RuntimeError("SDK attempt must belong to an asyncio task")
        with self.lock:
            if self.attempts[attempt]["status"] != "pending":
                raise ValueError("Cannot track an already settled attempt")
            self.async_attempts[attempt] = (loop, task)
            cancel_now = self.stop_requested
        if cancel_now:
            self._cancel_task(attempt, loop, task)

    def _cancel_task(self, attempt, loop, task):
        def cancel_if_still_owned():
            with self.lock:
                # The SDK call may settle before this queued callback executes.
                # The same task can then be doing unrelated work; leave it alone.
                if (self.async_attempts.get(attempt) == (loop, task)
                    and self.attempts[attempt]["status"] == "pending"
                    and not task.done()):
                    task.cancel()
        try:
            loop.call_soon_threadsafe(cancel_if_still_owned)
        except RuntimeError as error:
            # A closed loop is unknown cancellation, never successful quiescence.
            with self.lock:
                self.cancel_errors.append(str(error))

    def stop_async_attempts(self, reason="comparison sample stopped"):
        """Reject future calls and cancel only this sample's bound pending SDK tasks.

        Scheduling cancellation is not proof of cloud or task quiescence. Callers
        must observe the bound attempts settle before claiming local completion.
        """
        with self.lock:
            self.stop_requested = True
            self.stopped = self.stopped or reason
            self._persist()
            active = list(self.async_attempts.items())
        for attempt, (loop, task) in active:
            self._cancel_task(attempt, loop, task)

    def finish(self, attempt, usage):
        with self.lock:
            row = self.attempts[attempt]
            if row["status"] != "pending":
                raise ValueError("Attempt already settled")
            self.async_attempts.pop(attempt, None)
            fields = {key: count(obj(usage).get(key))
                for key in ("input_tokens", "output_tokens", "total_tokens")}
            if None in fields.values() or fields["total_tokens"] != fields["input_tokens"] + fields["output_tokens"]:
                row["status"] = "unattested"
                self.stopped = "incomplete or incoherent SDK usage"
                self._persist()
                raise SampleStopped(self.stopped)
            row.update(status="completed", usage=fields)
            known = [x["usage"] for x in self.attempts if x["usage"] is not None]
            self._persist()
            if sum(x["input_tokens"] for x in known) > self.limits["providerInputTokens"] or sum(x["output_tokens"] for x in known) > self.limits["providerOutputTokens"]:
                self.stopped = "observed sample token envelope exceeded"
                self._persist()
                raise SampleStopped(self.stopped)

    def incomplete(self, attempt, status):
        with self.lock:
            row = self.attempts[attempt]
            if row["status"] == "pending":
                row["status"] = status
                self.async_attempts.pop(attempt, None)
                self.stopped = self.stopped or "SDK attempt did not yield complete usage"
                self._persist()

    def report(self):
        with self.lock:
            complete = bool(self.attempts) and all(x["status"] == "completed" for x in self.attempts)
            known = [x["usage"] for x in self.attempts if x["usage"] is not None]
            return {"sample_id": self.sample_id, "provider_attempts": len(self.attempts),
                "complete": complete, "stop": self.stopped,
                "stop_requested": self.stop_requested,
                "active_async_attempts": len(self.async_attempts),
                "cancel_errors": list(self.cancel_errors),
                "total_usage": {key: sum(x[key] for x in known) if complete else None
                    for key in ("input_tokens", "output_tokens", "total_tokens")},
                "known_partial_usage": {key: sum(x[key] for x in known)
                    for key in ("input_tokens", "output_tokens", "total_tokens")},
                "attempts": [dict(x) for x in self.attempts],
                "coverage": "Only native entrypoints actually bound by the caller",
                "live_admission": False}


class SampleAsyncStream:
    """Own a bound SDK stream without changing its events or reported usage.

    This comparison-side guard is explicit assistance, not native agent behavior.
    Some SDK iterators expose only their HTTP response rather than a close API.
    Cancellation must close that exact response before settling an incomplete row.
    """

    def __init__(self, ledger, attempt, stream, *, on_event=None):
        self.ledger, self.attempt = ledger, attempt
        self.stream = stream.__aiter__()
        self.on_event = on_event
        self.closed = False
        self._close_error = None
        self._close = getattr(stream, "aclose", None) or getattr(stream, "close", None)
        if not callable(self._close):
            self._close = getattr(getattr(stream, "response", None), "aclose", None)
        if not callable(self._close):
            raise TypeError("Bound SDK stream has no owned close operation")

    def __aiter__(self):
        return self

    async def __anext__(self):
        # The SDK request carrier can finish before a different task consumes it.
        # Bind the actual consumer only while this attempt is still pending.
        with self.ledger.lock:
            if self.ledger.attempts[self.attempt]["status"] == "pending":
                self.ledger.track_async_attempt(self.attempt)
        try:
            event = await self.stream.__anext__()
            if self.on_event is not None:
                self.on_event(event)
            return event
        except BaseException:
            await self.aclose()
            raise

    async def aclose(self):
        if self.closed:
            return
        if self._close_error is not None:
            # HTTPX can mark a response closed before its byte-stream close fails.
            # A second no-op close must not falsely settle that failed operation.
            raise self._close_error
        try:
            result = self._close()
            if inspect.isawaitable(result):
                await result
        except BaseException as error:
            self._close_error = error
            raise
        self.closed = True
        self.ledger.incomplete(self.attempt, "comparison_stream_closed_usage_unknown")

def preflight(exports=None, root=None):
    """Compare known observations. Equal nulls never satisfy an equality gate."""
    if exports is None:
        exports = {}
    if not isinstance(exports, dict) or set(exports) - set(PARTICIPANTS):
        raise ValueError("expected only eva2 and eva1-bottie exports")
    root = root or Path(__file__).resolve().parents[2]
    reports = {p: normalize(p, exports.get(p, {})) for p in PARTICIPANTS}
    for participant, report in reports.items():
        report["source_files"] = {
            p: hashlib.sha256((root / p).read_bytes()).hexdigest()
            if (root / p).is_file() and not (root / p).is_symlink()
            else None
            for p in SOURCES[participant]
        }
        if None in report["source_files"].values():
            report["blockers"].append("native_source_missing")
        if participant == "eva2":
            from cp001_journal_admission import inspect_export, provider_limit_contract

            report["provider_attempt_contract"] = provider_limit_contract(
                root / "src/eva/adapters/model/pydantic_conversation.py"
            )
            data = exports.get(participant, {})
            report["direct_journal_admission"] = inspect_export(
                data.get("direct_journal"),
                request=data.get("request"),
                sources=report["source_files"],
            )
    equality = {}
    for field in LIMIT_FIELDS:
        a, b = (reports[p]["observed_provider_limits"][field] for p in PARTICIPANTS)
        equality[field] = (
            "unknown" if a is None or b is None else ("equal" if a == b else "mismatch")
        )
    return {
        "mode": "offline-native-accounting-preflight",
        "participants": reports,
        "observed_limit_equality": equality,
        "admission": "unknown",
        "live_enabled": False,
        "network_used": False,
        "model_calls": 0,
        "product_result": "unknown",
        "ranking": None,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--exports",
        type=Path,
        help="explicit local JSON export; no hosts or credentials",
    )
    parser.add_argument(
        "--reader-bundle", type=Path, help="signed private snapshot bundle"
    )
    parser.add_argument(
        "--reader-trust",
        type=Path,
        help="separately pinned reader key/context/challenge",
    )
    args = parser.parse_args()
    try:
        if bool(args.reader_bundle) != bool(args.reader_trust) or (
            args.reader_bundle and args.exports
        ):
            raise ValueError("reader bundle and independent trust required together")
        provenance = None
        if args.reader_bundle:
            from cp001_reader_receipt import verify_bundle

            export, provenance = verify_bundle(
                json.loads(args.reader_bundle.read_text()),
                json.loads(args.reader_trust.read_text()),
            )
            exports = {"eva2": export}
        else:
            exports = json.loads(args.exports.read_text()) if args.exports else None
        report = preflight(exports)
        if provenance is not None:
            report["reader_provenance"] = provenance
    except (OSError, ValueError) as error:
        parser.exit(2, f"Invalid local accounting export: {type(error).__name__}\n")
    print(json.dumps(report, indent=2, allow_nan=False))
    return 2  # Native comparison is not admitted by current contracts.


if __name__ == "__main__":
    raise SystemExit(main())
