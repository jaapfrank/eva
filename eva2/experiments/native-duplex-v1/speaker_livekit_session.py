"""Continuous VoicePE candidate using the same EvaAgent as the laptop.

Supply to SpeakerEndpoint after provisioned device admission. This module opens
no listener and creates no rights. Existing HA providers and a current local
household-authority check are required. No owner backend is accepted.
"""
import asyncio
from livekit.agents import AgentSession
from eva.voice_service.agent import BridgeState, EvaAgent, HouseholdCoreClient
from eva.voice_service.media import HomeAssistantSTT, HomeAssistantTTS
from speaker_livekit_audio import SpeakerAudioInput, SpeakerAudioOutput
from speaker_wire import Kind, WireError
from speaker_input import CaptureEnded


class LiveKitHouseholdSpeakerSession:
    def __init__(self, *, core_url, household_token, stt_source, tts_source,
                 vad, check, notify=lambda event: None):
        self.core_url, self.token = core_url, household_token
        self.stt_source, self.tts_source = stt_source, tts_source
        self.vad, self.check, self.notify = vad, check, notify

    def make_session(self):
        return AgentSession(stt=HomeAssistantSTT(self.stt_source, self.check),
            tts=HomeAssistantTTS(self.tts_source, self.check), vad=self.vad,
            turn_detection="vad", allow_interruptions=True,
            min_interruption_duration=.2, false_interruption_timeout=None,
            # This sink confirms physical STOP but cannot pause/resume audio.
            resume_false_interruption=False,
            aec_warmup_duration=0)

    async def __call__(self, binding, wire):
        self.check()
        channel = wire.microphone
        if channel is None: raise ValueError("Speaker microphone is not enabled")
        backend = HouseholdCoreClient(self.core_url, self.token,
            satellite_id=binding.satellite_id, device_id=binding.device_id)
        if not await backend.ready(): raise ValueError("Household core admission unavailable")
        state = BridgeState(backend, self.notify, replay_unheard=False)
        session = self.make_session()
        sink = SpeakerAudioOutput(wire)
        session.output.audio = sink
        locally_closed = False
        def local_control(kind):
            nonlocal locally_closed
            if kind in {Kind.MUTED, Kind.END_SESSION}:
                locally_closed = True
                sink.closed = True
                sink.clear_buffer()
        channel.control_observer = local_control
        source = None
        work_open = False
        return_closed = False
        tasks = set()
        closed = asyncio.Event()
        session.on("close", lambda event: closed.set())

        def current_session():
            self.check()
            if return_closed or locally_closed or closed.is_set():
                raise ValueError("household media session closed")

        async def watch_authority():
            renewed = asyncio.get_running_loop().time()
            while True:
                current_session()
                if work_open and asyncio.get_running_loop().time() - renewed >= 5:
                    await backend.work_return("renew", state.session_id)
                    renewed = asyncio.get_running_loop().time()
                await asyncio.sleep(1)
        try:
            tasks.add(asyncio.create_task(watch_authority()))
            wake = asyncio.create_task(channel.wait_wake())
            tasks.add(wake)
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done: await task
            tasks.remove(wake)
            if backend.work_returns:
                opened = await backend.work_return("open", state.session_id)
                state.session_id = opened["session_id"]
                work_open = True
            source = SpeakerAudioInput(await channel.begin_capture())
            session.input.audio = source
            agent = EvaAgent(state)
            await session.start(agent)
            if work_open:
                tasks.add(asyncio.create_task(agent.poll_saved_work(session, current_session)))
            ending = asyncio.create_task(closed.wait())
            tasks.add(ending)
            while True:
                control = asyncio.create_task(channel.next_control())
                tasks.add(control)
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                if control not in done:
                    for task in done: await task
                    return
                kind = await control
                tasks.remove(control)
                if kind in {Kind.MUTED, Kind.END_SESSION}:
                    return  # finally closes output admission before its first await.
                # Active wakeword interruption preserves the conversation.
                # Explicit stop/mute above ends media, not admitted work.
                session.input.audio = None
                session.clear_user_turn()
                session.interrupt()
                sink.clear_buffer()
                if sink.barrier is not None: await asyncio.shield(sink.barrier)
                await source.aclose()
                source = None
                self.check()
                if locally_closed:
                    return  # A terminal stop overtook the interruption barrier.
                source = SpeakerAudioInput(await channel.begin_capture())
                session.input.audio = source
        except CaptureEnded:
            if not locally_closed: raise
        finally:
            # Stop physical audio before potentially slower session/tool cleanup.
            return_closed = True
            sink.closed = True
            sink.clear_buffer()
            channel.start_close()
            for task in tasks: task.cancel()
            try:
                await sink.aclose()
            finally:
                try:
                    await session.aclose()
                finally:
                    await channel.close()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    if work_open:
                        try:
                            await backend.work_return("close", state.session_id)
                        except Exception:
                            pass  # Core lease expires; closing media never cancels work.
