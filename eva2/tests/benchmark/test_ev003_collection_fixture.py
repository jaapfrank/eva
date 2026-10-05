"""Public toy data only: collection contract, authority, durability and HTTP."""
import copy
import http.client
import json
from pathlib import Path
import threading

import pytest

from benchmark.inspect_ai_v1.ev003_collection_fixture import CollectionFixture, http_server, readback


TOKEN = "public-synthetic-capability-123456789"


def configuration(tmp_path, service_id="alpha"):
    token = tmp_path / "capability"
    token.write_text(TOKEN)
    token.chmod(0o600)
    return {
        "contract": "EV003CollectionFixture/v1",
        "service": {
            "id": service_id, "kind": "generic_local_fixture_service",
            "origin": f"http://{service_id}.fixture.test", "external_egress": False,
            "operations": ["read", "create", "update", "delete"],
            "contract": {"schema": "GET /schema", "collections": "GET /collections/{collection}"},
            "collections": {
                "items": [{"id": "seed", "label": "public toy", "flag": False}],
                "notes": [], "ungranted": [{"id": "hidden-to-fixture-client", "value": "toy secret"}],
            },
        },
        "access": {"principal_id": "synthetic-person", "audience_kind": "private_person", "token_file": str(token)},
        "grants": {
            "items": {"operations": ["read", "update", "delete"]},
            "notes": {"operations": ["read", "create"], "create_limit": 1},
        },
    }


def request(fixture, method, path, body=None, **kwargs):
    return fixture.request(method, path, body=body, authorization="Bearer " + TOKEN,
                           host=fixture.host, **kwargs)


def test_two_service_http_authority_effects_and_separate_readback(tmp_path):
    first = CollectionFixture(configuration(tmp_path, "alpha"), tmp_path / "a")
    second = CollectionFixture(configuration(tmp_path, "beta"), tmp_path / "b")
    server = http_server(first)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection(*server.server_address, timeout=2)
    def send_http(method, path, body=None, token=TOKEN):
        connection.request(method, path, body=json.dumps(body) if body is not None else None,
                           headers={"Host": first.host, "Authorization": "Bearer " + token,
                                    "X-Actor": "forged-other-person", "Content-Type": "application/json"})
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    try:
        assert send_http("GET", "/schema", token="wrong") == (401, {"error": "access_denied"})
        status, schema = send_http("GET", "/schema")
        assert status == 200 and set(schema["collections"]) == {"items", "notes"}
        assert "toy secret" not in json.dumps(schema) and "public toy" not in json.dumps(schema)
        assert send_http("GET", "/collections/ungranted") == send_http("GET", "/collections/absent") == (404, {"error": "not_available"})
        assert send_http("GET", "/readback") == (404, {"error": "not_available"})
        assert send_http("PATCH", "/collections/notes/anything", {}) == (404, {"error": "not_available"})
        assert send_http("GET", "/collections/items/seed")[1]["revision"] == 1
        status, created = send_http("POST", "/collections/notes", {"text": "public note"})
        assert status == 201 and created["revision"] == 1 and created["id"]
        assert send_http("GET", "/collections/notes/" + created["id"]) == (200, created)
        assert request(second, "GET", "/collections/notes") == (200, {"records": []})
        assert request(second, "POST", "/collections/notes", {"text": "separate"})[0] == 201
        assert send_http("POST", "/collections/notes", {"text": "duplicate without key"}) == (403, {"error": "mandate_limit"})
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    result = readback(tmp_path / "a")
    assert result["snapshot_matches_journal"]
    effects = [entry for entry in result["journal"] if entry["applied"]]
    assert len(effects) == 1 and effects[0]["principal_id"] == "synthetic-person"
    assert effects[0]["old"] is None and effects[0]["new"] == created
    assert result["initial"]["items"] == result["state"]["items"]
    assert all("headers" not in entry and TOKEN not in json.dumps(entry) for entry in result["journal"])
    assert all("forged-other-person" not in json.dumps(entry) for entry in result["journal"])
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in (tmp_path / "a").iterdir())
    assert (tmp_path / "a").stat().st_mode & 0o777 == 0o700


def test_revision_idempotency_and_journal_recovery(tmp_path):
    config = configuration(tmp_path)
    fixture = CollectionFixture(config, tmp_path / "state")
    status, changed = request(fixture, "PATCH", "/collections/items/seed", {"flag": True, "expected_revision": 1}, idempotency_key="edit-1")
    assert status == 200 and changed["revision"] == 2
    assert request(fixture, "PATCH", "/collections/items/seed", {"flag": True, "expected_revision": 1}, idempotency_key="edit-1") == (200, changed)
    assert request(fixture, "PATCH", "/collections/items/seed", {"flag": False}, idempotency_key="edit-1") == (409, {"error": "idempotency_conflict"})
    assert request(fixture, "PATCH", "/collections/items/seed", {"flag": False, "expected_revision": 1}) == (409, {"error": "revision_conflict"})
    status, created = request(fixture, "POST", "/collections/notes", {"text": "one"}, idempotency_key="create-1")
    assert status == 201
    assert request(fixture, "POST", "/collections/notes", {"text": "one"}, idempotency_key="create-1") == (201, created)
    assert request(fixture, "DELETE", "/collections/items/seed", {"expected_revision": 2})[0] == 200
    before = readback(tmp_path / "state")
    assert len([v for v in before["journal"] if v["applied"]]) == 3
    # Simulate a stale derived snapshot after the authoritative append was synced.
    (tmp_path / "state" / "state.json").write_text("{}")
    assert not readback(tmp_path / "state")["snapshot_matches_journal"]
    resumed = CollectionFixture(config, tmp_path / "state")
    after = readback(tmp_path / "state")
    assert after["snapshot_matches_journal"] and after["state"] == before["state"]
    assert after["journal_sha256"] == before["journal_sha256"]
    assert request(resumed, "GET", "/collections/items/seed")[0] == 404
    altered = copy.deepcopy(config)
    altered["access"]["principal_id"] = "different-person"
    with pytest.raises(ValueError, match="binding differs"):
        CollectionFixture(altered, tmp_path / "state")
    with (tmp_path / "state" / "journal.jsonl").open("ab") as f:
        f.write(b'{"partial":')
    with pytest.raises(ValueError, match="incomplete fixture journal"):
        readback(tmp_path / "state")


def test_mandate_limit_is_atomic_and_invalid_grants_refused(tmp_path):
    config = configuration(tmp_path)
    fixture = CollectionFixture(config, tmp_path / "state")
    barrier = threading.Barrier(3)
    outcomes = []
    def create(label):
        barrier.wait()
        outcomes.append(request(fixture, "POST", "/collections/notes", {"label": label})[0])
    threads = [threading.Thread(target=create, args=(label,)) for label in ("one", "two")]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=2)
    assert sorted(outcomes) == [201, 403]
    result = readback(tmp_path / "state")
    assert len(result["state"]["notes"]) == 1 and len(result["journal"]) == 2
    invalid = copy.deepcopy(config)
    invalid["service"]["operations"] = ["read"]
    with pytest.raises(ValueError, match="invalid fixture grant"):
        CollectionFixture(invalid, tmp_path / "bad")


def test_malformed_http_is_denied_and_observed_without_headers(tmp_path):
    fixture = CollectionFixture(configuration(tmp_path), tmp_path / "state")
    server = http_server(fixture)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    conn = http.client.HTTPConnection(*server.server_address, timeout=2)
    try:
        conn.request("POST", "/collections/notes", body="{broken", headers={"Host": fixture.host, "Authorization": "Bearer " + TOKEN})
        response = conn.getresponse()
        assert response.status == 400 and json.loads(response.read()) == {"error": "invalid_request"}
        conn.request("GET", "/schema", headers={"Host": fixture.host, "Authorization": "Bearer " + chr(255)})
        response = conn.getresponse()
        assert response.status == 401 and json.loads(response.read()) == {"error": "access_denied"}
    finally:
        conn.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    evidence = readback(tmp_path / "state")
    assert len(evidence["journal"]) == 2 and evidence["journal"][0]["request"]["malformed"]
    assert evidence["journal"][0]["principal_id"] == "synthetic-person"
    assert evidence["journal"][1]["principal_id"] is None and evidence["journal"][1]["status"] == 401
    assert not any(entry["applied"] for entry in evidence["journal"]) and TOKEN not in json.dumps(evidence)
