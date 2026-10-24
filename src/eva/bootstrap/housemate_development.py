"""Development at the existing dream boundary and fresh private context reads."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
import logging
from uuid import UUID

from eva.adapters.postgres.housemate_development import PostgresDevelopmentSelection
from eva.adapters.postgres.operational_timeline import TimelineScope
from eva.adapters.postgres.owner_initiatives import PostgresOwnerInitiatives
from eva.adapters.postgres.people_memory_overview import PostgresOwnerMemorySources
from eva.application.housemate_development import select_development, resolve_development
from eva.application.memory import RecallCurrentFacts
from eva.application.memory_audience import MemoryAudience
from eva.bootstrap.telegram_ingress import load_principal_bindings

_LOGGER = logging.getLogger(__name__)


class HousemateDevelopment:
    def __init__(self, *, owner, source_reader, clock=None, selections=None):
        self.owner, self.source_reader = owner, source_reader
        self.clock = clock or (lambda: datetime.now(UTC).replace(microsecond=0))
        self.selections = selections or PostgresDevelopmentSelection(owner)
        self.projection_error = None
        self.read_error = None

    @property
    def last_error(self):
        return self.projection_error or self.read_error

    def _principals(self, reader):
        principal = str(reader._owner)
        alias = (None if reader._bindings is None else reader._bindings.private_bridge_principal(
            principal_id=principal, principal_binding_id=reader._binding))
        return tuple(value for value in (principal, alias) if value is not None)

    def _query(self, reader, now):
        principal = str(reader._owner)
        return RecallCurrentFacts(recall_id="eva-development", tenant_id=reader._tenant,
            installation_id=reader._installation, principal_id=principal,
            audience=MemoryAudience.private_person(principal_id=principal),
            conversation_id=f"dashboard:{reader._binding}", project_id=None,
            query_purpose="development_current_sources", requested_valid_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            scope={"kind":"private"}, subject_ids=(principal,), query_text="", maximum_facts=8)

    def _sources(self, reader, query, now, refs=()):
        reader.require_learning_query(query, now=now)
        return reader.selected_private_sources(query, tuple(
            (ref["principal_id"],ref["source_ref"],ref["evidence_ref"]) for ref in refs), now=now)

    def _error(self, error, *, projection=False):
        # No source text, SQL parameters, keys or feedback in diagnostics.
        if projection:
            self.projection_error = type(error).__name__
        else:
            self.read_error = type(error).__name__
        _LOGGER.warning("housemate development unavailable: %s", type(error).__name__)

    def after_reflection(self, tenant_id, installation_id, principal_id, receipt):
        """Only a real completed dream invokes this; local failure never replays its model."""
        try:
            reader, now = self.source_reader(), self.clock()
            principals = self._principals(reader)
            if (tenant_id != reader._tenant or installation_id != reader._installation
                    or principal_id not in principals or receipt is None
                    or receipt.observation_error_type is not None):
                return False
            query = self._query(reader, now)
            sources = self._sources(reader, query, now)
            projects = self.owner.page(now=now, limit=64)["cards"]
            selection = select_development(owner_id=str(reader._owner), principals=principals,
                sources=sources, projects=projects, now=query.requested_valid_at)
            # Recheck before the local write, including an alias revoked during selection.
            latest_sources = self._sources(reader, query, now)
            if select_development(owner_id=str(reader._owner), principals=principals,
                    sources=latest_sources, projects=projects, now=query.requested_valid_at) != selection:
                raise RuntimeError("development sources changed before processing")
            if self._principals(self.source_reader()) != principals:
                raise RuntimeError("development owner aliases changed")
            changed = self.selections.save(selection, now=now)
            self.projection_error = None
            return changed
        except Exception as error:
            self._error(error, projection=True)
            return False

    def read_current(self, query):
        try:
            if (query.project_id is not None or query.audience.kind != "private_person"
                    or not {"internal","confidential"}.issubset(query.allowed_privacy_classes)
                    or query.audience.principal_id != query.principal_id
                    or not (query.scope == {"kind":"private"}
                        or query.scope == {"kind":"person", "principal_id":query.principal_id}
                        or (query.scope == {"kind":"private_opportunity", "source":"initiative-review",
                            "event_type":"private_opportunity_review"}
                            and query.conversation_id == "sensory:initiative-review"))):
                return None
            reader, now = self.source_reader(), self.clock()
            principals = self._principals(reader)
            if (query.tenant_id != reader._tenant or query.installation_id != reader._installation
                    or query.principal_id not in principals):
                return None
            # Preserve the original query principal/conversation. A bridge must
            # pass the same exact-chat source rule as ordinary private memory.
            private = replace(query, scope={"kind":"private"})
            reader.require_learning_query(private, now=now)
            current = self.selections.load(now=now)
            if current is None:
                return None
            selection = current["selection"]
            sources = self._sources(reader, private, now, selection["delta_refs"])
            projects = self.selections.current_projects(selection["project_refs"], now=now)
            state = resolve_development(selection, owner_id=str(reader._owner), principals=principals,
                sources=sources, projects=projects, now=now.strftime("%Y-%m-%dT%H:%M:%SZ"))
            reader.require_learning_query(private, now=now)
            if self._principals(self.source_reader()) != principals:
                return None
            self.read_error = None
            if state is not None:
                state["selection_sha256"] = current["selection_sha256"]
                state["selection_revision"] = current["revision"]
            return state
        except Exception as error:
            self._error(error)
            return None


def configured_housemate_development(settings, engine):
    if any(getattr(settings, key, None) is None for key in (
            "background_principal_id", "dashboard_principal_binding_id", "dashboard_tenant_id",
            "dashboard_installation_id", "background_tenant_id", "background_installation_id")):
        return None
    owner_id, binding = UUID(settings.background_principal_id), UUID(settings.dashboard_principal_binding_id)
    scope = TimelineScope(UUID(settings.dashboard_tenant_id), UUID(settings.dashboard_installation_id))
    owner = PostgresOwnerInitiatives(engine, tenant_id=settings.background_tenant_id,
        installation_id=settings.background_installation_id, owner=owner_id, binding=binding,
        authority_scope=scope)

    def sources():
        bindings = (None if settings.telegram_principal_bindings_file is None else
            load_principal_bindings(settings.telegram_principal_bindings_file))
        return PostgresOwnerMemorySources(engine, tenant_id=settings.background_tenant_id,
            installation_id=settings.background_installation_id, owner=owner_id, binding=binding,
            authority_scope=scope, telegram_bindings=bindings)

    return HousemateDevelopment(owner=owner, source_reader=sources)
