import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { TransportJournal, computeTelegramVaultProof, createTransportHandler, telegramReplyContext } from './standalone.mjs';
import { chatComplete as eva2PhotoChatComplete, imageMsg as eva2ImageMsg } from './src/integrations/eva2photo_provider.mjs';
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
test('one-time 256-bit invite admits private user while invalid token stays silent (default-deny)', async () => {
  const validToken = 'inv_' + 'A'.repeat(43);
  let used = false;
  const sent = [];
  const h = harness({
    redeemInvite: async ({ token, telegramUserId }) => {
      if (token === validToken && !used) {
        used = true;
        return { admitted: true, welcome_text: `Welkom gebruiker ${telegramUserId}!` };
      }
      return { admitted: false };
    },
  });
  // Invalid token from stranger 77 -> 0 replies, 0 core calls
  await h.handler(ctx(20, '/start inv_' + 'B'.repeat(43), 77, sent));
  assert.equal(sent.length, 0);
  assert.equal(h.calls.length, 0);

  // Valid single-use token from invited person 88 -> welcomed and immediately able to chat
  await h.handler(ctx(21, `/start ${validToken}`, 88, sent));
  assert.equal(sent.length, 1);
  assert.match(sent[0].text, /Welkom gebruiker 88/);
  assert.equal(h.calls.length, 0);

  // Subsequent normal message from admitted user 88 reaches Eva2 core
  await h.handler(ctx(22, 'hoi eva', 88, sent));
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0].fromId, 88);

  // Replaying the burned token from stranger 99 -> 0 replies, 0 core calls
  await h.handler(ctx(23, `/start ${validToken}`, 99, sent));
  assert.equal(sent.length, 2);
  assert.equal(h.calls.length, 1);
  h.journal.close();
});

test('pre-LLM /kluis intercepts password before SQLite & LLM, deletes chat message, and redacts journal even on error', async () => {
  const sent = [];
  const deleted = [];
  const storedSecrets = [];
  const h = harness({
    storeVaultSecret: async ({ telegramUserId, deviceId, secretValue }) => {
      if (deviceId === 'reject_me') throw new Error('HTTP 403');
      storedSecrets.push({ telegramUserId, deviceId, secretValue });
      return { stored: true, reply_text: `Geheim veilig opgeslagen voor ${deviceId}` };
    },
  });

  // 1. Valid /kluis command with super-secret password
  const c1 = ctx(30, '/kluis cam_voordeur MijnSuperGeheimeWachtwoord!99', 12, sent);
  c1.message.chat.type = 'private';
  c1.api = { deleteMessage: async (chatId, msgId) => { deleted.push([chatId, msgId]); } };
  await h.handler(c1);

  assert.equal(h.calls.length, 0); // LLM coreRequest NEVER invoked!
  assert.deepEqual(deleted, [[12, 30]]); // Original password message deleted from Telegram chat!
  assert.equal(storedSecrets.length, 1);
  assert.equal(storedSecrets[0].deviceId, 'cam_voordeur');
  assert.equal(storedSecrets[0].secretValue, 'MijnSuperGeheimeWachtwoord!99');
  assert.match(sent[0].text, /Geheim veilig opgeslagen voor cam_voordeur/);

  // SQLite journal must NEVER contain the plaintext password
  const dump1 = JSON.stringify(h.journal.db.prepare('SELECT * FROM telegram_transport').all());
  assert.equal(dump1.includes('MijnSuperGeheimeWachtwoord!99'), false);
  assert.equal(dump1.includes('[REDACTED]'), true);

  // 2. Rejected /kluis (e.g. non-owner 403 or invalid id) STILL deletes chat message and redacts SQLite
  const c2 = ctx(31, '/wachtwoord reject_me AnderGeheimWachtwoord#42', 12, sent);
  c2.message.chat.type = 'private';
  c2.api = { deleteMessage: async (chatId, msgId) => { deleted.push([chatId, msgId]); } };
  await h.handler(c2);

  assert.equal(h.calls.length, 0);
  assert.deepEqual(deleted, [[12, 30], [12, 31]]);
  const dump2 = JSON.stringify(h.journal.db.prepare('SELECT * FROM telegram_transport').all());
  assert.equal(dump2.includes('AnderGeheimWachtwoord#42'), false);
  h.journal.close();
});

test('security hardening: uppercase /Kluis, caption secrets, link_preview_options stripping, group refusal, and reply-context scrub', async () => {
  const sent = [];
  const storedCalls = [];
  const h = harness({
    bindings: [{ telegram_user_id: 12, allowed_chat_ids: ['12', '-999'] }],
    storeVaultSecret: async payload => {
      storedCalls.push(payload);
      return { stored: true, reply_text: `Opgeslagen voor ${payload.deviceId}` };
    },
  });

  // 1. Photo caption with uppercase /Kluis + link_preview_options + deleteMessage failing
  const cCap = ctx(40, undefined, 12, sent);
  delete cCap.message.text;
  cCap.message.caption = '/Kluis ha_10_0_20_74 https://secret:GeheimToken999@10.0.20.74';
  cCap.message.link_preview_options = { url: 'https://secret:GeheimToken999@10.0.20.74' };
  cCap.message.chat.type = 'private';
  cCap.api = { deleteMessage: async () => { throw new Error('Telegram delete failed'); } };
  await h.handler(cCap);

  assert.equal(h.calls.length, 0);
  assert.equal(storedCalls.length, 1);
  assert.equal(storedCalls[0].messageDeleted, false);
  assert.match(sent[0].text, /handmatig/);

  // Verify link_preview_options & caption were completely stripped from SQLite
  const sqlDump = JSON.stringify(h.journal.db.prepare('SELECT * FROM telegram_transport').all());
  assert.equal(sqlDump.includes('GeheimToken999'), false);
  assert.equal(sqlDump.includes('link_preview_options'), false);

  // 2. Group chat /kluis is intercepted, redacted, never stored, and never forwarded to LLM
  const cGroup = ctx(41, '/kluis cam_tuin GroepsGeheim888!', 12, sent);
  cGroup.message.chat = { id: -999, type: 'supergroup' };
  await h.handler(cGroup);
  assert.equal(h.calls.length, 0);
  assert.equal(storedCalls.length, 1); // unchanged
  const sqlDump2 = JSON.stringify(h.journal.db.prepare('SELECT * FROM telegram_transport').all());
  assert.equal(sqlDump2.includes('GroepsGeheim888!'), false);

  // 3. telegramReplyContext refuses to quote an un-deleted /kluis message
  const replyCtx = telegramReplyContext({
    message_id: 50,
    chat: { id: 12, type: 'private' },
    reply_to_message: {
      message_id: 49,
      chat: { id: 12, type: 'private' },
      date: 1000,
      text: '/kluis cam_tuin OudGeheim777',
    },
  });
  assert.equal(replyCtx, null);

  // 4. Bridge HMAC proof is 64-char hex and changes with token
  const p1 = computeTelegramVaultProof('123456:ABCDEF');
  const p2 = computeTelegramVaultProof('123456:ABCDEF_DIFFERENT');
  assert.equal(p1.length, 64);
  assert.notEqual(p1, p2);
  h.journal.close();
});

test('pure Eva2 photo provider parses SSE, refreshes on 401 with 0o600 atomic write, and uses zero Bottie/SQLite dependencies', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'eva2-photo-test-'));
  const authPath = path.join(dir, 'auth.json');
  fs.writeFileSync(
    authPath,
    JSON.stringify({
      access_token: 'old_tok_123',
      refresh_token: 'ref_tok_456',
      expires_at: Date.now() + 3600_000,
      account_id: 'acc_999',
    }),
    { mode: 0o600 },
  );

  let callCount = 0;
  const fakeFetch = async (url, init) => {
    callCount += 1;
    if (callCount === 1) {
      // First Responses call returns 401 -> triggers OAuth refresh
      assert.match(url, /chatgpt\.com\/backend-api\/codex\/responses/);
      return new Response('unauthorized', { status: 401 });
    }
    if (callCount === 2) {
      // Token refresh call
      assert.match(url, /auth\.openai\.com\/oauth\/token/);
      return new Response(
        JSON.stringify({ access_token: 'fresh_tok_789', refresh_token: 'ref_tok_new', expires_in: 3600 }),
        { status: 200, headers: { 'content-type': 'application/json' } },
      );
    }
    // Retried Responses call with fresh token succeeds with SSE stream
    assert.equal(init.headers.Authorization, 'Bearer fresh_tok_789');
    const sse = [
      'data: {"type":"response.completed","response":{"output":[{"type":"message","content":[{"type":"output_text","text":"Een kop koffie op tafel."}]}],"usage":{"input_tokens":120,"output_tokens":8}}}',
      'data: [DONE]',
      '',
    ].join('\n');
    return new Response(sse, { status: 200, headers: { 'content-type': 'text/event-stream' } });
  };

  const msg = eva2ImageMsg('Wat zie je?', 'data:image/jpeg;base64,AAAA');
  assert.equal(msg.content[1].type, 'input_image');

  const out = await eva2PhotoChatComplete({
    instructions: 'Beschrijf de foto.',
    input: [msg],
    authFile: authPath,
    fetchImpl: fakeFetch,
  });
  assert.equal(out.text, 'Een kop koffie op tafel.');
  assert.equal(callCount, 3);
  const statMode = fs.statSync(authPath).mode & 0o777;
  assert.equal(statMode, 0o600);
  const updatedAuth = JSON.parse(fs.readFileSync(authPath, 'utf8'));
  assert.equal(updatedAuth.access_token, 'fresh_tok_789');
  fs.rmSync(dir, { recursive: true, force: true });
});
