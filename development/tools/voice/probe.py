"""Bounded synthetic audio test. Receives the existing EVA access lease on stdin.

Requires aiortc. Never records a microphone or invokes EVA tools. The sample is
model-generated Dutch audio; it does not establish physical-speaker quality.
"""
import argparse
import asyncio
import json
import math
import struct
import sys
import wave
from pathlib import Path
from aiortc import AudioStreamTrack, RTCPeerConnection, RTCSessionDescription
from codex_voice_session import CodexVoiceSession

async def run(args, lease):
    stats = {'decoded_frames': 0, 'frames_rms_over_200': 0}
    peer = RTCPeerConnection()
    peer.addTrack(AudioStreamTrack())  # Synthetic silence; no microphone access.
    channel = peer.createDataChannel('oai-events')
    started = asyncio.Event()
    output = None
    consumers = []
    event_types = set()
    metadata = {}
    @channel.on('message')
    def message(raw):
        try: event = json.loads(raw)
        except (ValueError, TypeError): return
        event_types.add(event.get('type', 'unknown'))
        if event.get('type') == 'session.started':
            metadata['reported_model'] = event.get('session', {}).get('model')
            started.set()
    @peer.on('track')
    def track_received(track):
        async def consume():
            nonlocal output
            try:
                while True:
                    frame = await track.recv()
                    if frame.format.name != 's16':
                        raise ValueError('Unexpected decoded audio format')
                    channels = len(frame.layout.channels)
                    pcm = bytes(frame.planes[0])[:frame.samples * channels * 2]
                    values = struct.unpack('<' + 'h' * (len(pcm) // 2), pcm)
                    rms = math.sqrt(sum(v*v for v in values) / len(values))
                    stats['decoded_frames'] += 1
                    stats['frames_rms_over_200'] += rms > 200
                    if output is None:
                        output = wave.open(str(args.audio), 'wb')
                        output.setnchannels(channels)
                        output.setsampwidth(2)
                        output.setframerate(frame.sample_rate)
                        metadata.update(sample_rate=frame.sample_rate, channels=channels)
                    output.writeframesraw(pcm)
            except asyncio.CancelledError: raise
            except Exception as error:
                stats['consumer_end_type'] = type(error).__name__
        consumers.append(asyncio.create_task(consume()))
    result = {'contract':'EvaSubscriptionVoiceProbe/v1', 'synthetic_only':True,
              'eva_backend_connected':False, 'exact_desktop_model_verified':False}
    try:
        async with asyncio.timeout(55):
            await peer.setLocalDescription(await peer.createOffer())
            async with CodexVoiceSession(args.codex, lambda:(lease['access_token'], lease['account_id'])) as session:
                answer = await session.start(peer.localDescription.sdp,
                    prompt='Synthetic voice transport test. Speak Dutch. Do not delegate work or use tools.')
                await peer.setRemoteDescription(RTCSessionDescription(sdp=answer, type='answer'))
                await asyncio.wait_for(started.wait(), 15)
                await session.say('Hallo, dit is Eva. Dit is een korte test van de stemverbinding.')
                await asyncio.sleep(10)
                result['connected'] = peer.connectionState == 'connected'
    except Exception as error:
        result['error_type'] = type(error).__name__
    finally:
        await peer.close()
        for task in consumers: task.cancel()
        await asyncio.gather(*consumers, return_exceptions=True)
        if output: output.close()
        result.update(audio=stats, metadata=metadata, event_types=sorted(event_types))
        result['audio_verified'] = stats['frames_rms_over_200'] > 0 and result.get('connected',False)
        args.receipt.write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result))
    return result['audio_verified']

if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--codex',required=True)
    p.add_argument('--audio',required=True,type=Path)
    p.add_argument('--receipt',required=True,type=Path)
    args=p.parse_args()
    lease=json.load(sys.stdin)
    if lease.get('contract')!='EvaModelAccessLease/v1':
        raise SystemExit('Expected existing EVA model access lease')
    raise SystemExit(0 if asyncio.run(run(args,lease)) else 1)
