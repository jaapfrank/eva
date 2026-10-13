"""Exercise publication -> signed payment -> fulfillment on real PostgreSQL."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from threading import Thread
from urllib import parse

import sqlalchemy as sa

from eva.adapters.connectors.stripe import (
    StripePublicationAdapter,
    StripePublicationConfig,
)
from eva.adapters.connectors.stripe_webhook import (
    StripeWebhookConfig,
    StripeWebhookVerifier,
)
from eva.adapters.postgres.commerce import (
    PostgresPaidOrderStore,
    PostgresProductPublicationStore,
)
from eva.adapters.postgres.conversation_actions import (
    PostgresPersonalProductReleaseStore,
)
from eva.application.commerce import ProductPublicationApproval, publication_plan
from eva.bootstrap.runtime import PostgresHindsightMemoryRuntime


TENANT = "live-test-tenant"
INSTALLATION = "live-test-installation"
PRINCIPAL = "live-test-jaap"
ACCOUNT = "acct_gate12345678"
WEBHOOK_SECRET = "whsec_gate_signing_secret_12345678"
PAYMENT_LINK = "plink_gate12345678"
PUBLIC_BASE = "http://127.0.0.1"


class StripeContractServer:
    def __init__(self) -> None:
        self.plan = None
        self.posts: list[dict[str, list[str]]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format: str, *_args: object) -> None:
                pass

            def do_GET(self) -> None:  # noqa: N802
                plan = outer.plan
                assert plan is not None
                if self.path == "/v1/account":
                    self._send({"id": ACCOUNT})
                elif self.path == f"/v1/payment_links/{PAYMENT_LINK}":
                    self._send(
                        {
                            "active": True,
                            "after_completion": {
                                "redirect": {
                                    "url": PUBLIC_BASE
                                    + "/public/v1/commerce/orders/"
                                    "{CHECKOUT_SESSION_ID}/download"
                                },
                                "type": "redirect",
                            },
                            "id": PAYMENT_LINK,
                            "livemode": False,
                            "metadata": {
                                "eva_operation_id": plan.operation_id,
                                "eva_plan_sha256": plan.sha256,
                                "eva_release_sha256": plan.release_sha256,
                            },
                            "url": "https://buy.stripe.com/test_gate12345678",
                        }
                    )
                elif self.path.startswith(
                    f"/v1/payment_links/{PAYMENT_LINK}/line_items"
                ):
                    self._send(
                        {
                            "data": [
                                {
                                    "quantity": 1,
                                    "price": {
                                        "currency": "eur",
                                        "unit_amount": plan.price_eur_cents,
                                        "product": {
                                            "metadata": {
                                                "eva_release_sha256": plan.release_sha256
                                            }
                                        },
                                    },
                                }
                            ]
                        }
                    )
                else:
                    self.send_error(404)

            def do_POST(self) -> None:  # noqa: N802
                size = int(self.headers.get("Content-Length", "0"))
                form = parse.parse_qs(
                    self.rfile.read(size).decode("ascii"), keep_blank_values=True
                )
                form["_idempotency_key"] = [
                    self.headers.get("Idempotency-Key", "")
                ]
                outer.posts.append(form)
                self._send({"id": PAYMENT_LINK}, status=201)

            def _send(self, payload: object, *, status: int = 200) -> None:
                body = json.dumps(payload, separators=(",", ":")).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> StripeContractServer:
        self.thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()


def main() -> None:
    dsn = os.environ["EVA_GATE_DATABASE_DSN"]
    engine = sa.create_engine(dsn, pool_pre_ping=False)
    now = datetime.now(tz=UTC).replace(microsecond=0)
    releases = PostgresPersonalProductReleaseStore(engine)
    artifact = releases.latest(
        tenant_id=TENANT,
        installation_id=INSTALLATION,
        principal_id=PRINCIPAL,
        now=now,
    )
    if artifact is None:
        raise RuntimeError("the live B timeline has no release candidate")
    plan = publication_plan(
        artifact,
        tenant_id=TENANT,
        installation_id=INSTALLATION,
        principal_id=PRINCIPAL,
        provider_account_id=ACCOUNT,
        live_mode=False,
        created_at=now,
    )
    publications = PostgresProductPublicationStore(
        engine, tenant_id=TENANT, installation_id=INSTALLATION
    )
    publications.retain_plan(plan)
    approval = ProductPublicationApproval(
        approval_id=f"approval:gate:{plan.sha256[:32]}",
        plan_sha256=plan.sha256,
        release_sha256=plan.release_sha256,
        provider_account_id=ACCOUNT,
        live_mode=False,
        approved_by=PRINCIPAL,
        approved_at=now,
        expires_at=now + timedelta(minutes=15),
    )
    publications.approve(plan, approval, now=now)
    publications.open_call(
        plan_sha256=plan.sha256,
        approval_sha256=approval.sha256,
        now=now,
    )
    with StripeContractServer() as provider:
        provider.plan = plan
        adapter = StripePublicationAdapter(
            StripePublicationConfig(
                account_id=ACCOUNT,
                secret_key="sk_test_gate_private",
                fulfillment_base_url=PUBLIC_BASE,
                base_url=(
                    f"http://127.0.0.1:{provider.server.server_port}"
                ),
            )
        )
        publication_receipt = asyncio.run(
            adapter.publish(plan, approval, now=now + timedelta(seconds=1))
        )
        form = provider.posts[0]
    publications.record_verified(
        plan,
        approval,
        publication_receipt,
        now=publication_receipt.observed_at,
    )

    checkout_session_id = "cs_test_" + plan.sha256[:24]
    event = {
        "id": "evt_" + plan.sha256[24:48],
        "type": "checkout.session.completed",
        "created": int((now + timedelta(seconds=2)).timestamp()),
        "livemode": False,
        "data": {
            "object": {
                "object": "checkout.session",
                "id": checkout_session_id,
                "created": int((now + timedelta(seconds=2)).timestamp()),
                "status": "complete",
                "payment_status": "paid",
                "currency": "eur",
                "amount_total": plan.price_eur_cents,
                "payment_link": PAYMENT_LINK,
                "metadata": {
                    "eva_plan_sha256": plan.sha256,
                    "eva_release_sha256": plan.release_sha256,
                },
                "customer_details": {"email": "gate@example.invalid"},
            }
        },
    }
    body = json.dumps(event, separators=(",", ":")).encode()
    signed_at = now + timedelta(seconds=2)
    timestamp = int(signed_at.timestamp())
    digest = hmac.new(
        WEBHOOK_SECRET.encode(),
        str(timestamp).encode() + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    paid = StripeWebhookVerifier(
        StripeWebhookConfig(signing_secret=WEBHOOK_SECRET)
    ).verify(body, f"t={timestamp},v1={digest}", now=signed_at)
    orders = PostgresPaidOrderStore(
        engine, tenant_id=TENANT, installation_id=INSTALLATION
    )
    first_order = orders.retain_paid(paid, publication_receipt)
    restarted_orders = PostgresPaidOrderStore(
        engine, tenant_id=TENANT, installation_id=INSTALLATION
    )
    duplicate_order = restarted_orders.retain_paid(paid, publication_receipt)

    runtime = object.__new__(PostgresHindsightMemoryRuntime)
    runtime._engine = engine
    runtime._release_store = releases
    runtime._commerce_tenant_id = TENANT
    runtime._commerce_installation_id = INSTALLATION
    first_download = runtime.paid_product_download(
        checkout_session_id=checkout_session_id
    )
    second_download = runtime.paid_product_download(
        checkout_session_id=checkout_session_id
    )
    publication_events = publications._timeline.events(
        publications.thread_id(plan.sha256)
    )
    order_events = restarted_orders._timeline.events(
        restarted_orders.thread_id(checkout_session_id)
    )
    result = {
        "artifact_byte_length": len(first_download.content),
        "artifact_sha256": hashlib.sha256(first_download.content).hexdigest(),
        "checkout_session_id": checkout_session_id,
        "duplicate_order": duplicate_order,
        "first_order": first_order,
        "fulfillment_grant_sha256": first_download.grant.sha256,
        "idempotency_key_matches_plan": form["_idempotency_key"]
        == [plan.operation_id],
        "order_events": [event.event_type for event in order_events],
        "plan_sha256": plan.sha256,
        "publication_events": [event.event_type for event in publication_events],
        "redirect_is_checkout_bound": form["after_completion[redirect][url]"]
        == [
            PUBLIC_BASE
            + "/public/v1/commerce/orders/{CHECKOUT_SESSION_ID}/download"
        ],
        "repeat_download_identical": (
            first_download.content == second_download.content
            and first_download.grant == second_download.grant
        ),
        "release_sha256": artifact.release.content_sha256,
    }
    if result["artifact_sha256"] != result["release_sha256"]:
        raise RuntimeError("downloaded artifact differs from live B release")
    if result["publication_events"] != [
        "PRODUCT_PUBLICATION_PLANNED",
        "PRODUCT_PUBLICATION_APPROVED",
        "PRODUCT_PUBLICATION_CALL_OPENED",
        "PRODUCT_PUBLICATION_VERIFIED",
    ]:
        raise RuntimeError("publication timeline differs")
    if result["order_events"] != ["PAYMENT_VERIFIED", "FULFILLMENT_GRANTED"]:
        raise RuntimeError("order timeline differs")
    if not all(
        (
            result["idempotency_key_matches_plan"],
            result["redirect_is_checkout_bound"],
            result["repeat_download_identical"],
            duplicate_order == "already_recorded",
        )
    ):
        raise RuntimeError("paid fulfillment gate failed")
    print(json.dumps(result, indent=2, sort_keys=True))
    engine.dispose()


if __name__ == "__main__":
    main()
