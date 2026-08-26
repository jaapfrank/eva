"""Start the Telegram test ingress only after its in-container invariants hold."""

from __future__ import annotations

import os


TEST_ENVIRONMENT = {
    "EVA_LIVING_CORE_URL": "http://eva-test-core:8080",
    "EVA_TELEGRAM_API_BASE_URL": "https://api.telegram.org",
}


def main() -> int:
    """Refuse a generic ingress configuration inside the test image."""

    for name, expected in TEST_ENVIRONMENT.items():
        if os.environ.get(name) != expected:
            raise RuntimeError(f"Telegram test ingress {name} differs")
    from eva.operations import telegram_ingress

    return telegram_ingress.main()


if __name__ == "__main__":
    raise SystemExit(main())
