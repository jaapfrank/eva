"""Bounded Stripe Payment Link publication with independent readback."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
from datetime import datetime
import json
from typing import Any
from urllib import error, parse, request as urllib_request

from eva.application.commerce import (
    CommerceError,
    ProductPublicationApproval,
    ProductPublicationPlan,
    ProductPublicationReceipt,
)


@dataclass(frozen=True, slots=True)
class StripePublicationConfig:
    account_id: str
    secret_key: str = field(repr=False)
    fulfillment_base_url: str = ""
    live_mode: bool = False
    base_url: str = "https://api.stripe.com"
    api_version: str = "2025-06-30.basil"
    request_timeout_seconds: int = 15

    def __post_init__(self) -> None:
        parsed = parse.urlsplit(self.base_url)
        if not parsed.netloc or (
            parsed.scheme != "https"
            and not (
                parsed.scheme == "http"
                and parsed.hostname in {"127.0.0.1", "::1", "localhost"}
            )
        ):
            raise ValueError("Stripe API URL must be HTTPS")
        fulfillment = parse.urlsplit(self.fulfillment_base_url)
        if (
            not fulfillment.netloc
            or fulfillment.query
            or fulfillment.fragment
            or fulfillment.path not in {"", "/"}
            or (
                fulfillment.scheme != "https"
                and not (
                    fulfillment.scheme == "http"
                    and fulfillment.hostname in {"127.0.0.1", "::1", "localhost"}
                )
            )
        ):
            raise ValueError("commerce fulfillment base URL must be HTTPS")
        if not self.account_id.startswith("acct_") or len(self.account_id) > 69:
            raise ValueError("Stripe account ID is invalid")
        if not self.secret_key or len(self.secret_key) > 4096:
            raise ValueError("Stripe secret key is invalid")
        if not isinstance(self.live_mode, bool):
            raise ValueError("Stripe mode is invalid")
        if parsed.scheme == "https":
            expected_prefixes = (
                ("sk_live_", "rk_live_")
                if self.live_mode
                else ("sk_test_", "rk_test_")
            )
            if not self.secret_key.startswith(expected_prefixes):
                raise ValueError("Stripe key mode differs from configured mode")
        if not self.api_version or len(self.api_version) > 64:
            raise ValueError("Stripe API version is invalid")
        if not 2 <= self.request_timeout_seconds <= 30:
            raise ValueError("Stripe timeout is invalid")


class StripePublicationAdapter:
    """Create one hash-bound Payment Link and verify it from Stripe reads."""

    def __init__(self, config: StripePublicationConfig) -> None:
        self._config = config

    @property
    def account_id(self) -> str:
        return self._config.account_id

    @property
    def live_mode(self) -> bool:
        return self._config.live_mode

    async def publish(
        self,
        plan: ProductPublicationPlan,
        approval: ProductPublicationApproval,
        *,
        now: datetime,
    ) -> ProductPublicationReceipt:
        approval.authorize(plan, now=now)
        if plan.provider_account_id != self._config.account_id:
            raise CommerceError("configured Stripe account differs from publication plan")
        if plan.live_mode is not self._config.live_mode:
            raise CommerceError("configured Stripe mode differs from publication plan")
        await self._verify_account()
        encoded = parse.urlencode(
            {
                "after_completion[redirect][url]": (
                    self._config.fulfillment_base_url.rstrip("/")
                    + "/public/v1/commerce/orders/{CHECKOUT_SESSION_ID}/download"
                ),
                "after_completion[type]": "redirect",
                "line_items[0][price_data][currency]": "eur",
                "line_items[0][price_data][product_data][description]": (
                    plan.description
                ),
                "line_items[0][price_data][product_data][metadata][eva_release_sha256]": (
                    plan.release_sha256
                ),
                "line_items[0][price_data][product_data][name]": plan.headline,
                "line_items[0][price_data][unit_amount]": str(
                    plan.price_eur_cents
                ),
                "line_items[0][quantity]": "1",
                "metadata[eva_operation_id]": plan.operation_id,
                "metadata[eva_plan_sha256]": plan.sha256,
                "metadata[eva_release_sha256]": plan.release_sha256,
                "payment_intent_data[metadata][eva_plan_sha256]": plan.sha256,
                "payment_intent_data[metadata][eva_release_sha256]": (
                    plan.release_sha256
                ),
                "submit_type": "pay",
            }
        ).encode("ascii")
        created = await self._request_json(
            "POST",
            "/v1/payment_links",
            body=encoded,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Idempotency-Key": plan.operation_id,
            },
        )
        resource_id = created.get("id")
        if not isinstance(resource_id, str):
            raise CommerceError("Stripe publication response has no resource ID")
        return await self.readback(plan, resource_id=resource_id, now=now)

    async def readback(
        self,
        plan: ProductPublicationPlan,
        *,
        resource_id: str,
        now: datetime,
    ) -> ProductPublicationReceipt:
        if not resource_id.startswith("plink_") or len(resource_id) > 134:
            raise ValueError("Stripe Payment Link ID is invalid")
        if plan.provider_account_id != self._config.account_id:
            raise CommerceError("configured Stripe account differs from publication plan")
        if plan.live_mode is not self._config.live_mode:
            raise CommerceError("configured Stripe mode differs from publication plan")
        await self._verify_account()
        quoted = parse.quote(resource_id, safe="")
        link, lines = await asyncio.gather(
            self._request_json("GET", f"/v1/payment_links/{quoted}"),
            self._request_json(
                "GET",
                f"/v1/payment_links/{quoted}/line_items?limit=2"
                "&expand%5B%5D=data.price.product",
            ),
        )
        return self._receipt(plan, link=link, lines=lines, now=now)

    async def _verify_account(self) -> None:
        account = await self._request_json("GET", "/v1/account")
        if account.get("id") != self._config.account_id:
            raise CommerceError("Stripe credential belongs to another account")

    def _receipt(
        self,
        plan: ProductPublicationPlan,
        *,
        link: dict[str, Any],
        lines: dict[str, Any],
        now: datetime,
    ) -> ProductPublicationReceipt:
        metadata = link.get("metadata")
        data = lines.get("data")
        if not isinstance(metadata, dict) or not isinstance(data, list) or len(data) != 1:
            raise CommerceError("Stripe publication readback is incomplete")
        line = data[0]
        price = line.get("price") if isinstance(line, dict) else None
        product = price.get("product") if isinstance(price, dict) else None
        product_metadata = product.get("metadata") if isinstance(product, dict) else None
        if (
            metadata.get("eva_operation_id") != plan.operation_id
            or metadata.get("eva_plan_sha256") != plan.sha256
            or metadata.get("eva_release_sha256") != plan.release_sha256
            or not isinstance(product_metadata, dict)
            or product_metadata.get("eva_release_sha256") != plan.release_sha256
            or not isinstance(price, dict)
            or price.get("currency") != "eur"
            or price.get("unit_amount") != plan.price_eur_cents
            or line.get("quantity") != 1
            or link.get("livemode") is not plan.live_mode
            or link.get("after_completion")
            != {
                "redirect": {
                    "url": self._config.fulfillment_base_url.rstrip("/")
                    + "/public/v1/commerce/orders/{CHECKOUT_SESSION_ID}/download"
                },
                "type": "redirect",
            }
        ):
            raise CommerceError("Stripe publication readback differs from approved plan")
        resource_id = link.get("id")
        public_url = link.get("url")
        active = link.get("active")
        if (
            not isinstance(resource_id, str)
            or not isinstance(public_url, str)
            or not isinstance(active, bool)
        ):
            raise CommerceError("Stripe publication readback fields are invalid")
        return ProductPublicationReceipt(
            provider=plan.provider,
            provider_account_id=plan.provider_account_id,
            provider_resource_id=resource_id,
            public_url=public_url,
            plan_sha256=plan.sha256,
            release_sha256=plan.release_sha256,
            price_eur_cents=plan.price_eur_cents,
            live_mode=plan.live_mode,
            active=active,
            observed_at=now,
        )

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._request_json_sync, method, path, body, headers or {}
        )

    def _request_json_sync(
        self,
        method: str,
        path: str,
        body: bytes | None,
        headers: dict[str, str],
    ) -> dict[str, Any]:
        authorization = base64.b64encode(
            f"{self._config.secret_key}:".encode("utf-8")
        ).decode("ascii")
        req = urllib_request.Request(
            self._config.base_url.rstrip("/") + path,
            data=body,
            method=method,
            headers={
                "Accept": "application/json",
                "Authorization": f"Basic {authorization}",
                "Stripe-Version": self._config.api_version,
                **headers,
            },
        )
        try:
            with urllib_request.urlopen(
                req, timeout=self._config.request_timeout_seconds
            ) as response:
                raw = response.read(1_048_577)
        except error.HTTPError as exc:
            raise CommerceError(f"Stripe publication request failed ({exc.code})") from exc
        if len(raw) > 1_048_576:
            raise CommerceError("Stripe publication response is too large")
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CommerceError("Stripe publication response is not JSON") from exc
        if not isinstance(value, dict):
            raise CommerceError("Stripe publication response is invalid")
        return value


__all__ = ["StripePublicationAdapter", "StripePublicationConfig"]
