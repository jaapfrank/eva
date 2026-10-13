import './isolated-data-dir.js';

import assert from 'node:assert/strict';
import test from 'node:test';

import { addAspiration, db } from '../src/db.js';
import {
  __curiosityTest,
  dueAttention,
  explorationById,
  settleIssuedAspirationAttention,
  settleIssuedExplorationAttention,
  startExploration,
} from '../src/mind/curiosity.js';

const DAY_MS = 24 * 60 * 60_000;

function clearAttentionRows() {
  db.prepare('DELETE FROM eva_explorations').run();
  db.prepare('DELETE FROM aspirations').run();
}

function insertAspiration({
  id, kind = 'interest', lastTouched = null, cadenceHours = null, priority = 0.5,
}) {
  const created = new Date(Date.now() - 60 * DAY_MS).toISOString();
  db.prepare(`INSERT INTO aspirations
    (id,created_ts,text,why,status,progress,last_touched,kind,goal_key,beneficiary,
      evidence,next_step,cadence_hours,priority)
    VALUES (?,?,?,'livenessproef','actief','',?,?,?,'het gezin','',
      'Kies precies één begrensde stap.',?,?)`).run(
    id, created, `Aandachtsrichting ${id}`, lastTouched, kind, `liveness-${id}`,
    cadenceHours, priority,
  );
}

test('NULL-cadans keert begrensd terug en meest overdue aandacht roteert eerlijk', () => {
  clearAttentionRows();
  const now = Date.now();
  insertAspiration({
    id: 1,
    kind: 'interest',
    lastTouched: new Date(now - 30 * DAY_MS).toISOString(),
    cadenceHours: null,
    priority: 0.1,
  });
  insertAspiration({
    id: 2,
    kind: 'development',
    lastTouched: new Date(now - 2 * DAY_MS).toISOString(),
    cadenceHours: 1,
    priority: 99,
  });
  insertAspiration({
    id: 3,
    kind: 'interest',
    lastTouched: new Date(now - DAY_MS).toISOString(),
    cadenceHours: null,
    priority: 100,
  });

  const oldInterest = db.prepare('SELECT * FROM aspirations WHERE id=1').get();
  const recentInterest = db.prepare('SELECT * FROM aspirations WHERE id=3').get();
  assert.equal(__curiosityTest.aspirationDue(oldInterest, now), true,
    'een oude actieve interesse met NULL-cadans mag niet permanent verdwijnen');
  assert.equal(__curiosityTest.aspirationDue(recentInterest, now), false,
    'de fallbackcadans maakt NULL niet bij iedere schedulerbeurt due');

  const first = dueAttention();
  assert.deepEqual(first, { kind: 'aspiration', id: 1, text: 'Aandachtsrichting 1' },
    'langst overdue wint deterministisch van alleen een hogere prioriteit');
  assert.equal(settleIssuedAspirationAttention(first), true);
  assert.equal(__curiosityTest.aspirationDue(
    db.prepare('SELECT * FROM aspirations WHERE id=1').get(),
  ), false, 'na selectie slaapt de interesse opnieuw tot haar fallbackcadans');

  const second = dueAttention();
  assert.deepEqual(second, { kind: 'aspiration', id: 2, text: 'Aandachtsrichting 2' });
  assert.equal(settleIssuedAspirationAttention(second), true);

  clearAttentionRows();
  insertAspiration({ id: 4, lastTouched: null, cadenceHours: 0, priority: 1 });
  const oneShot = dueAttention();
  assert.deepEqual(oneShot, { kind: 'aspiration', id: 4, text: 'Aandachtsrichting 4' });
  assert.equal(settleIssuedAspirationAttention(oneShot), true);
  assert.equal(__curiosityTest.aspirationDue(
    db.prepare('SELECT * FROM aspirations WHERE id=4').get(),
  ), false, 'expliciete cadans nul blijft bewust one-shot');
});

test('de normale aspiration-writer bewaart ontbrekende cadans als NULL', () => {
  clearAttentionRows();
  const id = addAspiration({
    text: 'Een eigen interesse die via de normale writer later opnieuw aandacht krijgt.',
    why: 'De standaardcadans mag niet stil veranderen in een eenmalige richting.',
  });
  const stored = db.prepare('SELECT * FROM aspirations WHERE id=?').get(id);
  assert.equal(stored.cadence_hours, null,
    'een weggelaten cadenceHours is semantisch afwezig, niet expliciet one-shot nul');

  db.prepare('UPDATE aspirations SET last_touched=? WHERE id=?').run(
    new Date(Date.now() - 8 * DAY_MS).toISOString(), id,
  );
  assert.equal(__curiosityTest.aspirationDue(
    db.prepare('SELECT * FROM aspirations WHERE id=?').get(id),
  ), true, 'de via de publieke writer gemaakte interesse keert na haar fallbackcadans terug');

  const oneShotId = addAspiration({
    text: 'Een expliciet eenmalige richting.',
    why: 'Nul blijft een bewuste writerkeuze.',
    cadenceHours: 0,
    touched: false,
  });
  assert.equal(db.prepare('SELECT cadence_hours FROM aspirations WHERE id=?')
    .get(oneShotId).cadence_hours, 0);
});

test('fairness selecteert ook een oudste due exploration buiten de nieuwste twintig', () => {
  clearAttentionRows();
  const ids = [];
  for (let index = 0; index < 25; index += 1) {
    const started = startExploration({
      question: `Levert unieke bron ${index} één concrete waarneming op?`,
      whyNow: `Starvationproef ${index} heeft een afzonderlijk beslisdomein.`,
      hypothesis: `Bron ${index} kan één eigen controleerbaar feit opleveren.`,
      expectedObservation: `Eén geverifieerde waarneming uit unieke bron ${index}.`,
      stopRule: `Stop na de eerste begrensde controle van bron ${index}.`,
      maxActions: 2,
      source: 'self',
      topicKey: `starvation_source_${index}`,
      topicAnchors: [`event:starvation-${index}`, `entity:test-source-${index}`],
    });
    assert.equal(started.ok, true, JSON.stringify(started));
    ids.push(started.exploration.id);
    const selectedTs = new Date(Date.now() - (25 - index) * 60_000).toISOString();
    db.prepare(`UPDATE eva_explorations
      SET updated_ts=?,last_selected_ts=? WHERE id=?`).run(
      selectedTs, selectedTs, started.exploration.id,
    );
  }

  const attention = dueAttention();
  assert.deepEqual(attention, {
    kind: 'exploration', id: ids[0],
    text: 'Levert unieke bron 0 één concrete waarneming op?',
  }, 'SQL-fairness moet vóór een eventuele fetchgrens worden toegepast');
});

test('tweede echte no-progress retry eindigt atomair als duidelijke inconclusive drop', () => {
  clearAttentionRows();
  const started = startExploration({
    question: 'Levert deze begrensde richting één nieuwe relevante waarneming op?',
    whyNow: 'De livenessproef moet een eindige lege route aantonen.',
    hypothesis: 'Twee echte lege beurten horen de richting expliciet te laten vallen.',
    expectedObservation: 'Een nieuwe geverifieerde waarneming of een duidelijke drop.',
    stopRule: 'Stop na de tweede werkelijk lege aandachtbeurt.',
    maxActions: 3,
    source: 'self',
    topicKey: 'curiosity_no_progress_liveness',
    topicAnchors: ['event:no-progress-liveness', 'constraint:finite-drop'],
  });
  assert.equal(started.ok, true);
  const explorationId = started.exploration.id;

  const first = dueAttention();
  assert.deepEqual(first, {
    kind: 'exploration', id: explorationId, text: started.exploration.question,
  });
  assert.equal(settleIssuedExplorationAttention(first), true);
  assert.equal(explorationById(explorationId).status, 'chosen',
    'de eerste lege beurt krijgt één begrensde retry, geen onmiddellijke drop');

  const retryDue = new Date(Date.now() - 31 * 24 * 60 * 60_000).toISOString();
  db.prepare(`UPDATE eva_explorations
    SET scheduler_deferred_ts=?,last_selected_ts=? WHERE id=?`).run(
    retryDue, retryDue, explorationId,
  );
  const second = dueAttention();
  assert.deepEqual(second, {
    kind: 'exploration', id: explorationId, text: started.exploration.question,
  });
  const timelineBefore = db.prepare('SELECT count(*) AS count FROM timeline').get().count;
  const messagesBefore = db.prepare('SELECT count(*) AS count FROM messages').get().count;
  assert.equal(settleIssuedExplorationAttention(second), true);

  const dropped = explorationById(explorationId);
  assert.equal(dropped.status, 'inconclusive');
  assert.match(dropped.result || '', /twee.*geen nieuwe.*waarneming/iu);
  assert.match(dropped.decision || '', /laten vallen|gestopt/iu);
  assert.equal(dropped.next_wake_ts, null);
  assert.equal(dueAttention(), null, 'de terminale drop wordt niet administratief herhaald');
  assert.equal(settleIssuedExplorationAttention(second), false,
    'het retrytoken blijft na de terminale CAS one-shot');
  assert.equal(db.prepare('SELECT count(*) AS count FROM timeline').get().count, timelineBefore,
    'een drop wordt niet als bijdrage of gezinsmelding voorgesteld');
  assert.equal(db.prepare('SELECT count(*) AS count FROM messages').get().count, messagesBefore,
    'de eindige lege route veroorzaakt geen notificatiespam');
});
