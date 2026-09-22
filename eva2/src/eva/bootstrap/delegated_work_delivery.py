"""Compose private task return routes from the existing trusted channel binding."""
from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from eva.application.conversation import ConversationTurn
from eva.application.telegram_ingress import TelegramPrincipalBindings


def private_destination_loader(*, owner_principal_id: UUID, principal_binding_id: UUID,
                               bindings_loader: Callable[[], TelegramPrincipalBindings]):
    def resolve(turn: ConversationTurn):
        from eva.application.delegated_work_delivery import PrivateWorkDestination

        if not turn.conversation_id.startswith('telegram:'):
            return None
        # This is an explicit channel envelope, not a destination inferred from
        # task text or a hashed conversation. The factory verifies it against the
        # fresh server-owned binding and the turn's private audience.
        chat_id = turn.conversation_id.removeprefix('telegram:')
        if not chat_id.isascii() or not chat_id.isdigit() or len(chat_id) > 16:
            raise ValueError('private return conversation is invalid')
        return PrivateWorkDestination.from_telegram_turn(
            turn, owner_principal_id=owner_principal_id,
            principal_binding_id=principal_binding_id,
            telegram_user_id=int(chat_id), chat_id=chat_id,
            bindings=bindings_loader(),
        )
    return resolve


def completion_worker(work_store, delivery, *, bindings_loader):
    from eva.adapters.postgres.delegated_work_delivery import PostgresDelegatedWorkDeliveryStore
    from eva.operations.delegated_work_delivery import DelegatedWorkDeliveryWorker

    return DelegatedWorkDeliveryWorker(
        PostgresDelegatedWorkDeliveryStore(work_store, bindings_loader=bindings_loader),
        delivery,
    )
