import assert from 'node:assert/strict';
import test from 'node:test';
import { analyseEva2Photo } from '../../deploy/telegram-bridge/src/integrations/eva2media.js';
import { askEva2Canary } from '../../deploy/telegram-bridge/src/integrations/eva2canary.js';

test('actual image analysis is separated from the caption and forwarded to the same Eva2 core', async () => {
  const caption = 'Het apparaat registreert het wel in de Tuya app.';
  let analysisCall;
  const media = await analyseEva2Photo({ buffer: Buffer.from('fixture-image'),
    sourceRef: 'telegram:7:12', caption,
    imageMessage: (text, image) => ({ text, image }),
    complete: async request => { analysisCall = request; return { text: 'Een Tuya-event wordt getoond.' }; },
  });
  assert.equal(analysisCall.model, 'gpt-6-astra');
  assert.equal(analysisCall.effort, 'medium');
  assert.deepEqual(analysisCall.tools, []);
  assert.match(analysisCall.input[0].image, /^data:image\/jpeg;base64,/);
  let actual;
  const reply = await askEva2Canary({ baseUrl: 'http://fixture', chatId: 7, fromId: 7,
    messageId: 12, telegramDate: 1787486400, text: caption, mediaContext: media,
    archivedMessage: { role: 'user', kind: 'photo', content: caption, meta: { dialogueRoute: 'eva2' } },
    fetchImpl: async (url, request) => {
      actual = JSON.parse(request.body);
      return new Response(JSON.stringify({ contract: 'EvaInternalGroundedReply/v1',
        conversation_id: 'telegram:7', message_id: 'telegram:7:12', text: 'Antwoord op de foto.' }));
    },
  });
  assert.equal(reply.text, 'Antwoord op de foto.');
  assert.equal(actual.text, caption);
  assert.equal(actual.media_context.description, media.description);
  assert.deepEqual(actual.recent_messages, []);
});

test('a photo without a caption has an implicit request and never selects Eva1', async () => {
  await askEva2Canary({ baseUrl: 'http://fixture', chatId: 7, fromId: 7,
    messageId: 12, telegramDate: 1787486400, text: '',
    archivedMessage: { role: 'user', kind: 'photo', content: '', meta: { dialogueRoute: 'eva2' } },
    fetchImpl: async (_, request) => {
      assert.equal(JSON.parse(request.body).text, 'Bekijk de aangeleverde foto.');
      return new Response(JSON.stringify({ contract: 'EvaInternalGroundedReply/v1',
        conversation_id: 'telegram:7', message_id: 'telegram:7:12', text: 'Fotoantwoord.' }));
    },
  });
});
