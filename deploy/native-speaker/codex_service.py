"""Codex media composition using the installed EVA household speaker transport."""
import argparse
import asyncio
import os
from pathlib import Path
import secrets
import signal
from speaker_service import ServiceConfig, server_tls, verify_home_assistant
from speaker_endpoint import SpeakerEndpoint
from speaker_livekit_service import load_media_authority
from speaker_livekit_session import LiveKitHouseholdSpeakerSession
from eva.voice_service.codex_media import CodexMediaSession
from eva.adapters.connectors.home_assistant_voice import HomeAssistantSpeechOutput

class CodexHouseholdSpeakerSession(LiveKitHouseholdSpeakerSession):
    def make_session(self):
        config={'wake_greeting':True,'codex_executable':os.environ['EVA_CODEX_EXECUTABLE'],
                'codex_auth_file':os.environ['EVA_CODEX_AUTH_FILE']}
        session=CodexMediaSession(config,None,self.check,speech_output=self.tts_source)
        session.prewarm()
        return session

async def main(args):
    config=ServiceConfig.load(args.config)
    authority=load_media_authority(policy_file=args.policy,token_file=args.household_token,identity_file=args.identity)
    def check():
        authority.validate_current()
        if not secrets.compare_digest(config.eva_token,authority.token):raise ValueError('Household authority changed')
        for grant in config.grants:
            if authority.policy.devices.get(grant.binding.satellite_id)!=grant.binding.device_id:
                raise ValueError('Household device changed')
    check();tls=server_tls(config);await verify_home_assistant(config)
    speech_output=HomeAssistantSpeechOutput(base_url=config.ha_url,token=config.ha_token,
        pipeline_id=config.pipeline_id,tts_engine=config.tts_engine,tts_language=config.tts_language,
        tts_voice="FennaNeural")
    session=CodexHouseholdSpeakerSession(core_url=config.eva_url,household_token=config.eva_token,
        stt_source=None,tts_source=speech_output,vad=None,check=check)
    endpoint=SpeakerEndpoint(grants=config.grants,pcm=None,microphone_enabled=True,session=session,lifetime_from_wake=True)
    stopped=asyncio.Event();loop=asyncio.get_running_loop()
    for kind in (signal.SIGINT,signal.SIGTERM):loop.add_signal_handler(kind,stopped.set)
    server=None
    try:
        server=await endpoint.listen(host=config.host,port=config.port,tls=tls)
        print('{"state":"listening","transport":"household-codex"}',flush=True)
        await stopped.wait()
    finally:
        if server:server.close(close_connections=False)
        await endpoint.close()
        if server:server.close();await server.wait_closed()
        for kind in (signal.SIGINT,signal.SIGTERM):loop.remove_signal_handler(kind)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for field in ('config','policy','household-token','identity'):parser.add_argument('--'+field,required=True,type=Path)
    try:asyncio.run(main(parser.parse_args()))
    except Exception:raise SystemExit('Household Codex media unavailable') from None
