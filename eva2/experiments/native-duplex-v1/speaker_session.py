"""Composition of native controls, continuous capture and Eva's household lane."""
import asyncio

from microphone_session import BufferedMicrophone, run_conversation
from reply_lane import HouseholdDuplexReplyLane
from speaker_wire import Kind, WireError


class HouseholdSpeakerSession:
    """Supply to SpeakerEndpoint with microphone_enabled=True.

    Local stop ends old capture, stops playback, then grants fresh capture
    without requiring another wakeword. Mute ends the entire connection.
    Retired core/STT cleanup never owns new audio or its generation.
    """

    def __init__(self, *, client, speech_input, detector_factory, idle_seconds=15):
        self.client = client
        self.speech_input = speech_input
        self.detector_factory = detector_factory
        self.idle_seconds = idle_seconds

    async def __call__(self, binding, output):
        channel = output.microphone
        if channel is None:
            raise ValueError("speaker microphone transport is not enabled")
        work = control = None
        retired: set[asyncio.Task] = set()
        generation = 0
        try:
            await channel.wait_wake()
            while True:
                for old in tuple(retired):
                    if old.done():
                        await asyncio.gather(old, return_exceptions=True)
                        retired.remove(old)
                if len(retired) >= 8:
                    raise WireError("retired microphone sessions exceeded their bound")
                source = await channel.begin_capture()
                lane = HouseholdDuplexReplyLane(binding=binding, client=self.client,
                                               output=output, initial_generation=generation)
                microphone = BufferedMicrophone(source, detector_factory=self.detector_factory)
                work = asyncio.create_task(run_conversation(
                    microphone=microphone, speech_input=self.speech_input,
                    lane=lane, idle_seconds=self.idle_seconds))
                control = asyncio.create_task(channel.next_control())
                await asyncio.wait((work, control), return_when=asyncio.FIRST_COMPLETED)
                if control.done() or channel.control_pending:
                    kind = await control
                    if not work.done() and not work.cancelling():
                        work.cancel()
                    # Physical stop precedes slow old STT/core cleanup.
                    generation = lane.generation
                    if generation:
                        await output.interrupt(generation=generation)
                    await source.aclose()
                    retired.add(work)
                    work = None
                    if kind == Kind.MUTED:
                        return
                    continue  # Local stop grants a new capture epoch, same household.
                await work
                return  # Ordinary idle/finished conversation requires a fresh wake.
        finally:
            channel.start_close()
            pending = retired | {task for task in (work, control) if task is not None}
            for task in pending:
                if not task.done() and not task.cancelling():
                    task.cancel()
            try:
                await channel.close()
            finally:
                await asyncio.gather(*pending, return_exceptions=True)
