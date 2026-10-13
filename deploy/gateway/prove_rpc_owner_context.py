"""Pinned RPC handler + actual service access policy, isolated SQLite admin.

Authentication context is a fixture; upstream invocation is replaced by a counter.
This is not an HTTP/JWT authentication or document-effect test.
"""
import ast
import asyncio
import json
import logging
from pathlib import Path
import types

import sqlalchemy as sa
from mcpgateway import bootstrap_db
from mcpgateway.auth_context import get_rpc_filter_context, get_user_email
from mcpgateway.db import EmailUser
from mcpgateway.services.tool_service import ToolNotFoundError, ToolService
from starlette.requests import Request


class RpcFailure(Exception):
    pass


def handler(source, service, cancellation):
    tree = ast.parse(source)
    function = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef)
                    and n.name == '_execute_rpc_tools_call')
    text = 'from __future__ import annotations\n' + ast.get_source_segment(source, function)
    namespace = {'asyncio': asyncio, 'settings': types.SimpleNamespace(mcpgateway_tool_cancellation_enabled=True),
                 'get_rpc_filter_context': get_rpc_filter_context,
                 'get_user_email': get_user_email, 'tool_service': service,
                 'cancellation_service': cancellation,
                 'ToolNotFoundError': ToolNotFoundError, 'JSONRPCError': RpcFailure,
                 'logger': logging.getLogger('fixture-rpc')}
    exec(compile(text, 'actual_rpc_handler', 'exec'), namespace)
    return namespace['_execute_rpc_tools_call']


async def main():
    await bootstrap_db.main()
    engine = sa.create_engine('sqlite:////tmp/rpc-owner.db')
    owner = 'fixture-admin@example.invalid'
    policy = ToolService()
    class Boundary:
        accepted = 0
        payload = {}
        async def invoke_tool(self, **kwargs):
            allowed = await policy._check_tool_access(kwargs['db'], self.payload,
                                                     kwargs['user_email'], kwargs['token_teams'])
            if not allowed:
                raise ToolNotFoundError('fixture access denied')
            self.accepted += 1
            return {'content': []}
    boundary = Boundary()
    class Cancellation:
        registered = {}
        unregistered = []
        async def register_run(self, run_id, **kwargs):
            self.registered[run_id] = (kwargs['owner_email'], kwargs['owner_team_ids'])
        async def get_status(self, run_id):
            return None
        async def unregister_run(self, run_id):
            self.unregistered.append(run_id)
    cancellation = Cancellation()
    cases = [
        ('admin_own_private', owner, None, True, 'private', owner, None, True),
        ('admin_other_private', owner, None, True, 'private', 'other@example.invalid', None, False),
        ('admin_public', owner, None, True, 'public', 'other@example.invalid', None, True),
        ('admin_team', owner, None, True, 'team', 'other@example.invalid', 'team-a', True),
        ('anonymous_admin_private', None, None, True, 'private', owner, None, False),
        ('public_only_owner_private', owner, [], True, 'private', owner, None, False),
        ('scoped_owner_private', owner, ['team-a'], False, 'private', owner, None, True),
        ('unscoped_nonadmin_private', owner, None, False, 'private', owner, None, False),
        ('matching_team', owner, ['team-a'], False, 'team', 'other@example.invalid', 'team-a', True),
        ('other_team', owner, ['team-b'], False, 'team', 'other@example.invalid', 'team-a', False),
        ('missing_email', None, None, True, 'private', owner, None, False),
        ('invalid_email', {'forged': owner}, None, True, 'private', owner, None, False),
        ('unverified_jwt', owner, None, True, 'private', owner, None, False),
        ('forged_params', owner, [], True, 'private', owner, None, False),
        ('stale_session_admin', owner, None, True, 'private', owner, None, False),
    ]
    results = {}
    for version in ['original', 'guarded']:
        call = handler(Path('/review/main.' + version + '.py').read_text(), boundary, cancellation)
        outcomes = {}
        with sa.orm.Session(engine) as db:
            for name, email, teams, admin, visibility, tool_owner, team_id, expected in cases:
                boundary.payload = {'visibility': visibility, 'owner_email': tool_owner, 'team_id': team_id}
                request = Request({'type': 'http', 'method': 'POST', 'path': '/rpc',
                                   'headers': [(b'authorization', b'Bearer unverified-forged-token')]})
                user = {'email': email, 'is_admin': admin}
                if name != 'unverified_jwt':
                    request.state._jwt_verified_payload = ('fixture-verified', {'user': user, 'teams': teams})
                    request.state.token_use = 'api'
                if name == 'stale_session_admin':
                    db.execute(sa.update(EmailUser).where(EmailUser.email == owner).values(is_admin=False))
                    db.commit()
                    request.state.token_use = 'session'
                    request.state.token_teams = None
                expected_context = get_rpc_filter_context(request, user)
                params = {'name': 'fixture-tool'}
                if name == 'forged_params':
                    params.update(user_email=owner, is_admin=True, token_teams=None,
                                  arguments={'owner_email': owner, 'is_admin': True})
                before = boundary.accepted
                try:
                    await call(request, db, user, req_id=name, params=params,
                               lowered_request_headers={}, server_id=None)
                    accepted = True
                except RpcFailure:
                    accepted = False
                expected = False if version == 'original' and name == 'admin_own_private' else expected
                if accepted != expected or boundary.accepted-before != int(expected):
                    raise RuntimeError(version + ' unexpected access outcome: ' + name)
                if cancellation.registered[name] != (expected_context[0], expected_context[1] or []):
                    raise RuntimeError('Cancellation owner binding changed')
                if name not in cancellation.unregistered:
                    raise RuntimeError('Cancellation run not unregistered')
                if name == 'stale_session_admin':
                    db.execute(sa.update(EmailUser).where(EmailUser.email == owner).values(is_admin=True))
                    db.commit()
                outcomes[name] = accepted
        results[version] = outcomes
    print(json.dumps({'status': 'passed', 'cases_per_version': len(cases), 'results': results,
                      'http_authentication_tested': False, 'upstream_effects': 0,
                      'actual_auth_context_used': True, 'cancellation_owner_binding_preserved': True,
                      'actual_rpc_handler_and_live_service_access_policy': True}))


asyncio.run(main())
