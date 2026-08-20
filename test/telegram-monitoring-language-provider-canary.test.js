import assert from 'node:assert/strict';
import fs from 'node:fs';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync('/tmp/bottie-monitoring-provider-canary-');
process.env.EVA_DATA_DIR = dataDir;
process.env.EVA_RUNTIME_MODE = 'normal';
process.env.EVA_AUTONOMY_ENABLED = 'true';
process.env.EVA_AUTONOMY_LEVEL = 'full';
process.env.ALLOW_HOME_CONTROL = 'true';
process.env.TELEGRAM_ALLOWED_IDS = '424243';
process.env.SLEEP_START = '00:00';
process.env.WAKE_TIME = '00:00';
if (process.env.EVA_PROVIDER_CANARY === '1') {
  const authSource = String(process.env.EVA_PROVIDER_CANARY_AUTH_SOURCE || '');
  assert.ok(authSource, 'provider-canary vereist een expliciete OAuth-bron');
  fs.copyFileSync(authSource, `${dataDir}/auth.json`, fs.constants.COPYFILE_EXCL);
  fs.chmodSync(`${dataDir}/auth.json`, 0o600);
}
await import('./telegram-test-composition.js');
await import('./agent-test-composition.js');

const { config } = await import('../src/config.js');
const { authStatus } = await import('../src/brain/oauth.js');
const { db, getTimeline, upsertPerson } = await import('../src/db.js');
const { registerMessenger } = await import('../src/mind/tools.js');
const telegramModule = await import('../src/integrations/telegram.js');
const { prepareAuthenticatedTelegramTurnForTest, setBotForTest } =
  telegramModule.__telegramTransportTest;

upsertPerson('Jaap', { telegram_id: '424243' });

after(() => {
  registerMessenger(null);
  setBotForTest(null);
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

test('echt model bindt natuurlijke Telegramtaal aan actuele temperatuurmonitoring', {
  skip: process.env.EVA_PROVIDER_CANARY !== '1', timeout: 120_000,
}, async () => {
  assert.ok(config.openaiApiKey || authStatus().status === 'ok',
    'provider-canary vereist de echte lokale providerconfiguratie');
  const text = 'geef aan wanneer ik de ramen meot openen als het buiten kouder is dan binnen.';
  const chatId = '424243';
  const messageId = '94358';
  const homeStates = [{
    entity_id: 'weather.forecast_thuis', state: 'clear-night',
    last_updated: new Date().toISOString(),
    attributes: {
      friendly_name: 'Weer thuis', temperature: 20, temperature_unit: '°C',
    },
  }, {
    entity_id: 'climate.woonkamer', state: 'heat',
    last_updated: new Date().toISOString(),
    attributes: {
      friendly_name: 'Woonkamer', current_temperature: 21, temperature_unit: '°C',
    },
  }];
  let externalSends = 0;
  setBotForTest({ api: { sendMessage: async () => {
    externalSends += 1;
    throw new Error('monitoringstart mag geen Telegrambericht transporteren');
  } } });
  registerMessenger(async () => {
    externalSends += 1;
    throw new Error('monitoringstart mag geen externe aflevering uitvoeren');
  });

  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, options = {}) => {
    const target = String(url);
    if (!target.startsWith(config.haUrl)) return originalFetch(url, options);
    if (target.endsWith('/api/states')) {
      return new Response(JSON.stringify(homeStates), {
        status: 200, headers: { 'content-type': 'application/json' },
      });
    }
    const entityId = decodeURIComponent(target.split('/api/states/')[1] || '');
    const state = homeStates.find((item) => item.entity_id === entityId);
    return new Response(JSON.stringify(state || {}), {
      status: state ? 200 : 404, headers: { 'content-type': 'application/json' },
    });
  };

  try {
    const turn = await prepareAuthenticatedTelegramTurnForTest({
      ctx: {
        chat: { id: Number(chatId) },
        from: { id: Number(chatId), first_name: 'Jaap' },
        message: { message_id: Number(messageId), date: 1_786_100_000, text },
      },
      sender: 'Jaap', text,
    });
    const diagnostic = getTimeline({ limit: 12 }).filter((row) => (
      ['turn_understanding', 'turn_planning'].includes(row.kind)
    ));
    assert.equal(turn.understood, true, JSON.stringify({
      error: turn.error, failureStage: turn.failureStage, tools: turn.tools, reply: turn.reply,
      diagnostic,
    }));
    assert.equal(turn.tools.filter((item) => item.name === 'huis_status').length, 1,
      JSON.stringify({ turn, diagnostic }));
    assert.equal(turn.tools.filter((item) => item.name === 'ha_lees').length, 2,
      JSON.stringify({ turn, diagnostic }));
    assert.equal(turn.tools.filter((item) => item.name === 'werk_bijhouden').length, 1,
      JSON.stringify(turn));
    assert.equal(turn.tools.some((item) => item.name === 'bedien_huis'), false,
      JSON.stringify(turn));
    assert.equal(turn.tools.some((item) => item.name === 'stuur_bericht'), false,
      JSON.stringify(turn));
    assert.equal(externalSends, 0);

    const bound = db.prepare(`SELECT w.state_json,e.state episode_state,e.origin_kind,
      e.origin_conversation_id,e.origin_message_id,e.requester,e.delivery_policy
      FROM eva_work_items w JOIN eva_goal_episodes e ON e.work_item_id=w.id
      WHERE e.origin_conversation_id=? AND e.origin_message_id=?`)
      .get(`telegram:${chatId}`, messageId);
    assert.ok(bound, JSON.stringify(turn));
    const contract = JSON.parse(bound.state_json).monitoring;
    assert.equal(contract.contract, 'MonitoringContract/v1');
    assert.equal(contract.definitionOfDone, text);
    assert.deepEqual(contract.readBindings.map((item) => item.entityId).sort(), [
      'climate.woonkamer', 'weather.forecast_thuis',
    ]);
    assert.equal(new Set(contract.readBindings.map((item) => item.requirementId)).size, 2);
    assert.equal(bound.episode_state, 'waiting');
    assert.equal(bound.origin_kind, 'user');
    assert.equal(bound.requester, 'Jaap');
    assert.equal(bound.delivery_policy, 'required');
  } finally {
    globalThis.fetch = originalFetch;
  }
});
