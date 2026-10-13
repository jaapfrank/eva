from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]
COMPOSE = ROOT / "deploy" / "living-core" / "commerce-stripe.compose.yml"


def test_stripe_publication_is_an_optional_secret_file_only_override() -> None:
    document = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    core = document["services"]["core"]
    environment = core["environment"]

    assert environment["EVA_STRIPE_SECRET_KEY_FILE"] == (
        "/run/secrets/stripe_secret_key"
    )
    assert "EVA_STRIPE_SECRET_KEY" not in environment
    assert environment["EVA_STRIPE_LIVE_MODE"] == "${EVA_STRIPE_LIVE_MODE:-false}"
    assert environment["EVA_STRIPE_WEBHOOK_SECRET_FILE"] == (
        "/run/secrets/stripe_webhook_secret"
    )
    assert "EVA_STRIPE_WEBHOOK_SECRET" not in environment
    assert core["secrets"] == ["stripe_secret_key", "stripe_webhook_secret"]
    assert document["secrets"]["stripe_secret_key"]["file"].startswith(
        "${EVA_STRIPE_SECRET_KEY_FILE_HOST:"
    )
    assert document["secrets"]["stripe_webhook_secret"]["file"].startswith(
        "${EVA_STRIPE_WEBHOOK_SECRET_FILE_HOST:"
    )
