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


def completion_worker(work_store, delivery, *, bindings_loader, document_source=None):
    from eva.adapters.postgres.delegated_work_delivery import PostgresDelegatedWorkDeliveryStore
    from eva.operations.delegated_work_delivery import DelegatedWorkDeliveryWorker

    return DelegatedWorkDeliveryWorker(
        PostgresDelegatedWorkDeliveryStore(work_store, bindings_loader=bindings_loader,
                                           document_source=document_source),
        delivery,
    )


def delivery_status_loader(work_store, *, owner_principal_id: UUID,
                           principal_binding_id: UUID, bindings_loader):
    """Read delivery status independently of the switch that permits new sends."""
    from eva.adapters.postgres.delegated_work_delivery import PostgresDelegatedWorkDeliveryStore

    store = PostgresDelegatedWorkDeliveryStore(work_store, bindings_loader=bindings_loader)
    return lambda work_id: store.status(work_id, owner_principal_id=owner_principal_id,
                                       principal_binding_id=principal_binding_id)


def document_completion_source(engine):
    """Opt into the same host document configuration; no new task or transport."""
    import os
    from eva.adapters.credentials.runtime_files import read_runtime_secret_file
    from eva.adapters.postgres.durable_goals import PostgresDurableGoalStore
    from eva.providers.document_completion import DocumentCompletionSource
    from eva.providers.document_goal_actions import DocumentGoalActionsProviderFactory
    from eva.providers.private_owner_authority import configured_private_owner_resolver
    from eva.providers.mcp_owner_authority import McpOwnerAuthority
    from eva.providers.mcp_autonomy import _SourceTurnLoader
    settings = DocumentGoalActionsProviderFactory.load_settings()
    if settings is None or not settings.pool_file:
        return None
    resolver = configured_private_owner_resolver(engine,
        tenant_id=settings.tenant_id, installation_id=settings.installation_id)
    if resolver is None:
        return None
    authority = McpOwnerAuthority(engine, settings.mandate_file,
        os.environ.get('EVA_MCP_MANAGED_ENDPOINTS_FILE') or None,
        private_owner_resolver=resolver)
    return DocumentCompletionSource(
        store=PostgresDurableGoalStore(engine, tenant_id=settings.tenant_id,
                                      installation_id=settings.installation_id),
        authority=authority,
        read_pool=lambda: read_runtime_secret_file(settings.pool_file, setting_name='EVA_DOCUMENT_TASK_POOL_FILE'),
        load_source_turn=_SourceTurnLoader(engine, authority))
