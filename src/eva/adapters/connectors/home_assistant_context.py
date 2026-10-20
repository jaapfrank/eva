"""Current HA device context on the existing authenticated event connection.

The supported subscribe_entities stream supplies an atomic initial state and
ordered deltas. Registry events invalidate selection until fresh lists arrive.
An application-level ping fences reads behind already queued updates. Nothing
here is execution proof; effects retain independent REST readback.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import json
import hashlib
import threading
import time
from uuid import uuid4


class HomeAssistantDeviceContext:
    def __init__(self, *, scope_sha256: str, clock=time.monotonic):
        self._scope_sha256 = scope_sha256
        self._clock = clock
        self._lock = threading.RLock()
        self._states = {}
        self._registries = {}
        self._sender = None
        self._waiters = {}
        self._next_id = 20
        self._initial = False
        self._subscriptions = set()
        self._pending = {}
        self._refresh_again = False
        self._last_pong = 0.0
        self._generation = 0
        self._binding = None
        self._version = None
        self._connection_epoch = None

    def disconnect(self):
        with self._lock:
            self._sender = None
            self._initial = False
            self._states = {}
            self._registries = {}
            self._binding = None
            self._connection_epoch = None
            self._generation += 1
            for waiter in self._waiters.values():
                if waiter is not None:
                    waiter.set()
            self._waiters.clear()

    def _ready(self):
        return (self._sender is not None and self._initial and not self._pending
                and not self._refresh_again and self._subscriptions == set(range(1, 7))
                and set(self._registries) == {"areas", "devices", "entities", "account"})

    def _send(self, kind, **fields):
        number = self._next_id
        self._next_id += 1
        self._sender(json.dumps({"id": number, "type": kind, **fields}))
        return number

    def _refresh(self):
        self._binding = None
        if self._pending:
            self._refresh_again = True
            return
        self._generation += 1
        for key, kind in (("areas", "config/area_registry/list"),
                          ("devices", "config/device_registry/list"),
                          ("entities", "config/entity_registry/list"),
                          ("account", "auth/current_user")):
            self._pending[self._send(kind)] = key

    @staticmethod
    def _timestamp(value):
        if not isinstance(value, (float, int)) or isinstance(value, bool):
            raise ValueError("HA compressed state timestamp is invalid")
        return datetime.fromtimestamp(value, UTC).isoformat()

    def _delta(self, update):
        if not isinstance(update, dict) or set(update) - {"a", "c", "r"}:
            raise ValueError("HA entity delta is invalid")
        if not self._initial:
            if "c" in update or "r" in update or not isinstance(update.get("a"), dict):
                raise ValueError("HA initial entity snapshot is incomplete")
            self._states = {}
        for entity_id, compressed in update.get("a", {}).items():
            if (not isinstance(entity_id, str) or "." not in entity_id
                    or not isinstance(compressed, dict) or not isinstance(compressed.get("s"), str)
                    or not isinstance(compressed.get("a"), dict)):
                raise ValueError("HA added entity is invalid")
            changed = self._timestamp(compressed["lc"])
            self._states[entity_id] = {"entity_id": entity_id, "state": compressed["s"],
                "attributes": deepcopy(compressed["a"]), "last_changed": changed,
                "last_updated": self._timestamp(compressed["lu"]) if compressed.get("lu") else changed}
            self._binding = None
        for entity_id in update.get("r", []):
            self._states.pop(entity_id, None)
            self._binding = None
        for entity_id, delta in update.get("c", {}).items():
            row = self._states.get(entity_id)
            if row is None or not isinstance(delta, dict) or set(delta) - {"+", "-"}:
                raise ValueError("HA delta references an unknown entity")
            added, removed = delta.get("+", {}), delta.get("-", {})
            if not isinstance(added, dict) or not isinstance(removed, dict):
                raise ValueError("HA compressed entity change is invalid")
            if "s" in added:
                if not isinstance(added["s"], str):
                    raise ValueError("HA changed state is invalid")
                row["state"] = added["s"]
            if "lc" in added:
                row["last_updated"] = row["last_changed"] = self._timestamp(added["lc"])
            elif "lu" in added:
                row["last_updated"] = self._timestamp(added["lu"])
            attributes = added.get("a", {})
            removed_attributes = removed.get("a", [])
            if not isinstance(attributes, dict) or not isinstance(removed_attributes, list):
                raise ValueError("HA attribute delta is invalid")
            if {"friendly_name", "entity_id"} & (set(attributes) | set(removed_attributes)):
                self._binding = None
            row["attributes"].update(deepcopy(attributes))
            for key in removed_attributes:
                row["attributes"].pop(key, None)
        if len(self._states) > 50000:
            raise ValueError("HA entity context exceeds its bound")
        self._initial = True

    def _consume(self, frame):
        with self._lock:
            number, kind = frame.get("id"), frame.get("type")
            if kind == "pong" and number in self._waiters:
                waiter = self._waiters.pop(number)
                self._last_pong = self._clock()
                if waiter is not None:
                    waiter.set()
            elif kind == "result":
                if frame.get("success") is not True:
                    raise RuntimeError("HA live context command was rejected")
                if number in range(1, 7):
                    self._subscriptions.add(number)
                elif number in self._pending:
                    key = self._pending.pop(number)
                    value = frame.get("result")
                    if not isinstance(value, dict if key == "account" else list):
                        raise ValueError("HA live registry response is invalid")
                    self._registries[key] = value
                    if not self._pending and self._refresh_again:
                        self._refresh_again = False
                        self._refresh()
            elif kind == "event" and number == 2:
                self._delta(frame.get("event"))
            elif kind == "event" and number in range(3, 7):
                self._refresh()

    def listen(self, socket, stop, handler, *, version):
        self.disconnect()
        with self._lock:
            self._version = version
            self._connection_epoch = str(uuid4())
            self._sender = socket.send
            self._subscriptions = set()
            self._pending = {}
            self._refresh_again = False
            self._last_pong = self._clock()
            socket.send(json.dumps({"id": 1, "type": "subscribe_events", "event_type": "state_changed"}))
            socket.send(json.dumps({"id": 2, "type": "subscribe_entities"}))
            for number, kind in enumerate(("area_registry_updated", "device_registry_updated",
                                           "entity_registry_updated", "core_config_updated"), 3):
                socket.send(json.dumps({"id": number, "type": "subscribe_events", "event_type": kind}))
            self._refresh()
            number = self._send("ping")
            self._waiters[number] = None
        try:
            while not stop.is_set():
                try:
                    raw = socket.recv(timeout=.5)
                except TimeoutError:
                    raw = None
                if raw is not None:
                    frame = json.loads(raw)
                    if not isinstance(frame, dict):
                        raise ValueError("HA live context frame is invalid")
                    self._consume(frame)
                    if frame.get("type") == "event" and frame.get("id") == 1:
                        handler(raw)
                with self._lock:
                    age = self._clock() - self._last_pong
                    if age > 8:
                        raise TimeoutError("HA live context heartbeat expired")
                    if age > 3 and not self._waiters:
                        self._waiters[self._send("ping")] = None
        finally:
            self.disconnect()

    def _read(self, project, *, timeout=2):
        waiter = threading.Event()
        with self._lock:
            if not self._ready():
                raise RuntimeError("HA live context is not current")
            number = self._send("ping")
            self._waiters[number] = waiter
        try:
            if not waiter.wait(timeout):
                raise TimeoutError("HA context read barrier expired")
            with self._lock:
                if not self._ready() or number in self._waiters or self._clock() - self._last_pong > 8:
                    raise RuntimeError("HA live context changed during read")
                return project()
        finally:
            with self._lock:
                self._waiters.pop(number, None)

    def _metadata(self):
        if self._binding is None:
            # A refresh fences reads but is not itself a selection change.
            # HA also updates an entity's bookkeeping timestamp when its
            # serialized selection/permission content remains identical.
            registries = {**self._registries, "entities": [
                {key: value for key, value in row.items() if key != "modified_at"}
                for row in self._registries["entities"]]}
            observed = {"scope": self._scope_sha256,
                "version": self._version,
                "connection_epoch": self._connection_epoch,
                "registries": registries,
                "names_and_groups": {k: {field: v["attributes"].get(field)
                    for field in ("friendly_name", "entity_id")} for k, v in self._states.items()
                    if k.startswith(("light.", "switch."))}}
            # HA registry metadata includes external finite JSON floats.
            self._binding = hashlib.sha256(json.dumps(observed, ensure_ascii=False,
                sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        return {"binding_sha256": self._binding, "source": "ha_subscribe_entities",
                "checked_at": datetime.now(UTC).isoformat(), "complete": True}

    def _inventory(self):
        from eva.adapters.connectors.home_assistant_admin import HomeAssistantAdminAdapter
        return HomeAssistantAdminAdapter._project_household(
            self._registries["areas"], self._registries["devices"],
            self._registries["entities"], list(self._states.values()))

    def binding_snapshot(self, *, timeout=2):
        return self._read(self._metadata, timeout=timeout)

    def snapshot(self, *, timeout=2):
        return self._read(lambda: {**self._metadata(),
            "states": deepcopy(list(self._states.values())), "inventory": self._inventory()}, timeout=timeout)

    def inventory(self):
        return self._read(self._inventory)

    def catalog(self, domain):
        return self._read(lambda: deepcopy([row for row in self._states.values()
            if row["entity_id"].startswith(domain + ".")]))
