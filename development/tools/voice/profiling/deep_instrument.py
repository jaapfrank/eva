"""Development-only hooks; no production changes or payload logging."""
import asyncio,json,time
import instrument
from safe_codex_trace import Projection
from eva.voice_service.codex_media import CodexMediaSession,MicrophoneTrack
projection=Projection()
spawn=asyncio.create_subprocess_exec
async def traced_spawn(*args,**kwargs):
 process=await spawn(*args,**kwargs)
 if 'app-server' in args and process.stdout is not None:
  readline=process.stdout.readline
  async def read():
   raw=await readline()
   try:
    event=json.loads(raw)
    safe=projection.project(event) if isinstance(event,dict) else None
    if safe:instrument.mark('codex_notification',**safe)
   except (ValueError,TypeError):pass
   return raw
  process.stdout.readline=read
 return process
asyncio.create_subprocess_exec=traced_spawn

base_start=CodexMediaSession.start
async def start(self,*args,**kwargs):
 result=await base_start(self,*args,**kwargs)
 async def stats():
  while not self.closed:
   report=await self.peer.getStats()
   for value in report.values():
    fields={}
    for key in ('packetsSent','packetsReceived','packetsLost','bytesSent','bytesReceived','jitter','roundTripTime','fractionLost'):
     number=getattr(value,key,None)
     if type(number) in (int,float):fields[key]=number
    if fields and value.type in {'inbound-rtp','outbound-rtp','remote-inbound-rtp','remote-outbound-rtp','transport'}:
     instrument.mark('rtc_stats',stats_type=value.type,**fields)
   instrument.mark('microphone_queue',frames=self.microphone.frames.qsize())
   await asyncio.sleep(1)
 self.task(stats());return result
CodexMediaSession.start=start
base_recv=MicrophoneTrack.recv
async def recv(self):
 frame=await base_recv(self)
 import struct
 values=struct.unpack('<960h',bytes(frame.planes[0])[:1920])
 if max(map(abs,values))>80:
  self.last_signal_ms=round((time.monotonic()-instrument.active.opened_at)*1000)
 return frame
MicrophoneTrack.recv=recv
base_close=CodexMediaSession.aclose
async def close(self):
 if not getattr(self,'deep_reported',False):
  self.deep_reported=True
  instrument.mark('last_microphone_signal',signal_elapsed_ms=getattr(self.microphone,'last_signal_ms',None))
 return await base_close(self)
CodexMediaSession.aclose=close
