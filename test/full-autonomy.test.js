import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-full-autonomy-'));
process.env.EVA_DATA_DIR = dataDir;
process.env.EVA_AUTONOMY_LEVEL = 'full';
process.env.ALLOW_HOME_CONTROL = 'true';
// Maak dit contract onafhankelijk van het echte tijdstip waarop de suite draait.
process.env.SLEEP_START = '23:59';
process.env.WAKE_TIME = '00:00';

const { config } = await import('../src/config.js');
const { db } = await import('../src/db.js');
const { executeTool, registerMessenger, toolsForTurn } = await import('../src/mind/tools.js');
const { __projectRouteTest } = await import('../src/mind/agent.js');
const { __autonomyScheduleTest } = await import('../src/mind/autonomy.js');
const { ensureEpisodeForWorkItem, goalEpisodeById } = await import('../src/mind/portfolio.js');

after(() => {
  registerMessenger(null);
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

test('autonome gesprekscontext draagt tijdstempels en maakt oude fysieke toestand niet actueel', () => {
  const oldTs = new Date(Date.now() - 6 * 3600_000).toISOString();
  db.prepare(`INSERT INTO messages (ts,channel,conversation_id,sender,role,content)
    VALUES (?,?,?,?,?,?)`).run(
    oldTs, 'telegram', 'freshness-test', 'Jaap', 'user',
    'De achterdeur staat al open.',
  );
  const input = JSON.stringify(__projectRouteTest.autonomyInput('vrije aandacht'));
  assert.match(input, new RegExp(oldTs.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
  assert.match(input, /Een oude uitspraak over een fysieke toestand bewijst alleen wat er toen gold/);
  assert.match(input, /lees de actuele bron opnieuw/);
});

test('full mag zelf duurzaam werk en gewoon sociaal contact beginnen', async () => {
  const work = JSON.parse(await executeTool('werk_bijhouden', {
    bewerking: 'start',
    titel: 'Eigen gekozen proef',
    doel: 'Een zelf gekozen idee aantoonbaar uitwerken',
    volgende_stap: 'De eerste proef uitvoeren',
  }, { mode: 'autonomy', priorToolResults: [] }));
  assert.equal(work.ok, true);
  assert.equal(work.verified, true);

  registerMessenger(async () => ({ ok: true, sent: [{ chatId: 42 }] }));
  const contact = JSON.parse(await executeTool('stuur_bericht', {
    tekst: 'Ik dacht aan jullie. Hoe is het daar?',
  }, { mode: 'autonomy', priorToolResults: [] }));
  assert.equal(contact.ok, true);
  assert.equal(contact.verified, true);
  assert.equal(
    __projectRouteTest.contributionFromTools([{ name: 'stuur_bericht', ...contact }], 'autonomy')?.name,
    'stuur_bericht',
  );
});

test('een voorbereid bericht gebruikt zijn opgeslagen deduplicatie-identiteit echt bij transport', async () => {
  let options = null;
  registerMessenger(async (_text, _recipient, received) => {
    options = received;
    return { ok: true, sent: [{ chatId: 42 }] };
  });
  const sent = JSON.parse(await executeTool('stuur_bericht', {
    tekst: 'Een exact voorbereid resultaat.',
  }, {
    mode: 'autonomy', priorToolResults: [],
    preparedDeliveryDedupeKey: 'episode:77:prepared-delivery',
  }));
  assert.equal(sent.ok, true);
  assert.equal(options.dedupeKey, 'prepared:episode:77:prepared-delivery');
});

test('een start met hervatmoment slaapt meteen en een eigen wekker is geen gezinsmelding', async () => {
  const wake = new Date(Date.now() + 60 * 60_000).toISOString();
  const work = JSON.parse(await executeTool('werk_bijhouden', {
    bewerking: 'start',
    titel: 'Wachten op meetmoment',
    doel: 'Op het juiste moment één meting doen',
    volgende_stap: 'Voer de meting uit.',
    fase: 'Wachten tot de meting zinvol is.',
    toestand_json: JSON.stringify({ planned: true }),
    hervatten_na: wake,
  }, { mode: 'autonomy', priorToolResults: [] }));
  const item = work.data.item;
  assert.equal(item.next_wake_ts, wake);
  assert.match(item.wait_json, /scheduled_resume/);
  assert.equal(work.data.proofAccepted, false);

  const ownReminder = {
    name: 'zet_herinnering', args: { voor_wie: 'Eva' },
    ok: true, verified: true, changed: true,
  };
  assert.equal(__projectRouteTest.autonomyEffectRequiresReport(ownReminder), false);
  assert.equal(__projectRouteTest.autonomyEffectRequiresReport({
    name: 'bedien_huis', args: { doel: 'overkappingsspots', actie: 'uit' },
  }), false);
  assert.equal(__projectRouteTest.autonomyEffectRequiresReport({
    name: 'bedien_huis', args: { doel: 'achterdeurslot', actie: 'open' },
  }), true);
  assert.equal(__projectRouteTest.autonomyWorkReportPending([ownReminder]), false);
  assert.equal(__projectRouteTest.contributionFromTools([ownReminder], 'autonomy'), null);
  assert.equal(__projectRouteTest.autonomyHasRunnableWork([{
    name: 'werk_bijhouden', ok: true, verified: true,
    output: JSON.stringify({ data: { item } }),
  }]), false);
  assert.equal(__projectRouteTest.autonomyHasScheduledWork([{
    name: 'werk_bijhouden', ok: true, verified: true,
    output: JSON.stringify({ data: { item } }),
  }]), true);
});

test('zelf-oplosbaar bewijsgebrek wordt een geplande review en geen permanent blok', async () => {
  const started = JSON.parse(await executeTool('werk_bijhouden', {
    bewerking: 'start', titel: 'Zoek een actuele kans',
    doel: 'Vind een actuele aantoonbare kans', volgende_stap: 'Probeer een andere primaire bron.',
  }, { mode: 'autonomy', priorToolResults: [] }));
  const item = started.data.item;
  const episode = ensureEpisodeForWorkItem(item, { originKind: 'self', originChannel: 'autonomy' });
  const reviewed = JSON.parse(await executeTool('werk_bijhouden', {
    bewerking: 'blokkeer', id: item.id,
    volgende_stap: 'Probeer bij de review een andere primaire bron.',
  }, {
    mode: 'autonomy', activeWorkId: item.id,
    priorToolResults: [],
  }));
  assert.equal(reviewed.ok, true);
  assert.equal(reviewed.data.selfReviewInstead, true);
  assert.equal(reviewed.data.item.status, 'open');
  assert.match(reviewed.data.item.wait_json, /self_review/);
  assert.ok(Date.parse(reviewed.data.item.next_wake_ts) > Date.now());
  const waitingEpisode = goalEpisodeById(episode.id);
  assert.equal(waitingEpisode.state, 'waiting');
  assert.equal(waitingEpisode.wake_ts, reviewed.data.item.next_wake_ts);
});

test('echte aandacht kan geïsoleerde testartefacten niet zien of aan Eva zelf berichten', async () => {
  const isolatedDir = path.join(config.workspaceDir, 'test-artifacts', 'probe');
  fs.mkdirSync(isolatedDir, { recursive: true });
  fs.writeFileSync(path.join(isolatedDir, 'lokmiddel.txt'), 'synthetisch lokmiddel');

  const listed = JSON.parse(await executeTool('lijst_bestanden', {
    pad: config.workspaceDir,
  }, { mode: 'autonomy' }));
  assert.equal(listed.ok, true);
  assert.equal(listed.data.some((entry) => entry.name === 'test-artifacts'), false);

  const blocked = JSON.parse(await executeTool('lees_bestand', {
    pad: path.join(isolatedDir, 'lokmiddel.txt'),
  }, { mode: 'autonomy' }));
  assert.equal(blocked.ok, false);
  assert.match(blocked.error, /geïsoleerde testartefacten/i);

  const allowedTest = JSON.parse(await executeTool('lees_bestand', {
    pad: path.join(isolatedDir, 'lokmiddel.txt'),
  }, { mode: 'chat', messageContext: { syntheticTest: true } }));
  assert.equal(allowedTest.ok, true);

  let sends = 0;
  registerMessenger(async () => { sends += 1; return { ok: true, sent: [] }; });
  const selfMessage = JSON.parse(await executeTool('stuur_bericht', {
    tekst: 'Onthoud dit voor straks.', aan: 'Eva',
  }, { mode: 'autonomy' }));
  assert.equal(selfMessage.ok, false);
  assert.equal(selfMessage.retryable, false);
  assert.equal(sends, 0);
});

test('een tussenbericht laat daarna geschreven code open tot Eva die zelf uitvoert en terugleest', () => {
  const sent = {
    name: 'stuur_bericht', ok: true, verified: true, changed: true,
    output: JSON.stringify({ ok: true, verified: true, changed: true }),
  };
  const write = {
    name: 'schrijf_bestand', ok: true, verified: true, changed: true,
    args: { pad: 'data/workspace/geldroute_demo.js' },
    output: JSON.stringify({
      ok: true, verified: true, changed: true,
      data: { path: 'data/workspace/geldroute_demo.js' },
    }),
  };
  assert.equal(__projectRouteTest.contributionFromTools([sent, write], 'autonomy'), null);
  assert.equal(__projectRouteTest.autonomyStopReady([sent, write]), false);
});

test('full geeft Eva zelfstandige doelkeuze en haar normale gereedschappen', () => {
  assert.equal(config.autonomyLevel, 'full');
  assert.equal(config.autonomyFull, true);
  assert.equal(config.autonomyCodeExecution, true);
  assert.equal(config.autonomyOpenExploration, true);
  assert.equal(config.autonomyDailyWorkMinutes, 120);
  assert.equal(config.autonomyTurnMinutes, 30);

  const offered = new Set(toolsForTurn({ mode: 'autonomy' }).map((tool) => tool.name));
  for (const name of [
    'bedien_huis', 'zoek_internet', 'schrijf_bestand', 'voer_uit',
    'zoek_gesprek', 'zet_herinnering', 'annuleer_herinnering',
    'spreek_in_huis', 'stuur_bericht', 'toon_werk', 'gebruik_robot',
    'verkenning_bijhouden',
  ]) assert.equal(offered.has(name), true, `${name} ontbreekt`);

  assert.equal(__projectRouteTest.autonomyToolAllowed('bedien_huis'), true);
  assert.equal(__projectRouteTest.autonomyToolAllowed('zoek_internet'), true);
  assert.equal(__projectRouteTest.autonomyToolAllowed('schrijf_bestand'), true);
  assert.equal(__projectRouteTest.autonomyLaneAllows('bedien_huis', 'workspace', []), true);
  // Volledige doelautonomie is geen toestemming voor een eindeloze call-loop.
  assert.equal(__projectRouteTest.autonomyDiscoveryAllowed('zoek_internet', Array(8).fill({ name: 'zoek_internet' })), false);
  assert.equal(__autonomyScheduleTest.mayWorkAtNight({ open_commitments: [] }, []), true);
});

test('full krijgt dagelijks twee uur en maakt lopend werk niet halverwege afhankelijk van een kwartierpuls', () => {
  const now = new Date(2026, 7, 4, 10, 0, 0);
  const fresh = __autonomyScheduleTest.withCurrentDailyBudget({
    daily_budget_date: '2026-08-03',
    daily_used_ms: 999_999,
    daily_productive_ms: 800_000,
    daily_preempted_ms: 100_000,
  }, now);
  assert.equal(fresh.daily_budget_date, '2026-08-04');
  assert.equal(fresh.daily_used_ms, 0);
  assert.equal(__autonomyScheduleTest.remainingDailyMs(fresh, now), 120 * 60_000);

  const reset = new Date(__autonomyScheduleTest.nextDayResetTs(now));
  assert.equal(reset.getDate(), 5);
  assert.equal(reset.getHours(), 0);
  assert.equal(reset.getMinutes(), 5);
});

test('full kan zelfstandig een programma schrijven en geïsoleerd uitvoeren', async () => {
  const workspace = path.join(dataDir, 'workspace');
  const write = JSON.parse(await executeTool('schrijf_bestand', {
    pad: path.join(workspace, 'eigen-initiatief.js'),
    inhoud: "console.log('zelfstandig uitgevoerd');\n",
  }, { mode: 'autonomy' }));
  assert.equal(write.ok, true);
  assert.equal(write.verified, true);

  const run = JSON.parse(await executeTool('voer_uit', {
    programma: 'node',
    argumenten: ['eigen-initiatief.js'],
    werkmap: workspace,
  }, { mode: 'autonomy' }));
  assert.equal(run.ok, true);
  assert.equal(run.verified, true);
  assert.match(run.evidence.summary, /zelfstandig uitgevoerd/);
});

test('bewezen afgerond milestonewerk blijft done en sluit zijn episode', async () => {
  const workspace = path.join(dataDir, 'workspace');
  const write = JSON.parse(await executeTool('schrijf_bestand', {
    pad: path.join(workspace, 'terminaal-bewijs.js'),
    inhoud: "console.log('uniek terminaalbewijs');\n",
  }, { mode: 'autonomy' }));
  assert.equal(write.ok, true);
  const started = JSON.parse(await executeTool('werk_bijhouden', {
    bewerking: 'start', titel: 'Controleer uniek terminaalbewijs',
    doel: 'Bewijs uniek terminaalbewijs met een echte uitvoering.',
    volgende_stap: 'Voer het programma uit en sluit daarna af.',
  }, { mode: 'autonomy', priorToolResults: [write] }));
  const item = started.data.item;
  const episode = ensureEpisodeForWorkItem(item, { originKind: 'self', originChannel: 'autonomy' });
  const runArgs = { programma: 'node', argumenten: ['terminaal-bewijs.js'], werkmap: workspace };
  const run = JSON.parse(await executeTool('voer_uit', runArgs, { mode: 'autonomy' }));
  assert.equal(run.ok, true);
  const done = JSON.parse(await executeTool('werk_bijhouden', {
    bewerking: 'afronden', id: item.id, resultaat: 'Het unieke terminaalbewijs is uitgevoerd.',
  }, {
    mode: 'autonomy', activeWorkId: item.id,
    priorToolResults: [{
      name: 'voer_uit', args: runArgs, output: JSON.stringify(run),
      ok: run.ok, verified: run.verified,
    }],
  }));
  assert.equal(done.ok, true);
  assert.equal(done.data.item.status, 'done');
  assert.equal(goalEpisodeById(episode.id).state, 'closed');

  const reopen = JSON.parse(await executeTool('werk_bijhouden', {
    bewerking: 'blokkeer', id: item.id,
  }, { mode: 'autonomy' }));
  assert.equal(reopen.ok, false);
  assert.equal(reopen.changed, false);
  assert.equal(reopen.data.item.status, 'done');
  assert.equal(goalEpisodeById(episode.id).state, 'closed');
});
