import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {EventEmitter} from 'node:events';

export class ChromeWorker extends EventEmitter {
  constructor(command,args,env=process.env) {
    super();this.serial=0;this.pending=new Map();this.closed=false;
    this.child=spawn(command,args,{stdio:['pipe','pipe','pipe'],windowsHide:true,env});
    this.child.stderr.on('data',()=>{}); // Upstream diagnostics may contain page data/relay credentials.
    this.exit=new Promise(resolve=>this.child.once('close',()=>{this.closed=true;resolve();this.fail();this.emit('disconnected');}));
    this.ready=new Promise((resolve,reject)=>{this.resolveReady=resolve;this.rejectReady=reject;});
    this.child.on('error',()=>this.fail());
    createInterface({input:this.child.stdout}).on('line',line=>{
      let msg;try {msg=JSON.parse(line);}catch{return;}
      if(msg.event==='catalog')this.resolveReady(msg.tools);
      else if(msg.event==='disconnected')this.emit('disconnected');
      else if(this.pending.has(msg.id)) {const p=this.pending.get(msg.id);this.pending.delete(msg.id);clearTimeout(p.timer);msg.error?p.reject(Error(msg.error)):p.resolve(msg.result);}
    });
  }
  fail() {this.rejectReady(Error('Chrome worker stopped'));for(const p of this.pending.values()){clearTimeout(p.timer);p.reject(Error('Chrome worker stopped; do not replay an interrupted action.'));}this.pending.clear();}
  request(payload) {if(this.closed)return Promise.reject(Error('Chrome worker stopped'));return new Promise((resolve,reject)=>{const id=++this.serial;const timer=setTimeout(()=>{this.pending.delete(id);reject(Error('Chrome action timed out'));void this.stop();},120000);this.pending.set(id,{resolve,reject,timer});this.child.stdin.write(JSON.stringify({id,...payload})+'\n');});}
  async stop() {
    if(this.unconfirmed)throw Error('Windows termination unconfirmed');
    if(this.closed)return;
    this.fail();
    // On WSL the child is Windows node.exe. Closing stdin asks it to exit;
    // SIGKILL is a fallback. Do not announce owner control until close arrives.
    this.child.stdin.end();
    const kill=setTimeout(()=>{this.unconfirmed=true;this.child.kill('SIGKILL');},3000);
    let timeout;
    try {await Promise.race([this.exit,new Promise((_,reject)=>{timeout=setTimeout(()=>reject(Error('Worker exit unconfirmed')),5000);})]);if(this.unconfirmed)throw Error('Windows termination unconfirmed');}
    finally {clearTimeout(kill);clearTimeout(timeout);}
  }
}
