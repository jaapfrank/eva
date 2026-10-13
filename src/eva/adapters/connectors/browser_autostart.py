"""Start only the configured managed browser for a newly admitted MCP use."""
from __future__ import annotations

from dataclasses import dataclass, field

from eva.adapters.connectors.browser_session import BrowserSessionClient, BrowserSessionError
from eva.adapters.connectors.mcp_registration import canonical_mcp_endpoint
from eva.application.cancellation import check_cancellation
from eva.application.conversation import ConversationConflict


@dataclass(frozen=True, slots=True)
class BrowserAutostartConfig:
    endpoint: str
    client: BrowserSessionClient = field(repr=False)

    def __post_init__(self):
        if canonical_mcp_endpoint(self.endpoint) != self.endpoint:
            raise ValueError("managed browser endpoint must be canonical")


class ManagedBrowserAutostart:
    def __init__(self, config: BrowserAutostartConfig, connector_id: str, *, session_aliases=None):
        self.config = config
        self.connector_id = connector_id
        self._session_aliases = dict(session_aliases or {})

    def matches(self, connector_id: str) -> bool:
        return connector_id == self.connector_id or connector_id in self._session_aliases

    def session_binding(self, connector_id: str) -> str:
        if not self.matches(connector_id):raise ConversationConflict("browser session binding differs")
        return self._session_aliases.get(connector_id,connector_id)

    @staticmethod
    def _state(value):
        keys = ("owner_control", "eva_control", "browser_ready", "mcp_ready")
        if (not isinstance(value, dict) or value.get("contract") != "EvaBrowserSession/v1"
                or type(value.get("revision")) is not int or value["revision"] < 0
                or any(type(value.get(key)) is not bool for key in keys)):
            raise ConversationConflict("managed browser status is invalid")
        state = value.get("state")
        if state == "eva":
            if value["owner_control"] or not all(value[key] for key in keys[1:]):
                raise ConversationConflict("managed browser readiness is not confirmed")
        elif state == "stopped":
            if any(value[key] for key in keys):
                raise ConversationConflict("managed browser stopped state is not confirmed")
        else:
            raise ConversationConflict(
                "managed browser is under owner control or requires explicit recovery; "
                "automatic resume is not allowed"
            )
        return state

    async def verify_generation(self, connector_id: str, revision: int) -> None:
        """Read back the same live generation without starting or resuming anything."""
        if not self.matches(connector_id):
            raise ConversationConflict("browser session binding differs")
        check_cancellation()
        try:
            observed = await self.config.client.status()
            check_cancellation()
            if self._state(observed) != "eva" or observed["revision"] != revision:
                raise ConversationConflict("managed browser generation changed during readiness")
        except BrowserSessionError as error:
            raise ConversationConflict("managed browser readiness status is uncertain") from error

    async def ensure_available(self, connector_id: str) -> int | None:
        if not self.matches(connector_id):
            return
        check_cancellation()
        try:
            current = await self.config.client.status()
            check_cancellation()
            if self._state(current) == "eva":
                return current["revision"]
            # The host owns the compare-and-swap and serializes the exact pair.
            # A conflict or uncertain response is never retried here.
            started = await self.config.client.control("start", current["revision"])
            check_cancellation()
            if (self._state(started) != "eva"
                    or started["revision"] <= current["revision"]):
                raise ConversationConflict("managed browser start was not confirmed")
            observed = await self.config.client.status()
            check_cancellation()
            if (self._state(observed) != "eva"
                    or observed["revision"] != started["revision"]):
                raise ConversationConflict("managed browser state changed after start")
            return observed["revision"]
        except BrowserSessionError as error:
            raise ConversationConflict(
                "managed browser start or status is uncertain; refresh state before further work"
            ) from error


def managed_browser_session_id(token: str, connector_id: str, revision: int) -> str:
    """Private stable session for one authorized owner binding and browser generation."""
    import hmac
    from eva.contracts.canonical_json import canonical_json_bytes
    return hmac.digest(token.encode("utf-8"), canonical_json_bytes({
        "contract": "EvaManagedBrowserMcpSession/v1",
        "connector_id": connector_id,
        "browser_revision": revision,
    }), "sha256").hex()


def direct_browser_owner_binding(principal_sha256: str, gateway: str, controller: str) -> str:
    from eva.contracts.canonical_json import canonical_sha256
    return canonical_sha256({'contract':'EvaDirectBrowserOwner/v1','principal_sha256':principal_sha256,
                             'gateway':gateway,'controller':controller})
