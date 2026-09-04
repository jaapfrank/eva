"""Compose and run the opt-in B-native Telegram polling connector.

This executable wiring belongs at the composition root: it selects concrete
transport and persistence adapters but adds no Telegram rules to Eva's core.
"""

from __future__ import annotations

import json
import os
import signal
from threading import Event
from uuid import UUID

import sqlalchemy as sa

from eva.adapters.connectors.telegram import (
    LivingCoreTurnHttpAdapter,
    TelegramBotApiAdapter,
)
from eva.adapters.postgres.telegram_ingress import PostgresTelegramIngressStore
from eva.adapters.postgres.telegram_group_consent import (
    PostgresTelegramGroupConsentStore,
)
from eva.adapters.postgres.telegram_pairing import PostgresTelegramPairingStore
from eva.adapters.postgres.connection import sqlalchemy_postgres_dsn
from eva.adapters.postgres.delegated_work import PostgresDelegatedWorkStore
from eva.adapters.postgres.operational_timeline import external_timeline_scope
from eva.application.telegram_ingress import (
    TelegramIngressWorker,
    TelegramPrincipalBinding,
    TelegramPrincipalBindings,
)
from eva.application.telegram_group_consent import TelegramGroupConsentResponder
from eva.application.telegram_pairing import TelegramPairingResponder
from eva.adapters.credentials.runtime_files import read_runtime_secret_file
from eva.adapters.credentials.runtime_files import (
    required_environment as _required_environment,
)
from eva.bootstrap.delegated_work_commands import DelegatedWorkConversationCommands


def load_principal_bindings(path_value: str) -> TelegramPrincipalBindings:
    raw = read_runtime_secret_file(
        path_value, setting_name="EVA_TELEGRAM_PRINCIPAL_BINDINGS_FILE"
    )
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError("Telegram principal bindings are invalid JSON") from error
    contract = decoded.get("contract") if isinstance(decoded, dict) else None
    if (
        not isinstance(decoded, dict)
        or set(decoded) != {"bindings", "contract"}
        or contract not in {
            "EvaTelegramPrincipalBindings/v1",
            "EvaTelegramPrincipalBindings/v2",
        }
        or not isinstance(decoded.get("bindings"), list)
    ):
        raise RuntimeError("Telegram principal bindings contract differs")
    bindings: list[TelegramPrincipalBinding] = []
    for item in decoded["bindings"]:
        expected_keys = (
            {"allowed_chat_ids", "principal_id", "telegram_user_id"}
            if contract == "EvaTelegramPrincipalBindings/v1"
            else {
                "allowed_chat_ids",
                "principal_binding_id",
                "principal_id",
                "telegram_user_id",
            }
        )
        if (
            not isinstance(item, dict)
            or set(item) != expected_keys
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
                item.get("principal_binding_id"),
            )
        )
    return TelegramPrincipalBindings(tuple(bindings))


def _delegated_commands(
    engine: sa.Engine,
    *,
    tenant_id: str,
    installation_id: str,
    bindings: TelegramPrincipalBindings,
) -> DelegatedWorkConversationCommands | None:
    names = (
        "EVA_DELEGATED_WORK_OWNER_PRINCIPAL_ID",
        "EVA_DELEGATED_WORK_OWNER_BINDING_ID",
    )
    values = {name: os.environ.get(name, "").strip() for name in names}
    if not any(values.values()):
        return None
    if not all(values.values()):
        raise RuntimeError("delegated Telegram command authority is incomplete")
    try:
        scope = external_timeline_scope(
            tenant_id,
            installation_id,
            "delegated Telegram command scope is required",
        )
        owner = UUID(values["EVA_DELEGATED_WORK_OWNER_PRINCIPAL_ID"])
        binding = UUID(values["EVA_DELEGATED_WORK_OWNER_BINDING_ID"])
    except ValueError as error:
        raise RuntimeError("delegated Telegram command authority is invalid") from error
    if not bindings.contains_binding(
        principal_id=str(owner), principal_binding_id=binding
    ):
        raise RuntimeError(
            "delegated Telegram owner binding is absent from the trusted bindings"
        )
    return DelegatedWorkConversationCommands(
        PostgresDelegatedWorkStore(engine, scope),
        owner_principal_id=owner,
        principal_binding_id=binding,
    )


def main() -> int:
    database_dsn = read_runtime_secret_file(
        _required_environment("EVA_DATABASE_DSN_FILE"),
        setting_name="EVA_DATABASE_DSN_FILE",
    )
    token = read_runtime_secret_file(
        _required_environment("EVA_TELEGRAM_BOT_TOKEN_FILE"),
        setting_name="EVA_TELEGRAM_BOT_TOKEN_FILE",
    )
    pairing_secret = read_runtime_secret_file(
        _required_environment("EVA_TELEGRAM_PAIRING_SECRET_FILE"),
        setting_name="EVA_TELEGRAM_PAIRING_SECRET_FILE",
    ).encode("utf-8")
    tenant_id = _required_environment("EVA_RUNTIME_TENANT_ID")
    installation_id = _required_environment("EVA_RUNTIME_INSTALLATION_ID")
    bindings = load_principal_bindings(
        _required_environment("EVA_TELEGRAM_PRINCIPAL_BINDINGS_FILE")
    )
    engine = sa.create_engine(sqlalchemy_postgres_dsn(database_dsn), pool_pre_ping=True)
    provider = TelegramBotApiAdapter(
        bot_token=token,
        api_base_url=os.environ.get(
            "EVA_TELEGRAM_API_BASE_URL", "https://api.telegram.org"
        ),
        timeout_seconds=10,
    )
    pairing_store = PostgresTelegramPairingStore(
        engine,
        tenant_id=tenant_id,
        installation_id=installation_id,
        trusted_bindings=bindings,
    )
    delegated_commands = _delegated_commands(
        engine,
        tenant_id=tenant_id,
        installation_id=installation_id,
        bindings=bindings,
    )
    core = LivingCoreTurnHttpAdapter(
        base_url=os.environ.get("EVA_LIVING_CORE_URL", "http://core:8080"),
        tenant_id=tenant_id,
        installation_id=installation_id,
    )
    worker = TelegramIngressWorker(
        provider,
        core,
        PostgresTelegramIngressStore(
            engine,
            tenant_id=tenant_id,
            installation_id=installation_id,
        ),
        bindings,
        TelegramPairingResponder(
            pairing_store,
            derivation_secret=pairing_secret,
        ),
        TelegramGroupConsentResponder(
            PostgresTelegramGroupConsentStore(
                engine, tenant_id=tenant_id, installation_id=installation_id
            )
        ),
        dynamic_bindings=pairing_store,
        delegated_work_commands=delegated_commands,
        tenant_id=tenant_id,
        installation_id=installation_id,
        reply_drafts=provider,
        streaming_core=core,
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
