"""Bind an optional voice choice to existing verified HA access, without audio."""
import asyncio
import hashlib
import json
from urllib.parse import urlsplit, urlunsplit
from eva.contracts.canonical_json import canonical_json_bytes
from eva.operations.installer_identity_storage import read_private_artifact, write_private_once


def _read(verifier, digest):
    import re
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("voice evidence identity invalid")
    raw = read_private_artifact(verifier.evidence_workspace / (digest + ".json"), label="voice selection")
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("voice evidence changed")
    return json.loads(raw)


def _save(verifier, payload):
    raw = canonical_json_bytes(payload)
    digest = hashlib.sha256(raw).hexdigest()
    path = verifier.evidence_workspace / (digest + ".json")
    if not path.exists(): write_private_once(path, raw)
    elif path.read_bytes() != raw: raise ValueError("voice evidence changed")
    return digest


def ha_binding(verifier, configuration_store):
    step = verifier.capability_workflow.status().step("home_assistant")
    if step.state != "ready": raise ValueError("verify Home Assistant before choosing voice")
    config = configuration_store.read(slot_id="home_assistant")
    secret = next(s for s in verifier.secret_store.status() if s.slot_id == "home_assistant")
    receipt = _read(verifier, step.evidence_sha256)
    binding = {"configuration_sha256": config.configuration_sha256,
               "secret_reference": secret.reference, "secret_generation": secret.generation}
    if (secret.state != "present" or receipt.get("contract") != "EvaInstallerProviderVerificationReceipt/v4"
            or receipt.get("slot_id") != "home_assistant" or receipt.get("outcome") != "ready"
            or receipt.get("reason_code") != "verified"
            or any(receipt.get(k) != v for k, v in binding.items())):
        raise ValueError("voice requires the current verified Home Assistant access")
    return {**binding, "evidence_sha256": step.evidence_sha256, "url": config.value}


async def discover_pipelines(url, token):
    """One bounded HA WebSocket list; never run a pipeline or synthesize audio."""
    from websockets.asyncio.client import connect
    class NoRedirectConnect(connect):
        def process_redirect(self, exc):
            return exc
    origin = urlsplit(url)
    if origin.scheme not in {"http", "https"} or not origin.hostname or origin.username or origin.password:
        raise ValueError("invalid verified Home Assistant origin")
    address = urlunsplit(("wss" if origin.scheme == "https" else "ws", origin.netloc, "/api/websocket", "", ""))
    async with asyncio.timeout(10):
        async with NoRedirectConnect(address, proxy=None, open_timeout=5, max_size=65536) as socket:
            async def receive(): return json.loads(await socket.recv())
            if (await receive()).get("type") != "auth_required": raise ValueError("HA voice authentication failed")
            await socket.send(json.dumps({"type": "auth", "access_token": token}))
            if (await receive()).get("type") != "auth_ok": raise ValueError("HA voice authentication failed")
            await socket.send(json.dumps({"id": 1, "type": "assist_pipeline/pipeline/list"}))
            result = await receive()
            if result.get("id") != 1 or result.get("success") is not True:
                raise ValueError("HA pipeline listing unavailable")
            return result["result"]["pipelines"]


def pipeline_options(rows):
    options = []
    if not isinstance(rows, list) or len(rows) > 64: raise ValueError("invalid HA pipeline list")
    for row in rows:
        if not isinstance(row, dict): raise ValueError("invalid HA pipeline")
        fields = {k: row.get(k) for k in ("id", "name", "language", "stt_engine", "tts_engine", "tts_language")}
        if not all(isinstance(v, str) and 0 < len(v) <= 256 for v in fields.values()): continue
        if row.get("tts_voice") is not None:
            if not isinstance(row['tts_voice'], str) or len(row['tts_voice']) > 256: continue
            fields['tts_voice'] = row['tts_voice']
        fields['processing'] = ("Cloud: spraak wordt buiten deze computer verwerkt."
            if any('cloud' in fields[k].lower() for k in ('stt_engine','tts_engine')) else
            "Via jouw Home Assistant-spraakproviders; lokale verwerking is hiermee niet bewezen.")
        options.append(fields)
    if not options or len({o['id'] for o in options}) != len(options): raise ValueError("no unambiguous usable speech pipelines")
    return options


async def voice_inventory(verifier, store, discover=discover_pipelines):
    current=verifier.capability_workflow.status().step('voice')
    if current.state=='ready':
        verifier.capability_workflow.record_invalidation(step_id='voice', evidence_sha256=current.evidence_sha256)
    if verifier.capability_workflow.status().step('voice').state not in {'selected','blocked'}:
        raise ValueError('select voice first')
    binding = ha_binding(verifier, store)
    token = verifier.secret_store.read_secret(slot_id='home_assistant').decode()
    options = pipeline_options(await discover(binding['url'], token))
    if ha_binding(verifier, store) != binding: raise ValueError('HA access changed')
    payload = {'contract':'EvaInstallerVoiceInventory/v1', 'home_assistant':binding, 'pipelines':options}
    return {'inventory_sha256':_save(verifier, payload), 'pipelines':options}


def confirm_voice(verifier, store, *, inventory_sha256, pipeline_id, allow_provider_audio):
    if allow_provider_audio is not True: raise ValueError('confirm speech provider processing')
    status = verifier.capability_workflow.status()
    payload = _read(verifier, inventory_sha256)
    if payload.get('contract') != 'EvaInstallerVoiceInventory/v1' or payload.get('home_assistant') != ha_binding(verifier, store):
        raise ValueError('voice inventory no longer matches HA access')
    options = [row for row in payload['pipelines'] if row['id'] == pipeline_id]
    if len(options) != 1: raise ValueError('choose a discovered pipeline')
    require_supported_privacy(options[0])
    selection = {'contract':'EvaInstallerVoiceSelection/v1', 'home_assistant':payload['home_assistant'],
                 'pipeline': options[0], 'provider_audio_confirmed':True}
    digest = _save(verifier, selection)
    verifier.capability_workflow.record_verification(step_id='voice', outcome='ready', evidence_sha256=digest,
        expected_revision=status.revision, expected_head_sha256=status.head_sha256)
    return digest


def load_voice_selection(plan, verifier, store):
    step = next(row for row in plan['capabilities'] if row['step_id'] == 'voice')
    if step['state'] != 'ready': raise ValueError('confirmed voice choice required')
    selected = _read(verifier, step['evidence_sha256'])
    if (selected.get('contract') != 'EvaInstallerVoiceSelection/v1'
            or selected.get('provider_audio_confirmed') is not True
            or selected.get('home_assistant') != ha_binding(verifier, store)):
        raise ValueError('voice selection differs from current verified HA access')
    require_supported_privacy(selected['pipeline'])
    return selected


def require_supported_privacy(pipeline):
    # Reuse the existing HA Cloud privacy basis; discovery is not evidence for
    # the retention practices of an arbitrary third-party speech provider.
    if (pipeline.get('stt_engine'), pipeline.get('tts_engine')) != (
            'stt.home_assistant_cloud', 'tts.home_assistant_cloud'):
        raise ValueError('Deze spraakproviders hebben nog geen bevestigde bewaarbeleidbasis voor de bestaande voice policy.')
