import test from 'node:test';
import assert from 'node:assert/strict';
import {brokerResult,requestFrame,runBrokerResult} from '../eva2/deploy/living-core/workspace-create-only/broker-client.mjs';
const frame=requestFrame({operation_id:'a'.repeat(64),request_binding_sha256:'b'.repeat(64),expected_generation:0,path:'x',content:'x'});
test('absent, uncertain and conflicting observation remains blocked, never initialNone',()=>{
 for(const code of ['operation_not_found','pending_effect_uncertain','operation_conflict','journal_open_failed']) {
  assert.deepEqual(brokerResult({status:65,stderr:'workspace_writer_broker:'+code+'\n'},frame),{status:'blocked',code});
 }
});
test('process and corrupt receipt failures are bounded without leaking diagnostics',()=>{
 assert.deepEqual(brokerResult({status:null,error:new Error('private path')},frame),{status:'blocked',code:'execution_uncertain'});
 assert.deepEqual(brokerResult({status:65,stderr:'secret details'},frame),{status:'blocked',code:'broker_refused'});
 assert.deepEqual(brokerResult({status:0,stdout:Buffer.from('bad')},frame),{status:'blocked',code:'invalid_receipt'});
});
test('invalid request is blocked before spawning executable',()=>{
 assert.deepEqual(runBrokerResult({executable:'/must-not-run',request:{}}),{status:'blocked',code:'invalid_request'});
});
test('synchronous spawn validation failures return bounded blocked results',()=>{
 const request={operation_id:'a'.repeat(64),request_binding_sha256:'b'.repeat(64),expected_generation:0,path:'x',content:'x'};
 for(const executable of [undefined,'',42]) {
  assert.deepEqual(runBrokerResult({executable,request}),{status:'blocked',code:'execution_uncertain'});
 }
});
