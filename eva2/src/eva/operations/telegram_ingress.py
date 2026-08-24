"""Run the opt-in B-native Telegram polling connector."""

from __future__ import annotations

import json
import os
import signal
from threading import Event

import sqlalchemy as sa

from eva.adapters.connectors.telegram import (
    LivingCoreTurnHttpAdapter,
    TelegramBotApiAdapter,
)
from eva.adapters.postgres.telegram_ingress import PostgresTelegramIngressStore
from eva.application.telegram_ingress import (
    TelegramIngressWorker,
    TelegramPrincipalBinding,
    TelegramPrincipalBindings,
)
from eva.bootstrap.secrets import read_runtime_secret_file


def load_principal_bindings(path_value: str) -> TelegramPrincipalBindings:
    raw = read_runtime_secret_file(
        path_value, setting_name="EVA_TELEGRAM_PRINCIPAL_BINDINGS_FILE"
    )
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError("Telegram principal bindings are invalid JSON") from error
    if (
        not isinstance(decoded, dict)
        or set(decoded) != {"bindings", "contract"}
        or decoded.get("contract") != "EvaTelegramPrincipalBindings/v1"
        or not isinstance(decoded.get("bindings"), list)
    ):
        raise RuntimeError("Telegram principal bindings contract differs")
    bindings: list[TelegramPrincipalBinding] = []
    for item in decoded["bindings"]:
        if (
            not isinstance(item, dict)
            or set(item)
            != {"allowed_chat_ids", "principal_id", "telegram_user_id"}
            or not isinstance(item.get("telegram_user_id"), int)
            or not isinstance(item.get("principal_id"), str)
            or not isinstance(item.get("allowed_chat_ids"), list)
            or not all(isinstance(value, str) for value in item["allowed_chat_ids"])
        ):
            raise RuntimeError("Telegram principal binding entry is invalid")
        bindings.append(
            TelegramPrincipalBinding(
                item["telegram_user_id"],
                item["principal_id"],
                tuple(item["allowed_chat_ids"]),
            )
        )
    return TelegramPrincipalBindings(tuple(bindings))


def _required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def main() -> int:
    database_dsn = read_runtime_secret_file(
        _required_environment("EVA_DATABASE_DSN_FILE"),
        setting_name="EVA_DATABASE_DSN_FILE",
    )
    token = read_runtime_secret_file(
        _required_environment("EVA_TELEGRAM_BOT_TOKEN_FILE"),
        setting_name="EVA_TELEGRAM_BOT_TOKEN_FILE",
    )
    tenant_id = _required_environment("EVA_RUNTIME_TENANT_ID")
    installation_id = _required_environment("EVA_RUNTIME_INSTALLATION_ID")
    bindings = load_principal_bindings(
        _required_environment("EVA_TELEGRAM_PRINCIPAL_BINDINGS_FILE")
    )
    engine = sa.create_engine(database_dsn, pool_pre_ping=True)
    provider = TelegramBotApiAdapter(
        bot_token=token,
        api_base_url=os.environ.get(
            "EVA_TELEGRAM_API_BASE_URL", "https://api.telegram.org"
        ),
        timeout_seconds=10,
    )
    worker = TelegramIngressWorker(
        provider,
        LivingCoreTurnHttpAdapter(
            base_url=os.environ.get("EVA_LIVING_CORE_URL", "http://core:8080"),
            tenant_id=tenant_id,
            installation_id=installation_id,
        ),
        PostgresTelegramIngressStore(
            engine,
            tenant_id=tenant_id,
            installation_id=installation_id,
        ),
        bindings,
    )
    stop = Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        while not stop.is_set():
            try:
                worker.run_once()
            except Exception as error:
                print(
                    f"Telegram ingress retry after {type(error).__name__}",
                    flush=True,
                )
                if stop.wait(2):
                    break
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["load_principal_bindings", "main"]
