"""Future evaluator continuation: metadata only; no provider, input or runtime operations."""
import hashlib
import json
import math
import stat
from pathlib import Path

SCOPE_KEYS = {"entry", "deadline_sha256", "monotonic_deadline", "accounting_source_sha256", "actor_sha256", "session_sha256", "session_key_sha256", "main_request_role"}
BINDING_KEYS = {"turn_id", "native_thread_id_sha256", "generation", "canonical_clock_state_sha256", "native_temporal_context_sha256", "environment_turn_id", "native_environment_original_message_sha256"}

def _require(condition):
    if not condition:
        raise ValueError("native completed calendar provenance differs")

def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()

def _sha(value):
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)

def require_private_root(path):
    """Same gate requirement, checked before waiting-ready and again before clock."""
    path = Path(path)
    _require(path.is_absolute() and path.resolve(strict=True) == path)
    info = path.lstat()
    _require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o700)

def native_calendar_scope(entry, deadline_bytes, native_entry, session_key, accounting_source, main_request_role):
    origin = native_entry.get("delivery", {}).get("origin", {})
    fields = ("nativeDirectUserId", "from", "to", "nativeChannelId", "accountId", "surface", "chatType")
    _require(isinstance(entry, str) and entry and isinstance(deadline_bytes, bytes))
    _require(native_entry.get("chatType") == "direct" and origin.get("chatType") == "direct")
    _require(all(isinstance(origin.get(k), str) and origin[k] for k in fields))
    _require(isinstance(native_entry.get("sessionId"), str) and native_entry["sessionId"])
    _require(isinstance(session_key, str) and session_key and main_request_role in ("contact", "work"))
    deadline = json.loads(deadline_bytes)["monotonic_deadline"]
    _require(type(deadline) in (int, float) and math.isfinite(deadline))
    return dict(entry=entry, deadline_sha256=hashlib.sha256(deadline_bytes).hexdigest(), monotonic_deadline=deadline,
                accounting_source_sha256=hashlib.sha256(Path(accounting_source).read_bytes()).hexdigest(),
                actor_sha256=_digest([origin[k] for k in fields]), session_sha256=_digest(native_entry["sessionId"]),
                session_key_sha256=_digest(session_key), main_request_role=main_request_role)

def completed_calendar_snapshot(rows, parent_ledger, scope):
    """Called after old process closure; returned value enters existing sealed control FD.

    The new marker is written only after accepted completed-terminal settlement.
    Legacy terminal-type/usage fields alone deliberately cannot create eligibility.
    """
    _require(isinstance(scope, dict) and set(scope) == SCOPE_KEYS)
    _require(all(_sha(scope[k]) for k in SCOPE_KEYS if k.endswith("_sha256")))
    _require(isinstance(scope["entry"], str) and scope["entry"] and scope["main_request_role"] in ("contact", "work"))
    _require(type(scope["monotonic_deadline"]) in (int, float) and math.isfinite(scope["monotonic_deadline"]))
    _require(parent_ledger.get("sample_id") == scope["entry"] and parent_ledger.get("complete") is True)
    _require(parent_ledger.get("stop") is None and parent_ledger.get("stop_requested") is False)
    _require(isinstance(rows, list) and rows and isinstance(parent_ledger.get("attempts"), list))
    authoritative = parent_ledger["attempts"]
    _require(len(rows) == len(authoritative))
    by_id = {row.get("id"): row for row in authoritative}
    _require(len(by_id) == len(authoritative))
    records, ids, responses = [], set(), set()
    for row in rows:
        ident = row.get("attempt_id")
        _require(type(ident) is int and ident >= 0 and ident not in ids and ident in by_id)
        parent = by_id[ident]
        _require(row.get("status") == parent.get("status") == "completed" and parent.get("sample_id") == scope["entry"])
        _require(all(row.get(k) == parent.get(k) for k in ("role", "model", "reasoning_effort", "usage")))
        usage = row.get("usage")
        _require(isinstance(usage, dict) and all(type(usage.get(k)) is int and usage[k] >= 0 for k in ("input_tokens", "output_tokens", "total_tokens")))
        _require(usage["total_tokens"] == usage["input_tokens"] + usage["output_tokens"])
        roles = (row.get("role"), row.get("model"), row.get("reasoning_effort"))
        _require(roles == ("helper", "gpt-6-astra", "medium") or (scope["main_request_role"] == "contact" and roles == ("contact", "gpt-6-sol", "none")))
        marker = row.get("completed_calendar_reference")
        _require(isinstance(marker, dict) and set(marker) == {"scope", "response_id_sha256", "binding"} and marker["scope"] == scope)
        binding, response = marker["binding"], marker["response_id_sha256"]
        _require(_sha(response) and response not in responses and row.get("native_terminal_type") == "response.completed")
        _require(response == row.get("native_terminal_response_id_sha256"))
        _require(isinstance(binding, dict) and set(binding) == BINDING_KEYS)
        _require(all(_sha(binding[k]) for k in BINDING_KEYS if k.endswith("_sha256")))
        _require(type(binding["generation"]) is int and binding["generation"] >= 0)
        _require(all(isinstance(binding[k], str) and binding[k] for k in ("turn_id", "environment_turn_id")))
        _require(all(binding[k] == row.get("calendar_binding", {}).get(k) for k in BINDING_KEYS))
        records.append(dict(attempt_id=ident, role=row["role"], model=row["model"], reasoning_effort=row["reasoning_effort"], response_id_sha256=response, binding=binding.copy()))
        ids.add(ident); responses.add(response)
    _require(ids == set(by_id))
    return {"contract": "OpenClawCompletedCalendar/v1", "scope": scope.copy(), "records": records}
