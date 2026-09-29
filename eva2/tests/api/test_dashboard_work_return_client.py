"""Execute the packaged browser transport without runtime data or model calls."""
import subprocess
from eva.adapters.api.dashboard_work_return_client import DASHBOARD_WORK_RETURN_CLIENT_JS


def test_conversation_return_transport_boundaries():
    script = DASHBOARD_WORK_RETURN_CLIENT_JS + r"""
const assert=require('node:assert/strict');
const {createHash}=require('node:crypto');
const {EvaWorkReturns,workReturnHeading,readableWorkResult}=module.exports;
const digest=async s=>createHash('sha256').update(s).digest('hex');
const output='<img src=x onerror=alert(1)>\nBron: https://example.org';
assert.equal(readableWorkResult(JSON.stringify({summary:'Plan',findings:['Water','Opruimen']})), 'Plan\n\n- Water\n- Opruimen');
assert.equal(readableWorkResult(JSON.stringify({summary:'Plan',findings:[]})), 'Plan');
for(const unknown of ['null','[1,2]', '{"summary":"Plan","findings":[],"extra":"keep"}', '{"summary":"Plan","findings":[7]}',output]) assert.equal(readableWorkResult(unknown),unknown);
assert.equal(readableWorkResult(JSON.stringify({summary:output,findings:[]})),output);

const result={work_id:'work-one',operation_id:'a'.repeat(64),output,
 output_sha256:createHash('sha256').update(output).digest('hex')};
const reply=data=>({ok:true,json:async()=>({contract:'EvaPrivateWorkReturn/v1',...data})});
function setup(fetch){
 const rendered=[],observed=[],failures=[],timers=new Map(),delays=new Map();let id=0,visible=true,clears=0;
 const client=new EvaWorkReturns({fetch,visible:()=>visible,render:r=>{rendered.push(r);observed.push(r)},
 clear:()=>{clears++;rendered.length=0},failed:()=>failures.push(true),digest,
 schedule:(fn,delay)=>{timers.set(++id,fn);delays.set(id,delay);return id},cancel:id=>{timers.delete(id);delays.delete(id)}});
 return {client,rendered,observed,failures,timers,delays,setVisible:v=>visible=v,clears:()=>clears};
}
const historyReply=results=>reply({status:'history',results});
const emptyHistory=fetch=>(url,options)=>url.endsWith('/history')?historyReply([]):fetch(url,options);
(async()=>{
 const calls=[];let acknowledged=false,failPoll=false;
 const server=async(url,options)=>{
  calls.push([url,JSON.parse(options.body)]);
  if(url.endsWith('/history')) return historyReply(acknowledged?[result]:[]);
  if(url.endsWith('/ack')) {acknowledged=true;return reply({status:'acknowledged'});}
  if(failPoll) {failPoll=false;return {ok:false};}
  return reply(acknowledged?{status:'idle',result:null}:{status:'claimed',result});
 };
 const count=operation=>calls.filter(c=>c[0].endsWith('/'+operation)).length;
 const a=setup(server);
 await a.client.poll();
 assert.deepEqual([...a.delays.values()],[250]);
 await a.client.poll();
 assert.deepEqual([...a.delays.values()],[5000]);
 assert.equal(a.rendered.length,1);assert.equal(a.rendered[0].output,output);
 assert.equal(count('ack'),1);assert.equal(count('history'),1);
 assert.ok(calls.filter(c=>!c[0].endsWith('/ack')).every(c=>Object.keys(c[1]).length===0));
 // A cached page and a new page restore only freshly authorized history.
 a.client.close();assert.equal(a.rendered.length,0);await a.client.poll();assert.equal(calls.length,4);
 await a.client.resume();assert.equal(calls.length,6);assert.equal(a.rendered.length,1);
 assert.equal(count('ack'),1);assert.equal(count('history'),2);
 a.setVisible(false);a.client.suspend();await a.client.poll();
 assert.equal(a.rendered.length,0);assert.equal(calls.length,6);
 a.setVisible(true);await a.client.poll();
 assert.equal(a.rendered.length,1);assert.equal(count('history'),3);assert.equal(count('ack'),1);
 const reloaded=setup(server);await reloaded.client.poll();
 assert.equal(reloaded.rendered[0].output,output);assert.equal(count('ack'),1);reloaded.client.close();
 // A later transport failure clears private output; recovery reloads history once.
 failPoll=true;await a.client.poll();assert.equal(a.rendered.length,0);assert.equal(a.failures.length,1);
 const historiesBeforeRecovery=count('history');
 await a.client.poll();await a.client.poll();
 assert.equal(a.rendered.length,1);assert.equal(count('history'),historiesBeforeRecovery+1);
 assert.equal(count('ack'),1);a.client.close();

 const secondOutput='Independent second result';
 const titled=[
  {...result,title:'Home Assistant spraak'},
  {work_id:'work-two',operation_id:'b'.repeat(64),title:'<img src=x onerror=alert(1)>',
   output:secondOutput,output_sha256:await digest(secondOutput)}
 ];
 let nextResult=0;const acknowledgements=[];
 const two=setup(async(url,options)=>{
  if(url.endsWith('/history')) return historyReply([titled[0],titled[0]]);
  if(url.endsWith('/ack')) {acknowledgements.push(JSON.parse(options.body));return reply({status:'acknowledged'});}
  return reply({status:'claimed',result:titled[nextResult++]});
 });
 await two.client.poll();await two.client.poll();
 assert.deepEqual(two.rendered.map(r=>[r.work_id,workReturnHeading(r),r.output]),[
  ['work-one','Afgerond: Home Assistant spraak',output],
  ['work-two','Afgerond: <img src=x onerror=alert(1)>',secondOutput]
 ]);
 assert.deepEqual(acknowledgements,titled.map(r=>({work_id:r.work_id,operation_id:r.operation_id})));
 assert.equal(workReturnHeading(result),'Je achtergrondopdracht is afgerond');
 assert.equal(workReturnHeading({...result,title:{unsafe:true}}),'Je achtergrondopdracht is afgerond');
 assert.equal(workReturnHeading({...result,title:'  '}),'Je achtergrondopdracht is afgerond');
 assert.equal(workReturnHeading({...result,title:'x'.repeat(500)}).length,'Afgerond: '.length+240);
 two.client.close();

 let resolve;
 const b=setup(()=>new Promise(r=>resolve=r));
 const pending=b.client.poll();await b.client.poll(); // no overlapping poll
 b.setVisible(false);b.client.suspend();resolve(historyReply([result]));await pending;
 assert.equal(b.rendered.length,0);assert.equal(b.timers.size,0);

 const c=setup(emptyHistory(async()=>reply({status:'claimed',result:{...result,output_sha256:'0'.repeat(64)}})));
 await c.client.poll();assert.equal(c.rendered.length,0);assert.equal(c.failures.length,1);

 let n=0;
 const d=setup(emptyHistory(async()=>{n++;return n===1?reply({status:'claimed',result}):{ok:false};}));
 await d.client.poll();assert.equal(d.rendered.length,0);assert.equal(d.failures.length,1);
 assert.deepEqual([...d.delays.values()],[5000], 'failed acknowledgement must not accelerate polling');

 const e=setup(emptyHistory(async()=>reply({status:'idle',result:null})));
 await e.client.poll();assert.equal(e.rendered.length,0);assert.equal(e.failures.length,0);
 assert.deepEqual([...e.delays.values()],[5000]);
 // Reject the entire history before rendering any part, with bounded result sizes.
 for(const data of [
  {status:'claimed',results:[result]},
  {status:'history',results:null},
  {status:'history',results:Array(21).fill(result)},
  {status:'history',results:[result,{...result,output_sha256:'0'.repeat(64)}]},
  {status:'history',results:[null]},
  {status:'history',results:[{...result,work_id:'x'.repeat(241)}]},
  {status:'history',results:[{...result,output:'x'.repeat(65537)}]},
  {status:'history',results:[{...result,operation_id:'invalid'}]},
  {contract:'Other/v1',status:'history',results:[result]}
 ]) {
  let requests=0;
  const invalid=setup(async()=>{requests++;return reply(data)});
  await invalid.client.poll();
  assert.equal(requests,1);assert.equal(invalid.observed.length,0);
  assert.equal(invalid.rendered.length,0);assert.equal(invalid.failures.length,1);invalid.client.close();
 }
 // Browser Window timers reject an EvaWorkReturns receiver. Exercise defaults,
 // not just the injected test scheduler used by the other lifecycle cases.
 const previousSet=globalThis.setTimeout, previousClear=globalThis.clearTimeout;
 let timerCalls=0;
 try {
  globalThis.setTimeout=function(){assert.equal(this,globalThis);timerCalls++;return 99};
  globalThis.clearTimeout=function(){assert.equal(this,globalThis);timerCalls++};
  const browserDefaults=new EvaWorkReturns({fetch:emptyHistory(async()=>reply({status:'idle',result:null})),
   visible:()=>true,render:()=>{},clear:()=>{},failed:()=>assert.fail('timer binding'),digest});
  await browserDefaults.poll();browserDefaults.close();assert.ok(timerCalls>=4);
 } finally {globalThis.setTimeout=previousSet;globalThis.clearTimeout=previousClear;}
 console.log('return transport boundaries passed');
})().catch(error=>{console.error(error);process.exitCode=1});
"""
    result = subprocess.run(['node', '-e', script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert 'return transport boundaries passed' in result.stdout
