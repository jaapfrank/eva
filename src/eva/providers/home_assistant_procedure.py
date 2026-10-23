"""Bind one reviewed, separately installed camera proposal to household attention.

The saved application stays outside the core. Only current normalized camera
metadata enters the existing offline executor; no image, secret or private memory.
"""
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path

import httpx2 as httpx

from eva.adapters.connectors.camera_observer import HomeAssistantCameraDiscovery
from eva.adapters.connectors.procedure_execution import configured_procedure_executor
from eva.adapters.postgres.memory_outbox import PostgresMemoryOutbox
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256

# Trusted interface glue, not the authored application's decision logic.
RUNNER = '''import json,sys
from datetime import datetime
request=json.load(sys.stdin)
namespace={"__name__":"eva_installed_extension"}
exec(compile(request["source"],"installed_extension.py","exec"),namespace)
result=namespace["propose"](request["event"],request["binding"],now=datetime.fromisoformat(request["now"]))
print(json.dumps(result,allow_nan=False))
'''


class HomeAssistantProcedure:
    def __init__(self, path, *, settings, engine, household, authorize, executor=None):
        self.path = Path(path)
        self.settings, self.engine = settings, engine
        self.household, self.authorize = household, authorize
        self.executor = executor or configured_procedure_executor()
        self.discovery = HomeAssistantCameraDiscovery(base_url=settings.endpoint_url, token=settings.credential)

    def _current(self):
        self.authorize()
        config = json.loads(self.path.read_text())
        required = {'contract', 'enabled', 'household_binding', 'source_file', 'source_sha256',
                    'reference_id', 'source_principal_id', 'review_sha256'}
        if (set(config) != required or config['contract'] != 'EvaHouseholdProcedureBinding/v1'
                or config['enabled'] is not True or config['household_binding'] != self.household.to_payload()):
            raise ValueError('household procedure binding unavailable')
        import re
        if any(not isinstance(config[k], str) or re.fullmatch('[0-9a-f]{64}', config[k]) is None
               for k in ('source_sha256', 'review_sha256')):
            raise ValueError('household procedure review/source digest invalid')
        name = config['source_file']
        if not isinstance(name, str) or Path(name).name != name or not name.endswith('.py'):
            raise ValueError('household procedure source must be a sibling Python file')
        source = (self.path.parent / name).read_bytes()
        if not 1 <= len(source) <= 16384 or hashlib.sha256(source).hexdigest() != config['source_sha256']:
            raise ValueError('household procedure source changed')
        memory = PostgresMemoryOutbox(self.engine, tenant_id=self.household.tenant_id,
                                     installation_id=self.household.installation_id)
        doc = memory.resolve_source(config['reference_id'], principal_id=config['source_principal_id'], project_id=None)
        if doc is None:
            raise ValueError('household procedure reference removed')
        reference = json.loads(doc.content)
        if (reference.get('contract') != 'ProcedureReference/v1'
                or reference.get('content_sha256') != config['source_sha256']
                or datetime.fromisoformat(reference['retention_until'].replace('Z','+00:00')) <= datetime.now(UTC)):
            raise ValueError('household procedure reference changed or expired')
        from eva.providers.procedure_reference_actions import ProcedureReferenceProviderFactory
        from eva.application.procedure_references import ProcedureReferences, ProcedureScope
        route = ProcedureReferenceProviderFactory.build(
            ProcedureReferenceProviderFactory.load_settings(), self.engine).external_tools[0]
        turn = route.source_loader({'tenant_id':doc.tenant_id, 'installation_id':doc.installation_id,
            'principal_id':doc.principal_id, 'conversation_id':doc.conversation_id,
            'message_id':doc.source_ref, 'audience':{'kind':'private_person','principal_id':doc.principal_id}})
        refs = ProcedureReferences(memory, route._host(turn)).resolve_candidates(
            ProcedureScope(doc.tenant_id,doc.installation_id,doc.principal_id,doc.project_id),
            (config['reference_id'],), now=datetime.now(UTC).isoformat())
        if len(refs) != 1 or refs[0].untrusted_content.encode() != source:
            raise ValueError('installed procedure no longer matches current authorized artifact')
        executor_config = {key:getattr(self.executor,key) for key in
                           ('url','image_id','timeout_seconds','output_bytes')}
        executor_config['credential_sha256'] = hashlib.sha256(Path(self.executor.token_file).read_bytes()).hexdigest()
        execution = {'ha_endpoint':self.settings.endpoint_url,
            'ha_credential_sha256':hashlib.sha256(self.settings.credential.encode()).hexdigest(),
            'face_endpoint':self.settings.face_endpoint,'face_revision':self.settings.face_revision,
            'face_credential_sha256':hashlib.sha256(self.settings.face_credential.encode()).hexdigest(),
            'executor':executor_config}
        guard = canonical_sha256({'config':config, 'execution':execution,
                                  'wrapper_sha256':hashlib.sha256(RUNNER.encode()).hexdigest()})
        return source.decode('utf-8'), guard

    def validate_guard(self, digest):
        if digest is not None and self._current()[1] != digest:
            raise ValueError('household procedure configuration changed')

    def _binding(self, observation):
        payload = observation['payload']; camera = payload['camera_observation']
        inventory = self.discovery.discover()
        device = next((d for d in inventory.devices if d.device_id == camera['device_id']
                       and d.camera_entity_id == camera['camera_entity_id']
                       and payload['entity_id'] in d.trigger_entity_ids), None)
        if device is None or inventory.revision != camera['discovery_revision']:
            raise ValueError('camera registry binding changed')
        with httpx.Client(timeout=3, trust_env=False, follow_redirects=False) as client:
            with client.stream('GET', self.settings.face_endpoint.rstrip('/')+'/health',
                    headers={'Authorization':'Bearer '+self.settings.face_credential}) as response:
                response.raise_for_status()
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 16384:
                        raise ValueError('face health exceeds bound')
                health = json.loads(raw)
        return {**{k:observation[k] for k in ('source','source_event_id','occurred_at')},
                'trigger_entity_id':payload['entity_id'], 'camera_entity_id':device.camera_entity_id,
                'device_id':device.device_id, 'discovery_revision':inventory.revision,
                'backend_revision':self.settings.face_revision, 'profile_revision':health['profile_revision']}

    @staticmethod
    def _select_observation(parent, *, now):
        if parent['event_type'] != 'household_observation_batch':
            return (parent, {}) if 'camera_observation' in parent['payload'] else (None, {})
        members = parent['payload'].get('observations')
        if (not isinstance(members, list) or not 1 <= len(members) <= 16
                or len(canonical_json_bytes(members)) > 6144):
            raise ValueError('camera proposal batch differs from shared observation bounds')
        latest = {}
        for child in members:
            camera = child['payload'].get('camera_observation')
            if camera is None:
                continue
            at = datetime.fromisoformat(child['occurred_at'])
            key = camera['device_id']
            digest = canonical_sha256(child)
            previous = latest.get(key)
            if previous is None or at > previous[0]:
                latest[key] = (at, digest, child)
            elif at == previous[0] and digest != previous[1]:
                # Equal-second conflicting images cannot establish which is newest.
                latest[key] = (at, previous[1], None)
        eligible = []
        for at, digest, child in latest.values():
            if child is None or not 0 <= (now-at).total_seconds() < 30:
                continue
            camera = child['payload']['camera_observation']
            # Supersession precedes eligibility: never fall back to an older face.
            if (camera['status'] != 'observed' or not camera.get('image_sha256')
                    or not camera.get('recognition', {}).get('faces')):
                continue
            eligible.append((at, digest, child))
        if not eligible:
            return None, {}
        child = max(eligible, key=lambda item: item[:2])[2]
        return child, {'parent_observation_sha256': canonical_sha256(parent),
                       'selected_observation_sha256': canonical_sha256(child)}

    @staticmethod
    def _proposal_guard(configuration, binding, selection):
        return canonical_sha256({'configuration': configuration,
                                 'source': canonical_sha256(binding), **selection})

    def validate_current(self, context):
        _, guard = self._current()
        observation = context['observation']
        selection = {key: context[key] for key in
                     ('parent_observation_sha256', 'selected_observation_sha256') if key in context}
        if selection:
            import re
            if (len(selection) != 2 or any(re.fullmatch('[0-9a-f]{64}', str(value)) is None
                                          for value in selection.values())
                    or selection['selected_observation_sha256'] != canonical_sha256(observation)
                    or not 0 <= (datetime.now(UTC) - datetime.fromisoformat(
                        observation['occurred_at'])).total_seconds() < 30):
                raise ValueError('camera proposal batch selection changed or expired')
        binding = self._binding(observation)
        if self._proposal_guard(guard, binding, selection) != context['binding_sha256']:
            raise ValueError('camera proposal source or configuration changed')

    def validate_expression(self, event, digest):
        if digest is not None:
            if not self.household.matches_event(event):
                raise ValueError('procedure delivery source audience differs')
            observation, selection = self._select_observation(event.payload['observation'], now=datetime.now(UTC))
            if observation is None:
                raise ValueError('camera proposal no longer has a current selected source')
            self.validate_current({'observation': observation, 'binding_sha256': digest, **selection})

    def __call__(self, event, *, now):
        if event.event_type != 'household_observation_review':
            return None
        if not self.household.matches_event(event):
            raise ValueError('procedure observation audience differs')
        observation, selection = self._select_observation(event.payload['observation'], now=now)
        if observation is None:
            return None
        source, guard = self._current()
        binding = self._binding(observation)
        if self.executor is None:
            raise ValueError('existing procedure executor unavailable')
        inputs = {'source':source, 'event':observation, 'binding':binding, 'now':now.isoformat()}
        report = self.executor.execute(RUNNER, inputs)
        result = report['result']
        if (result.get('exit_code') != 0 or result.get('timed_out') is not False
                or result.get('cancelled') is not False or result.get('stdout_truncated') is not False
                or result.get('stderr_truncated') is not False):
            raise ValueError('isolated household proposal did not complete')
        output = json.loads(result['stdout'])
        if output.get('action') == 'none' and set(output) == {'action','reason','version'}:
            self.validate_guard(guard)
            return None
        fields = {'action','version','text','delivery_key','camera_entity_id','source_event_id',
                  'image_sha256','expires_at_epoch','capture_time_verified','identity_is_not_authentication',
                  'requires_existing_household_assessment_stop_and_durable_delivery'}
        expires = output.get('expires_at_epoch')
        if (set(output) != fields or output['action'] != 'propose_greeting'
                or not isinstance(output['text'], str) or not 1 <= len(output['text']) <= 600
                or output['source_event_id'] != observation['source_event_id']
                or output['camera_entity_id'] != binding['camera_entity_id']
                or output['image_sha256'] != observation['payload']['camera_observation'].get('image_sha256')
                or output['identity_is_not_authentication'] is not True
                or output['requires_existing_household_assessment_stop_and_durable_delivery'] is not True
                or type(expires) not in (int,float) or not float(expires).is_integer()
                or not now.timestamp() < expires <= datetime.fromisoformat(
                    observation['occurred_at']).timestamp()+30):
            raise ValueError('household procedure output is not a bound current proposal')
        context = {'proposal_text':output['text'], 'expires_at_epoch':int(expires),
                   'binding_sha256':self._proposal_guard(guard, binding, selection), 'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
                   'source_binding_sha256':canonical_sha256(binding), 'observation':observation,
                   'is_execution_permission':False, **selection}
        if len(canonical_json_bytes(context)) > 4096:
            # An optional proposal that cannot fit the existing model contract
            # must not turn an ordinary household assessment into an error.
            self.validate_guard(guard)
            return None
        self.validate_current(context)
        return context
