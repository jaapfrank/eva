from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from eva.adapters.postgres.delivery import PostgresFanoutDeliveryStore
from eva.application.delivery import FanoutDeliveryRequest, FanoutRecipientReceipt


NOW = datetime(2026, 8, 24, 7, 0, tzinfo=UTC)


class Timeline:
    def __init__(self) -> None:
        self.rows: list[SimpleNamespace] = []

    def create_thread(self, **values):  # type: ignore[no-untyped-def]
        self.rows.append(
            SimpleNamespace(
                event_type=values["event_type"], payload={"data": values["data"]}
            )
        )

    def events(self, _thread_id: str):  # type: ignore[no-untyped-def]
        return tuple(self.rows)

    def load(self, _thread_id: str):  # type: ignore[no-untyped-def]
        return SimpleNamespace(revision=len(self.rows))

    def transition(self, **values):  # type: ignore[no-untyped-def]
        assert values["expected_revision"] == len(self.rows)
        self.rows.append(
            SimpleNamespace(
                event_type=values["event_type"], payload={"data": values["data"]}
            )
        )


def test_store_records_one_result_per_recipient_and_exact_summary() -> None:
    timeline = Timeline()
    store = object.__new__(PostgresFanoutDeliveryStore)
    store._timeline = timeline  # type: ignore[attr-defined]
    request = FanoutDeliveryRequest(
        "fanout:test", "jaap", "Test", ("sink-a", "sink-b", "sink-c"), NOW
    )

    assert store.admit(request) == "admitted"
    for recipient in request.recipients:
        assert store.prepare(request, recipient) == "prepared"
        status = "rejected" if recipient == "sink-c" else "accepted"
        receipt = FanoutRecipientReceipt(
            recipient,
            request.idempotency_key(recipient),
            request.message_sha256,
            status,  # type: ignore[arg-type]
            None if status == "rejected" else f"provider:{recipient}",
            "recipient_rejected" if status == "rejected" else None,
            NOW,
        )
        assert store.record(request, receipt) == "recorded"
        assert store.record(request, receipt) == "already_recorded"

    result = store.complete(request)
    assert result.accepted_count == 2
    assert result.rejected_count == 1
    assert store.complete(request) == result
    assert [row.event_type for row in timeline.rows] == [
        "FANOUT_DELIVERY_ADMITTED",
        "FANOUT_RECIPIENT_PREPARED",
        "FANOUT_RECIPIENT_RECORDED",
        "FANOUT_RECIPIENT_PREPARED",
        "FANOUT_RECIPIENT_RECORDED",
        "FANOUT_RECIPIENT_PREPARED",
        "FANOUT_RECIPIENT_RECORDED",
        "FANOUT_DELIVERY_COMPLETED",
    ]
