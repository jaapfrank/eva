import {createHash} from 'node:crypto';
import {spawnSync} from 'node:child_process';
const sha=b=>createHash('sha256').update(b).digest();
export function requestFrame({operation_id,request_binding_sha256,expected_generation,path,content}) {
 for(const v of [operation_id,request_binding_sha256]) if(!/^[0-9a-f]{64}$/.test(v)||/^0+$/.test(v)) throw Error('invalid identity');
 if(!Number.isSafeInteger(expected_generation)||expected_generation<0) throw Error('invalid generation');
 if(typeof path!=='string'||!/^[A-Za-z0-9_.\/-]+$/.test(path)||path.startsWith('/')||path.split('/').some(x=>!x||x==='.'||x==='..')) throw Error('invalid relative path');
 if(typeof content!=='string') throw Error('invalid content');
 const target=Buffer.from(path,'ascii'),body=Buffer.from(content,'utf8');
 if(target.length>1024||body.length>1048576) throw Error('request too large');
 const h=Buffer.alloc(122);h.write('EVWWREQ2');Buffer.from(operation_id,'hex').copy(h,8);h.writeBigUInt64BE(BigInt(expected_generation),40);h.writeUInt16BE(target.length,48);h.writeBigUInt64BE(BigInt(body.length),50);sha(body).copy(h,58);Buffer.from(request_binding_sha256,'hex').copy(h,90);
 return Buffer.concat([h,target,body]);
}
export function parseReceipt(raw,frame) {
 if(raw.length<163||raw.subarray(0,8).toString()!=='EVWWRCP1') throw Error('invalid receipt');
 const length=raw.readUInt16BE(121),end=131+length;
 if(raw.length!==end+32||!sha(raw.subarray(0,end)).equals(raw.subarray(end))||![1,3].includes(raw[8])) throw Error('invalid receipt');
 if(!raw.subarray(25,57).equals(frame.subarray(8,40))||!raw.subarray(57,89).equals(sha(frame))||!raw.subarray(89,121).equals(frame.subarray(58,90))||!raw.subarray(131,end).equals(frame.subarray(122,122+frame.readUInt16BE(48)))||raw.readBigUInt64BE(123)!==frame.readBigUInt64BE(50)||raw.readBigUInt64BE(9)!==frame.readBigUInt64BE(40)||raw.readBigUInt64BE(17)!==frame.readBigUInt64BE(40)+1n) throw Error('receipt binding differs');
 return {request_binding_sha256:frame.subarray(90,122).toString('hex'),operation_id:raw.subarray(25,57).toString('hex'),request_sha256:raw.subarray(57,89).toString('hex'),content_sha256:raw.subarray(89,121).toString('hex'),path:raw.subarray(131,end).toString('ascii'),effect:raw[8]===1?'created':'noop',receipt_sha256:sha(raw).toString('hex')};
}
// FDs, executable and authority remain host-owned; never accept them from tool input.
// No failure (including absence) authorizes another dispatch.
export function brokerResult(result, frame) {
 if (result.error || result.signal) return {status:'blocked',code:'execution_uncertain'};
 if (result.status !== 0) {
  const match=/^workspace_writer_broker:([a-z_]+)\n?$/.exec(String(result.stderr||''));
  const known=new Set(['operation_not_found','operation_conflict','pending_effect_uncertain','journal_open_failed','generation_conflict']);
  return {status:'blocked',code:match&&known.has(match[1])?match[1]:'broker_refused'};
 }
 try { return {status:'receipt',receipt:parseReceipt(result.stdout,frame)}; }
 catch { return {status:'blocked',code:'invalid_receipt'}; }
}
export function runBrokerResult({executable,rootFd,journalFd,request,observe=false,relocationFd=null}) {
 let frame;
 try { frame=requestFrame(request); }
 catch { return {status:'blocked',code:'invalid_request'}; }
 try {
  const args=relocationFd===null?(observe?['--observe']:[]):[observe?'--observe-relocated':'--relocated'];
  const descriptors=['pipe','pipe','pipe',rootFd,journalFd];
  if(relocationFd!==null) descriptors.push(relocationFd);
  const result=spawnSync(executable,args,{input:frame,stdio:descriptors,env:{PATH:'/usr/bin:/bin',LANG:'C'},timeout:10000,maxBuffer:2097152});
  return brokerResult(result,frame);
 } catch {
  return {status:'blocked',code:'execution_uncertain'};
 }
}
export function runBroker(options) {
 const result=runBrokerResult(options);
 if(result.status==='blocked') {
  const error=new Error('broker refused: '+result.code);error.code=result.code;throw error;
 }
 return result.receipt;
}
