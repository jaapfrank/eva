"""Explicit service composition; device provisioning and activation are separate.

Reuse ServiceConfig/TLS/HA registry validation from the native speaker candidate.
The caller supplies the same loaded HouseholdVoiceAuthority as the core. This
entry point creates neither an owner mandate nor device credentials.
"""
import asyncio
from contextlib import asynccontextmanager
import secrets
from livekit.plugins import silero
from eva.adapters.connectors.home_assistant_voice import HomeAssistantSpeechInput, HomeAssistantSpeechOutput
from eva.voice_service.agent import HouseholdCoreClient
from speaker_endpoint import SpeakerEndpoint
from speaker_livekit_session import LiveKitHouseholdSpeakerSession
from speaker_service import server_tls, verify_home_assistant


@asynccontextmanager
async def open_livekit_service(config, *, authority):
    def check():
        authority.validate_current()
        if not secrets.compare_digest(config.eva_token, authority.token):
            raise ValueError("Speaker credential differs from household authority")
        for grant in config.grants:
            if authority.policy.devices.get(grant.binding.satellite_id) != grant.binding.device_id:
                raise ValueError("Speaker device differs from household authority")
    check()
    tls=server_tls(config)
    await verify_home_assistant(config)
    for grant in config.grants:
        backend=HouseholdCoreClient(config.eva_url, config.eva_token,
            satellite_id=grant.binding.satellite_id,device_id=grant.binding.device_id)
        if not await backend.ready(): raise ValueError("Continuous household core is not ready")
    vad=silero.VAD.load(min_speech_duration=.1,min_silence_duration=.45)
    speech_input=HomeAssistantSpeechInput(base_url=config.ha_url,token=config.ha_token,
        pipeline_id=config.pipeline_id,expected_stt_engine=config.stt_engine,
        expected_pipeline_language=config.pipeline_language)
    speech_output=HomeAssistantSpeechOutput(base_url=config.ha_url,token=config.ha_token,
        pipeline_id=config.pipeline_id,tts_engine=config.tts_engine,tts_language=config.tts_language)
    endpoint=SpeakerEndpoint(grants=config.grants,pcm=None,microphone_enabled=True,
        session=LiveKitHouseholdSpeakerSession(core_url=config.eva_url,
            household_token=config.eva_token,stt_source=speech_input,tts_source=speech_output,
            vad=vad,check=check))
    server=None
    try:
        check()
        server=await endpoint.listen(host=config.host,port=config.port,tls=tls)
        yield server,endpoint
    finally:
        async def cleanup():
            if server is not None: server.close(close_connections=False)
            try: await endpoint.close()
            finally:
                if server is not None:
                    server.close()
                    await server.wait_closed()
        owned=asyncio.create_task(cleanup())
        cancelled=False
        while not owned.done():
            try: await asyncio.shield(owned)
            except asyncio.CancelledError: cancelled=True
        await owned
        if cancelled: raise asyncio.CancelledError



def load_media_authority(*, policy_file, token_file, identity_file):
    """Reuse current household policy checking without starting a core runtime.

    The private policy pins the canonical IdentityPack hash. The core verifies
    its signed identity independently at readiness and every admitted turn.
    """
    from datetime import datetime, UTC
    from eva.bootstrap.household_voice import HouseholdVoiceAuthority, _read_policy
    from eva.adapters.credentials.runtime_files import read_private_runtime_file_bytes
    from eva.contracts.identity import IdentityPack
    def identity():
        return IdentityPack.model_validate_json(read_private_runtime_file_bytes(
            str(identity_file),setting_name="speaker_identity_file"))
    token=read_private_runtime_file_bytes(str(token_file),setting_name="speaker_household_token").decode().strip()
    policy=_read_policy(str(policy_file))
    authority=HouseholdVoiceAuthority(path=str(policy_file),token_path=str(token_file),
        policy=policy,token=token,identity_loader=identity,clock=lambda:datetime.now(UTC))
    authority.validate_current()
    return authority


async def main(args):
    import signal
    from speaker_service import ServiceConfig
    config=ServiceConfig.load(args.config)
    authority=load_media_authority(policy_file=args.policy,token_file=args.household_token,
                                   identity_file=args.identity)
    stop=asyncio.Event()
    loop=asyncio.get_running_loop()
    for kind in (signal.SIGTERM,signal.SIGINT):loop.add_signal_handler(kind,stop.set)
    try:
        async with open_livekit_service(config,authority=authority):
            print('{"state":"listening","transport":"household-livekit"}',flush=True)
            await stop.wait()
    finally:
        for kind in (signal.SIGTERM,signal.SIGINT):loop.remove_signal_handler(kind)


if __name__=="__main__":
    import argparse
    from pathlib import Path
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ("config","policy","household-token","identity"):
        parser.add_argument("--"+name,required=True,type=Path)
    try:asyncio.run(main(parser.parse_args()))
    except Exception:
        print('{"state":"failed","detail":"Household speaker configuration or transport unavailable"}',flush=True)
        raise SystemExit(1) from None
