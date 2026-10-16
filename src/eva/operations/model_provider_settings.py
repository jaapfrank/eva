"""Optional model configuration, using the existing private artifact/secret stores."""
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlsplit
import re

from eva.contracts.canonical_json import canonical_json_bytes, require_canonical_bytes
from eva.operations.installer_identity_storage import atomic_write_private, read_private_artifact
from eva.operations.installer_secret_store import InstallerSecretStore
from eva.operations.installer_safety import owner_only_lock, require_owner_only_directory


def timestamp():
    return datetime.now(UTC).isoformat()


class ModelProviderSettings:
    def __init__(self, directory: Path):
        self.directory = directory
        if directory.exists() or directory.is_symlink(): self._require_directory()

    def _require_directory(self):
        require_owner_only_directory(self.directory, error=RuntimeError,
            unavailable_message="model settings unavailable", invalid_message="model settings must be private")

    def _lock(self):
        return owner_only_lock(self.directory/'.settings.lock', error=RuntimeError,
            unavailable_message="model settings lock unavailable")

    def _load(self):
        path = self.directory/'settings.json'
        if not path.exists() and not path.is_symlink():
            return {'contract':'EvaOptionalModelSettings/v1', 'selected_id':None, 'connections':{}}
        data = require_canonical_bytes(read_private_artifact(path, label='model settings'))
        if data.get('contract') != 'EvaOptionalModelSettings/v1' or not isinstance(data.get('connections'), dict):
            raise RuntimeError('invalid model settings')
        return data

    def _save(self, data):
        atomic_write_private(self.directory/'settings.json', canonical_json_bytes(data))

    def _secrets(self, identifier):
        if not isinstance(identifier, str) or not re.fullmatch(r'[0-9a-f]{32}', identifier):
            raise ValueError('invalid model connection')
        return InstallerSecretStore(self.directory/identifier)

    def read(self):
        # An unchosen optional adapter must not prevent the normal runtime from
        # starting. Only configuration writes need a prepared private directory.
        if not self.directory.exists() and not self.directory.is_symlink():
            return {'contract':'EvaOptionalModelSettings/v1', 'selected_id':None, 'connections':{}}
        self._require_directory()
        with self._lock(): return self._load()

    def configure(self, *, kind, label, model, endpoint, api_key, workspace_id=''):
        if kind not in {'anthropic','openai_compatible'}: raise ValueError('unsupported model adapter')
        for value, limit in [(label,80),(model,160),(endpoint,500),(workspace_id,160)]:
            if not isinstance(value,str) or len(value)>limit or value!=value.strip() or any(ord(c)<32 or ord(c)==127 for c in value):
                raise ValueError('invalid model configuration')
        if not label or not re.fullmatch(r'[A-Za-z0-9_.:/-]{1,160}',model): raise ValueError('model and label required')
        url=urlsplit(endpoint)
        if url.scheme not in {'http','https'} or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError('invalid model endpoint')
        if url.scheme!='https' and url.hostname not in {'127.0.0.1','::1','localhost'}:
            raise ValueError('remote model connections require HTTPS')
        if kind=='anthropic' and endpoint.rstrip('/')!='https://api.anthropic.com':
            raise ValueError('Anthropic uses its official API endpoint')
        if not isinstance(api_key,str): raise ValueError('API key required')
        self.directory.mkdir(mode=0o700,parents=True,exist_ok=True)
        self._require_directory()
        InstallerSecretStore(self.directory).validate_secret(secret=api_key)
        identifier=uuid4().hex
        with self._lock():
            state=self._load()
            if len(state['connections'])>=12: raise ValueError('maximum model connections reached')
            directory=self.directory/identifier;directory.mkdir(mode=0o700)
            secret=self._secrets(identifier)
            secret.validate_secret(secret=api_key)
            secret.store(slot_id='model_provider',secret=api_key)
            state['connections'][identifier]={'id':identifier,'kind':kind,'label':label,'model':model,
                'endpoint':endpoint.rstrip('/'),'workspace_id':workspace_id,'enabled':True,
                'checked_at':None,'check_state':'unchecked'}
            self._save(state)
        return identifier

    def connection(self, identifier, *, selected=False):
        with self._lock():
            state=self._load();row=state['connections'].get(identifier)
            if row is None or not row['enabled'] or (selected and state['selected_id']!=identifier):
                raise RuntimeError('model connection is disabled or changed')
            return dict(row),self._secrets(identifier).read_secret(slot_id='model_provider').decode('ascii')

    def record_check(self, identifier, *, ready, usage):
        with self._lock():
            state=self._load();row=state['connections'][identifier]
            if not row['enabled']: raise RuntimeError('model connection disabled during check')
            row.update(checked_at=timestamp(),check_state='ready' if ready else 'failed',check_usage=usage)
            if not ready and state['selected_id']==identifier: state['selected_id']=None
            self._save(state)

    def select(self, identifier):
        with self._lock():
            state=self._load()
            if identifier is not None:
                row=state['connections'].get(identifier)
                if row is None or not row['enabled'] or row['check_state']!='ready':
                    raise ValueError('check the model connection before selecting it')
                self._secrets(identifier).read_secret(slot_id='model_provider')
            state['selected_id']=identifier;self._save(state)

    def disable(self, identifier):
        with self._lock():
            state=self._load()
            if identifier not in state['connections']: raise ValueError('unknown model connection')
            state['connections'][identifier]['enabled']=False
            if state['selected_id']==identifier: state['selected_id']=None
            self._save(state)
