"""Execute the actual registration script under python -O with recording fakes.

No credentials, network, database, or production containers are used. This proves
script ordering and POST counts, not the underlying client's authorization.
"""
import ast
import contextlib
import io
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


class RegistrationChecks(unittest.TestCase):
    def scenario(self, *, bad_config=False, existing=False, revoked=False, uncertain=False):
        calls = {'authorize': 0, 'find': 0, 'post': 0, 'discover': 0}
        class Authority:
            configuration_sha256 = 'wrong' if bad_config else '25db1dfbcaf4e2ce410f850f72fe633be0d1a1f43ae3a93889f036a5fac7ffb5'
            owner_key = 'fixture-owner'
            def __init__(self, *args):
                pass
            def authorize_current(self, **kwargs):
                calls['authorize'] += 1
                if revoked:
                    raise RuntimeError('revoked fixture')
        class Client:
            def __init__(self, *args):
                pass
            def find(self, **kwargs):
                calls['find'] += 1
                return {'id': 'existing-fixture'} if existing else None
            def register(self, **kwargs):
                calls['post'] += 1
                if uncertain:
                    raise RuntimeError('uncertain fixture response')
                return {'id': 'new-fixture'}
            def discover(self, **kwargs):
                calls['discover'] += 1
                return {'id': 'new-fixture'}, []
        specs = {
            'sqlalchemy': {'create_engine': lambda *a: types.SimpleNamespace(dispose=lambda: None)},
            'eva.adapters.credentials.runtime_files': {'read_runtime_secret_file': lambda *a, **k: 'fixture-only'},
            'eva.adapters.postgres.connection': {'sqlalchemy_postgres_dsn': lambda x: x},
            'eva.adapters.connectors.mcp_registration': {'McpRegistrationConfig': lambda *a, **k: None,
                'ContextForgeRegistrationClient': Client, 'McpRegistrationError': RuntimeError},
            'eva.providers.mcp_owner_authority': {'McpOwnerAuthority': Authority},
        }
        modules = {}
        for name, attributes in specs.items():
            parts = name.split('.')
            for end in range(1, len(parts)+1):
                key = '.'.join(parts[:end])
                modules.setdefault(key, types.ModuleType(key))
            modules[name].__dict__.update(attributes)
        env = {k: 'fixture-only' for k in ['EVA_DATABASE_DSN_FILE', 'EVA_MCP_AUTONOMY_MANDATE_FILE',
               'EVA_MCP_GATEWAY_URL', 'EVA_MCP_REGISTRATION_TOKEN_FILE']}
        source = Path(__file__).with_name('phase1_register_private.py').read_text()
        error = None
        with patch.dict(sys.modules, modules), patch.dict(os.environ, env), contextlib.redirect_stdout(io.StringIO()):
            try:
                exec(compile(source, 'phase1_register_private.py', 'exec', optimize=2), {'__name__': '__main__'})
            except RuntimeError as exc:
                error = exc
        return calls, error

    def test_wrong_configuration_zero_post(self):
        calls, error = self.scenario(bad_config=True)
        self.assertIsNotNone(error)
        self.assertEqual(calls, {'authorize': 0, 'find': 0, 'post': 0, 'discover': 0})

    def test_existing_registration_zero_post(self):
        calls, error = self.scenario(existing=True)
        self.assertIsNotNone(error)
        self.assertEqual(calls, {'authorize': 1, 'find': 1, 'post': 0, 'discover': 0})

    def test_current_authorization_remains_required(self):
        calls, error = self.scenario(revoked=True)
        self.assertIsNotNone(error)
        self.assertEqual(calls, {'authorize': 1, 'find': 0, 'post': 0, 'discover': 0})

    def test_success_exactly_one_post(self):
        calls, error = self.scenario()
        self.assertIsNone(error)
        self.assertEqual(calls, {'authorize': 1, 'find': 1, 'post': 1, 'discover': 1})

    def test_uncertain_response_not_retried(self):
        calls, error = self.scenario(uncertain=True)
        self.assertIsNotNone(error)
        self.assertEqual(calls, {'authorize': 1, 'find': 1, 'post': 1, 'discover': 0})

    def test_active_script_guards_do_not_use_assert(self):
        for name in ['phase1_register_private.py', 'phase1_core_readonly_check.py',
                     'retry_quiescence.py', 'phase1_read_gateway.py']:
            tree = ast.parse(Path(__file__).with_name(name).read_text())
            self.assertFalse(any(isinstance(n, ast.Assert) for n in ast.walk(tree)), name)


if __name__ == '__main__':
    unittest.main()
