import test from 'node:test';
import assert from 'node:assert/strict';
import { TransportJournal, createTransportHandler } from './standalone.mjs';
const bindings = [{ telegram_user_id: 12, allowed_chat_ids: ['12'] }];
function ctx(id, text = 'hallo', sender = 12, sent = []) {
  const message = { message_id: id, text, from: { id: sender }, chat: { id: sender }, date: 1000 };
  return { update: { update_id: id, message }, message,
    replyWithChatAction: async () => {},
    reply: async (text, options) => { sent.push({id, text, replyTo: options.reply_parameters.message_id});
      return { message_id: id + 100, chat: message.chat, reply_to_message: {message_id: id}, text }; } };
}
function harness(overrides = {}) {
  const journal = new TransportJournal(':memory:');
  let calls = [];
  const handler = createTransportHandler({ journal, bindings, baseUrl: 'http://localhost', makeDraft: () => null,
    coreRequest: async args => {calls.push(args); return {text: `antwoord ${args.messageId}`};}, ...overrides });
  return { journal, handler, calls };
}
test('binds every answer to its original message and never redispatches a delivered update', async () => {
  const h = harness(); const sent = []; const c = ctx(1, 'hallo', 12, sent);
  await h.handler(c); await h.handler(c);
  assert.equal(h.calls.length, 1); assert.equal(sent.length, 1); assert.equal(sent[0].replyTo, 1);
  h.journal.close();
});
test('small conversation overtakes long work without mixing question identities', async () => {
  let release; const slow = new Promise(resolve => {release = resolve;}); const sent = [];
  const h = harness({coreRequest: async args => {if (args.messageId === 2) await slow; return {text:`antwoord ${args.messageId}`};}});
  const first = h.handler(ctx(2, 'onderzoek', 12, sent));
  await h.handler(ctx(3, 'hoe gaat het', 12, sent));
  assert.deepEqual(sent.map(x=>x.replyTo), [3]); release(); await first;
  assert.deepEqual(sent.map(x=>x.replyTo), [3,2]); h.journal.close();
});
test('uncertain delivery is retained and not automatically repeated', async () => {
  const h = harness(); const c = ctx(4); let sends = 0;
  c.reply = async () => {sends++; throw new Error('connection lost after possible acceptance');};
  await h.handler(c); await h.handler(c);
  assert.equal(h.calls.length,1); assert.equal(sends,1);
  assert.equal(h.journal.db.prepare('SELECT state FROM telegram_transport').get().state,'delivery_uncertain'); h.journal.close();
});
test('unbound sender cannot invoke Eva or create a transport job', async () => {
  const h = harness(); await h.handler(ctx(5,'hallo',99));
  assert.equal(h.calls.length,0); assert.equal(h.journal.db.prepare('SELECT COUNT(*) AS n FROM telegram_transport').get().n,0); h.journal.close();
});
test('legacy command explains the stop and never invokes either agent', async () => {
  const h = harness(); const sent = []; await h.handler(ctx(6,'/eva1 hallo',12,sent));
  assert.equal(h.calls.length,0); assert.match(sent[0].text,/Eva1 is gestopt/); h.journal.close();
});
test('existing photo adapter feeds interpreted image to the same Eva2 route', async () => {
  let submitted; const h = harness({photoProvider:{chatComplete:async req=>({text:'Een groene doos'}),imageMsg:(text,image)=>({text,image})},
    downloadFile:async()=>Buffer.from('image'),coreRequest:async args=>{submitted=args;return {text:'Ik zie een doos'};}});
  const c=ctx(7);delete c.message.text;c.message.photo=[{file_id:'image-file'}];
  await h.handler(c);assert.equal(submitted.mediaContext.description,'Een groene doos');assert.equal(submitted.mediaContext.source_ref,'telegram:12:7');
  h.journal.close();
});
test('blocked private account records bounded identity metadata, not content or permission', async () => {
  const h = harness(); const c = ctx(8, 'private message must not be retained', 99);
  c.message.chat.type = 'private'; c.message.from.first_name = 'New household member';
  await h.handler(c); await h.handler(c);
  const rows = h.journal.db.prepare('SELECT * FROM telegram_access_requests').all();
  assert.equal(rows.length, 1); assert.equal(rows[0].telegram_user_id, 99);
  assert.equal(rows[0].first_name, 'New household member');
  assert.equal(JSON.stringify(rows).includes('private message'), false);
  assert.equal(h.calls.length, 0);
  assert.equal(h.journal.db.prepare('SELECT count(*) AS n FROM telegram_transport').get().n, 0);
  const group = ctx(9, 'group private text', 100); group.message.chat = {type:'group',id:-123};
  await h.handler(group);
  assert.equal(h.journal.db.prepare('SELECT count(*) AS n FROM telegram_access_requests').get().n, 1);
  h.journal.close();
});
