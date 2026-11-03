"""Owner configuration/consent for independently reviewed packages, without chat secrets."""
import html
import json
import re
import secrets
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from eva.adapters.api.installer_request_json import read_small_json_request
from eva.adapters.connectors.component_evaluation import configured_component_evaluator
from eva.contracts.canonical_json import canonical_sha256


def install_dashboard_packages(app, *, access, runtime):
    client = configured_component_evaluator()
    if client is None or not client.owned_package_scopes:
        return

    def authorize(request, review_id, *, callback=False):
        # Mutating package setup is never offered in the unauthenticated,
        # read-only development dashboard.
        if access.token is None:
            raise HTTPException(503, 'owner authentication required')
        access.require(request)
        if re.fullmatch(r'[0-9a-f]{64}', review_id) is None or (request.query_params and not callback):
            raise HTTPException(422, 'invalid package reference')
        origin = request.headers.get('origin')
        if origin and urlsplit(origin).netloc != request.headers.get('host') and not callback:
            raise HTTPException(403, 'same-origin owner request required')
        from eva.bootstrap.dashboard_work_context import owner_turn
        try:
            turn = owner_turn(runtime)
            scope = canonical_sha256({key: getattr(turn, key) for key in
                ('tenant_id', 'installation_id', 'principal_id')})
            if scope not in client.owned_package_scopes:
                raise ValueError('owner scope differs')
            return scope
        except Exception:
            raise HTTPException(403, 'current package owner required') from None

    def call(action, review_id, scope, **values):
        try:
            return client._post('/owned-package-setup', {'action': action, 'review_id': review_id,
                'scope_sha256': scope, **values}, limit=160000)
        except Exception:
            # Neither a remote error body nor supplied credentials enter logs,
            # model context or reflected validation errors.
            raise HTTPException(503, 'package operation not confirmed') from None

    @app.get('/internal/v1/dashboard/owned-packages/{review_id}')
    def page(review_id: str, request: Request):
        scope = authorize(request, review_id)
        state = call('describe', review_id, scope)
        nonce = secrets.token_urlsafe(24)
        fields = []
        for name, schema in state['schema'].get('properties', {}).items():
            value = state['environment'].get(name, '')
            fields.append('<label>' + html.escape(name) + '<input name="env.' + html.escape(name, quote=True)
                + '" value="' + html.escape(value, quote=True) + '" autocomplete="off"></label>')
        for name in state['secret_names']:
            fields.append('<label>' + html.escape(name) + '<input name="secret.' + html.escape(name, quote=True)
                + '" type="password" autocomplete="new-password" placeholder="'
                + ('Al ingesteld' if name in state['configured_secrets'] else 'Nog nodig') + '"></label>')
        body = '<!doctype html><meta charset="utf-8"><title>Eva — voorziening activeren</title><h1>' + html.escape(state['name']) + '</h1>'
        body += '<p>Vul ontbrekende toegang hier in. Geheimen worden buiten het gesprek bewaard. Installatie en werkelijk gebruik blijven afzonderlijk gecontroleerd.</p>'
        body += '<p id="status">Status: ' + html.escape(state['state']) + '</p><form id="settings">' + ''.join(fields)
        body += '<button>Instellingen opslaan</button></form><button id="activate">Aanmelden bij de dienst</button><p><a id="authorization" rel="noopener noreferrer" target="_blank" hidden>Open de echte aanmeldpagina</a></p>'
        data = json.dumps({'redirect_env': state.get('redirect_env'), 'installed': state['installed'],
                           'activation': state['activation_available']}).replace('<', '\\u003c')
        body += '<script nonce="' + nonce + '">const info=' + data + ''';
const form=document.getElementById('settings'),status=document.getElementById('status');
const redirect=form.elements.namedItem('env.'+info.redirect_env);
if(redirect&&!redirect.value)redirect.value=location.origin+location.pathname+'/callback';
form.querySelector('button').disabled=info.installed;
document.getElementById('activate').disabled=!info.installed||!info.activation;
async function send(action,payload){const r=await fetch(location.pathname+'/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});if(!r.ok)throw Error('De handeling is niet bevestigd. Controleer de bestaande status voordat je haar herhaalt.');return await r.json();}
form.onsubmit=async e=>{e.preventDefault();const environment={},secret_values={};for(const [k,v] of new FormData(form)){if(k.startsWith('env.'))environment[k.slice(4)]=v;else if(k.startsWith('secret.'))secret_values[k.slice(7)]=v;}try{await send('save',{environment,secret_values});for(const input of form.querySelectorAll('[type=password]'))input.value='';status.textContent='Instellingen opgeslagen. De beheerde toepassing loopt via Eva; vernieuw deze pagina voor de echte status.';}catch(err){status.textContent=err.message;}};
document.getElementById('activate').onclick=async()=>{try{const r=await send('start',{});const a=document.getElementById('authorization');a.href=r.authorization_url;a.hidden=false;status.textContent='Gebruik de echte aanmeldpagina om toestemming te geven.';}catch(err){status.textContent=err.message;}};
</script>'''
        return HTMLResponse(body, headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer',
            'X-Content-Type-Options': 'nosniff', 'Content-Security-Policy':
            "default-src 'none'; script-src 'nonce-" + nonce + "'; connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"})

    @app.post('/internal/v1/dashboard/owned-packages/{review_id}/{action}')
    async def change(review_id: str, action: str, request: Request):
        scope = authorize(request, review_id)
        if request.headers.get('content-type', '').split(';', 1)[0].strip() != 'application/json':
            raise HTTPException(415, 'JSON request required')
        payload = await read_small_json_request(request, 24000, 'package setup')
        fields = {'save': {'environment', 'secret_values'}, 'start': set()}
        if not isinstance(payload, dict) or action not in fields or set(payload) != fields[action]:
            raise HTTPException(422, 'invalid package setup request')
        import asyncio
        def invoke():
            if authorize(request, review_id) != scope:
                raise HTTPException(403, 'package owner changed before dispatch')
            result = call(action, review_id, scope, **payload)
            authorize(request, review_id)
            return result
        result = await asyncio.to_thread(invoke)
        return JSONResponse(result, headers={'Cache-Control': 'no-store'})

    @app.get('/internal/v1/dashboard/owned-packages/{review_id}/callback')
    def callback(review_id: str, request: Request):
        scope = authorize(request, review_id, callback=True)
        query = request.url.query
        if not query or len(query.encode()) > 8192:
            raise HTTPException(422, 'bounded authorization callback required')
        call('callback', review_id, scope, query=query)
        return HTMLResponse('<meta charset="utf-8"><p>Autorisatie ontvangen. Eva moet de echte verbinding nog controleren. Je kunt dit venster sluiten.</p>',
                            headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'})
