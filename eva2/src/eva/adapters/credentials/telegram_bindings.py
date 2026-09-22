"""Read the existing trusted Telegram principal binding file."""
import json
from eva.adapters.credentials.runtime_files import read_runtime_secret_file
from eva.application.telegram_ingress import TelegramPrincipalBinding, TelegramPrincipalBindings


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


