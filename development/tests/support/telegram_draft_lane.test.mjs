import assert from 'node:assert/strict';
import test from 'node:test';
import {askEva2Canary, createEva2DraftSink, labelEva2TelegramReply} from '../../../deploy/telegram-bridge/src/integrations/eva2canary.js';
const encoder=new TextEncoder();
const finalReply={contract:'EvaInternalGroundedReply/v1',conversation_id:'telegram:7',message_id:'telegram:7:9401',text:'a'.repeat(384)};
function respond(rows, observed) {
  let index=0;
  return new Response(new ReadableStream({pull(controller) {
    observed.pulls++;
    if(index===rows.length) {controller.close();return;}
    controller.enqueue(encoder.encode(JSON.stringify(rows[index++])+'\n'));
  }}),{headers:{'content-type':'application/x-ndjson'}});
}
function ask(sink, response) {
  return askEva2Canary({baseUrl:'http://fixture',chatId:7,fromId:7,messageId:9401,
    text:'/eva2 Hello',telegramDate:1787486400,onDraft:sink.map(labelEva2TelegramReply),fetchImpl:async()=>response});
}
test('slow Telegram drafts do not hold the model reader and obsolete previews are discarded',async()=>{
  let release;let entered;const started=new Promise(resolve=>entered=resolve);
  const blocked=new Promise(resolve=>release=resolve);const sent=[];
  const sink=createEva2DraftSink({chatId:7,draftId:9401,sendDraft:async payload=>{
    sent.push(payload);entered();await blocked;return true;
  }});
  const rows=Array.from({length:6},()=>({contract:'EvaTextDelta/v1',text:'a'.repeat(64)}));
  rows.push(finalReply);const observed={pulls:0};
  let completed=false;const result=ask(sink,respond(rows,observed)).then(value=>{completed=true;return value;});
  await started;await new Promise(resolve=>setImmediate(resolve));
  try {
    assert.equal(observed.pulls,rows.length+1,'read through terminal record before draft finishes');
    assert.equal(completed,false,'finish waits only for the already in-flight draft');
    assert.equal(sent.length,1,'one in-flight request, no unbounded draft fanout');
    assert.equal(sent[0].text,'a'.repeat(64),'presentation retains the draft lane without a product label');
  } finally {release();}
  assert.deepEqual((await result).message_id,finalReply.message_id);
  assert.equal(sent.length,1,'saved result supersedes stale queued previews');
  assert.equal(sink.enqueue('a'.repeat(400)),false,'closed lane cannot send after final reply');
});
test('a live stream coalesces pending previews to the latest text',async()=>{
  const sent=[];let release;
  const sink=createEva2DraftSink({chatId:7,draftId:9402,sendDraft:async payload=>{
    sent.push(payload.text);if(sent.length===1)await new Promise(resolve=>release=resolve);return true;
  }});
  sink.enqueue('a'.repeat(64));sink.enqueue('a'.repeat(128));sink.enqueue('a'.repeat(192));
  assert.equal(sent.length,1);release();await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(sent,['a'.repeat(64),'a'.repeat(192)]);
  await sink.finish();assert.equal(sink.enqueue('a'.repeat(256)),false);
});
test('stream failure cancels the transient lane without replacing the original failure',async()=>{
  let signalSeen;let entered;const started=new Promise(resolve=>entered=resolve);
  const sink=createEva2DraftSink({chatId:7,draftId:9401,sendDraft:async (_payload,signal)=>{
    signalSeen=signal;entered();await new Promise(resolve=>signal.addEventListener('abort',resolve,{once:true}));return false;
  }});
  const observed={pulls:0};const result=ask(sink,respond([
    {contract:'EvaTextDelta/v1',text:'Pending text'},
    {contract:'EvaTextStreamError/v1',code:'provider_rate_limited'},
  ],observed));
  const rejected=assert.rejects(result,/modelprovider/u);await started;await rejected;
  assert.equal(signalSeen.aborted,true);assert.equal(sink.enqueue('Pending text again'),false);
});
test('unavailable optional drafts preserve saved answer and exact original question binding',async()=>{
  const sink=createEva2DraftSink({chatId:7,draftId:9401,sendDraft:async()=>{throw new Error('draft unavailable');}});
  const result=await ask(sink,respond([{contract:'EvaTextDelta/v1',text:'Hi'},finalReply],{pulls:0}));
  assert.equal(result.message_id,'telegram:7:9401');assert.equal(result.text,finalReply.text);
});

test('incomplete EOF aborts a blocked preview and preserves the stream error',async()=>{
  let signalSeen;
  const sink=createEva2DraftSink({chatId:7,draftId:9401,sendDraft:async (_payload,signal)=>{
    signalSeen=signal;
    await new Promise(resolve=>signal.addEventListener('abort',resolve,{once:true}));return false;
  }});
  const response=new Response(new ReadableStream({start(controller){
    controller.enqueue(encoder.encode(JSON.stringify({contract:'EvaTextDelta/v1',text:'Pending'})+'\n'+
      '{"contract":"EvaInternalGroundedReply/v1"'));
    controller.close();
  }}),{headers:{'content-type':'application/x-ndjson'}});
  await assert.rejects(ask(sink,response),/tekststream eindigde onvolledig/u);
  assert.equal(signalSeen.aborted,true);
});

test('unexpected core stream failure remains failed without an internal project label', async()=>{
  const sink=createEva2DraftSink({chatId:7,draftId:9401,sendDraft:async()=>true});
  const response=respond([{contract:'EvaTextStreamError/v1',code:'unexpected'}],{pulls:0});
  await assert.rejects(ask(sink,response),error=>{
    assert.equal(error.message,'Ik kon mijn antwoord niet afronden');
    assert.doesNotMatch(error.message,/Eva2|oorzaak.*onbekend/u);
    return true;
  });
  assert.equal(sink.enqueue('Een antwoord na de fout'),false);
});
