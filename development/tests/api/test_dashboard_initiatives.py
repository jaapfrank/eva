from fastapi import FastAPI
from fastapi.testclient import TestClient
from eva.adapters.api.dashboard_access import DashboardAccess
from eva.adapters.api.dashboard_initiatives import install_dashboard_initiatives


def test_owner_routes_reject_browser_scope_and_return_exact_artifact():
    calls = []
    class Runtime:
        def dashboard_initiatives(self):
            calls.append("read")
            return {"contract": "EvaOwnerInitiatives/v1", "cards": [], "truncated": False}
        def dashboard_stop_initiative(self, intent):
            calls.append(intent)
            return {"contract": "EvaOwnerInitiativeStop/v1", "intent_id": intent, "status": "stopped"}
        def dashboard_initiative_artifact(self, intent):
            return {"filename": "gezin.md", "content": "# Gezinsplan", "sha256": "a"*64}
    app = FastAPI()
    install_dashboard_initiatives(app, access=DashboardAccess("t"*32), runtime=Runtime())
    client = TestClient(app)
    url = "/internal/v1/dashboard/initiatives"
    auth = {"authorization": "Bearer " + "t"*32}
    assert client.get(url).status_code == 401
    assert client.post(url + "/stop", json={"intent_id": "test"}).status_code == 401
    assert client.get(url + "?principal_id=other", headers=auth).status_code == 422
    assert client.post(url + "/stop", headers=auth, json={"intent_id": "test", "owner": "other"}).status_code == 422
    assert calls == []
    assert client.get(url, headers=auth).json()["cards"] == []
    result = client.post(url + "/stop", headers=auth, json={"intent_id": "test"})
    assert result.json()["status"] == "stopped"
    assert result.headers["cache-control"] == "no-store"
    artifact = client.get(url + "/test/artifact", headers=auth)
    assert artifact.text == "# Gezinsplan"
    assert artifact.headers["content-disposition"] == "attachment; filename*=UTF-8''gezin.md"


def test_private_review_recovery_is_authenticated_and_scope_closed():
    calls = []
    class Runtime:
        def dashboard_abandon_private_review(self, thread_id, revision):
            calls.append((thread_id, revision))
            return {"contract":"EvaPrivateReviewRecovery/v1", "disposition":"abandoned", "usage":"unknown", "replayed":False}
    app = FastAPI()
    install_dashboard_initiatives(app, access=DashboardAccess("t"*32), runtime=Runtime())
    client = TestClient(app)
    url = "/internal/v1/dashboard/initiatives/review/abandon"
    payload = {"thread_id":"review-fixture", "expected_revision":4}
    auth = {"authorization":"Bearer " + "t"*32}
    assert client.post(url, json=payload).status_code == 401
    assert client.post(url, json={**payload,"owner":"other"}, headers=auth).status_code == 422
    assert client.post(url + "?owner=other", json=payload, headers=auth).status_code == 422
    assert calls == []
    reply = client.post(url, json=payload, headers=auth)
    assert reply.status_code == 200 and reply.json()["usage"] == "unknown"
    assert calls == [("review-fixture", 4)]



def test_household_provision_routes_bind_scope_and_evidence_to_host():
    calls = []
    class Runtime:
        def dashboard_household_provisions(self):
            calls.append("read")
            return {"contract": "EvaHouseholdProvisions/v1", "cards": []}
        def dashboard_register_household_provision(self, request_id):
            calls.append(("register", request_id))
            return {"status": "proposed", "verification_sha256": None}
        def dashboard_household_provision_command(self, *args):
            calls.append(args)
            return {"status": "paused", "revision": 3}
    app = FastAPI()
    install_dashboard_initiatives(app, access=DashboardAccess("t" * 32), runtime=Runtime())
    client, auth = TestClient(app), {"authorization": "Bearer " + "t" * 32}
    url = "/internal/v1/dashboard/household-provisions"
    assert client.get(url).status_code == 401
    assert client.post(url + "/register", json={"request_id": "fixture"}).status_code == 401
    assert client.get(url + "?household_id=other", headers=auth).status_code == 422
    for extra in ({"owner": "other"}, {"evidence": {"verified": True}}, {"authorize": True}):
        assert client.post(url + "/register", headers=auth,
            json={"request_id": "fixture", **extra}).status_code == 422
    assert calls == []
    assert client.get(url, headers=auth).json()["cards"] == []
    proposed = client.post(url + "/register", headers=auth, json={"request_id": "fixture"})
    assert proposed.json() == {"status": "proposed", "verification_sha256": None}
    assert proposed.headers["cache-control"] == "no-store"
    command = {"provision_id": "fixture-provision", "action": "pause", "expected_revision": 2,
               "binding_sha256": "a" * 64, "request_id": "pause-fixture"}
    assert client.post(url + "/command", json=command).status_code == 401
    result = client.post(url + "/command", headers=auth, json=command)
    assert result.status_code == 200 and result.json()["status"] == "paused"
    assert result.headers["cache-control"] == "no-store"
    assert calls[-1] == ("fixture-provision", "pause", 2, "a" * 64, "pause-fixture")


def test_household_provision_commands_reject_scope_and_untyped_revision():
    calls = []
    class Runtime:
        def dashboard_household_provision_command(self, *args): calls.append(args); return {}
    app = FastAPI()
    install_dashboard_initiatives(app, access=DashboardAccess("t" * 32), runtime=Runtime())
    client, auth = TestClient(app), {"authorization": "Bearer " + "t" * 32}
    url = "/internal/v1/dashboard/household-provisions/command"
    command = {"provision_id": "fixture", "action": "pause", "expected_revision": 2,
               "binding_sha256": "a" * 64, "request_id": "fixture-request"}
    for change in ({"expected_revision": True}, {"expected_revision": 0}, {"expected_revision": "2"},
                   {"owner": "other"}, {"household_id": "other"}, {"action": "force_active"},
                   {"binding_sha256": "A" * 64}, {"request_id": ""}, {"provision_id": {"id": "fixture"}}):
        assert client.post(url, headers=auth, json={**command, **change}).status_code == 422
    assert client.post(url + "?installation=other", headers=auth, json=command).status_code == 422
    assert calls == []


def test_household_provision_routes_fail_closed_when_runtime_control_is_absent():
    app = FastAPI()
    install_dashboard_initiatives(app, access=DashboardAccess("t" * 32), runtime=object())
    client, auth = TestClient(app), {"authorization": "Bearer " + "t" * 32}
    url = "/internal/v1/dashboard/household-provisions"
    assert client.get(url, headers=auth).status_code == 503
    assert client.post(url + "/register", headers=auth,
                       json={"request_id": "fixture"}).status_code == 503


def test_household_version_routes_use_server_choices_and_exact_revision_binding():
    calls = []
    class Runtime:
        def dashboard_household_provision_version_choices(self):
            calls.append(("choices",))
            return {"contract": "EvaHouseholdProvisionChoices/v1", "choices": []}
        def dashboard_household_provision_version_history(self, provision_id):
            calls.append(("history", provision_id))
            return {"contract": "EvaHouseholdProvisionHistory/v1", "entries": []}
        def dashboard_stage_household_provision_version(self, *args):
            calls.append(("stage", *args)); return {"contract": "EvaOwnerHouseholdProvisionVersion/v1"}
        def dashboard_change_household_provision_version(self, *args):
            calls.append(("change", *args)); return {"contract": "EvaOwnerHouseholdProvisionVersion/v1"}
        def dashboard_undo_household_provision_version(self, *args):
            calls.append(("undo", *args)); return {"contract": "EvaOwnerHouseholdProvisionVersion/v1"}
    app = FastAPI()
    install_dashboard_initiatives(app, access=DashboardAccess("t" * 32), runtime=Runtime())
    client, auth = TestClient(app), {"authorization": "Bearer " + "t" * 32}
    url = "/internal/v1/dashboard/household-provisions"
    assert client.get(url + "/versions").status_code == 401
    assert client.get(url + "/fixture/history").status_code == 401
    assert client.get(url + "/versions?owner=other", headers=auth).status_code == 422
    assert client.get(url + "/fixture/history?installation=other", headers=auth).status_code == 422
    assert calls == []
    assert client.get(url + "/versions", headers=auth).headers["cache-control"] == "no-store"
    assert client.get(url + "/fixture/history", headers=auth).json()["entries"] == []
    common = {"provision_id": "fixture", "expected_revision": 7,
              "binding_sha256": "a" * 64, "request_id": "version-change"}
    for action, selector in (("stage", "choice_id"), ("change", "version_id"), ("undo", None)):
        body = {**common, **({selector: "b" * 64} if selector else {})}
        assert client.post(url + "/versions/" + action, json=body).status_code == 401
        reply = client.post(url + "/versions/" + action, json=body, headers=auth)
        assert reply.status_code == 200 and reply.headers["cache-control"] == "no-store"
        assert calls[-1] == (action, "fixture", *(("b" * 64,) if selector else ()), 7, "a" * 64, "version-change")


def test_household_version_mutations_reject_caller_proof_scope_and_finish_actions():
    calls = []
    class Runtime:
        def dashboard_stage_household_provision_version(self, *args): calls.append(args); return {}
        def dashboard_change_household_provision_version(self, *args): calls.append(args); return {}
        def dashboard_undo_household_provision_version(self, *args): calls.append(args); return {}
    app = FastAPI()
    install_dashboard_initiatives(app, access=DashboardAccess("t" * 32), runtime=Runtime())
    client, auth = TestClient(app), {"authorization": "Bearer " + "t" * 32}
    url = "/internal/v1/dashboard/household-provisions/versions/"
    common = {"provision_id": "fixture", "expected_revision": 7,
              "binding_sha256": "a" * 64, "request_id": "version-change"}
    for action, selector in (("stage", "choice_id"), ("change", "version_id"), ("undo", None)):
        body = {**common, **({selector: "b" * 64} if selector else {})}
        for extra in ({"owner": "other"}, {"source": "caller code"}, {"proof": True},
                      {"review_id": "caller-reviewed"}, {"expected_revision": True},
                      {"expected_revision": 0}, {"binding_sha256": "A" * 64}, {"request_id": ""}):
            assert client.post(url + action, json={**body, **extra}, headers=auth).status_code == 422
        assert client.post(url + action + "?scope=other", json=body, headers=auth).status_code == 422
        if selector:
            assert client.post(url + action, json={**body, selector: {}}, headers=auth).status_code == 422
    assert client.post(url + "finish", json=common, headers=auth).status_code == 404
    assert calls == []


def test_household_trial_routes_accept_only_typed_owner_choices_and_exact_notice_receipt():
    calls = []
    class Runtime:
        def dashboard_authorize_household_provision_trial(self, *args): calls.append(("authorize", *args)); return {"contract": "EvaHouseholdProvisionReceipt/v1"}
        def dashboard_evaluate_household_provision_trial(self, *args): calls.append(("evaluate", *args)); return {"contract": "EvaHouseholdProvisionReceipt/v1"}
        def dashboard_acknowledge_household_provision_trial_notice(self, *args): calls.append(("acknowledge", *args)); return {"contract": "EvaHouseholdProvisionReceipt/v1"}
    app = FastAPI()
    install_dashboard_initiatives(app, access=DashboardAccess("t" * 32), runtime=Runtime())
    client, auth = TestClient(app), {"authorization": "Bearer " + "t" * 32}
    url = "/internal/v1/dashboard/household-provisions/trial/"
    common = {"provision_id": "fixture", "expected_revision": 9,
              "binding_sha256": "a" * 64, "request_id": "owner-trial"}
    for action, extra, invalid in (
        ("authorize", {"expires_at_epoch": 1792809000, "max_effects": 1},
         ({"expires_at_epoch": True}, {"max_effects": True}, {"max_effects": 0}, {"max_effects": 17})),
        ("evaluate", {"working": True, "useful": False, "feedback": "De melding kwam te laat."},
         ({"working": "true"}, {"useful": 1}, {"feedback": " "}, {"feedback": "a" * 1001})),
        ("acknowledge", {"notice_id": "b" * 64, "nonce": "c" * 64},
         ({"notice_id": {}}, {"nonce": "C" * 64}, {"nonce": ""})),
    ):
        body = {**common, **extra}
        before = len(calls)
        assert client.post(url + action, json=body).status_code == 401
        for bad in (*invalid, {"expected_revision": True}, {"proof": "caller-proof"},
                    {"owner": "another-owner"}, {"message": "Caller-authored notice"}):
            assert client.post(url + action, headers=auth, json={**body, **bad}).status_code == 422
        assert client.post(url + action + "?scope=other", headers=auth, json=body).status_code == 422
        assert len(calls) == before
        result = client.post(url + action, headers=auth, json=body)
        assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
        assert calls[-1] == (action, "fixture", *extra.values(), 9, "a" * 64, "owner-trial")
