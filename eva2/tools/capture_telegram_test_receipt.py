#!/usr/bin/env python3
"""Write one redacted evidence receipt from an isolated real Telegram test.

The command reads only the test configuration under ``/run/eva2-test`` and
the append-only Telegram ingress ledger in its matching disposable database.
It deliberately omits bot tokens, names, account/chat IDs, Telegram message
IDs, provider receipt IDs and message text.  It is evidence of what Eva's
ledger observed, not proof that a human saw a reply in the Telegram UI.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Iterable, NoReturn
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa

from eva.adapters.postgres.operational_timeline import (
    CheckpointState,
    PostgresOperationalTimeline,
    TimelineEvent,
    TimelineScope,
)
from eva.bootstrap.secrets import read_runtime_secret_file
from eva.operations.telegram_test_preflight import (
    TelegramTestPlan,
    load_telegram_test_plan,
    preflight_telegram_test,
)


THREAD_PATTERN = re.compile(r"telegram-inbound:[0-9]+\Z")
EVENT_TYPES = frozenset(
    {
        "TELEGRAM_INGRESS_ADMITTED",
        "TELEGRAM_INGRESS_REJECTED",
        "TELEGRAM_INGRESS_REJECTED_AFTER_ADMISSION",
        "TELEGRAM_REPLY_PREPARED",
        "TELEGRAM_DELIVERY_CLAIMED",
        "TELEGRAM_REPLY_DELIVERED",
        "TELEGRAM_DELIVERY_AMBIGUOUS",
        "RECOVERY_FAILED",
        "RECOVERY_NEEDS_HELP",
    }
)
MAX_THREADS = 256


def _utc_now() -> str:
    return datetime.now(tz=UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _scope(plan: TelegramTestPlan) -> TimelineScope:
    tenant = plan.value("EVA_TEST_RUNTIME_TENANT_ID")
    installation = plan.value("EVA_TEST_RUNTIME_INSTALLATION_ID")
    return TimelineScope(
        tenant_id=uuid5(NAMESPACE_URL, f"eva2:{tenant}"),
        installation_id=uuid5(NAMESPACE_URL, f"eva2:{tenant}:{installation}"),
    )


def _scope_sha256(plan: TelegramTestPlan) -> str:
    """Bind receipt to the test scope without publishing either UUID."""

    value = "\x00".join(
        (
            plan.value("EVA_TEST_RUNTIME_TENANT_ID"),
            plan.value("EVA_TEST_RUNTIME_INSTALLATION_ID"),
        )
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _counts(values: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))


def build_receipt(
    *,
    plan: TelegramTestPlan,
    checkpoints: tuple[CheckpointState, ...],
    events_by_thread: dict[str, tuple[TimelineEvent, ...]],
    observed_at: str,
) -> dict[str, object]:
    """Summarize validated ledger facts without copying user-controlled data."""

    if not checkpoints:
        raise RuntimeError("Telegram test ledger has no ingress updates yet")
    if len(checkpoints) > MAX_THREADS:
        raise RuntimeError("Telegram test receipt exceeds its thread limit")
    event_types: list[str] = []
    for checkpoint in checkpoints:
        if not THREAD_PATTERN.fullmatch(checkpoint.thread_id):
            raise RuntimeError("Telegram test checkpoint thread differs")
        events = events_by_thread.get(checkpoint.thread_id)
        if events is None or not events:
            raise RuntimeError("Telegram test checkpoint has no append-only event")
        previous_sequence = 0
        for event in events:
            if (
                event.thread_id != checkpoint.thread_id
                or event.sequence != previous_sequence + 1
                or event.event_type not in EVENT_TYPES
            ):
                raise RuntimeError("Telegram test ledger event differs")
            previous_sequence = event.sequence
            event_types.append(event.event_type)
    event_count = _counts(event_types)
    delivered = event_count.get("TELEGRAM_REPLY_DELIVERED", 0)
    ambiguous = event_count.get("TELEGRAM_DELIVERY_AMBIGUOUS", 0)
    receipt = {
        "contract": "EvaTelegramNativeTestReceipt/v1",
        "observed_at": observed_at,
        "test_input": {
            "living_core_image": plan.value("EVA_TEST_LIVING_CORE_IMAGE"),
            "scope_sha256": _scope_sha256(plan),
        },
        "ledger": {
            "checkpoint_count": len(checkpoints),
            "checkpoint_statuses": _counts(
                checkpoint.status for checkpoint in checkpoints
            ),
            "event_count": sum(event_count.values()),
            "event_types": event_count,
            "delivered_reply_count": delivered,
            "ambiguous_delivery_count": ambiguous,
            "incomplete_checkpoint_count": sum(
                checkpoint.status != "completed" for checkpoint in checkpoints
            ),
            "integrity": "all included append-only event hashes were checked",
        },
        "nonclaims": [
            "This receipt contains no bot token, name, account ID, chat ID, message text or provider message ID.",
            "Telegram provider acceptance is not independent proof that a human read the reply.",
            "The fixed diagnostic core is not a full Eva conversation, memory or action proof.",
            "This test does not modify Eva A/B or prove managed-B deployment.",
        ],
    }
    return receipt


def capture_receipt(*, plan: TelegramTestPlan) -> dict[str, object]:
    """Preflight the running isolated stack, then read its scoped ledger."""

    preflight_telegram_test(plan, ingress_running=True)
    dsn = read_runtime_secret_file(
        plan.value("EVA_TEST_DATABASE_DSN_FILE_HOST"),
        setting_name="EVA_TEST_DATABASE_DSN_FILE_HOST",
    )
    engine = sa.create_engine(dsn, pool_pre_ping=True)
    try:
        timeline = PostgresOperationalTimeline(engine, _scope(plan))
        checkpoints = timeline.list_checkpoints(
            thread_prefix="telegram-inbound:", limit=MAX_THREADS
        )
        events = {
            checkpoint.thread_id: timeline.events(checkpoint.thread_id)
            for checkpoint in checkpoints
        }
    finally:
        engine.dispose()
    return build_receipt(
        plan=plan,
        checkpoints=checkpoints,
        events_by_thread=events,
        observed_at=_utc_now(),
    )


def _write_new_receipt(path: Path, receipt: dict[str, object]) -> None:
    path = path.resolve()
    if path.suffix != ".json" or not path.parent.is_dir():
        raise RuntimeError("receipt output must be a new .json file below an existing directory")
    payload = (
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_CLOEXEC, 0o600)
    except OSError as error:
        raise RuntimeError("receipt output must not already exist") from error
    try:
        os.fchmod(descriptor, 0o600)
        written = os.write(descriptor, payload)
        if written != len(payload):
            raise RuntimeError("could not write complete Telegram test receipt")
        os.fsync(descriptor)
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or stat.S_IMODE(details.st_mode) != 0o600:
            raise RuntimeError("Telegram test receipt mode differs")
    finally:
        os.close(descriptor)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file", type=Path, default=Path("/run/eva2-test/telegram-test.env")
    )
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    try:
        if os.geteuid() != 0:
            raise RuntimeError("Telegram test receipt capture must run as root")
        receipt = capture_receipt(
            plan=load_telegram_test_plan(arguments.env_file)
        )
        _write_new_receipt(arguments.output, receipt)
    except RuntimeError as error:
        _die(str(error))
    return 0


def _die(message: str) -> NoReturn:
    raise SystemExit(f"telegram-test-receipt: {message}")


if __name__ == "__main__":
    raise SystemExit(main())
