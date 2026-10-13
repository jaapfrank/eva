"""Opt-in action reservations on the existing sample accounting connection.

Returned means native invocation returned, never successful external effect or
completed delegated work. Arguments/results remain native and are not logged.
"""
from __future__ import annotations

from functools import wraps
import hashlib
import json
import math
import time

from cp001_accounting import SampleStopped


def action_config(value):
    if (not isinstance(value, dict) or set(value) != {"contract", "allowed_actions", "max_dispatches"}
            or value["contract"] != "Ev003ActionControl/v1"
            or type(value["max_dispatches"]) is not int or value["max_dispatches"] < 1
            or not isinstance(value["allowed_actions"], list) or not value["allowed_actions"]
            or any(not isinstance(n, str) or not n or len(n) > 128 for n in value["allowed_actions"])
            or len(set(value["allowed_actions"])) != len(value["allowed_actions"])):
        raise ValueError("explicit action control required")
    return {**value, "allowed_actions": sorted(value["allowed_actions"])}


def config_sha256(value):
    return hashlib.sha256(json.dumps(action_config(value), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class ActionBudget:
    """Parent-owned reservations, under the existing IPC parent lock."""
    def __init__(self, ledger, config, *, deadline_monotonic):
        self.ledger, self.config = ledger, action_config(config)
        if not callable(deadline_monotonic) and (type(deadline_monotonic) not in (int, float) or not math.isfinite(deadline_monotonic)):
            raise ValueError("original sample wall deadline required")
        self.deadline = deadline_monotonic
        cap = ledger.limits.get("toolCalls")
        if type(cap) is not int or self.config["max_dispatches"] > cap:
            raise ValueError("action cap exceeds declared sample tool budget")
        self.digest = config_sha256(self.config)
        self.rows, self.refused = [], []

    def reserve(self, namespace, name, binding, digest):
        deadline = self.deadline() if callable(self.deadline) else self.deadline
        expired = type(deadline) not in (int, float) or not math.isfinite(deadline) or time.monotonic() >= deadline
        if (expired or digest != self.digest or name not in self.config["allowed_actions"]
                or self.ledger.stopped or self.ledger.stop_requested
                or len(self.rows) >= self.config["max_dispatches"]):
            self.refused.append({"namespace": namespace, "name": name, "dispatched": False})
            self.ledger.stop_async_attempts("action admission refused")
            return {"allow": False}
        allowed = {"call_id", "tenant_id", "installation_id", "principal_id", "conversation_id", "message_id", "work_id"}
        if not isinstance(binding, dict) or set(binding) - allowed or any(not isinstance(v, str) or not v or len(v) > 256 for v in binding.values()):
            raise ValueError("action binding metadata invalid")
        index = len(self.rows)
        self.rows.append({"namespace": namespace, "name": name, "binding": binding,
                          "status": "pending", "entered": None,
                          "kind": "protocol" if name.startswith("protocol:") else "native_action"})
        return {"allow": True, "action_id": index}

    def settle(self, namespace, index, status, entered):
        if (type(index) is not int or not 0 <= index < len(self.rows)
                or self.rows[index]["namespace"] != namespace
                or self.rows[index]["status"] != "pending"
                or status not in {"returned", "failed", "incomplete"} or type(entered) is not bool
                or (status in {"returned", "failed"} and not entered)):
            raise ValueError("action settlement binding differs")
        self.rows[index].update(status=status, entered=entered)
        if status == "incomplete":
            self.ledger.stop_async_attempts("native action completion unknown")

    def pending(self, namespace):
        return sum(r["namespace"] == namespace and r["status"] == "pending" for r in self.rows)

    def disconnect(self, namespace):
        for row in self.rows:
            if row["namespace"] == namespace and row["status"] == "pending":
                row.update(status="incomplete", entered=None)

    def report(self):
        return {"config_sha256": self.digest, "reserved_dispatches": len(self.rows),
                "native_action_reservations": sum(r["kind"] == "native_action" for r in self.rows),
                "protocol_reservations": sum(r["kind"] == "protocol" for r in self.rows),
                "attempts": [dict(r) for r in self.rows], "refused": list(self.refused),
                "pending": sum(r["status"] == "pending" for r in self.rows),
                "complete": all(r["status"] in {"returned", "failed"} for r in self.rows),
                "scope": "Native invocation settlement only; effect and delegated work completion require independent readback."}


class ActionAccounting:
    def __init__(self, ledger, config):
        self.ledger, self.config = ledger, action_config(config)
        self.digest = config_sha256(self.config)

    def call(self, name, invoke, *, binding):
        index = self.ledger.reserve_action(name, binding, self.digest)
        status, entered = "incomplete", False
        try:
            if self.ledger.stopped or self.ledger.stop_requested:
                raise SampleStopped("sample stopped before action dispatch")
            entered = True
            result = invoke()
            status = "returned"
            return result
        except SampleStopped:
            status = "incomplete"
            raise
        except Exception:
            status = "failed" if entered else "incomplete"
            raise
        finally:
            self.ledger.settle_action(index, status, entered=entered)

    def bind(self, method):
        @wraps(method)
        def execute(instance, turn, call, **kwargs):
            binding = {key: str(getattr(turn, key)) for key in
                       ("tenant_id", "installation_id", "principal_id", "conversation_id", "message_id")
                       if getattr(turn, key, None) is not None}
            binding["call_id"] = str(call.call_id)
            return self.call(call.name, lambda: method(instance, turn, call, **kwargs), binding=binding)
        return execute
