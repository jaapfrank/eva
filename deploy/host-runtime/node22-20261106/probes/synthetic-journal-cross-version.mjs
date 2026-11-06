import assert from 'node:assert/strict';
import {TransportJournal} from '../../../telegram-bridge/standalone.mjs';
const [file,phase]=process.argv.slice(2),journal=new TransportJournal(file);
const make=id=>({update_id:id,message:{message_id:id,chat:{id:12},from:{id:12},text:'synthetic'}});
try{
 if(phase==='old-write'){assert.equal(journal.claim(make(1)),true);journal.record(1,'delivery_uncertain');}
 if(phase==='new-read-write'){assert.equal(journal.claim(make(1)),false);assert.equal(journal.claim(make(2)),true);journal.record(2,'delivered',{response:'synthetic',receipt:'telegram:12:102'});}
 if(phase==='old-read-after-new'){assert.equal(journal.claim(make(1)),false);assert.equal(journal.claim(make(2)),false);assert.deepEqual(journal.db.prepare('SELECT update_id,state FROM telegram_transport ORDER BY update_id').all().map(x=>[x.update_id,x.state]),[[1,'delivery_uncertain'],[2,'delivered']]);}
 assert.equal(journal.db.prepare('PRAGMA integrity_check').get().integrity_check,'ok');
 console.log(JSON.stringify({phase,ok:true}));
}finally{journal.close();}
