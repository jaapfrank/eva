import {randomUUID} from 'node:crypto';
import {EventEmitter} from 'node:events';

export class ChromeGate extends EventEmitter {
  constructor(factory) {super();this.factory=factory;this.mode='disconnected';this.epoch=randomUUID();this.observation=null;this.worker=null;this.pending=false;this.transition=null;this.tools=[];}
  status() {return {contract:'EvaSharedChrome/v1',state:this.mode,epoch:this.epoch,busy:this.pending,connected:this.mode==='eva',visibleIn:'Google Chrome',freshObservationRequired:!this.observation};}
  invalidate() {this.epoch=randomUUID();this.observation=null;this.emit('change');}
  async stop(mode='owner') {
    if(this.transition)return this.transition;
    this.mode='taking_over';this.invalidate();
    const worker=this.worker;this.worker=null;
    this.transition=(async()=>{
      try {await worker?.stop();this.mode=mode;}catch {this.worker=worker;this.mode='recovery_required';}
      finally {this.pending=false;this.transition=null;this.emit('change');}
      return this.status();
    })();
    return this.transition;
  }
  async connect() {
    if(this.transition || this.worker || !['owner','disconnected','recovery_required'].includes(this.mode))throw Error('Connection already active or previous stop unconfirmed');
    this.mode='connecting';this.invalidate();const epoch=this.epoch;
    const worker=this.factory();this.worker=worker;
    worker.on('disconnected',()=>{if(this.worker===worker)void this.stop('disconnected');});
    try {
      this.tools=await worker.ready;
      await worker.request({method:'connect'});
      if(this.worker!==worker || epoch!==this.epoch)throw Error('Connection cancelled');
      this.mode='eva';this.emit('change');
      return this.status();
    } catch(error) {if(this.worker===worker)await this.stop('disconnected');throw error;}
  }
  catalog() {return this.tools.map(tool=>({...tool,inputSchema:{...tool.inputSchema,properties:{...tool.inputSchema.properties,...(tool.name==='chrome_snapshot'?{}:{observation:{type:'string',description:'Single-use observation from the latest chrome_snapshot; refresh after every action or handover.'}})},required:[...(tool.inputSchema.required || []),...(tool.name==='chrome_snapshot'?[]:['observation'])],additionalProperties:false},description:tool.description+' Only the owner-shared existing Chrome tab. If disconnected or owner control: stop, never reconnect or retry an action automatically.'}));}
  async call(name,args={}) {
    if(this.mode!=='eva' || !this.worker)throw Error('Eva has no browser control. Owner must explicitly give it back.');
    if(this.pending)throw Error('Another browser action is running; no queued actions allowed.');
    if(!this.tools.some(tool=>tool.name===name))throw Error('Tool unavailable');
    const {observation,...input}=args;
    if(name!=='chrome_snapshot' && (!this.observation || observation!==this.observation))throw Error('Fresh chrome_snapshot required; old page references are invalid.');
    const worker=this.worker,epoch=this.epoch;
    this.observation=null;this.pending=true;
    try {
      const result=await worker.request({method:'call',name,arguments:input});
      if(this.mode!=='eva' || this.worker!==worker || this.epoch!==epoch)throw Error('Action interrupted by owner or disconnect. Outcome may be partial; do not replay.');
      if(name==='chrome_snapshot' && !result.isError) {
        this.observation=randomUUID();
        return {...result,content:[{type:'text',text:`Fresh Chrome observation: ${this.observation}`},...(result.content || [])]};
      }
      return result;
    } finally {if(this.worker===worker)this.pending=false;}
  }
}
