"""Shared identity and authorized memory composition for private conversation work."""
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from eva.adapters.objects.identity_verifier import load_current_identity_pack
from eva.adapters.postgres.operational_timeline import TimelineScope
from eva.adapters.postgres.private_memory_aliases import PostgresPrivateMemoryAliases
from eva.adapters.postgres.people_memory_overview import PostgresOwnerMemorySources
from eva.adapters.projection.hindsight import HindsightMemoryEngine, HindsightProjectionConfig
from eva.application.conversation import IdentityProfile
from eva.application.identity_verifier import load_identity_pack_trust_binding
from eva.bootstrap.telegram_ingress import load_principal_bindings


def load_conversation_identity_pack(settings):
    """Read current signed identity and trust bindings on every invocation."""
    return load_current_identity_pack(
        None if settings.identity_pack_file is None else Path(settings.identity_pack_file),
        trust=(None if settings.identity_pack_trust_file is None
               else load_identity_pack_trust_binding(Path(settings.identity_pack_trust_file))),
    )


def build_private_conversation_resources(settings, engine, *, group_consent,
                                         household_voice, identity_pack):
    """Preserve the runtime's memory aliases, consent resolvers and personality."""
    private_memory_aliases = None
    if (settings.dashboard_principal_binding_id is not None
            and settings.telegram_principal_bindings_file is not None):
        private_memory_aliases = PostgresPrivateMemoryAliases(
            engine, tenant_id=settings.background_tenant_id,
            installation_id=settings.background_installation_id,
            owner=UUID(settings.background_principal_id),
            binding=UUID(settings.dashboard_principal_binding_id),
            authority_scope=TimelineScope(UUID(settings.dashboard_tenant_id),
                                          UUID(settings.dashboard_installation_id)),
            bindings_loader=lambda: load_principal_bindings(settings.telegram_principal_bindings_file),
        )
    def original_sources(query, selected_refs):
        if (settings.dashboard_principal_binding_id is None
                or settings.background_principal_id is None):
            return ()
        # Reload the same trusted bridge for each source lookup, including a
        # semantic cache hit. A previous read grants no lasting source access.
        bindings = (None if settings.telegram_principal_bindings_file is None
                    else load_principal_bindings(settings.telegram_principal_bindings_file))
        reader = PostgresOwnerMemorySources(
            engine, tenant_id=settings.background_tenant_id,
            installation_id=settings.background_installation_id,
            owner=UUID(settings.background_principal_id),
            binding=UUID(settings.dashboard_principal_binding_id),
            authority_scope=TimelineScope(UUID(settings.dashboard_tenant_id),
                                          UUID(settings.dashboard_installation_id)),
            telegram_bindings=bindings,
        )
        return reader.selected_private_sources(
            query, selected_refs, now=datetime.now(UTC).replace(microsecond=0))

    memory = HindsightMemoryEngine(
        HindsightProjectionConfig(
            settings.hindsight_url,
            bank_namespace=settings.hindsight_bank_namespace,
            recall_budget=settings.hindsight_recall_budget,
            recall_max_tokens=settings.hindsight_recall_max_tokens,
            recall_node_budget=settings.hindsight_recall_node_budget,
        ),
        active_group_consent_resolver=group_consent,
        active_household_consent_resolver=household_voice,
        private_alias_resolver=private_memory_aliases,
        private_source_resolver=original_sources,
    )
    identity = IdentityProfile(
        name=settings.identity_name,
        description=settings.identity_description,
        principles=(
            "Zeg eerlijk wat je wel en niet weet.",
            "Reageer als één persoon, niet als een verzameling scripts.",
            "Beweer een handeling pas na onafhankelijk bewijs.",
            "Gebruik herinneringen om de persoon te begrijpen, niet om hem na te doen.",
            "Krijg je concrete feedback op je manier van helpen of antwoorden, leg de relevante "
            "bijstelling vast met record_human_understanding wanneer die tool beschikbaar is; "
            "alleen instemmen bewaart niets voor later. Behoud context, onzekerheid en wie de "
            "feedback gaf. Herzie een bestaande bijstelling met haar nieuwste bronverwijzingen. "
            "Maak van een eenmalig verzoek geen algemene voorkeur en bewaar geen dubbele bijstelling.",
        ),
        maximum_reply_words=110,
        identity_pack=identity_pack,
    )
    return memory, identity
