from __future__ import annotations

from eva.application.telegram_ingress import (
    TelegramPrincipalBinding,
    TelegramPrincipalBindings,
)
from eva.bootstrap import telegram_ingress


def test_v2_binding_file_carries_exact_database_binding(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        telegram_ingress,
        "read_runtime_secret_file",
        lambda path, *, setting_name: (
            '{"bindings":[{"allowed_chat_ids":["101"],'
            '"principal_binding_id":"44444444-4444-4444-4444-444444444444",'
            '"principal_id":"33333333-3333-3333-3333-333333333333",'
            '"telegram_user_id":101}],'
            '"contract":"EvaTelegramPrincipalBindings/v2"}'
        ),
    )

    bindings = telegram_ingress.load_principal_bindings("/run/eva/bindings")

    assert bindings.resolve(101, "101") == (
        "33333333-3333-3333-3333-333333333333"
    )
    assert bindings.resolve_binding_id(101, "101").hex == (
        "44444444444444444444444444444444"
    )


def test_main_wires_one_pairing_store_as_responder_and_dynamic_resolver(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    bindings = TelegramPrincipalBindings(
        (TelegramPrincipalBinding(101, "person:jaap", ("101",)),)
    )
    pairing_store = object()
    pairing_responder = object()
    provider = object()
    core = object()
    captured: dict[str, object] = {}

    class Engine:
        def dispose(self) -> None:
            captured["disposed"] = True

    class StoppedEvent:
        def is_set(self) -> bool:
            return True

    monkeypatch.setattr(
        telegram_ingress,
        "_required_environment",
        lambda name: name,
    )
    monkeypatch.setattr(
        telegram_ingress,
        "read_runtime_secret_file",
        lambda path, *, setting_name: (
            "p" * 32
            if setting_name == "EVA_TELEGRAM_PAIRING_SECRET_FILE"
            else "postgresql://eva:test@postgres/eva"
            if setting_name == "EVA_DATABASE_DSN_FILE"
            else "test-value"
        ),
    )
    monkeypatch.setattr(
        telegram_ingress, "load_principal_bindings", lambda path: bindings
    )
    monkeypatch.setattr(
        telegram_ingress.sa, "create_engine", lambda dsn, **kwargs: Engine()
    )
    monkeypatch.setattr(
        telegram_ingress, "TelegramBotApiAdapter", lambda **kwargs: provider
    )
    monkeypatch.setattr(
        telegram_ingress, "LivingCoreTurnHttpAdapter", lambda **kwargs: core
    )
    monkeypatch.setattr(
        telegram_ingress,
        "PostgresTelegramIngressStore",
        lambda *args, **kwargs: object(),
    )

    def create_pairing_store(*args, **kwargs):  # type: ignore[no-untyped-def]
        captured["pairing_store_created"] = True
        return pairing_store

    def create_pairing_responder(store, **kwargs):  # type: ignore[no-untyped-def]
        assert store is pairing_store
        captured["pairing_secret"] = kwargs["derivation_secret"]
        return pairing_responder

    def create_worker(*args, **kwargs):  # type: ignore[no-untyped-def]
        captured["pairing_responder"] = args[4]
        captured["dynamic_bindings"] = kwargs["dynamic_bindings"]
        captured["reply_drafts"] = kwargs["reply_drafts"]
        captured["streaming_core"] = kwargs["streaming_core"]
        return object()

    monkeypatch.setattr(
        telegram_ingress, "PostgresTelegramPairingStore", create_pairing_store
    )
    monkeypatch.setattr(
        telegram_ingress, "TelegramPairingResponder", create_pairing_responder
    )
    monkeypatch.setattr(
        telegram_ingress,
        "PostgresTelegramGroupConsentStore",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(
        telegram_ingress, "TelegramGroupConsentResponder", lambda store: object()
    )
    monkeypatch.setattr(telegram_ingress, "TelegramIngressWorker", create_worker)
    monkeypatch.setattr(telegram_ingress, "Event", StoppedEvent)
    monkeypatch.setattr(telegram_ingress.signal, "signal", lambda *args: None)

    assert telegram_ingress.main() == 0
    assert captured == {
        "disposed": True,
        "dynamic_bindings": pairing_store,
        "pairing_responder": pairing_responder,
        "pairing_secret": b"p" * 32,
        "pairing_store_created": True,
        "reply_drafts": provider,
        "streaming_core": core,
    }
