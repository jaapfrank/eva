"""Preserved gateway aliases must resolve exclusively to the restored target."""
import json
from unittest.mock import patch

import pytest
from tools import installation_document_connection as connection


@pytest.mark.parametrize("existing_alias", ["original-provider", "ORIGINAL-provider"])
def test_existing_network_alias_is_refused_before_provider_creation(existing_alias):
    network = "a" * 64
    responses = [json.dumps([{"Containers": {"existing": {}}}]),
                 json.dumps([{"Name": "/other", "NetworkSettings": {"Networks": {
                     "internal": {"NetworkID": network, "Aliases": [existing_alias]}}}}])]
    with patch.object(connection, "_command", side_effect=responses) as command:
        with pytest.raises(ValueError, match="already belongs"):
            connection._require_free_alias(network, "original-provider")
        assert all(call.args[0][1] in {"network", "inspect"} for call in command.call_args_list)


def test_unrelated_alias_does_not_block_recovery():
    responses = [json.dumps([{"Containers": {"existing": {}}}]),
                 json.dumps([{"Name": "/other", "NetworkSettings": {"Networks": {
                     "internal": {"NetworkID": "a" * 64, "Aliases": ["other"]}}}}])]
    with patch.object(connection, "_command", side_effect=responses):
        connection._require_free_alias("a" * 64, "original-provider")


def test_started_provider_must_retain_configured_alias():
    config = {"container_name": "restored", "network_id": "a" * 64}
    prep = {"image": "sha256:" + "b" * 64, "runtime_uid": 1000, "runtime_gid": 1000}
    endpoint = {"NetworkID": config["network_id"], "Aliases": ["original-provider"]}
    actual = {"Id": "c" * 64, "Name": "/restored", "Image": prep["image"],
              "State": {"Running": True}, "Config": {"User": "1000:1000", "Entrypoint": [connection._ENTRYPOINT]},
              "HostConfig": {"ReadonlyRootfs": True}, "Mounts": [],
              "NetworkSettings": {"Networks": {"internal": endpoint}}}
    with patch.object(connection, "_command", return_value=json.dumps([actual])):
        connection._verify_running(actual["Id"], config, prep, {}, alias="original-provider")
    endpoint["Aliases"] = ["restored"]
    with patch.object(connection, "_command", return_value=json.dumps([actual])):
        with pytest.raises(ValueError, match="lacks preserved"):
            connection._verify_running(actual["Id"], config, prep, {}, alias="original-provider")
