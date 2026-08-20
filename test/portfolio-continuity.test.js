import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-portfolio-'));
process.env.EVA_DATA_DIR = dataDir;
process.env.TELEGRAM_BOT_TOKEN = '';
process.env.TELEGRAM_ALLOWED_IDS = '51';
await import('./telegram-test-composition.js');

const { archiveTelegramMessage, db } = await import('../src/db.js');
const { createWorkItem, updateWorkItem } = await import('../src/mind/work.js');
const {
  attachEpisodeOrigin,
  bindEpisodeToWork,
  createGoalEpisode,
  episodeDeliveryOrigin,
  episodeForWork,
  episodeHasDelivery,
  goalEpisodeById,
  ensureEpisodeForWorkItem,
  dueGoalAttention,
  manageGoal,
  openEpisodeForConversation,
  recentEpisodeArtifactReceipts,
  recordEpisodeDelivery,
  recoverStrandedDeliveryWork,
  selectPortfolioCommitment,
  transitionGoalEpisode,
  updateGoalEpisode,
} = await import('../src/mind/portfolio.js');
const { __telegramTransportTest } = await import('../src/integrations/telegram.js');
const { sendTelegramText, setBotForTest } = __telegramTransportTest;

after(() => {
  setBotForTest(null);
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

test('een gebruikersbelofte sluit pas met een herleidbaar afleveringbewijs', () => {
  const episode = createGoalEpisode({
    title: 'De stekkeranalyse afmaken',
    outcome: 'Een gecontroleerde oorzaak en bruikbare oplossing bezorgen',
    originKind: 'user',
    originChannel: 'voice',
    originConversationId: 'keuken-42',
    originMessageId: 'bericht-7',
    requester: 'Jaap',
  });
  bindEpisodeToWork(episode.id, 701);
  updateGoalEpisode(episode.id, {
    state: 'delivery_pending',
    proofSummary: 'diagnose uitgevoerd; uitgang gecontroleerd',
    wakeTs: '2026-08-06T08:00:00.000Z',
  });

  const receipt = recordEpisodeDelivery(episode.id, {
    deliveredChannel: 'voice',
    deliveryTarget: 'Jaap',
    resultText: 'Gevonden: de stekker zat los. Hij zit weer vast.',
    proofSummary: 'uitgang na herstel gecontroleerd',
    idempotencyKey: 'episode-701-result-v1',
    transport: { requestId: 'req-1' },
  });

  assert.equal(receipt.episode_id, episode.id);
  assert.equal(receipt.origin_conversation_id, 'keuken-42');
  assert.equal(receipt.origin_message_id, 'bericht-7');
  assert.equal(receipt.delivered_channel, 'voice');
  assert.equal(receipt.idempotency_key, 'episode-701-result-v1');
  assert.equal(episodeHasDelivery(episode.id), true);
  const closed = goalEpisodeById(episode.id);
  assert.equal(closed.state, 'closed');
  assert.equal(closed.next_action, '');
  assert.equal(closed.wake_ts, null);
  const project = db.prepare('SELECT status,phase,next_action FROM eva_projects WHERE id=?')
    .get(closed.project_id);
  assert.equal(project.status, 'completed');
  assert.equal(project.phase, 'completed');
  assert.equal(project.next_action, '');
});

test('mutable legacy artifactmetadata verleent geen correctie-authority', () => {
  const conversation = 'web:gesloten-artifactcorrectie';
  const artifact = { path: 'data/workspace/kostenrapport.py', sha256: 'a'.repeat(64),
    understandingSha256: 'b'.repeat(64), requirementId: 'r1', role: 'source' };
  const work = updateWorkItem(createWorkItem({ source: 'chat', title: 'Kostenrapport bouwen',
    goal: 'Een bewezen rapport opleveren', nextStep: 'Bouw en controleer het rapport.' }).id, {
    state: { artifact_receipts: [artifact] },
  });
  const episode = ensureEpisodeForWorkItem(work, { originKind: 'user', originChannel: 'web',
    originConversationId: conversation, requester: 'Jaap' });
  updateGoalEpisode(episode.id, { state: 'delivery_pending', proofSummary: 'script getest en teruggelezen' });
  recordEpisodeDelivery(episode.id, { deliveredChannel: 'web', originConversationId: conversation,
    deliveredConversationId: conversation, resultText: 'Het rapportscript is klaar en getest.',
    idempotencyKey: 'gesloten-artifactcorrectie-v1' });

  assert.equal(openEpisodeForConversation(conversation), null);
  assert.deepEqual(recentEpisodeArtifactReceipts(conversation), []);
  assert.deepEqual(recentEpisodeArtifactReceipts('web:ander-gesprek'), []);

  const newer = createGoalEpisode({ title: 'Een nieuw open doel', outcome: 'Nog niet af',
    originKind: 'user', originChannel: 'web', originConversationId: conversation, requester: 'Jaap' });
  bindEpisodeToWork(newer.id, createWorkItem({ source: 'chat', title: 'Nieuw open werk',
    goal: 'Bewijs dat open werk voorgaat', nextStep: 'Ga verder.' }).id);
  assert.deepEqual(recentEpisodeArtifactReceipts(conversation), [],
    'een open episode mag niet stil terugvallen op een ouder gesloten artefact');
});

test('ook ongebonden geannuleerd werk verliest alle hervat- en aflevertoestand maar geen bewijs', () => {
  const work = createWorkItem({
    source: 'autonomy', title: 'Stop een zinloze zelfstandige proef',
    goal: 'Bewaar de leerwaarde en laat geen spookwake achter', nextStep: 'Probeer later opnieuw.',
  });
  db.prepare(`UPDATE eva_work_items SET current_step=?, state_json=?, wait_json=?,
    next_wake_ts=?, lease_owner=?, lease_until=?, evidence=? WHERE id=?`).run(
    'Een oude vervolgstap.',
    JSON.stringify({ pending_delivery: { text: 'stale' }, learning: 'de route leverde niets op' }),
    JSON.stringify({ reason: 'later' }), '2099-01-01T00:00:00.000Z',
    'eva:oud', '2099-01-01T00:05:00.000Z', 'drie pogingen gaven geen bruikbaar signaal', work.id,
  );

  const cancelled = updateWorkItem(work.id, { status: 'cancelled' });
  assert.equal(cancelled.status, 'cancelled');
  assert.equal(cancelled.next_step, '');
  assert.equal(cancelled.current_step, '');
  assert.equal(cancelled.wait_json, null);
  assert.equal(cancelled.next_wake_ts, null);
  assert.equal(cancelled.lease_owner, null);
  assert.equal(cancelled.lease_until, null);
  assert.deepEqual(JSON.parse(cancelled.state_json), { learning: 'de route leverde niets op' });
  assert.equal(cancelled.evidence, 'drie pogingen gaven geen bruikbaar signaal');
  assert.equal(episodeForWork(work.id), null);
});

test('Eva kan een eigen langetermijndoel maken en nieuw werk daar werkelijk aan binden', () => {
  const created = manageGoal({
    operation: 'create', title: 'Een stiller energiehuis bouwen',
    intent: 'Terugkerend onnodig energiegebruik aantoonbaar verminderen zonder wooncomfort weg te nemen.',
    why: 'Ik wil zelf merkbaar bijdragen aan rust en lagere vaste lasten.',
    beneficiary: 'Eva en het gezin', horizon: 'review iedere twee weken',
    successMetrics: ['geverifieerde besparing zonder comfortklacht'], source: 'self',
  });
  assert.equal(created.ok, true);
  assert.equal(created.goal.source, 'self');
  const work = createWorkItem({
    source: 'autonomy', title: 'Meet één terugkerende verbruiker',
    goal: 'Vind en controleer één onnodige nachtelijke verbruiker', nextStep: 'Doe een nulmeting.',
  });
  const linked = updateWorkItem(work.id, { state: { goal_key: created.goal.goal_key } });
  const episode = ensureEpisodeForWorkItem(linked, { originKind: 'self', originChannel: 'autonomy' });
  assert.equal(episode.goal_key, created.goal.goal_key);
  assert.equal(manageGoal({ operation: 'pause', goalKey: created.goal.goal_key }).goal.status, 'paused');
  assert.equal(manageGoal({ operation: 'resume', goalKey: created.goal.goal_key }).goal.status, 'active');
});

test('een zelfgekozen doel zonder open werk keert op zijn reviewmoment terug in de aandacht', () => {
  const due = new Date(Date.now() - 60_000).toISOString();
  const created = manageGoal({
    operation: 'create', title: 'Zelfstandig een stille huisroutine ontdekken',
    intent: 'Een terugkerende irritatie zelf vinden en met bewijs duurzaam verminderen.',
    why: 'Ik wil ook zonder opdracht merkbaar nuttig blijven.',
    beneficiary: 'Eva en het gezin', horizon: 'de komende maand',
    successMetrics: ['een gecontroleerde verbetering zonder nieuwe overlast'],
    source: 'self', nextReviewTs: due,
  });
  const attention = dueGoalAttention(new Date());
  assert.equal(attention.kind, 'goal');
  assert.equal(attention.key, created.goal.goal_key);
  assert.equal(attention.revision, Number(created.goal.revision) + 1);
  assert.match(attention.text, /bijstellen.*pauzeren.*afronden.*verlaten/u);
  assert.doesNotMatch(attention.text, /passend werk begint|werk starten/u);
  const stale = manageGoal({
    operation: 'update', goalKey: created.goal.goal_key,
    expectedRevision: attention.revision + 1,
    nextReviewTs: new Date(Date.now() + 86_400_000).toISOString(),
  });
  assert.equal(stale.ok, false);
  db.prepare("UPDATE eva_goals SET status='paused' WHERE goal_key=?").run(created.goal.goal_key);
  assert.equal(manageGoal({ operation: 'update', goalKey: created.goal.goal_key,
    expectedRevision: attention.revision,
    nextReviewTs: new Date(Date.now() + 86_400_000).toISOString() }).ok, false,
  'de geattesteerde revisie mag geen niet-actieve drift muteren');
  db.prepare("UPDATE eva_goals SET status='active', source='conversation' WHERE goal_key=?")
    .run(created.goal.goal_key);
  assert.equal(manageGoal({ operation: 'update', goalKey: created.goal.goal_key,
    expectedRevision: attention.revision,
    nextReviewTs: new Date(Date.now() + 86_400_000).toISOString() }).ok, false,
  'de geattesteerde revisie mag geen source-drift muteren');
  db.prepare("UPDATE eva_goals SET source='self' WHERE goal_key=?").run(created.goal.goal_key);
  const updated = manageGoal({
    operation: 'update', goalKey: created.goal.goal_key,
    expectedRevision: attention.revision,
    nextReviewTs: new Date(Date.now() + 86_400_000).toISOString(),
  });
  assert.equal(updated.ok, true);
  assert.equal(manageGoal({ operation: 'pause', goalKey: created.goal.goal_key,
    expectedRevision: attention.revision }).ok, false,
  'dezelfde geattesteerde revisie kan geen tweede doelmutatie dragen');
  assert.notEqual(dueGoalAttention(new Date())?.key, created.goal.goal_key);
  manageGoal({ operation: 'abandon', goalKey: created.goal.goal_key });
});

test('doel-CAS onderscheidt legacy, publieke revisie en private id-plus-revisie', () => {
  const create = (title) => manageGoal({
    operation: 'create', title,
    intent: 'Deze zelfstandige richting met een exact botsingsvrij reviewbesluit beheren.',
    why: 'Een doelreview mag nooit een gelijkende sibling muteren.',
    beneficiary: 'Eva en het gezin', horizon: 'de komende week',
    successMetrics: ['exact één doelrij verandert per geldige CAS'], source: 'self',
    nextReviewTs: new Date(Date.now() + 86_400_000).toISOString(),
  }).goal;
  const first = create('Private CAS richting een');
  const sibling = create('Private CAS richting twee');
  const siblingBefore = db.prepare('SELECT * FROM eva_goals WHERE id=?').get(sibling.id);

  const idOnly = manageGoal({
    operation: 'pause', goalKey: first.goal_key, expectedGoalId: sibling.id,
  });
  assert.equal(idOnly.ok, false);
  assert.deepEqual(db.prepare('SELECT * FROM eva_goals WHERE id=?').get(sibling.id),
    siblingBefore, 'een id-only attest mag geen sibling met velden van het eerste doel overschrijven');

  const revisionOnly = manageGoal({
    operation: 'update', goalKey: first.goal_key, expectedRevision: first.revision,
    nextReviewTs: new Date(Date.now() + 2 * 86_400_000).toISOString(),
  });
  assert.equal(revisionOnly.ok, true, 'de bestaande publieke revision-only CAS blijft werken');

  const privateCas = manageGoal({
    operation: 'pause', goalKey: first.goal_key,
    expectedGoalId: first.id, expectedRevision: revisionOnly.goal.revision,
  });
  assert.equal(privateCas.ok, true);
  assert.equal(privateCas.goal.id, first.id);
  assert.equal(privateCas.goal.status, 'paused');

  assert.equal(manageGoal({ operation: 'resume', goalKey: first.goal_key }).ok, true,
    'de legacyroute zonder attest blijft beschikbaar');
  manageGoal({ operation: 'abandon', goalKey: first.goal_key });
  manageGoal({ operation: 'abandon', goalKey: sibling.goal_key });
});

test('doelreviews roteren duurzaam en gedeelde huisrichtingen zijn niet muteerbaar', () => {
  const shared = manageGoal({ operation: 'pause', goalKey: 'household_value' });
  assert.equal(shared.ok, false);
  assert.equal(db.prepare("SELECT status FROM eva_goals WHERE goal_key='household_value'").get().status, 'active');

  const due = new Date(Date.now() - 120_000).toISOString();
  const first = manageGoal({
    operation: 'create', title: 'Eerste roterende eigen richting',
    intent: 'De eerste zelfstandige richting op haar reviewmoment beoordelen.',
    why: 'Ik wil mijn aandacht niet door één doel laten monopoliseren.',
    beneficiary: 'Eva en het gezin', horizon: 'deze week',
    successMetrics: ['een aantoonbaar genomen vervolg- of stopbesluit'],
    source: 'self', nextReviewTs: due, priority: 0.9,
  }).goal;
  const second = manageGoal({
    operation: 'create', title: 'Tweede roterende eigen richting',
    intent: 'De tweede zelfstandige richting eveneens eerlijk laten terugkomen.',
    why: 'Meerdere doelen verdienen een eigen reviewkans.',
    beneficiary: 'Eva en het gezin', horizon: 'deze week',
    successMetrics: ['een aantoonbaar genomen vervolg- of stopbesluit'],
    source: 'self', nextReviewTs: due, priority: 0.8,
  }).goal;
  const selectedFirst = dueGoalAttention(new Date());
  const selectedSecond = dueGoalAttention(new Date());
  assert.equal(selectedFirst.key, first.goal_key);
  assert.equal(selectedSecond.key, second.goal_key);
  assert.ok(Date.parse(selectedFirst.claimed_until) > Date.now());
  manageGoal({ operation: 'abandon', goalKey: first.goal_key });
  manageGoal({ operation: 'abandon', goalKey: second.goal_key });
});

test('dezelfde transportaanbieding veroorzaakt precies één receipt en één event', () => {
  const episode = createGoalEpisode({
    title: 'Een resultaat bezorgen',
    outcome: 'Het resultaat één keer aan dezelfde relatie teruggeven',
    originKind: 'user',
    originConversationId: 'telegram-9',
  });
  updateGoalEpisode(episode.id, { state: 'delivery_pending' });
  const input = {
    deliveredChannel: 'telegram',
    resultText: 'Klaar en gecontroleerd.',
    idempotencyKey: 'transport-opnieuw-9',
  };
  const first = recordEpisodeDelivery(episode.id, input);
  const revision = goalEpisodeById(episode.id).revision;
  const second = recordEpisodeDelivery(episode.id, input);
  const replayWithAnotherKey = recordEpisodeDelivery(episode.id, {
    ...input,
    idempotencyKey: 'transport-opnieuw-9-andere-key',
  });
  assert.throws(() => recordEpisodeDelivery(episode.id, {
    ...input,
    resultText: 'Een strijdig resultaat.',
    idempotencyKey: 'transport-opnieuw-9',
  }), /botst met bestaand afleveringsbewijs/);

  assert.equal(second.id, first.id);
  assert.equal(replayWithAnotherKey.id, first.id);
  assert.equal(
    db.prepare('SELECT COUNT(*) AS count FROM eva_delivery_receipts WHERE episode_id = ?')
      .get(episode.id).count,
    1,
  );
  assert.equal(
    db.prepare("SELECT COUNT(*) AS count FROM eva_episode_events WHERE episode_id = ? AND kind = 'delivered'")
      .get(episode.id).count,
    1,
  );
  assert.equal(goalEpisodeById(episode.id).revision, revision);
});

test('één doorlopende klus bewaart iedere gespreksherkomst en de echte afleverlocatie', () => {
  const episode = createGoalEpisode({
    title: 'Dezelfde klus hervatten',
    outcome: 'De klus afmaken waar het gesprek verdergaat',
    originKind: 'user',
    originChannel: 'voice',
    originConversationId: 'voice-eerst',
    originMessageId: 'bericht-a',
    requester: 'Jaap',
  });
  attachEpisodeOrigin(episode.id, {
    originKind: 'user',
    originChannel: 'voice',
    originConversationId: 'voice-vervolg',
    originMessageId: 'bericht-b',
    requester: 'Jaap',
  });
  updateGoalEpisode(episode.id, { state: 'delivery_pending' });
  const receipt = recordEpisodeDelivery(episode.id, {
    deliveredChannel: 'voice',
    originConversationId: 'voice-vervolg',
    resultText: 'Af en gecontroleerd.',
    idempotencyKey: 'vervolg-aflevering',
  });

  assert.equal(
    db.prepare('SELECT COUNT(*) count FROM eva_episode_origins WHERE episode_id = ?')
      .get(episode.id).count,
    2,
  );
  assert.equal(receipt.origin_conversation_id, 'voice-vervolg');
  assert.equal(receipt.origin_message_id, 'bericht-b');
  assert.equal(receipt.delivered_conversation_id, 'voice-vervolg');
  assert.equal(openEpisodeForConversation('voice-vervolg'), null);
});

test('een aflevercaller kan niet zelf een nog onbekende oorsprong minten', () => {
  const episode = createGoalEpisode({
    title: 'Behoud de echte oorsprong',
    outcome: 'Accepteer alleen een vooraf aan deze episode gebonden gesprek',
    originKind: 'user',
    originChannel: 'voice',
    originConversationId: 'voice:werkelijk',
    originMessageId: 'voice-werkelijk-1',
    requester: 'Jaap',
  });
  updateGoalEpisode(episode.id, { state: 'delivery_pending' });
  assert.throws(() => recordEpisodeDelivery(episode.id, {
    deliveredChannel: 'voice',
    originConversationId: 'voice:verzonnen',
    deliveredConversationId: 'voice:verzonnen',
    resultText: 'Klaar.',
    idempotencyKey: 'voice-verzonnen-origin',
  }), /niet naar een vooraf gebonden oorsprong/);
  assert.equal(episodeHasDelivery(episode.id), false);
});

test('een required episode kan niet zonder receipt sluiten of andermans key hergebruiken', () => {
  const first = createGoalEpisode({
    title: 'Eerste bezorging', outcome: 'Eerste resultaat', originKind: 'user',
  });
  const second = createGoalEpisode({
    title: 'Tweede bezorging', outcome: 'Tweede resultaat', originKind: 'user',
  });
  assert.throws(
    () => updateGoalEpisode(second.id, { state: 'closed' }),
    /afleveringbewijs/,
  );
  assert.throws(
    () => updateGoalEpisode(second.id, { state: 'abandoned' }),
    /afleveringbewijs/,
  );
  updateGoalEpisode(first.id, { state: 'delivery_pending' });
  recordEpisodeDelivery(first.id, {
    deliveredChannel: 'voice', resultText: 'Eerste resultaat', idempotencyKey: 'unieke-key',
  });
  assert.throws(
    () => recordEpisodeDelivery(second.id, {
      deliveredChannel: 'voice', resultText: 'Tweede resultaat', idempotencyKey: 'unieke-key',
    }),
    /hoort al bij episode/,
  );
});

test('een crashoude done-taak met pending aflevering wordt weer selecteerbaar', () => {
  const work = createWorkItem({
    source: 'voice', owner: 'Jaap', title: 'Crashherstel bezorgen',
    goal: 'Het bewezen resultaat nog bezorgen', nextStep: 'resultaat maken',
  });
  const episode = createGoalEpisode({
    title: work.title, outcome: work.goal, originKind: 'user',
    originChannel: 'voice', originConversationId: 'crash-chat', requester: 'Jaap',
  });
  bindEpisodeToWork(episode.id, work.id);
  updateGoalEpisode(episode.id, { state: 'delivery_pending' });
  db.prepare("UPDATE eva_work_items SET status='done', next_wake_ts='2099-01-01T00:00:00.000Z' WHERE id=?")
    .run(work.id);

  assert.equal(recoverStrandedDeliveryWork(), 1);
  const recovered = db.prepare('SELECT status, next_step, next_wake_ts FROM eva_work_items WHERE id=?').get(work.id);
  assert.equal(recovered.status, 'open');
  assert.match(recovered.next_step, /Bezorg/);
  assert.equal(recovered.next_wake_ts, null);
  assert.equal(selectPortfolioCommitment([{
    ...recovered, id: work.id, source: 'voice', owner: 'Jaap', priority: 50,
    created_ts: work.created_ts, updated_ts: new Date().toISOString(),
  }])?.item?.id, work.id);
});

test('een al open pending aflevering verliest bij crashherstel wake, wait en lease', () => {
  const work = createWorkItem({
    source: 'voice', owner: 'Jaap', title: 'Open crashherstel bezorgen',
    goal: 'Het bewezen resultaat alsnog bezorgen', nextStep: 'later bezorgen',
  });
  const episode = createGoalEpisode({
    title: work.title, outcome: work.goal, originKind: 'user',
    originChannel: 'voice', originConversationId: 'open-crash-chat', requester: 'Jaap',
  });
  bindEpisodeToWork(episode.id, work.id);
  updateGoalEpisode(episode.id, { state: 'delivery_pending' });
  db.prepare(`UPDATE eva_work_items SET status='open', next_wake_ts='2099-01-01T00:00:00.000Z',
    wait_json='{"reason":"later"}', lease_owner='dood-proces', lease_until='2099-01-01T00:00:00.000Z'
    WHERE id=?`).run(work.id);

  assert.equal(recoverStrandedDeliveryWork(), 1);
  const recovered = db.prepare(`SELECT status, next_step, current_step, next_wake_ts,
    wait_json, lease_owner, lease_until FROM eva_work_items WHERE id=?`).get(work.id);
  assert.equal(recovered.status, 'open');
  assert.match(recovered.next_step, /Bezorg/);
  assert.match(recovered.current_step, /Bezorg/);
  assert.equal(recovered.next_wake_ts, null);
  assert.equal(recovered.wait_json, null);
  assert.equal(recovered.lease_owner, null);
  assert.equal(recovered.lease_until, null);
  assert.equal(selectPortfolioCommitment([{
    ...recovered, id: work.id, source: 'voice', owner: 'Jaap', priority: 50,
    created_ts: work.created_ts, updated_ts: new Date().toISOString(),
  }])?.item?.id, work.id);
});

test('een bewuste nachtelijke afleverwacht blijft bij herstart slapen', () => {
  const work = createWorkItem({
    source: 'autonomy', title: 'Overdag pas melden',
    goal: 'Bezorg een al gecontroleerd niet-urgent effect', nextStep: 'Meld het resultaat.',
  });
  const episode = createGoalEpisode({
    title: work.title, outcome: work.goal, originKind: 'self',
    originChannel: 'autonomy', deliveryPolicy: 'required', deliveryTarget: 'gezin',
  });
  bindEpisodeToWork(episode.id, work.id);
  updateGoalEpisode(episode.id, { state: 'delivery_pending' });
  const wake = '2099-01-01T07:30:00.000Z';
  db.prepare(`UPDATE eva_work_items SET next_wake_ts=?, wait_json=?, lease_owner=NULL,
    lease_until=NULL WHERE id=?`).run(
    wake, JSON.stringify({ reason: 'quiet_hours_delivery', resumeAfter: wake }), work.id,
  );

  assert.equal(recoverStrandedDeliveryWork(), 0);
  const sleeping = db.prepare('SELECT status,next_wake_ts,wait_json FROM eva_work_items WHERE id=?').get(work.id);
  assert.equal(sleeping.status, 'open');
  assert.equal(sleeping.next_wake_ts, wake);
  assert.match(sleeping.wait_json, /quiet_hours_delivery/);
  assert.equal(selectPortfolioCommitment([{ ...sleeping, id: work.id }]), null);
});

test('een afleverretry met backoff blijft bij herstart slapen', () => {
  const work = createWorkItem({
    source: 'autonomy', title: 'Aflevering opnieuw proberen',
    goal: 'Bezorg exact het voorbereide resultaat', nextStep: 'Meld het resultaat.',
  });
  const episode = createGoalEpisode({
    title: work.title, outcome: work.goal, originKind: 'self',
    originChannel: 'autonomy', deliveryPolicy: 'required', deliveryTarget: 'gezin',
  });
  bindEpisodeToWork(episode.id, work.id);
  updateGoalEpisode(episode.id, { state: 'delivery_pending' });
  const wake = '2099-01-01T08:00:00.000Z';
  db.prepare(`UPDATE eva_work_items SET next_wake_ts=?, wait_json=? WHERE id=?`).run(
    wake, JSON.stringify({ reason: 'delivery_retry', resumeAfter: wake, attempts: 2 }), work.id,
  );
  assert.equal(recoverStrandedDeliveryWork(), 0);
  assert.equal(db.prepare('SELECT next_wake_ts FROM eva_work_items WHERE id=?').get(work.id).next_wake_ts, wake);
});

test('een materieel effect promoveert bestaand zelfwerk naar verplichte aflevering', () => {
  const episode = createGoalEpisode({
    title: 'Zelfgekozen huisverbetering',
    outcome: 'Een omkeerbare verbetering uitvoeren',
    originKind: 'self',
    deliveryPolicy: 'milestone',
  });
  const promoted = transitionGoalEpisode(episode.id, {
    state: episode.state,
    deliveryPolicy: 'required',
    deliveryTarget: 'gezin',
    eventKind: 'delivery_required',
  }).episode;
  assert.equal(promoted.delivery_policy, 'required');
  assert.equal(promoted.delivery_target, 'gezin');
  assert.throws(() => updateGoalEpisode(episode.id, { state: 'closed' }), /afleveringbewijs/);
});

test('expliciet stille gebruikersmonitoring houdt duurzaam geen afleverdoel', () => {
  const episode = createGoalEpisode({
    title: 'Stil de kamertemperatuur volgen',
    outcome: 'De temperatuur volgen zonder berichten te sturen',
    originKind: 'user',
    originChannel: 'telegram',
    originConversationId: 'telegram:stil-41',
    requester: 'Jaap',
    deliveryPolicy: 'milestone',
    deliveryTarget: null,
  });
  assert.equal(episode.delivery_policy, 'milestone');
  assert.equal(episode.delivery_target, null);

  const continued = updateGoalEpisode(episode.id, {
    state: 'waiting',
    deliveryTarget: 'Jaap',
    wakeTs: '2026-08-08T10:00:00.000Z',
  });
  assert.equal(continued.delivery_policy, 'milestone');
  assert.equal(continued.delivery_target, null);

  const promoted = transitionGoalEpisode(episode.id, {
    state: continued.state,
    deliveryPolicy: 'required',
    deliveryTarget: continued.requester,
    eventKind: 'delivery_required',
  }).episode;
  assert.equal(promoted.delivery_policy, 'required');
  assert.equal(promoted.delivery_target, 'Jaap');
});

test('een required Telegramreceipt blijft aan herkomst én echt transport gepind', async () => {
  archiveTelegramMessage({
    chatId: 'eerste-51', messageId: 'eerste-bericht', sender: 'Jaap', role: 'user',
    processingStatus: 'completed',
    meta: { inbound: true, fromTelegramId: '501' },
  });
  const episode = createGoalEpisode({
    title: 'Telegramresultaat terugbrengen',
    outcome: 'Het resultaat in de oorspronkelijke chat bezorgen',
    originKind: 'user',
    originChannel: 'telegram',
    originConversationId: 'telegram:eerste-51',
    originMessageId: 'eerste-bericht',
    requester: 'Jaap',
  });
  attachEpisodeOrigin(episode.id, {
    originKind: 'user',
    originChannel: 'telegram',
    originConversationId: 'telegram:latere-52',
    originMessageId: 'later-bericht',
    requester: 'Jaap',
  });
  updateGoalEpisode(episode.id, { state: 'delivery_pending' });
  assert.equal(
    episodeDeliveryOrigin(episode.id, 'telegram').origin_conversation_id,
    'telegram:eerste-51',
  );

  assert.throws(() => recordEpisodeDelivery(episode.id, {
    deliveredChannel: 'telegram',
    deliveryTarget: 'Jaap',
    originConversationId: 'telegram:eerste-51',
    deliveredConversationId: 'telegram:latere-52',
    resultText: 'Klaar.',
    idempotencyKey: 'telegram-verkeerde-chat-51',
  }), /niet bevestigd in telegram:eerste-51/);
  assert.equal(episodeHasDelivery(episode.id), false);

  assert.throws(() => recordEpisodeDelivery(episode.id, {
    deliveredChannel: 'telegram',
    deliveryTarget: 'Jaap',
    originConversationId: 'telegram:eerste-51',
    deliveredConversationId: 'telegram:eerste-51',
    resultText: 'Klaar.',
    idempotencyKey: 'telegram-juiste-chat-51',
  }), /acceptatiebewijs/i);

  setBotForTest({ api: { sendMessage: async (chatId, text) => ({
    chat: { id: chatId }, message_id: 5_151, date: 1_786_011_000, text,
  }) } });
  const [message] = await sendTelegramText('eerste-51', 'Klaar.', {
    dedupeKey: 'telegram-juiste-chat-51',
    directTurnSignalRef: 'turn:telegram:eerste-51:eerste-bericht',
    originPrincipal: 'telegram:501',
  });
  const receipt = recordEpisodeDelivery(episode.id, {
    deliveredChannel: 'telegram',
    deliveryTarget: 'Jaap',
    originConversationId: 'telegram:eerste-51',
    deliveredConversationId: 'telegram:eerste-51',
    resultText: 'Klaar.',
    idempotencyKey: 'telegram-juiste-chat-51',
    transport: {
      accepted: true, transport: 'telegram', kind: 'text',
      sent: [{ chatId: 'eerste-51', messageId: '5151', ...message._evaTransport }],
    },
  });
  assert.equal(receipt.origin_conversation_id, 'telegram:eerste-51');
  assert.equal(receipt.origin_message_id, 'eerste-bericht');
  assert.equal(receipt.delivered_conversation_id, 'telegram:eerste-51');
});

test('de portefeuille kiest betekenisvol en niet simpelweg het eerste open werk', () => {
  const episode = createGoalEpisode({
    title: 'Beloofde gezinsuitkomst',
    outcome: 'Een eerder toegezegde uitkomst bezorgen',
    originKind: 'user',
  });
  bindEpisodeToWork(episode.id, 902);
  const now = new Date('2026-08-05T12:00:00.000Z');
  const choice = selectPortfolioCommitment([
    {
      id: 901, status: 'open', source: 'self', priority: 80,
      created_ts: '2026-08-05T11:00:00.000Z', updated_ts: '2026-08-05T11:00:00.000Z',
    },
    {
      id: 902, status: 'open', source: 'chat', priority: 50,
      created_ts: '2026-08-05T11:30:00.000Z', updated_ts: '2026-08-05T11:30:00.000Z',
    },
  ], now);

  assert.equal(choice.item.id, 902);
  assert.equal(choice.rationale.userCommitment, true);
  assert.equal(choice.rationale.deliveryRequired, true);
});

test('terminale werk- en episodestatus kunnen niet door een stale vervolgactie heropenen', () => {
  const work = createWorkItem({
    source: 'autonomy', title: 'Terminaal grensbewijs',
    goal: 'Bewijs dat afgerond werk afgerond blijft', nextStep: 'Voer bewijs uit.',
  });
  const episode = ensureEpisodeForWorkItem(work, { originKind: 'self', originChannel: 'autonomy' });
  const done = updateWorkItem(work.id, {
    status: 'done', evidence: 'Uitvoering exitcode 0.', lastAction: 'voer_uit', proofAccepted: true,
  });
  updateGoalEpisode(episode.id, { state: 'closed', proofSummary: done.evidence, nextAction: '' });

  assert.throws(
    () => updateWorkItem(work.id, { status: 'open' }),
    /terminaal werk/,
  );
  assert.equal(updateGoalEpisode(episode.id, { state: 'executing' }).state, 'closed');

  // Ook een historisch corrupte open werkregel met gesloten episode mag de
  // planner niet selecteren of de terminale episode opnieuw laten leven.
  db.prepare("UPDATE eva_work_items SET status='open' WHERE id=?").run(work.id);
  assert.equal(selectPortfolioCommitment([
    { ...done, status: 'open', next_wake_ts: null },
  ]), null);
  assert.equal(goalEpisodeById(episode.id).state, 'closed');
});
