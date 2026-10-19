"""Codex media composition using the installed EVA household speaker transport."""
import argparse
import asyncio
import os
from pathlib import Path
import secrets
import signal
import time
from speaker_service import ServiceConfig, server_tls, verify_home_assistant
from speaker_endpoint import SpeakerEndpoint
from speaker_livekit_service import load_media_authority
from speaker_livekit_session import LiveKitHouseholdSpeakerSession
from eva.voice_service.codex_media import CodexMediaSession
from eva.adapters.connectors.home_assistant_voice import HomeAssistantSpeechOutput

class NativeCodexMediaSession(CodexMediaSession):
    """Keep a listening utterance across the native LOCAL_STOP/resume handshake."""
    def clear_user_turn(self):
        self._native_capture_resume=True
        output=self.renderer
        source=self.source
        busy=(self._core_pending>0
              or bool(self.greeting and not self.greeting.done())
              or bool(source and (getattr(source,'active',False)
                                   or getattr(source,'queued_duration',0)>0))
              or bool(output and (getattr(output,'_speaking_handle',None) is not None
                    or any(not handle.done() for handle in output.speech_handles))))
        self._preserve_native_input=bool(
            (self._input_open or self._input_fragments) and not busy)
        # LOCAL_STOP may arrive after the listener has already opened the
        # correction. Retire admitted work, but keep this unadmitted utterance
        # on the new generation rather than silently retiring its future reply.
        self._native_unadmitted_input=set(self._input_open)
        self._native_unadmitted_input.update(row[0] for row in self._input_fragments)
        if not self._native_unadmitted_input:
            super().clear_user_turn()

    def interrupt(self):
        preserve=getattr(self,'_preserve_native_input',False)
        self._preserve_native_input=False
        unadmitted=getattr(self,'_native_unadmitted_input',set())
        self._native_unadmitted_input=set()
        local_resume=getattr(self,'_native_capture_resume',False)
        self._native_capture_resume=False
        self._native_stopped_output=None
        self.diagnostic('native_input_resume',preserved=preserve,
            generation=self._generation,response_floor=self._response_floor,
            reason='listening_capture_resume' if preserve else 'interrupt_pending_or_output')
        if not preserve:
            result=super().interrupt()
            if local_resume and self.source is not None:
                sink=getattr(self.source,'sink',None)
                segment=getattr(sink,'segment',None)
                self._native_stopped_output=(self.source,getattr(self.source,'epoch',None),sink,
                    getattr(segment,'generation',None),getattr(self,'_native_capture_epoch',None))
            if unadmitted:
                for identity in unadmitted:
                    self._input_generations[identity]=self._generation
                self._input_fragments=[(identity,text,self._generation if identity in unadmitted else generation)
                    for identity,text,generation in self._input_fragments]
                self.diagnostic('native_input_rebased',turns=len(unadmitted),
                    generation=self._generation,response_floor=self._response_floor)
            return result

    async def capture_native(self, frames):
        capture=getattr(frames,'capture',None)
        self._native_capture_epoch=getattr(capture,'epoch',None)
        stopped=getattr(self,'_native_stopped_output',None)
        self._native_stopped_output=None
        if stopped is not None:
            source,epoch,sink,generation,previous_capture=stopped
            barrier=getattr(sink,'barrier',None)
            wire=getattr(sink,'wire',None)
            # Only the inherited LOCAL_STOP -> STOPPED -> fresh CAPTURE route
            # may retain a new prefix. Never retain pre-STOP or normal echo PCM.
            bound=(epoch is not None and generation is not None and previous_capture is not None
                and capture is not None and capture.epoch>previous_capture
                and getattr(capture.owner,'_output',None) is wire
                and capture.state=='active' and source is self.source
                and source.epoch==epoch and barrier is not None and barrier.done()
                and not barrier.cancelled() and barrier.exception() is None)
            if bound:
                receipt=wire.playback_result(generation)
                bound=receipt.status=='interrupted' and receipt.confirmed_frames is not None
            if bound:
                started=time.monotonic()
                while source.input_suppressed and time.monotonic()-started<.3:
                    self.current()
                    if source.epoch!=epoch:break
                    await asyncio.sleep(min(.01,max(0,.3-(time.monotonic()-started))))
                retained=(source.epoch==epoch and not source.input_suppressed)
                self.diagnostic('native_post_stop_prefix',retained=retained,
                    wait_ms=round((time.monotonic()-started)*1000))
                if retained:
                    async def paced():
                        # Existing capture buffer holds at most two seconds.
                        # Pace its post-ACK prefix to avoid overflowing the live
                        # microphone queue. Added tail delay is at most 300 ms.
                        next_frame=time.monotonic()
                        async for frame in frames:
                            self.current()
                            await asyncio.sleep(max(0,next_frame-time.monotonic()))
                            self.current()
                            if capture.state!='active':return
                            next_frame=max(next_frame,time.monotonic())+frame.samples_per_channel/frame.sample_rate
                            yield frame
                    return await super().capture_native(paced())
        return await super().capture_native(frames)


class CodexHouseholdSpeakerSession(LiveKitHouseholdSpeakerSession):
    async def __call__(self, binding, wire):
        self.check()
        channel = wire.microphone
        if channel is None:
            raise ValueError("Speaker microphone is not enabled")
        # Keep the existing network-candidate warmup, without asking the core
        # to admit a conversation while this device is only in standby.
        media = self.make_session()
        wake = asyncio.create_task(channel.wait_wake())
        try:
            while not wake.done():
                self.check()
                await asyncio.wait((wake,), timeout=1)
            await wake
            self.check()
            # A connection-local composition owns this exact prewarmed media.
            # Concurrent speakers cannot exchange media or household bindings.
            session = LiveKitHouseholdSpeakerSession(
                core_url=self.core_url, household_token=self.token,
                stt_source=self.stt_source, tts_source=self.tts_source,
                vad=self.vad, check=self.check, notify=self.notify)
            session.make_session = lambda: media
            await session(binding, wire)
        finally:
            if not wake.done():
                wake.cancel()
            await asyncio.gather(wake, return_exceptions=True)
            # Also retire ICE if readiness fails before the inherited session
            # takes ownership, or the device disconnects without ever waking.
            await media.aclose()

    def make_session(self):
        config={'wake_greeting':True,'codex_executable':os.environ['EVA_CODEX_EXECUTABLE'],
                'codex_auth_file':os.environ['EVA_CODEX_AUTH_FILE']}
        session=NativeCodexMediaSession(config,None,self.check,speech_output=self.tts_source)
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
