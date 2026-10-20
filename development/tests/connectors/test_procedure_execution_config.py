"""Trusted procedure output limits; configuration reads never contact a service."""
import inspect
import json

import pytest

from eva.adapters.connectors.procedure_execution import (
    ProcedureExecutionClient, configured_procedure_executor,
)


def configuration(tmp_path, monkeypatch, **changes):
    data = {"url": "http://127.0.0.1:44719", "token_file": str(tmp_path / "not-read-token"),
        "image_id": "sha256:" + "a" * 64, "timeout_seconds": 60}
    data.update(changes)
    path = tmp_path / "client.json"
    path.write_text(json.dumps(data))
    monkeypatch.setenv("EVA_PROCEDURE_EXECUTION_CLIENT_FILE", str(path))
    return path


def test_legacy_config_retains_interface_and_uses_conservative_minimum(tmp_path, monkeypatch):
    configuration(tmp_path, monkeypatch)
    client = configured_procedure_executor()
    assert client.output_bytes == 1024
    assert client.timeout_seconds == 60
    assert list(inspect.signature(client.execute).parameters) == ["source", "input_data"]
    # Existing positional construction and the fourth timeout argument retain
    # their meaning. Neither construction opens the credential file or a URL.
    direct = ProcedureExecutionClient(client.url, client.token_file, client.image_id, 23)
    assert direct.timeout_seconds == 23 and direct.output_bytes == 1024


@pytest.mark.parametrize("limit", [1024, 4096, 16384])
def test_explicit_trusted_output_bound_is_exposed(tmp_path, monkeypatch, limit):
    configuration(tmp_path, monkeypatch, output_bytes=limit)
    client = configured_procedure_executor()
    assert client.output_bytes == limit
    assert client.image_id == "sha256:" + "a" * 64


@pytest.mark.parametrize("limit", [1023, 16385, 0, -1, True, 4096.0, "4096", None])
def test_invalid_or_untyped_output_bound_is_rejected_by_loader_and_direct_client(tmp_path, monkeypatch, limit):
    configuration(tmp_path, monkeypatch, output_bytes=limit)
    with pytest.raises(ValueError, match="output bound"):
        configured_procedure_executor()
    with pytest.raises(ValueError, match="output bound"):
        ProcedureExecutionClient("http://127.0.0.1", "/not-read", "sha256:" + "a" * 64,
                                 output_bytes=limit)


def test_unknown_fields_and_missing_old_required_fields_still_fail_closed(tmp_path, monkeypatch):
    path = configuration(tmp_path, monkeypatch, output_bytes=4096, invented_output_limit=8192)
    with pytest.raises(ValueError, match="fields differ"):
        configured_procedure_executor()
    data = json.loads(path.read_text())
    del data["invented_output_limit"]
    del data["timeout_seconds"]
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="fields differ"):
        configured_procedure_executor()


@pytest.mark.parametrize("data", [[], None, "not an object"])
def test_config_requires_an_object(tmp_path, monkeypatch, data):
    path = configuration(tmp_path, monkeypatch)
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="fields differ"):
        configured_procedure_executor()


def test_absent_client_configuration_remains_optional(monkeypatch):
    monkeypatch.delenv("EVA_PROCEDURE_EXECUTION_CLIENT_FILE", raising=False)
    assert configured_procedure_executor() is None
