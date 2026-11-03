"""Owner-only secret intake; no provider, model or application is contacted."""
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from eva.adapters.api.dashboard_access import DashboardAccess
from eva.contracts.canonical_json import canonical_sha256


def test_package_setup_redacts_secrets_and_rechecks_owner_after_body(monkeypatch):
    import eva.adapters.api.dashboard_packages as routes
    import eva.bootstrap.dashboard_work_context as authority
    turn = SimpleNamespace(tenant_id='tenant', installation_id='installation', principal_id='owner')
    scope = canonical_sha256(vars(turn))
    calls = []
    allowed = [True]
    def owner(runtime):
        if not allowed[0]: raise ValueError('revoked')
        return turn
    def post(path, payload, **kwargs):
        calls.append(payload)
        if payload['action'] == 'describe':
            return {'name': 'fixture-package', 'state': 'waiting_for_configuration',
                'schema': {'properties': {'CLIENT_ID': {'type': 'string'}}}, 'environment': {'CLIENT_ID': '<not markup>'},
                'secret_names': ['CLIENT_SECRET'], 'configured_secrets': [], 'installed': False, 'activation_available': True}
        return {'saved': True, 'installed': False}
    monkeypatch.setattr(routes, 'configured_component_evaluator', lambda: SimpleNamespace(owned_package_scopes=(scope,), _post=post))
    monkeypatch.setattr(authority, 'owner_turn', owner)
    app = FastAPI(); routes.install_dashboard_packages(app, access=DashboardAccess(token='a' * 48), runtime=object())
    base = '/internal/v1/dashboard/owned-packages/' + 'b' * 64
    with TestClient(app) as client:
        assert client.get(base).status_code == 401
        headers = {'Authorization': 'Bearer ' + 'a' * 48}
        page = client.get(base, headers=headers)
        assert page.status_code == 200 and '&lt;not markup&gt;' in page.text
        assert page.headers['cache-control'] == 'no-store' and 'frame-ancestors' in page.headers['content-security-policy']
        result = client.post(base + '/save', headers=headers, json={'environment': {'CLIENT_ID': 'client'}, 'secret_values': {'CLIENT_SECRET': 'private-fixture'}})
        assert result.status_code == 200 and 'private-fixture' not in result.text
        assert calls[-1]['secret_values'] == {'CLIENT_SECRET': 'private-fixture'}
        before = len(calls)
        assert client.post(base + '/save', headers={**headers, 'Origin': 'https://foreign.invalid'}, json={}).status_code == 403
        assert len(calls) == before
        async def body(*args):
            allowed[0] = False
            return {'environment': {}, 'secret_values': {}}
        monkeypatch.setattr(routes, 'read_small_json_request', body)
        assert client.post(base + '/save', headers=headers, json={}).status_code == 403
        assert len(calls) == before


def test_read_only_dashboard_does_not_enable_package_mutation(monkeypatch):
    import eva.adapters.api.dashboard_packages as routes
    monkeypatch.setattr(routes, 'configured_component_evaluator', lambda: SimpleNamespace(owned_package_scopes=('a' * 64,)))
    app = FastAPI(); routes.install_dashboard_packages(app, access=DashboardAccess(), runtime=object())
    with TestClient(app) as client:
        assert client.get('/internal/v1/dashboard/owned-packages/' + 'b' * 64).status_code == 503
