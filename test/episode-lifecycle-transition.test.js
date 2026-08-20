import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-episode-lifecycle-'));
process.env.EVA_DATA_DIR = dataDir;

const { db } = await import('../src/db.js');
const { createWorkItem } = await import('../src/mind/work.js');
const {
  EPISODE_TRANSITIONS,
  bindEpisodeToWork,
  createGoalEpisode,
  goalEpisodeById,
  recordEpisodeDelivery,
  recoverStrandedDeliveryWork,
  transitionGoalEpisode,
  updateDurableWork,
} = await import('../src/mind/portfolio.js');

after(() => {
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

function linkedEpisode({ deliveryPolicy = 'milestone', originKind = 'self' } = {}) {
  const work = createWorkItem({
    source: originKind === 'user' ? 'voice' : 'autonomy',
    owner: originKind === 'user' ? 'Jaap' : null,
    title: `Lifecycle-proef ${Date.now()}-${Math.random()}`,
    goal: 'Bewijs dat episode en werk één levenscyclus delen.',
    nextStep: 'Voer de eerste stap uit.',
  });
  const episode = createGoalEpisode({
    title: work.title,
    outcome: work.goal,
    originKind,
    deliveryPolicy,
    requester: originKind === 'user' ? 'Jaap' : null,
  });
  return { work, episode: bindEpisodeToWork(episode.id, work.id) };
}

test('de transition table is expliciet en weigert een ongeldige sprong', () => {
  assert.deepEqual(EPISODE_TRANSITIONS.closed, ['closed']);
  assert.ok(EPISODE_TRANSITIONS.executing.includes('blocked'));
  assert.ok(!EPISODE_TRANSITIONS.delivery_pending.includes('executing'));

  const { episode } = linkedEpisode({ deliveryPolicy: 'required', originKind: 'user' });
  transitionGoalEpisode(episode.id, { state: 'delivery_pending' });
  assert.throws(
    () => transitionGoalEpisode(episode.id, { state: 'executing' }),
    /ongeldige episode-overgang/,
  );
  recordEpisodeDelivery(episode.id, {
    deliveredChannel: 'test', resultText: 'Transition table gecontroleerd.',
    idempotencyKey: `transition-table-cleanup-${episode.id}`,
  });
});

test('episode-overgang en werkprojectie wijzigen atomair', () => {
  const { work, episode } = linkedEpisode();
  const blocked = transitionGoalEpisode(episode.id, {
    state: 'blocked',
    nextAction: 'Wacht op een echte externe afhankelijkheid.',
    work: {
      status: 'blocked',
      nextStep: 'Wacht op een echte externe afhankelijkheid.',
      state: { blocked_dependency: 'netbeheerder' },
    },
  });
  assert.equal(blocked.episode.state, 'blocked');
  assert.equal(blocked.workItem.status, 'blocked');
  assert.equal(blocked.workItem.next_step, blocked.episode.next_action);

  const episodeRevision = blocked.episode.revision;
  assert.throws(
    () => transitionGoalEpisode(episode.id, {
      state: 'executing',
      nextAction: 'Dit mag niet half worden opgeslagen.',
      work: { expectedRevision: Number(blocked.workItem.revision) + 1 },
    }),
    /veranderde intussen/,
  );
  assert.equal(goalEpisodeById(episode.id).revision, episodeRevision);
  assert.equal(db.prepare('SELECT status FROM eva_work_items WHERE id=?').get(work.id).status, 'blocked');
});

test('de runtimewerkroute houdt checkpoint en episode gelijk en dedupe heropent niet', () => {
  const { work, episode } = linkedEpisode();
  const wakeTs = '2026-08-07T10:00:00.000Z';
  const updated = updateDurableWork(work.id, {
    status: 'blocked', nextStep: 'Wacht op de netbeheerder.',
    evidence: 'De aansluiting is gecontroleerd.', nextWakeTs: wakeTs,
  });
  const currentEpisode = goalEpisodeById(episode.id);
  assert.equal(updated.status, 'blocked');
  assert.equal(currentEpisode.state, 'blocked');
  assert.equal(currentEpisode.next_action, updated.next_step);
  assert.equal(currentEpisode.proof_summary, updated.evidence);
  assert.equal(currentEpisode.wake_ts, updated.next_wake_ts);

  const duplicate = createWorkItem({
    source: work.source, owner: work.owner, title: work.title,
    goal: 'Deze invoer mag gebonden werk niet stil heropenen.', nextStep: 'Foute heropening.',
  });
  assert.notEqual(duplicate.id, work.id);
  assert.equal(db.prepare('SELECT status FROM eva_work_items WHERE id=?').get(work.id).status, 'blocked');
});

test('required delivery is een invariant van de canonieke writer', () => {
  const { work, episode } = linkedEpisode({ deliveryPolicy: 'required', originKind: 'user' });
  transitionGoalEpisode(episode.id, {
    state: 'delivery_pending',
    proofSummary: 'Uitvoering gecontroleerd.',
  });
  assert.throws(
    () => transitionGoalEpisode(episode.id, { state: 'closed' }),
    /afleveringbewijs/,
  );
  assert.equal(goalEpisodeById(episode.id).state, 'delivery_pending');
  assert.equal(db.prepare('SELECT status FROM eva_work_items WHERE id=?').get(work.id).status, 'open');

  recordEpisodeDelivery(episode.id, {
    deliveredChannel: 'voice',
    resultText: 'Het gecontroleerde resultaat is bezorgd.',
    idempotencyKey: `delivery-${episode.id}`,
  });
  assert.equal(goalEpisodeById(episode.id).state, 'closed');
  assert.equal(db.prepare('SELECT status FROM eva_work_items WHERE id=?').get(work.id).status, 'done');
});

test('de generieke werkroute sluit ook milestonewerk nooit zonder bewijs', () => {
  const { work } = linkedEpisode();
  assert.throws(
    () => updateDurableWork(work.id, { status: 'done' }),
    /geaccepteerd en geverifieerd resultaatbewijs/,
  );
  assert.throws(
    () => updateDurableWork(work.id, {
      status: 'done', evidence: 'Ik zeg zelf dat het klaar is.', proofAccepted: false,
    }),
    /geaccepteerd en geverifieerd resultaatbewijs/,
  );
});

test('een transition-key herhaalt alleen exact dezelfde betekenis', () => {
  const { episode } = linkedEpisode();
  const key = `turn-transition-${episode.id}`;
  const first = transitionGoalEpisode(episode.id, {
    state: 'executing',
    nextAction: 'Eerste concrete stap.',
    idempotencyKey: key,
  });
  const replay = transitionGoalEpisode(episode.id, {
    state: 'executing',
    nextAction: 'Eerste concrete stap.',
    idempotencyKey: key,
  });
  assert.throws(() => transitionGoalEpisode(episode.id, {
    state: 'waiting',
    nextAction: 'Een andere opdracht mag niet onder dezelfde sleutel schuilen.',
    idempotencyKey: key,
  }), /al aan een andere overgang gebonden/);
  assert.equal(replay.replayed, true);
  assert.equal(replay.episode.state, 'executing');
  assert.equal(replay.episode.next_action, 'Eerste concrete stap.');
  assert.equal(replay.episode.revision, first.episode.revision);
  assert.equal(db.prepare(`SELECT COUNT(*) count FROM eva_episode_events
    WHERE episode_id=? AND transition_key=?`).get(episode.id, key).count, 1);
});

test('restart-reconciliatie herbouwt een corrupte pending werkprojectie idempotent', () => {
  const { work, episode } = linkedEpisode({ deliveryPolicy: 'required', originKind: 'user' });
  transitionGoalEpisode(episode.id, { state: 'delivery_pending' });
  db.prepare(`UPDATE eva_work_items SET status='done', next_step='foutief klaar',
    current_step='foutief klaar', lease_owner='dood-proces',
    lease_until='2099-01-01T00:00:00.000Z' WHERE id=?`).run(work.id);

  assert.equal(recoverStrandedDeliveryWork(), 1);
  const repaired = db.prepare(`SELECT status,next_step,lease_owner,lease_until
    FROM eva_work_items WHERE id=?`).get(work.id);
  assert.equal(repaired.status, 'open');
  assert.match(repaired.next_step, /Bezorg/);
  assert.equal(repaired.lease_owner, null);
  assert.equal(repaired.lease_until, null);
  assert.equal(recoverStrandedDeliveryWork(), 0);
});
