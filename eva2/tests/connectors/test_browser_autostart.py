import asyncio

import pytest

from eva.adapters.connectors.browser_autostart import BrowserAutostartConfig, ManagedBrowserAutostart
from eva.adapters.connectors.browser_session import BrowserSessionError
from eva.application.cancellation import CancellationSignal, WorkInterrupted, cancellation_scope
from eva.application.conversation import ConversationConflict


def status(state="eva", revision=2):
    return {"contract": "EvaBrowserSession/v1", "revision": revision, "state": state,
            "owner_control": state == "owner", "eva_control": state == "eva",
            "browser_ready": state == "eva", "mcp_ready": state == "eva"}


class Client:
    def __init__(self, state="stopped"):
        self.current = status(state, 1)
        self.calls = []
        self.failure = None

    async def status(self):
        self.calls.append("status")
        return dict(self.current)

    async def control(self, action, revision):
        self.calls.append((action, revision))
        if self.failure:
            raise self.failure
        self.current = status("eva", revision + 2)
        return dict(self.current)


def helper(client):
    return ManagedBrowserAutostart(
        BrowserAutostartConfig("https://browser.example/mcp", client), "own-browser")


def test_stopped_starts_once_at_observed_revision_and_reads_back():
    client = Client()
    assert asyncio.run(helper(client).ensure_available("own-browser")) == 3
    assert client.calls == ["status", ("start", 1), "status"]
    assert asyncio.run(helper(client).ensure_available("own-browser")) == 3
    assert client.calls[-1] == "status"
    assert client.calls.count(("start", 1)) == 1


def test_other_connector_and_healthy_eva_never_start():
    client = Client("eva")
    auto = helper(client)
    assert asyncio.run(auto.ensure_available("personal-chrome")) is None
    assert client.calls == []
    assert asyncio.run(auto.ensure_available("own-browser")) == 1
    assert client.calls == ["status"]


@pytest.mark.parametrize("state", ["owner", "recovery_required", "starting", "unknown"])
def test_owner_and_uncertain_states_never_resume(state):
    client = Client(state)
    with pytest.raises(ConversationConflict, match="explicit recovery"):
        asyncio.run(helper(client).ensure_available("own-browser"))
    assert client.calls == ["status"]


def test_start_error_is_not_retried():
    client = Client()
    client.failure = BrowserSessionError("uncertain")
    with pytest.raises(ConversationConflict, match="uncertain"):
        asyncio.run(helper(client).ensure_available("own-browser"))
    assert client.calls == ["status", ("start", 1)]


def test_inconsistent_or_changed_status_is_rejected():
    client = Client()
    client.current["browser_ready"] = True
    with pytest.raises(ConversationConflict, match="stopped state"):
        asyncio.run(helper(client).ensure_available("own-browser"))
    assert client.calls == ["status"]
    client = Client()
    original = client.status
    async def changed():
        value = await original()
        if client.current["state"] == "eva":
            value["revision"] += 1
        return value
    client.status = changed
    with pytest.raises(ConversationConflict, match="changed after start"):
        asyncio.run(helper(client).ensure_available("own-browser"))
    assert client.calls == ["status", ("start", 1), "status"]


def test_cancellation_after_status_does_not_start():
    client = Client()
    signal = CancellationSignal()
    original = client.status
    async def stopped():
        value = await original()
        signal.cancel()
        return value
    client.status = stopped
    with cancellation_scope(signal), pytest.raises(WorkInterrupted):
        asyncio.run(helper(client).ensure_available("own-browser"))
    assert client.calls == ["status"]


def test_endpoint_must_be_canonical_and_client_is_not_in_repr():
    with pytest.raises(ValueError, match="canonical"):
        BrowserAutostartConfig("https://BROWSER.example/mcp", Client())
    assert "Client" not in repr(BrowserAutostartConfig("https://browser.example/mcp", Client()))


@pytest.mark.parametrize("state, revision", [
    ("stopped", 2), ("owner", 2), ("recovery_required", 2), ("eva", 4),
])
def test_readiness_generation_check_never_starts_or_resumes(state, revision):
    client = Client(state)
    client.current["revision"] = revision
    with pytest.raises(ConversationConflict):
        asyncio.run(helper(client).verify_generation("own-browser", 2))
    assert client.calls == ["status"]


def test_readiness_generation_check_confirms_exact_live_generation():
    client = Client("eva")
    asyncio.run(helper(client).verify_generation("own-browser", 1))
    assert client.calls == ["status"]
