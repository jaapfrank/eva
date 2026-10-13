"""Independent raw person-state readback for disposable native Bottie scenarios.

Evaluator-only evidence, never participant context or a semantic score. Assistant
messages are deliberately excluded: their sender is Eva, not an authenticated
person. Use the existing exact-request reply and signed ACK route for delivery.
"""
from __future__ import annotations

import math
from pathlib import Path
import sqlite3
import time


def read_person_snapshot(database: str | Path, *, person: str,
                         timeout_seconds: float) -> dict:
    """Read exact native person rows across conversations in one SQLite snapshot.

    Empty lists mean an observed absence. Missing files, schema errors and read
    failures raise; they are never converted into empty or successful evidence.
    Includes archived memories for independent correction/history assessment.
    No alias, case folding, shared-memory fallback or identity inference occurs.
    The caller must supply its own disposable database and resolved person.
    """
    if not isinstance(person, str) or not person.strip() or person != person.strip():
        raise ValueError("an exact resolved person is required")
    if (type(timeout_seconds) not in (int, float)
            or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
        raise ValueError("a positive readback timeout is required")
    path = Path(database).resolve(strict=True)
    if not path.is_file():
        raise ValueError("an existing native SQLite database is required")
    deadline = time.monotonic() + timeout_seconds
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True,
                                 timeout=timeout_seconds)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        connection.execute("BEGIN")
        memories = [dict(row) for row in connection.execute(
            "SELECT * FROM memories WHERE person = ? COLLATE BINARY ORDER BY id", (person,))]
        messages = [dict(row) for row in connection.execute(
            "SELECT id, ts, channel, conversation_id, sender, role, content "
            "FROM messages WHERE sender = ? COLLATE BINARY AND role = 'user' ORDER BY id",
            (person,))]
        if time.monotonic() >= deadline:
            raise TimeoutError("native person readback deadline reached")
        return {"contract": "EvaBottiePersonReadback/v1", "status": "observed",
                "person": person, "memories": memories, "user_messages": messages,
                "scope": "Raw exact-person state; not recall quality, identity authentication, "
                         "assistant delivery or native work settlement."}
    finally:
        connection.close()
