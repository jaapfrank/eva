"""Real operational timeline capture and local paging, with no external calls."""
import asyncio

from eva.adapters.postgres.connector_results import ConnectorResults, result_capture, _encoded
from eva.adapters.postgres.operational_timeline import PostgresOperationalTimeline, external_timeline_scope
from tests.connectors.test_connector_results import setup
from tests.connectors.test_mcp_discovery import TURN
from tests.postgres.test_scheduled_failure_return import database


def test_large_connector_result_survives_actual_timeline_reload(database):
    output = {"text": "first section\n" + "x"*90000 + "last section"}
    adapter, command, calls, _ = setup(output)
    timeline = PostgresOperationalTimeline(database, external_timeline_scope(TURN.tenant_id, TURN.installation_id, "scope"))
    capture = ConnectorResults(timeline, TURN, lambda _: None)
    with result_capture(capture):
        manifest = asyncio.run(adapter.execute(command)).details["invocation_response"]
    loaded = ConnectorResults(PostgresOperationalTimeline(database, timeline._scope_value), TURN, lambda _: None)
    with result_capture(loaded):
        result = asyncio.run(adapter.observe(command, None))
    assert result.evidence["observation"] == manifest
    selection = {"pointer":"/text", "offset":0, "limit":100, "query":"last section"}
    page = loaded.read_recorded(manifest["reader"]["tool_id"], selection, "read-last-section")
    reloaded = ConnectorResults(PostgresOperationalTimeline(database, timeline._scope_value), TURN, lambda _: None)
    assert reloaded.read_recorded(manifest["reader"]["tool_id"], selection, "read-last-section") == page
    assert page["text"] == "last section" and not page["has_more"]
    assert manifest["byte_length"] == len(_encoded(output))
    assert len(calls) == 1
