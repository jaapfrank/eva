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


LOCAL_MODEL_HOSTS = frozenset({'127.0.0.1', '::1', 'localhost', 'host.docker.internal'})

MODEL_PROVIDER_PRESETS: tuple[dict[str, object], ...] = (
    {
        'preset_id': 'gemini_cloud',
        'kind': 'gemini',
        'label': 'Google Gemini Cloud',
        'default_model': 'gemini-3.1-pro-preview',
        'default_endpoint': 'https://generativelanguage.googleapis.com/v1beta/openai',
        'requires_api_key': True,
        'hint_nl': 'Volledig in Google Cloud (0% lokale GPU). Gebruik een Google AI Studio API-sleutel.',
    },
    {
        'preset_id': 'anthropic_claude',
        'kind': 'anthropic',
        'label': 'Anthropic Claude Cloud',
        'default_model': 'claude-opus-5-5',
        'default_endpoint': 'https://api.anthropic.com',
        'requires_api_key': True,
        'hint_nl': 'Natuurlijke Nederlandse gespreksstijl en sterk redeneren in Anthropic Cloud.',
    },
    {
        'preset_id': 'openai_api',
        'kind': 'openai_compatible',
        'label': 'OpenAI Platform API',
        'default_model': 'gpt-6.2-astra',
        'default_endpoint': 'https://api.openai.com/v1',
        'requires_api_key': True,
        'hint_nl': 'Rechtstreeks via het officiële OpenAI Platform met een eigen API-sleutel.',
    },
    {
        'preset_id': 'openrouter',
        'kind': 'openai_compatible',
        'label': 'OpenRouter (300+ Modellen)',
        'default_model': 'anthropic/claude-opus-5.5',
        'default_endpoint': 'https://openrouter.ai/api/v1',
        'requires_api_key': True,
        'hint_nl': 'Eén sleutel voor honderden modellen (Claude, Gemini, GPT, Qwen, Llama, DeepSeek).',
    },
    {
        'preset_id': 'xai_grok',
        'kind': 'openai_compatible',
        'label': 'xAI Grok Cloud',
        'default_model': 'grok-4.7',
        'default_endpoint': 'https://api.x.ai/v1',
        'requires_api_key': True,
        'hint_nl': 'xAI Grok via de officiële OpenAI-compatibele cloud-API.',
    },
    {
        'preset_id': 'deepseek',
        'kind': 'openai_compatible',
        'label': 'DeepSeek Cloud',
        'default_model': 'deepseek-chat',
        'default_endpoint': 'https://api.deepseek.com/v1',
        'requires_api_key': True,
        'hint_nl': 'DeepSeek V3 / R1 via de officiële cloud-API.',
    },
    {
        'preset_id': 'mistral',
        'kind': 'openai_compatible',
        'label': 'Mistral AI (Europese Cloud)',
        'default_model': 'mistral-large-latest',
        'default_endpoint': 'https://api.mistral.ai/v1',
        'requires_api_key': True,
        'hint_nl': 'Europese AI-provider (Mistral Large / Small) via Le Plateforme.',
    },
    {
        'preset_id': 'groq',
        'kind': 'openai_compatible',
        'label': 'Groq LPU Ultra-Fast Cloud',
        'default_model': 'llama-3.3-70b-versatile',
        'default_endpoint': 'https://api.groq.com/openai/v1',
        'requires_api_key': True,
        'hint_nl': 'Extreem lage latentie op Groq LPU-hardware (geen lokale GPU nodig).',
    },
    {
        'preset_id': 'cerebras',
        'kind': 'openai_compatible',
        'label': 'Cerebras Wafer-Scale Cloud',
        'default_model': 'llama-3.3-70b',
        'default_endpoint': 'https://api.cerebras.ai/v1',
        'requires_api_key': True,
        'hint_nl': 'Razendsnelle cloud-inferentie op Cerebras Wafer-Scale Engines.',
    },
    {
        'preset_id': 'together_ai',
        'kind': 'openai_compatible',
        'label': 'Together AI Cloud',
        'default_model': 'Qwen/Qwen2.5-72B-Instruct-Turbo',
        'default_endpoint': 'https://api.together.xyz/v1',
        'requires_api_key': True,
        'hint_nl': 'Open-weights topmodellen (Qwen, Llama, DeepSeek) in de cloud.',
    },
    {
        'preset_id': 'fireworks_ai',
        'kind': 'openai_compatible',
        'label': 'Fireworks AI Cloud',
        'default_model': 'accounts/fireworks/models/deepseek-v3',
        'default_endpoint': 'https://api.fireworks.ai/inference/v1',
        'requires_api_key': True,
        'hint_nl': 'Snelle productie-inferentie voor open-weights modellen in de cloud.',
    },
    {
        'preset_id': 'perplexity_sonar',
        'kind': 'openai_compatible',
        'label': 'Perplexity Sonar Cloud',
        'default_model': 'sonar-pro',
        'default_endpoint': 'https://api.perplexity.ai',
        'requires_api_key': True,
        'hint_nl': 'Perplexity Sonar API via OpenAI-compatibel HTTPS endpoint.',
    },
    {
        'preset_id': 'ollama_local',
        'kind': 'openai_compatible',
        'label': 'Ollama (Optioneel Lokaal · Geen API-sleutel nodig)',
        'default_model': 'qwen2.5:14b',
        'default_endpoint': 'http://host.docker.internal:11434/v1',
        'requires_api_key': False,
        'hint_nl': 'Optioneel lokaal draaien zonder API-sleutel wanneer Ollama op je systeem draait.',
    },
    {
        'preset_id': 'lmstudio_local',
        'kind': 'openai_compatible',
        'label': 'LM Studio / llama.cpp (Optioneel Lokaal · Geen API-sleutel nodig)',
        'default_model': 'local-model',
        'default_endpoint': 'http://host.docker.internal:1234/v1',
        'requires_api_key': False,
        'hint_nl': 'Optioneel lokale OpenAI-server (LM Studio op poort 1234 of llama.cpp op poort 8080).',
    },
)


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
        if kind not in {'anthropic','gemini','openai_compatible'}: raise ValueError('unsupported model adapter')
        if kind=='gemini' and not endpoint:
            endpoint='https://generativelanguage.googleapis.com/v1beta/openai'
        for value, limit in [(label,80),(model,160),(endpoint,500),(workspace_id,160)]:
            if not isinstance(value,str) or len(value)>limit or value!=value.strip() or any(ord(c)<32 or ord(c)==127 for c in value):
                raise ValueError('invalid model configuration')
        if not label or not re.fullmatch(r'[A-Za-z0-9_.:/-]{1,160}',model): raise ValueError('model and label required')
        url=urlsplit(endpoint)
        if url.scheme not in {'http','https'} or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError('invalid model endpoint')
        is_local_host = url.hostname in LOCAL_MODEL_HOSTS
        if url.scheme!='https' and not is_local_host:
            raise ValueError('remote model connections require HTTPS')
        if kind=='anthropic' and endpoint.rstrip('/')!='https://api.anthropic.com':
            raise ValueError('Anthropic uses its official API endpoint')
        if kind=='gemini' and endpoint.rstrip('/')!='https://generativelanguage.googleapis.com/v1beta/openai':
            raise ValueError('Gemini uses Google official OpenAI-compatible HTTPS endpoint')
        if not isinstance(api_key,str): raise ValueError('API key required')
        effective_secret = api_key
        if kind=='openai_compatible' and is_local_host and not api_key.strip():
            effective_secret = 'eva-local-keyless-endpoint'
        self.directory.mkdir(mode=0o700,parents=True,exist_ok=True)
        self._require_directory()
        InstallerSecretStore(self.directory).validate_secret(secret=effective_secret)
        identifier=uuid4().hex
        with self._lock():
            state=self._load()
            if len(state['connections'])>=32: raise ValueError('maximum model connections reached')
            directory=self.directory/identifier;directory.mkdir(mode=0o700)
            secret=self._secrets(identifier)
            secret.validate_secret(secret=effective_secret)
            secret.store(slot_id='model_provider',secret=effective_secret)
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
