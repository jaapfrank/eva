import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
import { setTimeout as delay } from 'node:timers/promises';
import { TransportJournal, createTransportHandler } from '../../../telegram-bridge/standalone.mjs';
import { pollTelegramConcurrently } from '../../../telegram-bridge/src/integrations/telegrampolling.js';
const { Bot }=createRequire(process.env.EVA_NODE_TELEGRAM_DEPENDENCY_MANIFEST)('grammy');
const info={id:123,is_bot:true,first_name:'Synthetic',username:'SyntheticBot',can_join_groups:true,can_read_all_group_messages:false,supports_inline_queries:false};
function update(id){return {update_id:id,message:{message_id:id,date:1000+id,from:{id:12,is_bot:false,first_name:'Synthetic'},chat:{id:12,type:'private'},text:'synthetic '+id}};}
test('actual grammy and standalone polling drain without acknowledging unfinished work or duplicate sends',{timeout:9000},async()=>{
 const dir=mkdtempSync(path.join(tmpdir(),'eva-host-node-')); const dbfile=path.join(dir,'synthetic.sqlite');
 const journal=new TransportJournal(dbfile), bot=new Bot('123:synthetic-unused',{botInfo:info}), controller=new AbortController();
 const offsets=[],sent=[],coreCalls=[],errors=[]; let release, later=false;
 const gate=new Promise(resolve=>{release=resolve;}); const until=async predicate=>{const deadline=Date.now()+5000;while(!predicate()){assert.ok(Date.now()<deadline,'synthetic polling deadline');await delay(5);}};
 bot.api.config.use(async(_previous,method,payload)=>{
  if(method==='getUpdates'){await delay(5);offsets.push(payload.offset);return {ok:true,result:[update(1),...(later?[update(2)]:[])].filter(x=>x.update_id>=payload.offset)};}
  if(method==='deleteWebhook'){assert.equal(payload.drop_pending_updates,false);return {ok:true,result:true};}
  if(method==='sendChatAction')return {ok:true,result:true};
  if(method==='sendMessage'){sent.push(payload);return {ok:true,result:{message_id:100+sent.length,chat:{id:payload.chat_id,type:'private'},date:2000,text:payload.text,reply_to_message:{message_id:payload.reply_parameters.message_id}}};}
  throw new Error('unexpected synthetic method '+method);
 });
 bot.on('message',createTransportHandler({journal,bindings:[{telegram_user_id:12,allowed_chat_ids:['12']}],baseUrl:'http://unused.invalid',makeDraft:()=>null,coreRequest:async args=>{coreCalls.push(args.messageId);if(args.messageId===1)await gate;return {text:'answer '+args.messageId};}}));
 const polling=pollTelegramConcurrently(bot,{signal:controller.signal,canOverlap:()=>true,onError:error=>errors.push(error.name)});
 try{
  await until(()=>coreCalls.length===1);later=true;
  await until(()=>sent.some(x=>x.reply_parameters.message_id===2));
  assert.deepEqual(coreCalls,[1,2]);assert.equal(sent.some(x=>x.reply_parameters.message_id===1),false);assert.ok(offsets.every(x=>x===0));
  controller.abort();await delay(20);assert.equal(journal.db.prepare('SELECT state FROM telegram_transport WHERE update_id=1').get().state,'processing');
  release();await polling;assert.deepEqual(errors,[]);assert.deepEqual(sent.map(x=>x.reply_parameters.message_id),[2,1]);
  journal.close();const reopened=new TransportJournal(dbfile);
  try{assert.equal(reopened.claim(update(1)),false);assert.equal(reopened.claim(update(2)),false);assert.equal(reopened.db.prepare('PRAGMA integrity_check').get().integrity_check,'ok');assert.equal(reopened.db.prepare("SELECT count(*) n FROM telegram_transport WHERE state='delivered'").get().n,2);}finally{reopened.close();}
 }finally{release();controller.abort();await polling;journal.db.isOpen&&journal.close();rmSync(dir,{recursive:true,force:true});}
});
