"""Offline native accounting admission. Exports are observations, never authority.

No network, credentials, database discovery or model execution. Current native
interfaces cannot attest complete sample accounting or server-wide limits.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

PARTICIPANTS = ("eva2", "eva1-bottie")
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
        "eva2/src/eva/application/conversation.py",
        "eva2/src/eva/adapters/postgres/conversation_journal.py",
        "eva2/src/eva/adapters/model/pydantic_conversation.py",
        "eva2/src/eva/adapters/model/openai_codex_provider.py",
        "eva2/src/eva/contracts/runtime_budget.py",
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


def preflight(exports=None, root=None):
    """Compare known observations. Equal nulls never satisfy an equality gate."""
    if exports is None:
        exports = {}
    if not isinstance(exports, dict) or set(exports) - set(PARTICIPANTS):
        raise ValueError("expected only eva2 and eva1-bottie exports")
    root = root or Path(__file__).resolve().parents[3]
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
                root / "eva2/src/eva/adapters/model/pydantic_conversation.py"
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
