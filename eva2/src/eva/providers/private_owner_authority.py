"""Host composition for private document owner authority."""
from uuid import UUID
from eva.adapters.postgres.private_owner import PostgresPrivateOwnerResolver
from eva.adapters.postgres.operational_timeline import TimelineScope


def configured_private_owner_resolver(engine, *, tenant_id, installation_id):
    """Opt in only through existing trusted host binding configuration."""
    import os
    from eva.adapters.credentials.telegram_bindings import load_principal_bindings

    binding = os.environ.get("EVA_DASHBOARD_PRINCIPAL_BINDING_ID", "").strip()
    bindings_file = os.environ.get("EVA_TELEGRAM_PRINCIPAL_BINDINGS_FILE", "").strip()
    if not binding or not bindings_file:
        return None
    owner = UUID(os.environ["EVA_RUNTIME_PRINCIPAL_ID"])
    return PostgresPrivateOwnerResolver(
        engine, tenant_id=tenant_id, installation_id=installation_id,
        owner=owner, binding=UUID(binding),
        authority_scope=TimelineScope(
            UUID(os.environ.get("EVA_DASHBOARD_TENANT_ID") or tenant_id),
            UUID(os.environ.get("EVA_DASHBOARD_INSTALLATION_ID") or installation_id)),
        bindings_loader=lambda: load_principal_bindings(bindings_file),
    )
