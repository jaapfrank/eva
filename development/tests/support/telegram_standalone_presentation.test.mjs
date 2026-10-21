import assert from 'node:assert/strict';
import test from 'node:test';
import {TransportJournal, createTransportHandler} from '../../../deploy/telegram-bridge/standalone.mjs';
import {createEva2DraftSink} from '../../../deploy/telegram-bridge/src/integrations/eva2canary.js';

function fixture({coreRequest, reply} = {}) {
  const journal = new TransportJournal(':memory:');
  const sent = [], drafts = [];
  const message = {message_id: 9, date: 1787486400,
    from: {id: 7}, chat: {id: 7, type: 'private'}, text: 'Onderzoek dit.'};
  const ctx = {message, update: {update_id: 1, message},
    replyWithChatAction: async () => {},
    reply: async (text, options) => {
      sent.push({text, options});
      if (reply) return reply(text, options);
      return {message_id: 10, chat: message.chat, text,
        reply_to_message: {message_id: message.message_id}};
    }};
  const handler = createTransportHandler({journal,
    bindings: [{telegram_user_id: 7, allowed_chat_ids: ['7']}], baseUrl: 'http://fixture',
    makeDraft: () => createEva2DraftSink({chatId: 7, draftId: 9,
      sendDraft: async payload => {drafts.push(payload); return true;}}),
    downloadFile: async () => {throw new Error('Unexpected download');},
    coreRequest: coreRequest || (async ({onDraft}) => {
      onDraft.enqueue('Ik lees de bronnen.');
      await onDraft.finish();
      return {text: 'Ik heb de officiële aankondiging gevonden.'};
    })});
  return {journal, sent, drafts, ctx, handler};
}

test('installed standalone handler keeps drafts and saved replies unlabelled and bound', async () => {
  const f = fixture();
  try {
    await f.handler(f.ctx);
    await f.handler(f.ctx);
    assert.equal(f.sent.length, 1, 'a duplicate update cannot resend the reply');
    assert.equal(f.sent[0].text, 'Ik heb de officiële aankondiging gevonden.');
    assert.equal(f.drafts[0].text, 'Ik lees de bronnen.');
    assert.deepEqual(f.sent[0].options.reply_parameters,
      {message_id: 9, allow_sending_without_reply: false});
    assert.equal(f.journal.db.prepare('SELECT state FROM telegram_transport').get().state, 'delivered');
  } finally {f.journal.close();}
});

test('standalone unsupported-file response has no project or legacy-agent label', async () => {
  const f = fixture();
  delete f.ctx.message.text;
  f.ctx.message.document = {mime_type: 'application/zip'};
  try {
    await f.handler(f.ctx);
    assert.match(f.sent[0].text, /^Ik kan dit bestandstype/u);
    assert.doesNotMatch(f.sent[0].text, /Eva[12]/u);
  } finally {f.journal.close();}
});

test('failed processing remains a failure without an Eva2 presentation prefix', async () => {
  const f = fixture({coreRequest: async () => {throw new Error('Verbinding onderbroken');}});
  try {
    await f.handler(f.ctx);
    assert.equal(f.sent[0].text, 'Je bericht is niet afgehandeld: Verbinding onderbroken.');
    assert.doesNotMatch(f.sent[0].text, /Eva[12]/u);
  } finally {f.journal.close();}
});

test('uncertain standalone send is retained and never replaced by a second reply', async () => {
  const f = fixture({reply: async () => {throw new Error('Unknown delivery outcome');}});
  try {
    await f.handler(f.ctx);
    await f.handler(f.ctx);
    assert.equal(f.sent.length, 1);
    assert.equal(f.journal.db.prepare('SELECT state FROM telegram_transport').get().state, 'delivery_uncertain');
  } finally {f.journal.close();}
});
