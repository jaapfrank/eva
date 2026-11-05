"""Telegram-specific authority and receipt checks for private completion delivery."""
from eva.adapters.postgres.telegram_pairing import PostgresPrivateTelegramRecipient


class TelegramDeliveryAuthority:
    """An adapter; generic claim fencing never resolves Telegram pairings."""

    def __init__(self, engine, scope, bindings_loader):
        self._engine, self._scope, self._bindings = engine, scope, bindings_loader

    def validate(self, destination, *, now):
        recipient = PostgresPrivateTelegramRecipient(self._engine, self._scope,
            owner=destination.owner_principal_id,
            binding=destination.principal_binding_id, bindings=self._bindings)
        if recipient.current_chat_id(now=now) != destination.chat_id:
            raise ValueError("private completion recipient changed")
        destination.validate_current(self._bindings())

    @staticmethod
    def accepts_route(route):
        return route['channel'] == 'telegram_private'

    @staticmethod
    def receipt_matches(receipt, destination):
        return isinstance(receipt, str) and receipt.startswith('telegram:' + destination.chat_id + ':')
