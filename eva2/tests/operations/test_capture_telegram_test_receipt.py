from __future__ import annotations

from datetime import UTC, datetime
import json
import stat
import tempfile
from uuid import uuid4
from pathlib import Path

import pytest

from eva.adapters.postgres.operational_timeline import CheckpointState, TimelineEvent
from eva.operations.telegram_test_preflight import TelegramTestPlan
import tools.capture_telegram_test_receipt as capture
from tools.capture_telegram_test_receipt import build_receipt, _write_new_receipt


def _plan() -> TelegramTestPlan:
    return TelegramTestPlan(
        (
            ("EVA_TEST_LIVING_CORE_IMAGE", "eva2-telegram-test@sha256:" + "a" * 64),
            ("EVA_TEST_RUNTIME_INSTALLATION_ID", "22222222-2222-4222-8222-222222222222"),
            ("EVA_TEST_RUNTIME_TENANT_ID", "11111111-1111-4111-8111-111111111111"),
        )
    )


def _checkpoint(*, status: str = "completed") -> CheckpointState:
    return CheckpointState(
        thread_id="telegram-inbound:44",
        checkpoint_id="telegram-inbound:44:delivered",
        operation_id="telegram-update:44",
        phase="reply_delivered",
        status=status,  # type: ignore[arg-type]
        retry_count=0,
        last_error_fingerprint=None,
        deadline=None,
        revision=3,
        updated_at=datetime(2026, 8, 26, 12, 0, tzinfo=UTC),
    )


def _event(*, sequence: int, event_type: str, payload: object) -> TimelineEvent:
    return TimelineEvent(
        event_id=uuid4(),
        thread_id="telegram-inbound:44",
        sequence=sequence,
        checkpoint_id=f"telegram-inbound:44:{sequence}",
        operation_id="telegram-update:44",
        event_type=event_type,
        payload=payload,  # type: ignore[arg-type]
        payload_sha256="a" * 64,
        occurred_at=datetime(2026, 8, 26, 12, sequence, tzinfo=UTC),
    )


def test_receipt_counts_verified_events_without_copying_message_data() -> None:
    checkpoint = _checkpoint()
    receipt = build_receipt(
        plan=_plan(),
        checkpoints=(checkpoint,),
        events_by_thread={
            checkpoint.thread_id: (
                _event(
                    sequence=1,
                    event_type="TELEGRAM_INGRESS_ADMITTED",
                    payload={"data": {"text": "private content"}},
                ),
                _event(
                    sequence=2,
                    event_type="TELEGRAM_REPLY_DELIVERED",
                    payload={"data": {"provider_message_id": "telegram:44:99"}},
                ),
            )
        },
        observed_at="2026-08-26T12:03:00Z",
    )

    assert receipt["contract"] == "EvaTelegramNativeTestReceipt/v1"
    assert receipt["ledger"] == {
        "checkpoint_count": 1,
        "checkpoint_statuses": {"completed": 1},
        "event_count": 2,
        "event_types": {
            "TELEGRAM_INGRESS_ADMITTED": 1,
            "TELEGRAM_REPLY_DELIVERED": 1,
        },
        "delivered_reply_count": 1,
        "ambiguous_delivery_count": 0,
        "incomplete_checkpoint_count": 0,
        "integrity": "all included append-only event hashes were checked",
    }
    rendered = json.dumps(receipt)
    assert "private content" not in rendered
    assert "telegram:44:99" not in rendered
    assert "telegram-inbound:44" not in rendered


@pytest.mark.parametrize(
    "events",
    [
        (),
        (_event(sequence=2, event_type="TELEGRAM_INGRESS_ADMITTED", payload={}),),
        (_event(sequence=1, event_type="NOT_A_TELEGRAM_EVENT", payload={}),),
    ],
)
def test_receipt_rejects_incomplete_or_unknown_ledger_events(
    events: tuple[TimelineEvent, ...],
) -> None:
    checkpoint = _checkpoint()
    with pytest.raises(RuntimeError, match="Telegram test"):
        build_receipt(
            plan=_plan(),
            checkpoints=(checkpoint,),
            events_by_thread={checkpoint.thread_id: events},
            observed_at="2026-08-26T12:03:00Z",
        )


def test_receipt_writer_is_new_private_json_only() -> None:
    with tempfile.TemporaryDirectory(dir="/tmp") as directory:
        root = Path(directory)
        destination = root / "telegram-proof.json"
        _write_new_receipt(
            destination, {"contract": "EvaTelegramNativeTestReceipt/v1"}
        )

        assert stat.S_IMODE(destination.stat().st_mode) == 0o600
        with pytest.raises(RuntimeError, match="already exist"):
            _write_new_receipt(destination, {"contract": "different"})
        with pytest.raises(RuntimeError, match=".json"):
            _write_new_receipt(root / "telegram-proof.txt", {"contract": "different"})


def test_capture_reads_the_ledger_inside_the_isolated_ingress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan()
    monkeypatch.setattr(capture, "preflight_telegram_test", lambda *_args, **_kwargs: None)
    observed: dict[str, object] = {}
    checkpoint = _checkpoint()

    def read_ledger(actual: TelegramTestPlan):
        observed["plan"] = actual
        return (
            (checkpoint,),
            {
                checkpoint.thread_id: (
                    _event(
                        sequence=1,
                        event_type="TELEGRAM_INGRESS_ADMITTED",
                        payload={},
                    ),
                    _event(
                        sequence=2,
                        event_type="TELEGRAM_REPLY_DELIVERED",
                        payload={},
                    ),
                )
            },
        )

    monkeypatch.setattr(
        capture,
        "_read_ledger_inside_ingress",
        read_ledger,
    )

    receipt = capture.capture_receipt(plan=plan)

    assert receipt["ledger"]["delivered_reply_count"] == 1
    assert observed == {"plan": plan}
