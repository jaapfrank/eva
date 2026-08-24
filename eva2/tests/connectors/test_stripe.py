from __future__ import annotations

import asyncio
import base64
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from urllib import parse
import threading

import pytest

from eva.adapters.connectors.stripe import (
    StripePublicationAdapter,
    StripePublicationConfig,
)
from eva.application.commerce import (
    CommerceError,
    ProductPublicationApproval,
    publication_plan,
)
from eva.application.personal_interests import (
    PersonalProductListing,
    PersonalProductRelease,
    PersonalProductReleaseArtifact,
)


NOW = datetime(2026, 8, 24, 10, 0, tzinfo=UTC)


def _plan():  # type: ignore[no-untyped-def]
    artifact = PersonalProductReleaseArtifact(
        "intent:planner:001",
        PersonalProductRelease(
            PersonalProductListing(
                "Slim weekmenu zonder gedoe",
                "Een compacte planner voor avondeten en boodschappen.",
                499,
                ("weekmenu", "planner", "boodschappen"),
                "chatgpt_codex",
            ),
            "weekmenu-bundel.zip",
            b"PK\x03\x04bound-release",
        ),
    )
    return publication_plan(
        artifact,
        tenant_id="tenant:live-test",
        installation_id="installation:live-test",
        principal_id="principal:jaap",
        provider_account_id="acct_12345678",
        live_mode=False,
        created_at=NOW,
    )


def _approval(plan):  # type: ignore[no-untyped-def]
    return ProductPublicationApproval(
        "approval:publication:001",
        plan.sha256,
        plan.release_sha256,
        plan.provider_account_id,
        plan.live_mode,
        "principal:jaap",
        NOW,
        NOW + timedelta(minutes=15),
    )


class _StripeServer:
    def __init__(self, *, wrong_price: bool = False) -> None:
        self.requests: list[dict[str, object]] = []
        self.wrong_price = wrong_price
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format: str, *_args: object) -> None:
                pass

            def do_GET(self) -> None:  # noqa: N802
                outer.requests.append(
                    {
                        "method": "GET",
                        "path": self.path,
                        "authorization": self.headers.get("Authorization"),
                        "stripe_version": self.headers.get("Stripe-Version"),
                    }
                )
                if self.path == "/v1/account":
                    self._send({"id": "acct_12345678", "object": "account"})
                    return
                if self.path.startswith("/v1/payment_links/plink_12345678/line_items"):
                    self._send(
                        {
                            "data": [
                                {
                                    "quantity": 1,
                                    "price": {
                                        "currency": "eur",
                                        "unit_amount": 500 if outer.wrong_price else 499,
                                        "product": {
                                            "metadata": {
                                                "eva_release_sha256": _plan().release_sha256
                                            }
                                        },
                                    },
                                }
                            ],
                            "object": "list",
                        }
                    )
                    return
                if self.path == "/v1/payment_links/plink_12345678":
                    plan = _plan()
                    self._send(
                        {
                            "active": True,
                            "after_completion": {
                                "redirect": {
                                    "url": "https://eva.example/public/v1/commerce/orders/{CHECKOUT_SESSION_ID}/download"
                                },
                                "type": "redirect",
                            },
                            "id": "plink_12345678",
                            "livemode": False,
                            "metadata": {
                                "eva_operation_id": plan.operation_id,
                                "eva_plan_sha256": plan.sha256,
                                "eva_release_sha256": plan.release_sha256,
                            },
                            "url": "https://buy.stripe.com/test_12345678",
                        }
                    )
                    return
                self.send_error(404)

            def do_POST(self) -> None:  # noqa: N802
                size = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(size).decode("ascii")
                outer.requests.append(
                    {
                        "body": parse.parse_qs(body, keep_blank_values=True),
                        "idempotency_key": self.headers.get("Idempotency-Key"),
                        "method": "POST",
                        "path": self.path,
                    }
                )
                self._send({"id": "plink_12345678", "object": "payment_link"}, 201)

            def _send(self, value: object, status: int = 200) -> None:
                body = json.dumps(value).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):  # type: ignore[no-untyped-def]
        self.thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()


def _adapter(server: _StripeServer) -> StripePublicationAdapter:
    return StripePublicationAdapter(
        StripePublicationConfig(
            account_id="acct_12345678",
            secret_key="sk_test_private",
            fulfillment_base_url="https://eva.example",
            base_url=f"http://127.0.0.1:{server.server.server_port}",
        )
    )


def test_stripe_publish_uses_idempotency_and_independent_exact_readback() -> None:
    plan = _plan()
    with _StripeServer() as server:
        receipt = asyncio.run(
            _adapter(server).publish(
                plan, _approval(plan), now=NOW + timedelta(minutes=1)
            )
        )

    post = next(item for item in server.requests if item["method"] == "POST")
    form = post["body"]
    assert post["idempotency_key"] == plan.operation_id
    assert form["metadata[eva_plan_sha256]"] == [plan.sha256]
    assert form["metadata[eva_release_sha256]"] == [plan.release_sha256]
    assert form["line_items[0][price_data][unit_amount]"] == ["499"]
    assert form["after_completion[type]"] == ["redirect"]
    assert form["after_completion[redirect][url]"] == [
        "https://eva.example/public/v1/commerce/orders/{CHECKOUT_SESSION_ID}/download"
    ]
    assert receipt.provider_resource_id == "plink_12345678"
    assert receipt.public_url == "https://buy.stripe.com/test_12345678"
    assert receipt.plan_sha256 == plan.sha256
    assert receipt.release_sha256 == plan.release_sha256
    assert receipt.active is True
    account_reads = [item for item in server.requests if item["path"] == "/v1/account"]
    assert len(account_reads) == 2
    expected_auth = "Basic " + base64.b64encode(b"sk_test_private:").decode("ascii")
    assert all(item["authorization"] == expected_auth for item in account_reads)


def test_stripe_readback_rejects_changed_price() -> None:
    plan = _plan()
    with _StripeServer(wrong_price=True) as server:
        with pytest.raises(CommerceError, match="differs from approved plan"):
            asyncio.run(
                _adapter(server).readback(
                    plan,
                    resource_id="plink_12345678",
                    now=NOW + timedelta(minutes=1),
                )
            )
