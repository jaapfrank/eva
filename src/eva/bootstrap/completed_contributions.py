"""Compose one saved-result handoff into the existing initiative lifecycle."""
from dataclasses import replace
from datetime import UTC, datetime

from eva.application.conversation import ConversationToolDescriptor
from eva.application.cancellation import check_cancellation
from eva.adapters.postgres.completed_contribution import CompletedContributionActions
from eva.adapters.postgres.owner_initiatives import PostgresOwnerInitiatives
from eva.bootstrap.native_initiative_work import configured_initiative_authority

NAME = "register_own_contribution"


def contribution_descriptor():
    return ConversationToolDescriptor(name=NAME,
        description="Registreer een door Eva zelf gekozen, al opgeslagen gezinsbijdrage "
            "als eigen initiatief. Gebruik de werk-ID en het exacte pad van het bewezen "
            "opgeslagen document. De host leest dezelfde bron opnieuw en regelt de bestaande "
            "privéaflevering; maak het document niet opnieuw en stuur niet zelf een bericht. "
            "Deze actie past ook bij een eerder geblokkeerde aflevering, maar verandert "
            "die eerdere taak niet in een geslaagde taak. Gewone gebruikersdocumenten "
            "worden niet automatisch eigen initiatief.",
        input_schema={"type": "object", "properties": {
            "title": {"type": "string", "minLength": 1, "maxLength": 160},
            "motivation": {"type": "string", "minLength": 1, "maxLength": 800},
            "source_work_id": {"type": "string", "minLength": 1, "maxLength": 240},
            "artifact_path": {"type": "string", "minLength": 1, "maxLength": 4096}},
            "required": ["title", "motivation", "source_work_id", "artifact_path"],
            "additionalProperties": False},
        completion_guidance="registered bevestigt de resultaatregistratie en de automatische "
            "afleverroute; deliveryPending is nog geen afleverreceipt of menselijk oordeel. "
            "Bewaar intent_id voor later resultaat, stoppen en feedback; niet zelf pollen.")


def with_completed_contributions(settings, engine, bundle):
    if bundle is None or any(NAME in route.action_names for route in bundle.external_tools):
        return bundle
    sources = [route for route in bundle.external_tools
               if callable(getattr(route, "resolve_contribution_artifact", None))]
    if len(sources) != 1 or sources[0].private_owner_resolver is None:
        return bundle
    source = sources[0]
    resolver = source.private_owner_resolver

    def authority():
        return configured_initiative_authority(PostgresOwnerInitiatives(engine,
            tenant_id=settings.background_tenant_id,
            installation_id=settings.background_installation_id,
            owner=resolver._owner, binding=resolver._binding,
            authority_scope=resolver._scope))

    def authorize(turn):
        check_cancellation()
        source._host(turn).require_current()

    handler = CompletedContributionActions(authority, resolver,
        source.resolve_contribution_artifact, authorize, lambda turn: check_cancellation(),
        lambda: datetime.now(UTC).replace(microsecond=0))
    return replace(bundle, conversation_tools=(*bundle.conversation_tools, contribution_descriptor()),
                   external_tools=(*bundle.external_tools, handler))
