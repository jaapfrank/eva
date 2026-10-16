"""Owner-only controls for explicit optional API model connections."""
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from eva.adapters.api.installer_request_json import read_small_json_request


def install_dashboard_model_providers(app, *, access, runtime):
    from eva.bootstrap.optional_model_providers import attach_optional_models
    control=attach_optional_models(runtime)

    def authorize(request):
        access.require(request)
        from eva.bootstrap.dashboard_work_context import owner_turn
        try: owner_turn(runtime)
        except Exception: raise HTTPException(403,'current owner required') from None
        if control is None: raise HTTPException(503,'optional model configuration unavailable')
        if request.query_params: raise HTTPException(422,'model configuration is server-bound')

    def response():
        return JSONResponse(control.status(),headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

    @app.get('/internal/v1/dashboard/model-providers')
    def status(request: Request):
        authorize(request)
        try: return response()
        except Exception: raise HTTPException(503,'model configuration unavailable') from None

    @app.post('/internal/v1/dashboard/model-providers/{operation}')
    async def change(operation: str, request: Request):
        authorize(request)
        if request.headers.get('content-type','').split(';',1)[0].strip()!='application/json':
            raise HTTPException(415,'JSON request required')
        payload=await read_small_json_request(request,12000,'model configuration')
        expected={'configure':{'kind','label','model','endpoint','api_key','workspace_id'},
            'check':{'id','allow_provider_request'},'select':{'id'},'disable':{'id'}}
        if not isinstance(payload,dict) or operation not in expected or set(payload)!=expected[operation]:
            raise HTTPException(422,'invalid model configuration request')
        import asyncio
        def invoke():
            authorize(request)
            if operation=='configure':control.settings.configure(**payload)
            elif operation=='check':
                if payload['allow_provider_request'] is not True: raise ValueError('explicit provider request required')
                control.check(payload['id'],admission_check=lambda:authorize(request))
            elif operation=='select':control.settings.select(payload['id'])
            else:control.settings.disable(payload['id'])
        try:
            await asyncio.to_thread(invoke)
            authorize(request)
            return response()
        except ValueError: raise HTTPException(422,'invalid or unchecked model connection') from None
        except HTTPException: raise
        except Exception: raise HTTPException(503,'model connection operation not confirmed') from None
