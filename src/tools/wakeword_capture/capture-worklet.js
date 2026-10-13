class WakeCapture extends AudioWorkletProcessor {
 constructor(){super();this.frames=[];this.count=0;this.finished=false;}
 process(inputs){
  if(this.finished)return false;
  const samples=inputs[0]?.[0];if(!samples)return true;
  for(const x of samples){this.frames.push(x);this.count++;if(this.frames.length===320){const values=Float32Array.from(this.frames);this.port.postMessage({values},[values.buffer]);this.frames=[];}if(this.count>=sampleRate*6){this.finished=true;this.port.postMessage({done:true});return false;}}
  return true;
 }
}
registerProcessor('wake-capture',WakeCapture);
