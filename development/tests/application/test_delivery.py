from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from eva.application.delivery import (
    FanoutDeliveryRequest,
    FanoutDeliveryResult,
    FanoutDeliveryWorker,
    FanoutRecipientReceipt,
)


NOW = datetime(2026, 8, 24, 7, 0, tzinfo=UTC)


class Store:
    def __init__(self) -> None:
        self.request: FanoutDeliveryRequest | None = None
        self.results: dict[str, FanoutRecipientReceipt] = {}
        self.prepared: list[str] = []

    def admit(self, request: FanoutDeliveryRequest) -> str:
        if self.request is None:
            self.request = request
            return "admitted"
        assert self.request == request
        return "already_admitted"

    def result_for(
        self, request: FanoutDeliveryRequest, recipient: str
    ) -> FanoutRecipientReceipt | None:
        assert request == self.request
        return self.results.get(recipient)

    def prepare(self, request: FanoutDeliveryRequest, recipient: str) -> str:
        assert request == self.request
        if recipient not in self.prepared:
            self.prepared.append(recipient)
            return "prepared"
        return "already_prepared"

    def record(
        self, request: FanoutDeliveryRequest, receipt: FanoutRecipientReceipt
    ) -> str:
        assert request == self.request
        existing = self.results.get(receipt.recipient)
        if existing is not None:
            assert existing == receipt
            return "already_recorded"
        self.results[receipt.recipient] = receipt
        return "recorded"

    def complete(self, request: FanoutDeliveryRequest) -> FanoutDeliveryResult:
        return FanoutDeliveryResult(
            request, tuple(self.results[item] for item in request.recipients)
        )


class Sink:
    def __init__(self, *, crash_after: str | None = None) -> None:
        self.accepted: dict[str, FanoutRecipientReceipt] = {}
        self.calls: list[str] = []
        self.crash_after = crash_after

    async def readback(
        self, *, recipient: str, idempotency_key: str
    ) -> FanoutRecipientReceipt | None:
        return self.accepted.get(idempotency_key)

    async def deliver(
        self,
        *,
        recipient: str,
        message: str,
        message_sha256: str,
        idempotency_key: str,
    ) -> FanoutRecipientReceipt:
        self.calls.append(recipient)
        receipt = FanoutRecipientReceipt(
            recipient,
            idempotency_key,
            message_sha256,
            "rejected" if recipient == "sink-c" else "accepted",
            None if recipient == "sink-c" else f"provider:{recipient}:1",
            "recipient_rejected" if recipient == "sink-c" else None,
            NOW,
        )
        if receipt.status == "accepted":
            self.accepted[idempotency_key] = receipt
        if self.crash_after == recipient:
            self.crash_after = None
            raise SystemExit("simulated process death after provider acceptance")
        return receipt


def request() -> FanoutDeliveryRequest:
    return FanoutDeliveryRequest(
        "fanout:test", "jaap", "Testbericht", ("sink-a", "sink-b", "sink-c"), NOW
    )


def test_fanout_records_two_acceptances_and_one_rejection_without_resend() -> None:
    store = Store()
    sink = Sink()
    result = asyncio.run(FanoutDeliveryWorker(store, sink).run(request()))

    assert result.accepted_count == 2
    assert result.rejected_count == 1
    assert sink.calls == ["sink-a", "sink-b", "sink-c"]
    assert [item.status for item in result.receipts] == [
        "accepted",
        "accepted",
        "rejected",
    ]


def test_restart_reads_accepted_effect_and_does_not_send_it_twice() -> None:
    store = Store()
    sink = Sink(crash_after="sink-a")
    worker = FanoutDeliveryWorker(store, sink)

    try:
        asyncio.run(worker.run(request()))
    except SystemExit:
        pass
    result = asyncio.run(worker.run(request()))

    assert result.accepted_count == 2
    assert result.rejected_count == 1
    assert sink.calls.count("sink-a") == 1
