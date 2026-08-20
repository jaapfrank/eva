import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';

const dataDir = mkdtempSync(path.join(tmpdir(), 'bottie-telegram-transport-'));
process.env.EVA_DATA_DIR = dataDir;
process.env.TELEGRAM_BOT_TOKEN = '';
process.env.TELEGRAM_ALLOWED_IDS = '12345,67890,24680';
process.env.EVA_ATTENTION_BATCH_MS = '10';
process.env.OPENAI_API_KEY = 'telegram-identity-context-test-only';
await import('./homeassistant-test-composition.js');
await import('./telegram-test-composition.js');

const {
  addMessage,
  addMemory,
  archiveTelegramMessage,
  db,
  newerTelegramUserMessage,
  telegramOutboundByDedupeKey,
} = await import('../src/db.js');
const {
  __telegramMediaTest,
  __telegramTransportTest,
  telegramPollingOptions,
  verifiedTelegramIdentityContext,
} = await import('../src/integrations/telegram.js');
const { __cameraTriggerTest } = await import('../src/integrations/homeassistant.js');
const doorbellAttention = await import('../src/mind/perceptionattention.js');
const {
  createFaceIdentityClaim,
  nameLastSeen,
  rememberNameablePhoto,
} = await import('../src/mind/vision.js');
const {
  directTurnIdentity,
  resolveTelegramTargets,
  telegramTextSendOptions,
} = __telegramTransportTest;

after(() => {
  db.close();
  rmSync(dataDir, { recursive: true, force: true });
});

const people = [
  { name: 'Jaap', telegram_id: 12345 },
  { name: 'J Frank', telegram_id: '12345' },
  { name: 'Lola', telegram_id: '67890' },
  { name: 'Sanne', telegram_id: 24680 },
  { name: 'Niet bereikbaar', telegram_id: null },
];

test('onbekende niet-lege ontvanger faalt gesloten, ook met broadcast-optie', () => {
  for (const options of [{}, { broadcast: true }]) {
    const result = resolveTelegramTargets(people, 'Japp', options);
    assert.equal(result.ok, false);
    assert.equal(result.code, 'TELEGRAM_RECIPIENT_UNKNOWN');
    assert.deepEqual(result.targets, []);
    assert.match(result.info, /niets verstuurd/i);
  }
});

test('ontbrekende ontvanger vereist expliciete gezinsbroadcast', () => {
  const missing = resolveTelegramTargets(people, null);
  assert.equal(missing.ok, false);
  assert.equal(missing.code, 'TELEGRAM_RECIPIENT_REQUIRED');

  const optionBroadcast = resolveTelegramTargets(people, null, { broadcast: true });
  assert.equal(optionBroadcast.ok, true);
  assert.equal(optionBroadcast.broadcast, true);

  const namedBroadcast = resolveTelegramTargets(people, 'het gezin');
  assert.equal(namedBroadcast.ok, true);
  assert.equal(namedBroadcast.broadcast, true);
});

test('numerieke en string-id van dezelfde persoon worden gededupliceerd', () => {
  const broadcast = resolveTelegramTargets(people, 'gezin');
  assert.equal(broadcast.ok, true);
  assert.deepEqual(
    broadcast.targets.map((person) => String(person.telegram_id)).sort(),
    ['12345', '24680', '67890'],
  );

  const alias = resolveTelegramTargets(people, 'J Frank');
  assert.equal(alias.ok, true);
  assert.equal(alias.targets.length, 1);
  assert.equal(String(alias.targets[0].telegram_id), '12345');

  const byId = resolveTelegramTargets(people, 12345);
  assert.equal(byId.ok, true);
  assert.equal(byId.targets.length, 1);
});

test('een onvolledige naam die meerdere chat-ids raakt is getypeerd ambigu', () => {
  const result = resolveTelegramTargets([
    { name: 'Sanne', telegram_id: 1 },
    { name: 'Sandra', telegram_id: 2 },
  ], 'san');
  assert.equal(result.ok, false);
  assert.equal(result.code, 'TELEGRAM_RECIPIENT_AMBIGUOUS');
  assert.deepEqual(result.targets, []);
});

test('direct antwoord krijgt stabiele turn-, signal- en dedupe-identiteit', () => {
  const identity = directTurnIdentity({
    chat: { id: 9001 },
    message: { message_id: 77 },
  });
  assert.deepEqual(identity, {
    chatId: '9001',
    messageId: '77',
    signalRef: 'turn:telegram:9001:77',
    dedupeKey: 'user-reply:turn:telegram:9001:77',
  });
});

test('reply-threading wordt alleen op het eerste Telegram-fragment gezet', () => {
  assert.deepEqual(telegramTextSendOptions({
    replyToMessageId: '77',
    messageThreadId: 12,
  }, 0), {
    message_thread_id: 12,
    reply_parameters: {
      message_id: 77,
      allow_sending_without_reply: true,
    },
  });
  assert.deepEqual(telegramTextSendOptions({
    replyToMessageId: '77',
    messageThreadId: 12,
  }, 1), {
    message_thread_id: 12,
  });
});

test('verwijzingen naar mijn of jouw laatste foto kiezen de juiste afzender', () => {
  assert.equal(
    __telegramMediaTest.telegramPhotoReferenceRole('Laatste foto die je liet zien'),
    'assistant',
  );
  assert.equal(
    __telegramMediaTest.telegramPhotoReferenceRole('Wat stond er op de foto die ik stuurde?'),
    'user',
  );
  assert.equal(
    __telegramMediaTest.telegramPhotoReferenceRole('Wat zie je op de laatste foto?'),
    null,
  );
});

test('een expliciete reply-keten blijft aan exact dezelfde foto gekoppeld', () => {
  const chatId = 'reply-chain';
  archiveTelegramMessage({
    chatId, messageId: '100', ts: '2026-08-01T10:00:00Z',
    sender: 'Eva', role: 'assistant', kind: 'photo', content: 'Wie is dit?', mediaRef: 'foto-100',
  });
  archiveTelegramMessage({
    chatId, messageId: '101', ts: '2026-08-01T10:00:10Z',
    sender: 'Jaap', role: 'user', kind: 'text', content: 'Dat is Lola', replyToMessageId: '100',
  });
  archiveTelegramMessage({
    chatId, messageId: '102', ts: '2026-08-01T10:00:20Z',
    sender: 'Eva', role: 'assistant', kind: 'text', content: 'Dank je, dat is Lola.', replyToMessageId: '101',
  });
  archiveTelegramMessage({
    chatId, messageId: '103', ts: '2026-08-01T10:00:25Z',
    sender: 'Jaap', role: 'user', kind: 'text', content: 'Nee Jaap.', replyToMessageId: '102',
  });

  assert.equal(__telegramMediaTest.telegramReplyChainPhoto(chatId, '102')?.message_id, '100');
  assert.equal(
    __telegramMediaTest.inheritedPhotoForShortFollowup({
      chat: { id: chatId },
      message: { message_id: '103', text: 'Nee Jaap.' },
    })?.message_id,
    '100',
  );
});

test('korte bevestiging pakt de direct voorafgaande foto en een oudere beurt wordt superseded', () => {
  const chatId = 'short-confirmation';
  archiveTelegramMessage({
    chatId, messageId: '200', ts: '2026-08-01T11:00:00Z',
    sender: 'Eva', role: 'assistant', kind: 'photo', content: 'Jaap is bij de voordeur.', mediaRef: 'foto-200',
  });
  archiveTelegramMessage({
    chatId, messageId: '201', ts: '2026-08-01T11:00:05Z',
    sender: 'Jaap', role: 'user', kind: 'text', content: 'Dat klopt',
  });
  archiveTelegramMessage({
    chatId, messageId: '202', ts: '2026-08-01T11:00:06Z',
    sender: 'Jaap', role: 'user', kind: 'text', content: 'En dit is een nieuwe vraag',
  });

  assert.equal(__telegramMediaTest.isShortPhotoFollowup('Dat klopt'), true);
  assert.equal(
    __telegramMediaTest.inheritedPhotoForShortFollowup({
      chat: { id: chatId },
      message: { message_id: '201', text: 'Dat klopt' },
    })?.message_id,
    '200',
  );
  assert.equal(newerTelegramUserMessage(chatId, '201')?.message_id, '202');
});

test('een korte kanaalcorrectie erft na tachtig minuten geen willekeurige oude foto', async () => {
  const chatId = 'stale-photo-channel-correction';
  archiveTelegramMessage({
    chatId, messageId: '4206', ts: '2026-08-06T07:16:38Z',
    sender: 'Eva', role: 'assistant', kind: 'photo', content: 'Wie is dit?', mediaRef: 'foto-4206',
  });
  archiveTelegramMessage({
    chatId, messageId: '4207', ts: '2026-08-06T07:16:59Z',
    sender: 'Jaap', role: 'user', kind: 'text', content: 'Geen idee wie dat is.',
  });
  archiveTelegramMessage({
    chatId, messageId: '4221', ts: '2026-08-06T08:36:34Z',
    sender: 'Jaap', role: 'user', kind: 'text', content: 'Inderdaad zeg wat tegen me.',
  });
  archiveTelegramMessage({
    chatId, messageId: '4222', ts: '2026-08-06T08:36:43Z',
    sender: 'Eva', role: 'assistant', kind: 'text', content: 'Hé Jaap.',
  });
  archiveTelegramMessage({
    chatId, messageId: '4223', ts: '2026-08-06T08:37:05Z',
    sender: 'Jaap', role: 'user', kind: 'text', content: 'Nee via de voice pe',
  });
  const ctx = { chat: { id: chatId }, message: { message_id: '4223', text: 'Nee via de voice pe' } };

  assert.equal(__telegramMediaTest.isShortPhotoFollowup(ctx.message.text), false);
  assert.equal(__telegramMediaTest.inheritedPhotoForShortFollowup(ctx), null);
  assert.equal(await __telegramMediaTest.describeRecentPhotoReference(ctx, {
    force: __telegramMediaTest.isShortPhotoFollowup(ctx.message.text),
  }), null);
});

test('alleen identiteitstaal mag een nabije foto als impliciete context erven', () => {
  assert.equal(__telegramMediaTest.isShortPhotoFollowup('Nee Jaap.'), true);
  assert.equal(__telegramMediaTest.isShortPhotoFollowup('Nee, dat is Mark.'), true);
  assert.equal(__telegramMediaTest.isShortPhotoFollowup('Dat klopt inderdaad.'), true);
  assert.equal(__telegramMediaTest.isShortPhotoFollowup('Nee via de Voice PE'), false);
  assert.equal(__telegramMediaTest.isShortPhotoFollowup('Nee zet de verwarming aan'), false);
  assert.equal(__telegramMediaTest.isShortPhotoFollowup('Nee morgen pas'), false);
});

test('biometrische claim begrijpt positieve scoped correctie en echte fotoreply', () => {
  const media = __telegramMediaTest.localPhotoReply({
    target: { chatId: 'claim', messageId: '10' }, buffer: Buffer.from('beeld'),
    ref: 'foto-mark', bindingKind: 'direct_reply',
  });
  assert.equal(media.evidence.photoBinding.kind, 'direct_reply');

  const corrected = createFaceIdentityClaim({
    name: 'Mark', text: 'Nee, dat is Mark', sender: 'Jaap', photoRef: 'foto-mark',
    bindingKind: 'direct_reply', replyText: 'Wie is dit?',
  });
  assert.equal(corrected.ok, true);
  assert.equal(corrected.assertionForm, 'scoped_correction');
  assert.equal(corrected.object.name, 'Mark');

  const direct = createFaceIdentityClaim({
    name: 'Sanne', text: 'Dat is Sanne', sender: 'Jaap', photoRef: 'foto-sanne',
    bindingKind: 'direct_reply', replyText: 'Wie is dit?',
  });
  assert.equal(direct.ok, true);
  assert.equal(direct.polarity, 'affirmed');
  assert.equal(direct.subject.ref, 'foto-sanne');

  const implicit = createFaceIdentityClaim({
    name: 'Sanne', text: 'Dat is Sanne', sender: 'Jaap', photoRef: 'oude-foto',
    bindingKind: 'implicit_reference', replyText: 'Wie is dit?',
  });
  assert.equal(implicit.ok, false);
});

test('een geauthenticeerde natuurlijke fotocorrectie bereikt exact eenmaal de gewone agentactie', async () => {
  const originalFetch = globalThis.fetch;
  const chatId = '12345';
  const photoMessageId = '490';
  const messageId = '491';
  const rawText = 'Dat is oma';
  const agentText = `[Jaap verwijst naar de laatste relevante foto uit dit Telegram-gesprek.]\nJaap: ${rawText}`;
  const rawStart = agentText.lastIndexOf(rawText);
  const photoRef = rememberNameablePhoto({
    buffer: Buffer.from('lokaal-bewaarde-oma-foto'),
    contentType: 'image/jpeg',
    suggestedPerson: 'Frank',
    telegramMessages: [{ chatId, messageId: photoMessageId }],
  });
  archiveTelegramMessage({
    chatId, messageId: photoMessageId, sender: 'Eva', role: 'assistant', kind: 'photo',
    content: 'Wie staat er bij de deur?', mediaRef: photoRef,
  });
  const target = {
    channel: 'telegram', chatId, messageId: photoMessageId, photoMessageId,
    text: 'Wie staat er bij de deur?', sender: 'Eva', isAssistant: true, kind: 'photo',
  };
  const media = __telegramMediaTest.localPhotoReply({
    target, buffer: Buffer.from('lokaal-bewaarde-oma-foto'), ref: photoRef,
    bindingKind: 'nearby_photo_followup',
  });
  const ctx = {
    chat: { id: Number(chatId) },
    from: { id: Number(chatId), first_name: 'Jaap' },
    message: { message_id: Number(messageId), date: 1_786_000_000, text: rawText },
  };
  let providerCalls = 0;
  let understandingCalls = 0;
  globalThis.fetch = async (_url, options = {}) => {
    providerCalls += 1;
    const body = JSON.parse(String(options.body || '{}'));
    const tool = body.tools?.[0]?.name || null;
    const event = (output) => new Response(`data: ${JSON.stringify({
      type: 'response.completed', response: { output },
    })}\n\n`, { status: 200, headers: { 'content-type': 'text/event-stream' } });
    if (tool === 'commit_turn_understanding') {
      understandingCalls += 1;
      return event([{
      type: 'function_call', call_id: 'understand-oma-correction', name: tool,
      arguments: JSON.stringify({
        dialogueAct: 'inform',
        goal: { statement: 'Corrigeer wie op de actuele foto staat.',
          outcome: 'De actuele foto is aan oma gekoppeld.', beneficiary: 'household' },
        conditions: [{ statement: 'De actuele foto toont de persoon die Jaap als oma identificeert.',
          state: 'met', role: 'action', requirementIds: ['r1'], afterRequirementIds: [] }],
        provenance: { sourceSpans: [{ start: 0, end: agentText.length }], contextHashes: [] },
        confidence: 0.99,
        actions: [{ capabilityId: 'identity.face.learn', operationId: 'learn', polarity: 'request',
          semanticInput: { naam: 'oma' },
          sourceSpans: [{ start: rawStart, end: rawStart + rawText.length }] }],
        requestedEffects: ['persistent'], deniedEffects: [],
        temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
        delivery: { mode: 'reply', target: 'origin' }, proofNeeds: ['accepted_result'],
        ambiguities: [],
        firstDecision: { kind: 'act', requirementIds: ['r1'], replyDraft: null },
      }),
      }]);
    }
    assert.deepEqual(body.tools || [], []);
    return event([{ type: 'message', role: 'assistant', content: [{ type: 'output_text',
      text: 'Dat is oma. Ik heb haar naam aan deze foto gekoppeld.' }] }]);
  };

  let turn;
  let issuedContext;
  try {
    turn = await __telegramTransportTest.prepareAuthenticatedTelegramTurnForTest({
      ctx, sender: 'Jaap', text: rawText, agentText,
      extras: { replyToMessageId: photoMessageId },
      replyTo: target, replyEvidence: media.evidence,
      onContext: (value) => { issuedContext = value; },
    });
  } finally {
    globalThis.fetch = originalFetch;
  }

  const contextProof = verifiedTelegramIdentityContext(issuedContext, {
    channel: 'telegram', conversationId: `telegram:${chatId}`, messageId,
    requester: 'Jaap', originalText: agentText, name: 'oma',
  });
  assert.ok(contextProof, JSON.stringify({ turn, evidence: media.evidence }));
  assert.equal(understandingCalls, 1, JSON.stringify(turn));
  assert.ok(providerCalls >= 2 && providerCalls <= 4, JSON.stringify(turn));
  assert.deepEqual(turn.tools.map((item) => item.name), ['leer_gezicht_van_foto']);
  assert.equal(turn.tools[0].ok, true, JSON.stringify(turn.tools[0]));
  assert.equal(db.prepare('SELECT named_person FROM nameable_photos WHERE id=?')
    .get(photoRef)?.named_person, 'Oma');
  assert.equal(db.prepare("SELECT COUNT(*) AS count FROM people WHERE lower(name)='oma'")
    .get().count, 1);
  assert.match(turn.reply, /oma.*foto gekoppeld/iu);
});

test('kanaalcorrectie kan aan de mutatiegrens geen persoon of geheugen maken', async () => {
  const content = 'Jaap wil via de Voice PE met Eva praten.';
  addMemory({ category: 'afspraak', person: 'Jaap', content, importance: 0.9 });
  const ref = rememberNameablePhoto({ buffer: Buffer.from('geen-echt-beeld') });
  const claim = createFaceIdentityClaim({
    name: 'via de voice pe', text: 'Nee via de voice pe', sender: 'Jaap', photoRef: ref,
    bindingKind: 'nearby_photo_followup', replyText: 'Wie is dit?',
  });
  assert.equal(claim.ok, false);

  const result = await nameLastSeen('via de voice pe', { ref, identityClaim: claim });
  assert.equal(result.ok, false);
  assert.equal(result.changed, false);
  assert.equal(db.prepare("SELECT COUNT(*) AS count FROM people WHERE lower(name)='via de voice pe'").get().count, 0);
  assert.equal(db.prepare('SELECT COUNT(*) AS count FROM memories WHERE content=?').get(content).count, 1);
});

test('herstart bewaart Telegram-updates die tijdens downtime zijn binnengekomen', () => {
  assert.deepEqual(telegramPollingOptions(), {
    drop_pending_updates: false,
  });
});

test('een later gearchiveerd oud reply-doel geldt niet als nieuwer Telegram-bericht', () => {
  const chatId = 'out-of-order-reply';
  archiveTelegramMessage({
    chatId, messageId: '300', sender: 'Jaap', role: 'user', kind: 'text', content: 'Actuele vraag',
  });
  archiveTelegramMessage({
    chatId, messageId: '250', sender: 'Jaap', role: 'user', kind: 'text', content: 'Oud reply-doel',
  });
  assert.equal(newerTelegramUserMessage(chatId, '300'), null);
});

test('Telegram-deduplicatie blijft binnen de bedoelde chat', () => {
  archiveTelegramMessage({
    chatId: 'chat-a', messageId: '10', sender: 'Eva', role: 'assistant', kind: 'text',
    content: 'voor A', meta: { dedupeKey: 'zelfde-aflevering' },
  });
  archiveTelegramMessage({
    chatId: 'chat-b', messageId: '11', sender: 'Eva', role: 'assistant', kind: 'text',
    content: 'voor B', meta: { dedupeKey: 'zelfde-aflevering' },
  });
  assert.equal(telegramOutboundByDedupeKey('zelfde-aflevering', 'chat-a')?.content, 'voor A');
  assert.equal(telegramOutboundByDedupeKey('zelfde-aflevering', 'chat-b')?.content, 'voor B');
});

test('een natuurlijke deurbelvervolgvraag behoudt het geverifieerde bronbewijs', async () => {
  const originalFetch = globalThis.fetch;
  const chatId = '24680';
  const place = `de testzijdeur ${Date.now()}`;
  const sensor = `event.test_deurbel_${Date.now()}`;
  const notification = `Er werd aangebeld bij ${place}, maar ik kreeg geen bruikbaar beeld.`;
  const followup = 'Hoe weet je dat dan?';
  const source = __cameraTriggerTest.issue({
    type: 'doorbell', source: 'home_assistant', camera: `camera.test_${Date.now()}`,
    sensor, waar: place, correlationId: `telegram-provenance-${Date.now()}`,
    triggerTs: new Date(Date.now() - 5_000).toISOString(), frames: [],
  });
  const recorded = doorbellAttention.recordDoorbellEvidence(source);
  assert.equal(recorded.ok, true);
  const claim = doorbellAttention.claimReadyPerceptionIncident({
    householdRecipients: [{ label: 'Sanne', chatIds: [chatId] }],
  });
  const decision = doorbellAttention.normalizePerceptionDecision(claim, {
    contract: 'DoorbellAttentionDecision/v1', action: 'household_text',
    recipient: 'Sanne', text: notification,
  });
  const capability = doorbellAttention.issuePerceptionIncidentPlan(claim, decision);
  const plan = doorbellAttention.beginPerceptionIncidentEffect(capability);
  const deliveryKey = `perception:${plan.incidentId}:${plan.planId}`;
  archiveTelegramMessage({
    chatId, messageId: '6100', ts: new Date().toISOString(),
    sender: 'Eva', role: 'assistant', kind: 'text', content: notification,
    processingStatus: 'completed',
    meta: {
      outbound: true, transportAccepted: true, deliveryKey,
      dedupeKey: `${deliveryKey}:chunk:1`, chunkIndex: 0, chunkCount: 1,
    },
  });
  assert.deepEqual(doorbellAttention.settlePerceptionIncidentPlan(capability, {
    result: 'delivered', receipt: `telegram:${chatId}:6100`,
  }), { ok: true, status: 'delivered' });

  const providerEvidence = [];
  const event = (output) => new Response(`data: ${JSON.stringify({
    type: 'response.completed', response: { output },
  })}\n\n`, { status: 200, headers: { 'content-type': 'text/event-stream' } });
  globalThis.fetch = async (_url, options = {}) => {
    const body = JSON.parse(String(options.body || '{}'));
    const tool = body.tools?.[0]?.name || null;
    const inputText = String(body.input?.[0]?.content?.[0]?.text || '');
    providerEvidence.push({ tool, inputText });
    if (tool === 'commit_turn_understanding') {
      return event([{
        type: 'function_call', call_id: 'understand-doorbell-source-followup', name: tool,
        arguments: JSON.stringify({
          dialogueAct: 'question',
          goal: { statement: 'Leg de bron van Eva haar vorige deurbelmelding uit.',
            outcome: 'Sanne weet welk bewijs de melding droeg.', beneficiary: 'requester' },
          conditions: [],
          provenance: { sourceSpans: [{ start: 0, end: followup.length }], contextHashes: [] },
          confidence: 0.99, actions: [], requestedEffects: [], deniedEffects: [],
          temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
          delivery: { mode: 'reply', target: 'origin' }, proofNeeds: [], ambiguities: [],
          firstDecision: { kind: 'reply', requirementIds: [],
            replyDraft: `De sensor bij ${place} bevestigde het aanbellen. Er was geen beeld, dus ik kon niet zien wie het was.` },
        }),
      }]);
    }
    assert.deepEqual(body.tools || [], []);
    return event([{ type: 'message', role: 'assistant', content: [{ type: 'output_text',
      text: `De Home Assistant-deurbelsensor bij ${place} bevestigde dat er werd aangebeld. Er was geen beeld beschikbaar, dus ik kon niet vaststellen wie het was.`,
    }] }]);
  };

  let turn;
  try {
    turn = await __telegramTransportTest.prepareAuthenticatedTelegramTurnForTest({
      ctx: {
        chat: { id: Number(chatId) }, from: { id: Number(chatId), first_name: 'Sanne' },
        message: { message_id: 6101, date: Math.floor(Date.now() / 1_000), text: followup },
      },
      sender: 'Sanne', text: followup,
    });
  } finally {
    globalThis.fetch = originalFetch;
  }

  const understandingInput = providerEvidence.find((item) => (
    item.tool === 'commit_turn_understanding'
  ))?.inputText || '';
  const composerInput = providerEvidence.find((item) => item.tool == null)?.inputText || '';
  for (const input of [understandingInput, composerInput]) {
    assert.match(input, new RegExp(sensor, 'u'));
    assert.match(input, new RegExp(place, 'u'));
    assert.match(input, /deurbeltrigger/iu);
    assert.match(input, /beeld[^\n]{0,80}niet beschikbaar/iu);
    assert.match(input, /identiteit[^\n]{0,80}niet vaststelbaar/iu);
  }
  assert.match(turn.reply, /sensor[^.]*bevestigde[^.]*aangebeld/iu);
  assert.match(turn.reply, /geen beeld[^.]*niet vaststellen wie/iu);
  assert.doesNotMatch(turn.reply, /geen bevestigde deurbelmelding|niet terugvinden/iu);
});

test('Telegram-draad 4347-4352 bindt de bezorger aan 4349 en bewaart geen zwevende fotoherinnering', async () => {
  const originalFetch = globalThis.fetch;
  const chatId = '12345';
  const conversationId = `telegram:${chatId}`;
  const confirmation = 'Klopt dat was een bezorger.';
  const correction = 'Nee kijk naar je laatste bericht. Daar ging het over…..';
  const badReply = 'Helder, bij G5 tuin was het een bezorger — niet jij en niet Jasmin.';
  const correctionAgentText = `[Jaap reageert op mijn eerdere bericht: "${badReply}"]\nJaap: ${correction}`;
  const vagueMemory = 'De persoon op de laatst relevante camerafoto was een bezorger.';
  const staleLocalAssistant = 'Oude lokale G5/Jasmin-draad die niet in een vol Telegramvenster hoort.';
  const providerTurns = [];
  const composerInputs = [];
  const attemptsByText = new Map();
  const event = (output) => new Response(`data: ${JSON.stringify({
    type: 'response.completed', response: { output },
  })}\n\n`, { status: 200, headers: { 'content-type': 'text/event-stream' } });
  const replyUnderstanding = (text, replyDraft) => ({
    dialogueAct: text === correctionAgentText ? 'correct' : 'inform',
    goal: {
      statement: text === correctionAgentText
        ? 'Herstel de verkeerde koppeling in Eva haar laatste antwoord.'
        : 'Begrijp welke eerdere melding Jaap bevestigt.',
      outcome: text === correctionAgentText
        ? 'Eva corrigeert de verkeerde koppeling aan G5 en Jasmin.'
        : 'De persoon met een pakketje aan de voordeur is als bezorger begrepen.',
      beneficiary: 'requester',
    },
    conditions: [],
    provenance: { sourceSpans: [{ start: 0, end: text.length }], contextHashes: [] },
    confidence: 0.99,
    actions: [], requestedEffects: [], deniedEffects: [],
    temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
    delivery: { mode: 'reply', target: 'origin' }, proofNeeds: [], ambiguities: [],
    firstDecision: { kind: 'reply', requirementIds: [], replyDraft },
  });

  archiveTelegramMessage({
    chatId, messageId: '4347', ts: '2026-08-14T15:17:13.000Z',
    sender: 'Eva', role: 'assistant', kind: 'photo',
    content: 'Er is iemand bij G4 Doorbell Pro PoE. Ik zie wel een gezicht, maar herken diegene nog niet betrouwbaar. Weet jij wie het is?\nCamera: G4 Doorbell Pro PoE',
    mediaRef: 'camera-g4-4347',
  });
  archiveTelegramMessage({
    chatId, messageId: '4348', ts: '2026-08-14T15:27:52.000Z',
    sender: 'Eva', role: 'assistant', kind: 'photo',
    content: 'Er is iemand bij G4 Doorbell Pro PoE. Ik zie wel een gezicht, maar herken diegene nog niet betrouwbaar. Weet jij wie het is?\nCamera: G4 Doorbell Pro PoE',
    mediaRef: 'camera-g4-4348',
  });
  archiveTelegramMessage({
    chatId, messageId: '4349', ts: '2026-08-14T15:28:00.000Z',
    sender: 'Eva', role: 'assistant', kind: 'text',
    content: 'Er staat iemand aan de voordeur met een pakketje of zak in de hand.',
  });
  addMessage('telegram', 'Eva', 'assistant', staleLocalAssistant, conversationId);

  globalThis.fetch = async (_url, options = {}) => {
    const body = JSON.parse(String(options.body || '{}'));
    const tool = body.tools?.[0]?.name || null;
    const inputText = String(body.input?.[0]?.content?.[0]?.text || '');
    if (tool === 'commit_turn_understanding') {
      const payload = JSON.parse(inputText);
      const text = String(payload.turn?.text || '');
      providerTurns.push({ text, payload, instructions: String(body.instructions || '') });
      const attempt = (attemptsByText.get(text) || 0) + 1;
      attemptsByText.set(text, attempt);
      if (text === confirmation && attempt === 1) {
        return event([{
          type: 'function_call', call_id: 'understand-vague-bezorger', name: tool,
          arguments: JSON.stringify({
            dialogueAct: 'inform',
            goal: { statement: 'Bewaar wat Jaap over de persoon bevestigt.',
              outcome: 'De bevestiging is blijvend onthouden.', beneficiary: 'household' },
            conditions: [{
              statement: 'Bewaar de bevestiging als herinnering.', role: 'action', state: 'unmet',
              sourceSpans: [{ start: 0, end: confirmation.length }],
              requirementIds: ['r1'], afterRequirementIds: [],
            }],
            provenance: { sourceSpans: [{ start: 0, end: confirmation.length }], contextHashes: [] },
            confidence: 0.99,
            actions: [{
              capabilityId: 'memory.fact.write', operationId: 'remember', polarity: 'request',
              semanticInput: { inhoud: vagueMemory, persoon: 'Jaap', categorie: 'gebeurtenis', belang: 0.6 },
              sourceSpans: [{ start: 0, end: confirmation.length }],
            }],
            requestedEffects: ['persistent'], deniedEffects: [],
            temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
            delivery: { mode: 'reply', target: 'origin' }, proofNeeds: [], ambiguities: [],
            firstDecision: { kind: 'act', requirementIds: ['r1'], replyDraft: null },
          }),
        }]);
      }
      if (text === confirmation) {
        return event([{
          type: 'function_call', call_id: 'understand-concrete-bezorger', name: tool,
          arguments: JSON.stringify(replyUnderstanding(text,
            'Klopt — de persoon met het pakketje aan de voordeur was dus een bezorger.')),
        }]);
      }
      if (text === correctionAgentText) {
        return event([{
          type: 'function_call', call_id: 'understand-last-answer-correction', name: tool,
          arguments: JSON.stringify(replyUnderstanding(text,
            'Je hebt gelijk: je bevestigde mijn melding over iemand met een pakketje aan de voordeur, niet een foto van G5.')),
        }]);
      }
      throw new Error(`onverwachte Telegram-testbeurt: ${text}`);
    }
    assert.deepEqual(body.tools || [], []);
    composerInputs.push(inputText);
    const selfCorrection = inputText.includes(correction);
    return event([{ type: 'message', role: 'assistant', content: [{ type: 'output_text',
      text: selfCorrection
        ? 'Je hebt gelijk. Je bevestigde mijn melding over iemand met een pakketje aan de voordeur; ik koppelde dat daarna ten onrechte aan G5 en Jasmin.'
        : 'Klopt — de persoon met het pakketje aan de voordeur was dus een bezorger.',
    }] }]);
  };

  let confirmationTurn;
  let correctionTurn;
  try {
    confirmationTurn = await __telegramTransportTest.prepareAuthenticatedTelegramTurnForTest({
      ctx: {
        chat: { id: Number(chatId) }, from: { id: Number(chatId), first_name: 'Jaap' },
        message: { message_id: 4350, date: 1_786_724_694, text: confirmation },
      },
      sender: 'Jaap', text: confirmation,
    });
    archiveTelegramMessage({
      chatId, messageId: '4351', ts: '2026-08-14T16:25:03.000Z',
      sender: 'Eva', role: 'assistant', kind: 'text',
      content: badReply, replyToMessageId: '4350',
    });
    const repliedMessage = {
      message_id: 4351, date: 1_786_724_703, text: badReply,
      from: { id: 999, first_name: 'Eva', is_bot: true },
    };
    correctionTurn = await __telegramTransportTest.prepareAuthenticatedTelegramTurnForTest({
      ctx: {
        chat: { id: Number(chatId) }, from: { id: Number(chatId), first_name: 'Jaap' },
        message: {
          message_id: 4352, date: 1_786_724_738, text: correction,
          reply_to_message: repliedMessage,
        },
      },
      sender: 'Jaap', text: correction, agentText: correctionAgentText,
      replyTo: {
        channel: 'telegram', chatId, messageId: '4351', photoMessageId: '4351',
        text: repliedMessage.text,
        sender: 'Eva', isAssistant: true, kind: 'text',
      },
      extras: { replyToMessageId: '4351' },
    });
  } finally {
    globalThis.fetch = originalFetch;
  }

  const confirmationInputs = providerTurns.filter((item) => item.text === confirmation);
  const correctionInputs = providerTurns.filter((item) => item.text === correctionAgentText);
  const dialogueText = (item) => (item?.payload?.context?.dialogueExcerpts || [])
    .map((row) => String(row.text || '')).join('\n');
  assert.equal(confirmationInputs.length, 2, JSON.stringify({ confirmationTurn, providerTurns }));
  assert.match(dialogueText(confirmationInputs[0]),
    /Er staat iemand aan de voordeur met een pakketje of zak in de hand\./u);
  assert.match(dialogueText(confirmationInputs[0]), /Telegram-bericht 4347/u);
  assert.match(confirmationInputs[1].instructions,
    /unbound_persistent_memory_reference/u);
  assert.deepEqual(confirmationTurn.tools, []);
  assert.match(confirmationTurn.reply, /pakketje.*voordeur.*bezorger/iu);

  assert.equal(correctionInputs.length, 1, JSON.stringify({ correctionTurn, providerTurns }));
  assert.match(dialogueText(correctionInputs[0]),
    /Helder, bij G5 tuin was het een bezorger — niet jij en niet Jasmin\./u);
  assert.match(dialogueText(correctionInputs[0]),
    /Er staat iemand aan de voordeur met een pakketje of zak in de hand\./u);
  assert.ok(composerInputs.some((input) => input.includes(correction)
    && input.includes(badReply)
    && input.includes('Er staat iemand aan de voordeur met een pakketje of zak in de hand.')),
  JSON.stringify(composerInputs));
  assert.ok(composerInputs.every((input) => !input.includes(staleLocalAssistant)),
    'een vol canoniek Telegramvenster mag geen oude lokale assistantregel bijmengen');
  assert.match(correctionTurn.reply, /je hebt gelijk/iu);
  assert.match(correctionTurn.reply, /pakketje.*voordeur/iu);
  assert.match(correctionTurn.reply, /ten onrechte.*G5.*Jasmin/iu);
  assert.doesNotMatch(correctionTurn.reply, /wat bedoel je|kun je verduidelijken/iu);
  assert.equal(db.prepare('SELECT COUNT(*) AS count FROM memories WHERE content=?')
    .get(vagueMemory).count, 0);
  assert.equal(db.prepare(`SELECT COUNT(*) AS count FROM messages
    WHERE conversation_id=? AND sender='Eva'`).get(conversationId).count, 1);
});
