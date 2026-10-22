import assert from 'node:assert/strict';
import test from 'node:test';
import {TransportJournal, createTransportHandler, telegramReplyContext} from '../../../deploy/telegram-bridge/standalone.mjs';
import {askEva2Canary} from '../../../deploy/telegram-bridge/src/integrations/eva2canary.js';

function message() {return {message_id:20,date:1787486400,from:{id:7},chat:{id:7,type:'private'},
  text:'Dit is je laatste bericht!',reply_to_message:{message_id:9,date:1787486300,
    chat:{id:7,type:'private'},from:{id:1,is_bot:true},text:'😀 PHP was reported in August.'}};}

test('actual handler and canary request preserve current text and selected reply without replaying updates',async()=>{
  const journal=new TransportJournal(':memory:');const m=message();const requests=[],sent=[];
  m.quote={text:'PHP',position:3};
  const ctx={message:m,update:{update_id:1,message:m},replyWithChatAction:async()=>{},reply:async(text,options)=>{
    sent.push({text,options});return {message_id:21,chat:m.chat,text,reply_to_message:{message_id:m.message_id}};}};
  const handler=createTransportHandler({journal,bindings:[{telegram_user_id:7,allowed_chat_ids:['7']}],
    baseUrl:'http://fixture',makeDraft:()=>null,coreRequest:args=>askEva2Canary({...args,fetchImpl:async(_url,request)=>{
      requests.push(JSON.parse(request.body));return new Response(JSON.stringify({contract:'EvaInternalGroundedReply/v1',
        conversation_id:'telegram:7',message_id:'telegram:7:20',text:'Je bedoelt het PHP-bericht.'}),
        {headers:{'content-type':'application/json'}});}})});
  try {await handler(ctx);await handler(ctx);
    assert.equal(requests.length,1);assert.equal(sent.length,1);
    assert.equal(requests[0].text,m.text);
    assert.deepEqual(requests[0].reply_context,{source_ref:'telegram:7:9',text:m.reply_to_message.text,
      occurred_at:new Date(m.reply_to_message.date*1000).toISOString().replace('.000Z','Z'),selection:'PHP'});
    assert.equal(sent[0].options.reply_parameters.message_id,20);
  } finally {journal.close();}
});

test('cross-chat, self/future and non-private references are excluded',()=>{
  for(const alter of [m=>m.reply_to_message.chat.id=8,m=>m.reply_to_message.message_id=20,
    m=>m.reply_to_message.message_id=21,m=>m.chat.type='group']) {
    const m=message();alter(m);assert.equal(telegramReplyContext(m),null);
  }
});

test('an inconsistent selection cannot replace the actual quoted text',()=>{
  const m=message();m.quote={text:'Different message',position:3};
  const q=telegramReplyContext(m);assert.equal(q.text,m.reply_to_message.text);assert.equal(q.selection,undefined);
});
