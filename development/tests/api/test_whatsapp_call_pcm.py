"""Opt-in transport proof: real LiveKit PCM, isolated room, no Eva/model calls.
Run inside the pinned voice image with EVA_TEST_LIVEKIT_CONFIG mounted read-only.
"""
import asyncio
import array
import math
import os
from types import SimpleNamespace
import uuid

from livekit import api, rtc
import yaml
from eva.whatsapp_service.server import CallAudio


async def main():
    with open(os.environ["EVA_TEST_LIVEKIT_CONFIG"]) as source:
        config = yaml.safe_load(source)
    key, secret = next(iter(config["keys"].items()))
    room_name = "eva-whatsapp-pcm-proof-" + uuid.uuid4().hex
    def token(identity):
        return api.AccessToken(key, secret).with_identity(identity).with_grants(
            api.VideoGrants(room_join=True, room=room_name)).to_jwt()
    class Socket:
        def __init__(self):
            self.input = asyncio.Queue(maxsize=8)
            self.output = []
            self.ready = asyncio.Event()
        async def send_json(self, value):
            assert value == {"type":"ready", "sample_rate":16000, "channels":1, "encoding":"pcm_s16le"}
            self.ready.set()
        async def send_bytes(self, value):
            assert self.ready.is_set()
            self.output.extend(array.array("h", value))
        async def receive(self):
            return await self.input.get()
    class Lease:
        settings = SimpleNamespace(livekit_url="ws://127.0.0.1:7880")
        closed = False
        renewals = 0
        async def open(self): return {"room":room_name, "token":token("owner")}
        async def authorize(self): pass
        async def renew(self): self.renewals += 1
        async def close(self): self.closed = True
    socket, lease = Socket(), Lease()
    peer, source = rtc.Room(), rtc.AudioSource(16000, 1, queue_size_ms=120)
    received, streams, tasks = [], [], []
    async def capture(track):
        stream = rtc.AudioStream(track, sample_rate=16000, num_channels=1, frame_size_ms=20)
        streams.append(stream)
        async for event in stream:
            received.extend(array.array("h", bytes(event.frame.data)))
    @peer.on("track_subscribed")
    def subscribed(track, publication, participant):
        if participant.identity == "owner" and track.kind == rtc.TrackKind.KIND_AUDIO:
            tasks.append(asyncio.create_task(capture(track)))
    call = CallAudio(socket, lease)
    running = None
    try:
        await peer.connect(lease.settings.livekit_url, token("eva"))
        track = rtc.LocalAudioTrack.create_audio_track("proof-eva", source)
        await peer.local_participant.publish_track(track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
        running = asyncio.create_task(call.run())
        await asyncio.wait_for(socket.ready.wait(), 15)
        for index in range(180):
            def tone(frequency):
                return array.array("h", (int(7000*math.sin(2*math.pi*frequency*(index*320+j)/16000)) for j in range(320))).tobytes()
            await socket.input.put({"type":"websocket.receive", "bytes":tone(440)})
            await source.capture_frame(rtc.AudioFrame(data=tone(880), sample_rate=16000, num_channels=1, samples_per_channel=320))
            await asyncio.sleep(.02)
        await socket.input.put({"type":"websocket.disconnect"})
        await asyncio.wait_for(running, 10)
        def dominant(values):
            values = values[-16000:]
            assert len(values) >= 8000, "audio missing"
            energy = sum(x*x for x in values)/len(values)
            assert energy > 100000, "only silence received"
            scores = {f: abs(sum(x*complex(math.cos(2*math.pi*f*i/16000), math.sin(2*math.pi*f*i/16000)) for i,x in enumerate(values))) for f in (440,880)}
            return max(scores, key=scores.get)
        assert dominant(received) == 440, "caller PCM did not reach Eva participant"
        assert dominant(socket.output) == 880, "Eva PCM did not reach caller"
        assert lease.closed and lease.renewals >= 1 and not call.room.isconnected(), "lease/session cleanup missing"
        print("PASS: real SDK duplex 16kHz mono PCM, distinct 440/880Hz signals, heartbeat and room-specific cleanup; no model calls")
    finally:
        if running and not running.done():
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for stream in streams: await stream.aclose()
        await peer.disconnect()
        await source.aclose()

if __name__ == "__main__":
    asyncio.run(main())
