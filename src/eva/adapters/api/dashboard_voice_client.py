"""Browser voice client, packaged with the existing owner conversation page."""

DASHBOARD_VOICE_CLIENT_JS = r"""
(() => {
  'use strict';
  const WORKLET = `class EvaMic extends AudioWorkletProcessor {
    constructor() { super(); this.phase=0; this.sum=0; this.count=0; this.values=[]; }
    process(inputs) {
      const samples=inputs[0]?.[0];
      if (!samples) return true;
      for (const sample of samples) {
        this.sum+=sample; this.count++; this.phase+=16000;
        if (this.phase>=sampleRate) {
          this.phase-=sampleRate; this.values.push(this.sum/this.count);
          this.sum=0; this.count=0;
          if (this.values.length===320) {
            const bytes=new ArrayBuffer(640), view=new DataView(bytes);
            let power=0;
            this.values.forEach((x,i)=>{ power+=x*x; view.setInt16(i*2, Math.round(Math.max(-1,Math.min(1,x))*32767),true); });
            this.port.postMessage({bytes,rms:Math.sqrt(power/320)},[bytes]); this.values=[];
          }
        }
      }
      return true;
    }
  } registerProcessor('eva-mic',EvaMic);`;

  class EvaVoiceClient {
    constructor({onState=()=>{},onReply=()=>{},env=globalThis}={}) {
      this.env=env; this.onState=onState; this.onReply=onReply;
      this.epoch=0; this.running=false; this.generation=0; this.discardThrough=0;
      this.playEpoch=0; this.parts=[]; this.audioBytes=0; this.audioMime=null;
      this.capture=null; this.sendingCapture=null; this.preRoll=[]; this.loud=0; this.quiet=0; this.serverBusy=false;
    }
    state(text) { this.onState(text); }
    control(type) {
      if (this.socket?.readyState===1) this.socket.send(JSON.stringify({type}));
    }
    async start() {
      if (this.running) return;
      const epoch=++this.epoch; this.running=true;
      try {
        if (!this.env.isSecureContext || !this.env.navigator.mediaDevices?.getUserMedia) throw new Error('media');
        this.context=new this.env.AudioContext();
        await this.context.resume();
        if (epoch!==this.epoch) return;
        this.state('Verbinden met Eva…');
        const url=new URL('/internal/v1/dashboard/conversation/voice',this.env.location.href);
        url.protocol=url.protocol==='https:'?'wss:':'ws:';
        const socket=new this.env.WebSocket(url); this.socket=socket;
        await new Promise((resolve,reject)=>{
          const timer=this.env.setTimeout(()=>reject(new Error('timeout')),10000);
          const finish=(fn)=>{this.env.clearTimeout(timer);fn();};
          socket.onmessage=(event)=>{
            if (epoch!==this.epoch) return;
            try {
              const data=JSON.parse(event.data);
              if (data.type==='ready') {
                if (data.sample_rate!==16000 || data.channels!==1 || data.encoding!=='pcm_s16le') throw new Error('format');
                finish(resolve); return;
              }
              this.event(data);
            } catch (_) { finish(()=>reject(new Error('protocol'))); this.fail(); }
          };
          socket.onerror=()=>finish(()=>reject(new Error('connection')));
          socket.onclose=()=>{finish(()=>reject(new Error('closed')));if(epoch===this.epoch)this.fail();};
        });
        if (epoch!==this.epoch) return;
        const stream=await this.env.navigator.mediaDevices.getUserMedia({audio:{
          channelCount:1,echoCancellation:true,noiseSuppression:true,autoGainControl:true},video:false});
        if (epoch!==this.epoch) { stream.getTracks().forEach(track=>track.stop()); return; }
        this.stream=stream;
        const blob=new this.env.Blob([WORKLET],{type:'text/javascript'});
        const moduleUrl=this.env.URL.createObjectURL(blob);
        try { await this.context.audioWorklet.addModule(moduleUrl); }
        finally { this.env.URL.revokeObjectURL(moduleUrl); }
        if (epoch!==this.epoch) return;
        this.mic=this.context.createMediaStreamSource(stream);
        this.processor=new this.env.AudioWorkletNode(this.context,'eva-mic');
        this.processor.port.onmessage=({data})=>{if(epoch===this.epoch)this.frame(data);};
        this.processor.onprocessorerror=()=>this.fail();
        this.mute=this.context.createGain(); this.mute.gain.value=0;
        this.mic.connect(this.processor); this.processor.connect(this.mute); this.mute.connect(this.context.destination);
        this.state('Ik luister. Je kunt gewoon praten.');
      } catch (_) { if(epoch===this.epoch)this.fail(); }
    }
    frame({bytes,rms}) {
      if(!this.running) return;
      this.preRoll.push(bytes); if(this.preRoll.length>10)this.preRoll.shift();
      this.loud=rms>0.018?this.loud+1:0;
      if((!this.capture || this.capture.ended) && this.loud>=6) {
        this.clearAudio(); this.discardThrough=this.generation;
        if(this.serverBusy)this.control('interrupt');
        if(this.capture?.ended && !this.capture.sent) {
          // Resume queued speech while the preceding model turn is finishing.
          this.capture.frames.push(...this.preRoll);
          this.capture.total+=this.preRoll.length*640;
          if(this.capture.total>1920000){this.fail();return;}
          this.capture.ended=false;
        } else {
          // ACKs still belong to the previous capture; recording must not wait.
          this.capture={frames:[...this.preRoll],total:this.preRoll.length*640,ended:false,sent:false,requested:false,inFlight:0,ack:0};
        }
        this.quiet=0; this.requestCapture();
        return;
      }
      const capture=this.capture;
      if(!capture || capture.ended) return;
      capture.total+=bytes.byteLength;
      if(capture.total>1920000) { this.finishCapture(); return; }
      capture.frames.push(bytes); this.pumpCapture();
      this.quiet=rms<0.012?this.quiet+1:0;
      if(this.quiet>=30)this.finishCapture();
    }
    requestCapture() {
      if(this.capture && !this.capture.requested && !this.serverBusy) {
        this.capture.requested=true; this.control('start');
      }
    }
    sendFrame(bytes) {
      if(this.socket?.readyState!==1 || this.socket.bufferedAmount>262144) {this.fail();return false;}
      this.socket.send(bytes); return true;
    }
    pumpCapture() {
      const capture=this.sendingCapture;
      if(!capture?.sent)return;
      // Acknowledgements follow server consumption, not merely network receipt.
      while(capture.frames.length && capture.inFlight<4) {
        if(!this.sendFrame(capture.frames.shift()))return;
        capture.inFlight++;
      }
      if(capture.ended && !capture.frames.length && !capture.inFlight) {
        this.control('end');this.sendingCapture=null;
        if(this.capture===capture)this.capture=null;
      }
    }
    finishCapture() {
      const capture=this.capture;
      if(!capture)return;
      capture.ended=true; this.loud=0; this.preRoll=[];
      this.pumpCapture();
      this.state('Eva denkt na…');
    }
    event(data) {
      if(!Number.isInteger(data.generation)||data.generation<0)throw new Error('generation');
      if(data.type==='listening') {
        const capture=this.capture;
        if(!capture?.requested||data.generation<=this.generation)throw new Error('unexpected capture');
        this.generation=data.generation;this.serverBusy=true;capture.sent=true;this.sendingCapture=capture;
        this.pumpCapture();
        if(!capture.ended)this.state('Ik luister…');
      } else if(data.type==='audio_ack') {
        const capture=this.sendingCapture;
        if(data.generation!==this.generation || !capture?.sent)return;
        if(data.sequence!==capture.ack+1 || capture.inFlight<1)throw new Error('audio acknowledgement');
        capture.ack=data.sequence;capture.inFlight--;this.pumpCapture();
      } else if(data.type==='busy') {
        this.serverBusy=true;if(this.capture)this.capture.requested=false;
      } else if(data.type==='clear_audio') {
        this.discardThrough=Math.max(this.discardThrough,data.generation);this.clearAudio();
      } else if(data.type==='turn_finished') {
        if(data.generation!==this.generation)return;
        if(this.capture===this.sendingCapture){this.capture=null;this.preRoll=[];this.loud=0;this.quiet=0;}
        this.sendingCapture=null;
        this.serverBusy=false;this.requestCapture();
      } else if(data.type==='error') {
        this.clearAudio();this.state('Deze gesproken beurt is niet gelukt. Probeer opnieuw.');
      } else if(data.type==='stop_requested') {
        this.state('Stopverzoek opgeslagen.');
      } else if(data.generation===this.generation && data.generation>this.discardThrough) {
        if(data.type==='reply') {
          if(typeof data.text!=='string'||typeof data.message_id!=='string')throw new Error('reply');
          if(data.response_mode!=='silent')this.onReply(data.text,data.message_id);
        } else if(data.type==='audio') {
          if(!['audio/mpeg','audio/wav','audio/ogg','audio/flac'].includes(data.mime_type)
             ||typeof data.data!=='string'||data.data.length>90000)throw new Error('audio');
          if(this.audioMime && this.audioMime!==data.mime_type)throw new Error('mime');
          this.audioMime=data.mime_type;
          const binary=this.env.atob(data.data), bytes=Uint8Array.from(binary,x=>x.charCodeAt(0));
          this.audioBytes+=bytes.length;if(this.audioBytes>20000000)throw new Error('audio bound');
          this.parts.push(bytes);
        } else if(data.type==='audio_end') {
          const epoch=this.epoch, playback=this.playEpoch;
          void this.play(data.generation).catch(()=>{if(epoch===this.epoch && playback===this.playEpoch)this.fail();});
        }
      }
    }
    async play(generation) {
      // decodeAudioData needs a complete audio file, not arbitrary HTTP chunks.
      const epoch=this.playEpoch, context=this.context;
      const all=new Uint8Array(this.audioBytes);let offset=0;
      for(const part of this.parts){all.set(part,offset);offset+=part.length;}
      this.parts=[];this.audioBytes=0;this.audioMime=null;
      if(!all.length)throw new Error('empty audio');
      const buffer=await context.decodeAudioData(all.buffer);
      if(!this.running||epoch!==this.playEpoch||generation<=this.discardThrough)return;
      const source=context.createBufferSource();source.buffer=buffer;source.connect(context.destination);
      this.speaker=source;source.onended=()=>{source.disconnect();if(this.speaker===source){this.speaker=null;this.state('Ik luister.');}};
      source.start();this.state('Eva spreekt. Je kunt haar onderbreken.');
    }
    clearAudio() {
      ++this.playEpoch;this.parts=[];this.audioBytes=0;this.audioMime=null;
      if(this.speaker){this.speaker.onended=null;this.speaker.stop();this.speaker.disconnect();this.speaker=null;}
    }
    interrupt() {
      this.discardThrough=this.generation;this.clearAudio();this.control('interrupt');
      this.state('Onderbroken. Ik luister.');
    }
    stopTask() {
      this.discardThrough=this.generation;this.clearAudio();this.control('stop');
      this.state('Stopverzoek verstuurd…');
    }
    close() {
      this.running=false;++this.epoch;this.clearAudio();
      if(this.socket){this.socket.onclose=null;this.socket.onerror=null;this.socket.onmessage=null;this.socket.close();this.socket=null;}
      this.stream?.getTracks().forEach(track=>track.stop());this.stream=null;
      if(this.processor){this.processor.port.onmessage=null;this.processor.disconnect();this.processor=null;}
      this.mic?.disconnect();this.mic=null;this.mute?.disconnect();this.mute=null;
      if(this.context){void this.context.close().catch(()=>{});this.context=null;}
      this.capture=null;this.sendingCapture=null;this.preRoll=[];this.serverBusy=false;this.loud=0;this.quiet=0;
      this.generation=0;this.discardThrough=0;this.state('Microfoon uit.');
    }
    fail() {this.close();this.state('Stem is niet beschikbaar of de verbinding is verbroken. Je kunt blijven typen.');}
  }
  globalThis.EvaVoiceClient=EvaVoiceClient;
  const button=globalThis.document?.getElementById('voice-toggle');
  if(button) {
    const status=document.getElementById('voice-state');
    const client=new EvaVoiceClient({onState:text=>{status.textContent=text;button.textContent=client.running?'Microfoon uit':'Praten met Eva';},
      onReply:(text,id)=>message('eva',text,id)});
    button.addEventListener('click',()=>{if(client.running)client.close();else void client.start();});
    document.getElementById('voice-interrupt').addEventListener('click',()=>client.interrupt());
    // Existing HTTP stop remains authoritative; silence local output immediately.
    document.getElementById('stop').addEventListener('click',()=>{client.discardThrough=client.generation;client.clearAudio();});
    globalThis.addEventListener('pagehide',()=>client.close());
    document.addEventListener('visibilitychange',()=>{if(document.hidden)client.close();});
  }
})();
"""
