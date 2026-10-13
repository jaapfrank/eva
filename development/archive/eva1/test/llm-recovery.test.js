import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';
import './llm-test-composition.js';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-llm-recovery-'));
process.env.EVA_DATA_DIR = dataDir;

const { db, kvGet } = await import('../src/db.js');
const llmProductionModule = await import('../src/brain/llm.js');
const {
  __llmTest, chatComplete, llmRuntimeStatus, msg, runExecutionDriver, searchWeb,
} = await import('../src/brain/llm.js?llm-test-composition');

function completedResponse(output, usage = undefined) {
  const response = { output };
  if (usage !== undefined) response.usage = usage;
  return new Response(`data: ${JSON.stringify({
    type: 'response.completed', response,
  })}\n\n`, { status: 200, headers: { 'content-type': 'text/event-stream' } });
}

function oauthWireRequest(fetch) {
  return __llmTest.requestOnceWith({
    authStatus: () => ({ status: 'ok' }),
    getAccess: async () => ({ accessToken: 'test-oauth-token', accountId: 'test-account' }),
    fetch,
  });
}

function rotatingPrimitiveCredential(label) {
  let coercions = 0;
  return {
    value: Object.freeze({
      [Symbol.toPrimitive]() {
        coercions += 1;
        return `${label}-${coercions % 2 === 1 ? 'A' : 'B'}`;
      },
    }),
    coercions: () => coercions,
  };
}

function advanceWallClockPast(timestamp) {
  let iterations = 0;
  while (Date.now() <= timestamp) iterations += 1;
  return iterations;
}

after(() => {
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

test('de production LLM-namespace ontsluit geen caller-injecteerbaar hosttransport', async () => {
  let observedAuthorization = null;
  if (typeof llmProductionModule.__llmTest?.requestOnce === 'function') {
    await llmProductionModule.__llmTest.requestOnce({ model: 'test', stream: true }, {
      authStatus: () => ({ status: 'ok' }),
      getAccess: async () => ({ accessToken: 'production-export-leak', accountId: 'test' }),
      apiKey: 'caller-controlled-key',
      fetch: async (_url, options) => {
        observedAuthorization = options.headers.Authorization;
        return completedResponse([{ type: 'message', role: 'assistant',
          content: [{ type: 'output_text', text: 'Onbereikbaar zonder productielek.' }] }]);
      },
    });
  }

  assert.equal(Object.hasOwn(llmProductionModule, '__llmTest'), false);
  assert.equal(observedAuthorization, null,
    'productiecode mag OAuth/API-keyheaders niet aan callertransport prijsgeven');
});

test('webonderzoek blijft volledig binnen de centrale receiptgrens', async () => {
  let request = null;
  const result = await searchWeb('Vergelijk de beschikbare migratieroutes.', {
    complete: async (value) => {
      request = value;
      const links = Array.from({ length: 7 }, (_item, index) => (
        `https://example.com/bron-${index + 1}`
      ));
      return {
        text: `Compact bewijs. ${links.join(' ')}`,
        output: [{ type: 'web_search_call' }],
        usage: null,
      };
    },
  });

  assert.match(request.instructions, /volledige notitie.+onder 5\.500 tekens/u);
  for (const field of [
    'maxOutputTokens', 'rateLimitRetries', 'transientRetries', 'authenticationRetries',
  ]) {
    assert.equal(Object.hasOwn(request, field), false,
      `gewone searchWeb laat ${field} aan het bestaande chatComplete-defaultcontract`);
  }
  assert.equal(result.searched, true);
  assert.equal(result.sources.length, 4);
  assert.ok(JSON.stringify({
    ok: true, verified: true, message: result.text, sources: result.sources,
  }).length < 8_000);
});

test('twee korte providerstoringen herstellen binnen dezelfde gebruikersbeurt', async () => {
  const models = [];
  const waits = [];
  let attempts = 0;
  const result = await chatComplete({
    instructions: 'Antwoord kort.',
    input: [msg('user', 'Kies één dashboardrichting.')],
    model: 'gpt-5.6-terra',
    effort: 'low',
    request: async (body) => {
      attempts += 1;
      models.push(body.model);
      if (attempts <= 2) throw new Error('fetch failed');
      return {
        output: [{
          type: 'message', role: 'assistant',
          content: [{ type: 'output_text', text: 'Zo min mogelijk dashboards.' }],
        }],
        usage: null,
      };
    },
    pause: async (ms) => { waits.push(ms); },
  });

  assert.equal(attempts, 3);
  assert.deepEqual(waits, [500, 1_500]);
  assert.equal(result.text, 'Zo min mogelijk dashboards.');
  assert.equal(models[0], 'gpt-5.6-terra');
  assert.ok(models.slice(1).every(Boolean));
});

test('iedere geslaagde providercall bewaart lokaal haar token- en cachegebruik', async () => {
  await chatComplete({
    instructions: 'Antwoord kort.',
    input: [msg('user', 'Meet deze beurt.')],
    model: 'gpt-5.6-terra', effort: 'low', cacheKey: 'usage-canary',
    budgetClass: 'chat',
    request: async () => ({
      output: [{ type: 'message', role: 'assistant', content: [{ type: 'output_text', text: 'Gemeten.' }] }],
      usage: {
        input_tokens: 120, input_tokens_details: { cached_tokens: 80 },
        output_tokens: 14, output_tokens_details: { reasoning_tokens: 6 }, total_tokens: 134,
      },
    }),
  });

  const row = db.prepare("SELECT meta FROM timeline WHERE kind='llm_usage' ORDER BY id DESC LIMIT 1").get();
  const usage = JSON.parse(row.meta);
  assert.deepEqual({
    model: usage.model, budgetClass: usage.budgetClass,
    input: usage.inputTokens, cached: usage.cachedInputTokens,
    output: usage.outputTokens, reasoning: usage.reasoningTokens, total: usage.totalTokens,
  }, {
    model: 'gpt-5.6-terra', budgetClass: 'chat',
    input: 120, cached: 80, output: 14, reasoning: 6, total: 134,
  });
});

test('telemetriefout breekt een geslaagde modelbeurt niet', async () => {
  const result = await chatComplete({
    instructions: 'Antwoord kort.', input: [msg('user', 'Ga door.')],
    request: async () => ({
      output: [{ type: 'message', role: 'assistant', content: [{ type: 'output_text', text: 'Klaar.' }] }],
      usage: { input_tokens: 5, output_tokens: 2, total_tokens: 7 },
    }),
    recordUsage: () => { throw new Error('database tijdelijk gelockt'); },
  });
  assert.equal(result.text, 'Klaar.');
});

test('tokenmeting onderscheidt retries en het werkelijk gebruikte fallbackmodel', async () => {
  for (const scenario of [
    { kind: 'transient', error: Object.assign(new Error('service unavailable'), { status: 503 }) },
    { kind: 'rate', error: Object.assign(new Error('rate limited'), { status: 429 }) },
    { kind: 'auth', error: Object.assign(new Error('unauthorized'), { status: 401, viaOauth: true }) },
  ]) {
    let attempts = 0;
    let usage = null;
    await chatComplete({
      instructions: 'Antwoord kort.', input: [msg('user', `Herstel ${scenario.kind}.`)],
      model: 'gpt-5.6-sol', pause: async () => {},
      request: async () => {
        attempts += 1;
        if (attempts === 1) throw scenario.error;
        return {
          output: [{ type: 'message', role: 'assistant', content: [{ type: 'output_text', text: 'Hersteld.' }] }],
          usage: { input_tokens: 20, output_tokens: 4, total_tokens: 24 },
        };
      },
      recordUsage: (_kind, _content, meta) => { usage = meta; },
    });
    assert.equal(usage.requestAttempts, 2);
    assert.equal(usage.requestedModel, 'gpt-5.6-sol');
    assert.equal(usage.transientRetries, scenario.kind === 'transient' ? 1 : 0);
    assert.equal(usage.rateLimitRetries, scenario.kind === 'rate' ? 1 : 0);
    assert.equal(usage.authenticationRetries, scenario.kind === 'auth' ? 1 : 0);
    assert.equal(usage.fallbackUsed, scenario.kind === 'transient');
    assert.equal(usage.model !== usage.requestedModel, scenario.kind === 'transient');
  }
});

test('een latencykritische beurt kan verborgen providerherpogingen uitschakelen', async () => {
  let attempts = 0;
  await assert.rejects(chatComplete({
    instructions: 'Antwoord kort.',
    input: [msg('user', 'Kies een richting.')],
    model: 'gpt-5.6-terra',
    effort: 'low',
    rateLimitRetries: 0,
    transientRetries: 0,
    request: async () => {
      attempts += 1;
      const error = new Error('service unavailable');
      error.status = 503;
      throw error;
    },
    pause: async () => {
      throw new Error('er hoort zonder retries ook geen wachtronde te zijn');
    },
  }), /service unavailable/i);

  assert.equal(attempts, 1);
});

test('een latencykritische beurt kan ook de OAuth-401-herpoging uitschakelen', async () => {
  let attempts = 0;
  await assert.rejects(chatComplete({
    instructions: 'Antwoord kort.',
    input: [msg('user', 'Kies een richting.')],
    model: 'gpt-5.6-terra',
    effort: 'low',
    rateLimitRetries: 0,
    transientRetries: 0,
    authenticationRetries: 0,
    request: async () => {
      attempts += 1;
      throw Object.assign(new Error('oauth unauthorized'), { status: 401, viaOauth: true });
    },
  }), /unauthorized/i);

  assert.equal(attempts, 1);
});

test('bounded self-attention herhaalt exact één echte pre-output HTTP 503 met identieke wirebody', async () => {
  const bodies = [];
  const waits = [];
  let fetches = 0;
  let gates = 0;
  let usage = null;
  const before = llmRuntimeStatus().provider_request_attempts;
  const result = await chatComplete({
    instructions: 'Bind exact één veilige self-attention stap.',
    input: [msg('user', 'Controleer één actuele bijdrage.')],
    model: 'gpt-5.6-terra', effort: 'low', maxOutputTokens: 8_000,
    rateLimitRetries: 3, transientRetries: 2, authenticationRetries: 1,
    boundedIdenticalTransportRetry: {
      maxRetries: 99,
      assertCurrent: () => { gates += 1; },
    },
    request: oauthWireRequest(async (_url, options) => {
      fetches += 1;
      bodies.push(String(options.body));
      if (fetches === 1) return new Response('tijdelijk niet beschikbaar', { status: 503 });
      return completedResponse([{ type: 'message', role: 'assistant',
        content: [{ type: 'output_text', text: 'De actuele stap is gebonden.' }] }], {
        input_tokens: 30, output_tokens: 6, total_tokens: 36,
      });
    }),
    pause: async (ms) => { waits.push(ms); },
    recordUsage: (_kind, _content, meta) => { usage = meta; },
  });

  assert.equal(result.text, 'De actuele stap is gebonden.');
  assert.equal(fetches, 2);
  assert.equal(llmRuntimeStatus().provider_request_attempts - before, 2);
  assert.deepEqual(waits, [500]);
  assert.equal(gates, 3,
    'current/expiry wordt vóór en na de wacht én direct vóór wirecall twee gecontroleerd');
  assert.equal(bodies[0], bodies[1]);
  assert.equal(
    crypto.createHash('sha256').update(bodies[0]).digest('hex'),
    crypto.createHash('sha256').update(bodies[1]).digest('hex'),
  );
  assert.deepEqual({
    requestAttempts: usage.requestAttempts,
    boundedRetry: usage.boundedIdenticalTransportRetries,
    transientRetries: usage.transientRetries,
    authenticationRetries: usage.authenticationRetries,
    rateLimitRetries: usage.rateLimitRetries,
    fallbackUsed: usage.fallbackUsed,
    requestedModel: usage.requestedModel,
    model: usage.model,
  }, {
    requestAttempts: 2, boundedRetry: 1, transientRetries: 0,
    authenticationRetries: 0, rateLimitRetries: 0, fallbackUsed: false,
    requestedModel: 'gpt-5.6-terra', model: 'gpt-5.6-terra',
  });
});

test('bounded retry bindt caller-owned input en tools vóór poging één aan exacte wirebytes', async () => {
  const input = [msg('user', 'immutable-input-A')];
  const nativeTools = [{
    type: 'function', name: 'immutable_tool_A', description: 'immutable-description-A',
    parameters: {
      type: 'object', additionalProperties: false,
      properties: { query: { type: 'string', description: 'immutable-schema-A' } },
      required: ['query'],
    },
  }];
  const bodies = [];
  let oauthCalls = 0;
  const result = await chatComplete({
    instructions: 'Behoud exact dezelfde request.', input, nativeTools,
    boundedIdenticalTransportRetry: { maxRetries: 1, assertCurrent: () => {} },
    request: __llmTest.requestOnceWith({
      authStatus: () => ({ status: 'ok' }),
      getAccess: async () => {
        oauthCalls += 1;
        if (oauthCalls === 2) {
          nativeTools[0].parameters.properties.query.description = 'mutated-schema-B-during-oauth';
        }
        return { accessToken: 'test-oauth-token', accountId: 'test-account' };
      },
      fetch: async (_url, options) => {
        bodies.push(String(options.body));
        if (bodies.length === 1) return new Response('tijdelijk niet beschikbaar', { status: 503 });
        return completedResponse([{ type: 'message', role: 'assistant',
          content: [{ type: 'output_text', text: 'Exact herhaald.' }] }]);
      },
    }),
    pause: async () => {
      input[0].content[0].text = 'mutated-input-B-during-wait';
      nativeTools[0].name = 'mutated_tool_B_during_wait';
    },
  });

  assert.equal(result.text, 'Exact herhaald.');
  assert.equal(bodies.length, 2);
  assert.equal(bodies[1], bodies[0]);
  assert.match(bodies[0], /immutable-input-A/u);
  assert.match(bodies[0], /immutable_tool_A/u);
  assert.match(bodies[0], /immutable-schema-A/u);
  assert.doesNotMatch(bodies[1], /mutated-.+-B-/u);
});

test('bounded retry weigert een wisselende providerroute of OAuth-account vóór wire twee', async () => {
  for (const scenario of ['route', 'account']) {
    let authCalls = 0;
    let accessCalls = 0;
    let fetches = 0;
    await assert.rejects(chatComplete({
      instructions: 'Behoud dezelfde transportidentiteit.',
      input: [msg('user', scenario)],
      boundedIdenticalTransportRetry: { maxRetries: 1, assertCurrent: () => {} },
      request: __llmTest.requestOnceWith({
        authStatus: () => {
          authCalls += 1;
          return { status: scenario === 'route' && authCalls === 2 ? 'none' : 'ok' };
        },
        getAccess: async () => {
          accessCalls += 1;
          return {
            accessToken: 'stable-oauth-token',
            accountId: scenario === 'account' && accessCalls === 2 ? 'account-B' : 'account-A',
          };
        },
        apiKey: 'test-api-key',
        fetch: async () => {
          fetches += 1;
          if (fetches === 1) return new Response('tijdelijk niet beschikbaar', { status: 503 });
          return completedResponse([{ type: 'message', role: 'assistant',
            content: [{ type: 'output_text', text: 'Deze wirecall moet gesloten blijven.' }] }]);
        },
      }),
      pause: async () => {},
    }), (error) => error?.code === 'EVA_LLM_BOUNDED_TRANSPORT_UNVERIFIED'
      && error?.providerAttempts === 1);
    assert.equal(fetches, 1, scenario);
  }
});

test('bounded OAuth-retry weigert accessToken B bij hetzelfde account vóór wire twee', async () => {
  let accessCalls = 0;
  let fetches = 0;
  const authorizations = [];
  await assert.rejects(chatComplete({
    instructions: 'Behoud exact dezelfde OAuth-identiteit.',
    input: [msg('user', 'access-token-wissel')],
    boundedIdenticalTransportRetry: { maxRetries: 1, assertCurrent: () => {} },
    request: __llmTest.requestOnceWith({
      authStatus: () => ({ status: 'ok' }),
      getAccess: async () => {
        accessCalls += 1;
        return { accessToken: `oauth-token-${accessCalls === 1 ? 'A' : 'B'}`,
          accountId: 'stable-account' };
      },
      fetch: async (_url, options) => {
        fetches += 1;
        authorizations.push(options.headers.Authorization);
        if (fetches === 1) return new Response('tijdelijk niet beschikbaar', { status: 503 });
        return completedResponse([{ type: 'message', role: 'assistant',
          content: [{ type: 'output_text', text: 'Token B mag de wire niet bereiken.' }] }]);
      },
    }),
    pause: async () => {},
  }), (error) => error?.code === 'EVA_LLM_BOUNDED_TRANSPORT_UNVERIFIED'
    && error?.providerAttempts === 1);

  assert.equal(accessCalls, 2);
  assert.equal(fetches, 1);
  assert.deepEqual(authorizations, ['Bearer oauth-token-A']);
});

test('bounded API-retry weigert apiKey B op dezelfde route vóór wire twee', async () => {
  let keyReads = 0;
  let fetches = 0;
  const authorizations = [];
  const hostOptions = {
    authStatus: () => ({ status: 'none' }),
    get apiKey() {
      keyReads += 1;
      return `api-key-${keyReads === 1 ? 'A' : 'B'}`;
    },
    fetch: async (_url, options) => {
      fetches += 1;
      authorizations.push(options.headers.Authorization);
      if (fetches === 1) return new Response('tijdelijk niet beschikbaar', { status: 503 });
      return completedResponse([{ type: 'message', role: 'assistant',
        content: [{ type: 'output_text', text: 'API-key B mag de wire niet bereiken.' }] }]);
    },
  };
  await assert.rejects(chatComplete({
    instructions: 'Behoud exact dezelfde API-identiteit.',
    input: [msg('user', 'api-key-wissel')],
    boundedIdenticalTransportRetry: { maxRetries: 1, assertCurrent: () => {} },
    request: __llmTest.requestOnceWith(hostOptions),
    pause: async () => {},
  }), (error) => error?.code === 'EVA_LLM_BOUNDED_TRANSPORT_UNVERIFIED'
    && error?.providerAttempts === 1);

  assert.equal(keyReads, 2);
  assert.equal(fetches, 1);
  assert.deepEqual(authorizations, ['Bearer api-key-A']);
});

test('bounded credentials weigeren afzonderlijk lone-surrogate, control en niet-ASCII A→B vóór iedere fetch', async () => {
  const scenarios = [
    {
      name: 'api-key-lone-surrogate', values: ['api-key-\uD800', 'api-key-\uD801'],
      hostOptions(read) {
        return {
          authStatus: () => ({ status: 'none' }),
          get apiKey() { return read(); },
        };
      },
    },
    {
      name: 'oauth-token-control', values: ['oauth-token-\nA', 'oauth-token-\nB'],
      hostOptions(read) {
        return {
          authStatus: () => ({ status: 'ok' }),
          getAccess: async () => ({ accessToken: read(), accountId: 'stable-account' }),
        };
      },
    },
    {
      name: 'oauth-account-non-ascii', values: ['account-é-A', 'account-é-B'],
      hostOptions(read) {
        return {
          authStatus: () => ({ status: 'ok' }),
          getAccess: async () => ({ accessToken: 'stable-access-token', accountId: read() }),
        };
      },
    },
  ];

  for (const scenario of scenarios) {
    let reads = 0;
    let fetches = 0;
    const read = () => {
      const value = scenario.values[Math.min(reads, scenario.values.length - 1)];
      reads += 1;
      return value;
    };
    await assert.rejects(chatComplete({
      instructions: 'Weiger credentials buiten het exacte headerdomein.',
      input: [msg('user', scenario.name)],
      boundedIdenticalTransportRetry: { maxRetries: 1, assertCurrent: () => {} },
      request: __llmTest.requestOnceWith({
        ...scenario.hostOptions(read),
        fetch: async () => {
          fetches += 1;
          if (fetches === 1) return new Response('tijdelijk niet beschikbaar', { status: 503 });
          return completedResponse([{ type: 'message', role: 'assistant',
            content: [{ type: 'output_text', text: 'Deze fetch mag niet starten.' }] }]);
        },
      }),
      pause: async () => {},
    }), (error) => error?.code === 'EVA_LLM_BOUNDED_TRANSPORT_UNVERIFIED');
    assert.equal(reads, 1, scenario.name);
    assert.equal(fetches, 0, scenario.name);
  }
});

test('providercredentials hebben expliciete inclusieve lengtecaps', async () => {
  const bearerAtCap = 'A'.repeat(8_192);
  const accountAtCap = 'B'.repeat(256);
  let fetches = 0;
  const fetch = async () => {
    fetches += 1;
    return completedResponse([{ type: 'message', role: 'assistant',
      content: [{ type: 'output_text', text: 'Binnen de grens.' }] }]);
  };

  await __llmTest.requestOnce({ model: 'test', stream: true }, {
    authStatus: () => ({ status: 'none' }), apiKey: bearerAtCap, fetch,
  });
  await __llmTest.requestOnce({ model: 'test', stream: true }, {
    authStatus: () => ({ status: 'ok' }),
    getAccess: async () => ({ accessToken: bearerAtCap, accountId: accountAtCap }),
    fetch,
  });
  assert.equal(fetches, 2);

  for (const scenario of [
    {
      name: 'api-key-over-cap', options: {
        authStatus: () => ({ status: 'none' }), apiKey: 'A'.repeat(8_193), fetch,
      },
    },
    {
      name: 'oauth-token-over-cap', options: {
        authStatus: () => ({ status: 'ok' }),
        getAccess: async () => ({ accessToken: 'A'.repeat(8_193), accountId: 'stable-account' }),
        fetch,
      },
    },
    {
      name: 'oauth-account-over-cap', options: {
        authStatus: () => ({ status: 'ok' }),
        getAccess: async () => ({ accessToken: 'stable-access-token', accountId: 'B'.repeat(257) }),
        fetch,
      },
    },
  ]) {
    await assert.rejects(
      __llmTest.requestOnce({ model: 'test', stream: true }, scenario.options),
      (error) => error?.code === 'EVA_LLM_BOUNDED_TRANSPORT_UNVERIFIED',
      scenario.name,
    );
    assert.equal(fetches, 2, scenario.name);
  }
});

test('providercredentials weigeren leegte, witruimte en String-objecten zonder coercion of fetch', async () => {
  const StringCredential = class extends String {};
  for (const scenario of [
    {
      name: 'lege-api-key-is-ongeconfigureerd', code: 'EVA_LLM_UNAVAILABLE',
      options: { authStatus: () => ({ status: 'none' }), apiKey: '' },
    },
    {
      name: 'api-key-spatie', code: 'EVA_LLM_BOUNDED_TRANSPORT_UNVERIFIED',
      options: { authStatus: () => ({ status: 'none' }), apiKey: ' ' },
    },
    {
      name: 'api-key-control', code: 'EVA_LLM_BOUNDED_TRANSPORT_UNVERIFIED',
      options: { authStatus: () => ({ status: 'none' }), apiKey: '\t' },
    },
    {
      name: 'boxed-api-key', code: 'EVA_LLM_BOUNDED_TRANSPORT_UNVERIFIED',
      options: {
        authStatus: () => ({ status: 'none' }),
        apiKey: Reflect.construct(String, ['boxed-api-key']),
      },
    },
    {
      name: 'String-subclass-api-key', code: 'EVA_LLM_BOUNDED_TRANSPORT_UNVERIFIED',
      options: {
        authStatus: () => ({ status: 'none' }),
        apiKey: Reflect.construct(StringCredential, ['subclass-api-key']),
      },
    },
    {
      name: 'leeg-oauth-token', code: 'EVA_LLM_BOUNDED_TRANSPORT_UNVERIFIED',
      options: {
        authStatus: () => ({ status: 'ok' }),
        getAccess: async () => ({ accessToken: '', accountId: 'stable-account' }),
      },
    },
    {
      name: 'leeg-oauth-account', code: 'EVA_LLM_BOUNDED_TRANSPORT_UNVERIFIED',
      options: {
        authStatus: () => ({ status: 'ok' }),
        getAccess: async () => ({ accessToken: 'stable-access-token', accountId: '' }),
      },
    },
  ]) {
    let fetches = 0;
    await assert.rejects(__llmTest.requestOnce({ model: 'test', stream: true }, {
      ...scenario.options,
      fetch: async () => {
        fetches += 1;
        return completedResponse([]);
      },
    }), (error) => error?.code === scenario.code, scenario.name);
    assert.equal(fetches, 0, scenario.name);
  }
});

test('bounded API-route weigert een roterend apiKey-object zonder coercion of wirecall', async () => {
  const credential = rotatingPrimitiveCredential('hostile-api-key-secret-9417');
  let fetches = 0;
  let failure = null;
  try {
    await chatComplete({
      instructions: 'Weiger niet-primitieve API-credentials.',
      input: [msg('user', 'hostile-api-key-object')],
      boundedIdenticalTransportRetry: { maxRetries: 1, assertCurrent: () => {} },
      request: __llmTest.requestOnceWith({
        authStatus: () => ({ status: 'none' }),
        apiKey: credential.value,
        fetch: async () => {
          fetches += 1;
          if (fetches === 1) return new Response('tijdelijk niet beschikbaar', { status: 503 });
          return completedResponse([{ type: 'message', role: 'assistant',
            content: [{ type: 'output_text', text: 'Onbereikbaar.' }] }]);
        },
      }),
      pause: async () => {},
    });
  } catch (error) {
    failure = error;
  }

  assert.equal(failure?.code, 'EVA_LLM_BOUNDED_TRANSPORT_UNVERIFIED');
  assert.equal(failure?.providerAttempts, 1);
  assert.equal(credential.coercions(), 0);
  assert.equal(fetches, 0);
  assert.equal(JSON.stringify({ message: failure?.message, runtime:
    kvGet('llm_runtime_status_v1', null) }).includes('hostile-api-key-secret-9417'), false);
});

test('bounded OAuth-route weigert een roterend accessToken-object zonder coercion of wirecall', async () => {
  const credential = rotatingPrimitiveCredential('hostile-access-token-secret-7251');
  let fetches = 0;
  let failure = null;
  try {
    await chatComplete({
      instructions: 'Weiger niet-primitieve OAuth-tokens.',
      input: [msg('user', 'hostile-access-token-object')],
      boundedIdenticalTransportRetry: { maxRetries: 1, assertCurrent: () => {} },
      request: __llmTest.requestOnceWith({
        authStatus: () => ({ status: 'ok' }),
        getAccess: async () => ({ accessToken: credential.value, accountId: 'stable-account' }),
        fetch: async () => {
          fetches += 1;
          if (fetches === 1) return new Response('tijdelijk niet beschikbaar', { status: 503 });
          return completedResponse([{ type: 'message', role: 'assistant',
            content: [{ type: 'output_text', text: 'Onbereikbaar.' }] }]);
        },
      }),
      pause: async () => {},
    });
  } catch (error) {
    failure = error;
  }

  assert.equal(failure?.code, 'EVA_LLM_BOUNDED_TRANSPORT_UNVERIFIED');
  assert.equal(failure?.providerAttempts, 1);
  assert.equal(credential.coercions(), 0);
  assert.equal(fetches, 0);
  assert.equal(JSON.stringify({ message: failure?.message, runtime:
    kvGet('llm_runtime_status_v1', null) }).includes('hostile-access-token-secret-7251'), false);
});

test('bounded OAuth-route weigert een roterend accountId-object zonder coercion of wirecall', async () => {
  const credential = rotatingPrimitiveCredential('hostile-account-secret-6384');
  let fetches = 0;
  let failure = null;
  try {
    await chatComplete({
      instructions: 'Weiger niet-primitieve OAuth-accountidentiteit.',
      input: [msg('user', 'hostile-account-object')],
      boundedIdenticalTransportRetry: { maxRetries: 1, assertCurrent: () => {} },
      request: __llmTest.requestOnceWith({
        authStatus: () => ({ status: 'ok' }),
        getAccess: async () => ({ accessToken: 'stable-access-token',
          accountId: credential.value }),
        fetch: async (_url, options) => {
          fetches += 1;
          String(options.headers['chatgpt-account-id']);
          if (fetches === 1) return new Response('tijdelijk niet beschikbaar', { status: 503 });
          return completedResponse([{ type: 'message', role: 'assistant',
            content: [{ type: 'output_text', text: 'Onbereikbaar.' }] }]);
        },
      }),
      pause: async () => {},
    });
  } catch (error) {
    failure = error;
  }

  assert.equal(failure?.code, 'EVA_LLM_BOUNDED_TRANSPORT_UNVERIFIED');
  assert.equal(failure?.providerAttempts, 1);
  assert.equal(credential.coercions(), 0);
  assert.equal(fetches, 0);
  assert.equal(JSON.stringify({ message: failure?.message, runtime:
    kvGet('llm_runtime_status_v1', null) }).includes('hostile-account-secret-6384'), false);
});

test('bounded self-attention faalt na twee echte HTTP 503 responses gesloten', async () => {
  let fetches = 0;
  let usageRows = 0;
  const bodies = [];
  await assert.rejects(chatComplete({
    instructions: 'Bind exact één veilige self-attention stap.',
    input: [msg('user', 'Controleer één actuele bijdrage.')],
    rateLimitRetries: 3, transientRetries: 2, authenticationRetries: 1,
    boundedIdenticalTransportRetry: { maxRetries: 1, assertCurrent: () => {} },
    request: oauthWireRequest(async (_url, options) => {
      fetches += 1;
      bodies.push(String(options.body));
      return new Response('tijdelijk niet beschikbaar', { status: 503 });
    }),
    pause: async () => {},
    recordUsage: () => { usageRows += 1; },
  }), (error) => error?.status === 503 && error?.providerAttempts === 2);

  assert.equal(fetches, 2);
  assert.equal(usageRows, 0);
  assert.equal(bodies[0], bodies[1]);
});

test('een geconsumeerd echt HTTP 503 bewijs kan niet opnieuw retrybevoegdheid geven', async () => {
  let savedProviderError = null;
  let firstCallAttempts = 0;
  await assert.rejects(chatComplete({
    instructions: 'Bind exact één veilige self-attention stap.',
    input: [msg('user', 'Consumeer dit transportbewijs eenmaal.')],
    boundedIdenticalTransportRetry: { maxRetries: 1, assertCurrent: () => {} },
    request: oauthWireRequest(async () => {
      firstCallAttempts += 1;
      return new Response('tijdelijk niet beschikbaar', { status: 503 });
    }),
    pause: async () => {},
  }), (error) => {
    savedProviderError = error;
    return error?.status === 503 && error?.providerAttempts === 2;
  });
  assert.equal(firstCallAttempts, 2,
    'de eerste catch consumeert bewijs en opent hoogstens wireattempt twee');

  let replayAttempts = 0;
  let replayPauses = 0;
  await assert.rejects(chatComplete({
    instructions: 'Bind exact één veilige self-attention stap.',
    input: [msg('user', 'Replay hetzelfde foutobject niet.')],
    boundedIdenticalTransportRetry: { maxRetries: 1, assertCurrent: () => {} },
    request: async () => { replayAttempts += 1; throw savedProviderError; },
    pause: async () => { replayPauses += 1; },
  }), (error) => error === savedProviderError && error?.providerAttempts === 1);
  assert.equal(replayAttempts, 1);
  assert.equal(replayPauses, 0);
});

test('bounded identical retry isoleert 503 strikt van andere providerklassen', async () => {
  for (const status of [400, 401, 403, 422, 429, 500, 502, 504]) {
    let fetches = 0;
    let pauses = 0;
    await assert.rejects(chatComplete({
      instructions: 'Bind exact één veilige self-attention stap.',
      input: [msg('user', `Providerklasse ${status}.`)],
      rateLimitRetries: 3, transientRetries: 2, authenticationRetries: 1,
      boundedIdenticalTransportRetry: { maxRetries: 1, assertCurrent: () => {} },
      request: oauthWireRequest(async () => {
        fetches += 1;
        return new Response(JSON.stringify({ error: { code: `test_${status}` } }), {
          status, headers: { 'content-type': 'application/json' },
        });
      }),
      pause: async () => { pauses += 1; },
    }), (error) => error?.status === status && error?.providerAttempts === 1);
    assert.equal(fetches, 1, String(status));
    assert.equal(pauses, 0, String(status));
  }
});

test('bounded identical retry vertrouwt geen geforgede 503 of post-response fout', async () => {
  const scenarios = [
    {
      name: 'forged',
      request: async () => { throw Object.assign(new Error('service unavailable'), { status: 503 }); },
    },
    {
      name: 'network',
      fetch: async () => { throw new Error('fetch failed'); },
    },
    {
      name: 'timeout',
      fetch: async () => { throw Object.assign(new Error('request timeout'), { code: 'EVA_LLM_TIMEOUT' }); },
    },
    {
      name: 'empty',
      fetch: async () => new Response('', { status: 200,
        headers: { 'content-type': 'text/event-stream' } }),
    },
    {
      name: 'malformed-sse',
      fetch: async () => new Response('data: geen-json\n\n', { status: 200,
        headers: { 'content-type': 'text/event-stream' } }),
    },
    {
      name: 'response-failed',
      fetch: async () => new Response(`data: ${JSON.stringify({ type: 'response.failed',
        response: { error: { status: 503, message: 'service unavailable' } } })}\n\n`, {
        status: 200, headers: { 'content-type': 'text/event-stream' },
      }),
    },
  ];
  for (const scenario of scenarios) {
    let attempts = 0;
    let pauses = 0;
    const request = scenario.request || oauthWireRequest(async (...args) => {
      attempts += 1;
      return scenario.fetch(...args);
    });
    await assert.rejects(chatComplete({
      instructions: 'Bind exact één veilige self-attention stap.',
      input: [msg('user', scenario.name)],
      rateLimitRetries: 3, transientRetries: 2, authenticationRetries: 1,
      boundedIdenticalTransportRetry: { maxRetries: 1, assertCurrent: () => {} },
      request: async (...args) => {
        if (scenario.request) attempts += 1;
        return request(...args);
      },
      pause: async () => { pauses += 1; },
    }), (error) => error?.providerAttempts === 1);
    assert.equal(attempts, 1, scenario.name);
    assert.equal(pauses, 0, scenario.name);
  }
});

test('bounded retry stopt vóór wirecall twee bij deadline, preemption of claimexpiry', async () => {
  for (const scenario of [
    { name: 'deadline', timeoutMs: 400, gate: () => {} },
    { name: 'preempted-before-wait', gate: () => {
      throw Object.assign(new Error('beurt ingehaald'), { code: 'EVA_TURN_SUPERSEDED' });
    } },
    { name: 'goal-expired-before-retry', gate: () => {
      throw Object.assign(new Error('doelclaim verlopen'), { code: 'EVA_SELF_ATTENTION_EXPIRED' });
    } },
  ]) {
    let fetches = 0;
    let pauses = 0;
    await assert.rejects(chatComplete({
      instructions: 'Bind exact één veilige self-attention stap.',
      input: [msg('user', scenario.name)],
      timeoutMs: scenario.timeoutMs ?? 2_000,
      boundedIdenticalTransportRetry: { maxRetries: 1, assertCurrent: scenario.gate },
      request: oauthWireRequest(async () => {
        fetches += 1;
        return new Response('tijdelijk niet beschikbaar', { status: 503 });
      }),
      pause: async () => { pauses += 1; },
    }), (error) => error?.providerAttempts === 1);
    assert.equal(fetches, 1, scenario.name);
    assert.equal(pauses, 0, scenario.name);
  }

  let current = true;
  let fetches = 0;
  let pauses = 0;
  await assert.rejects(chatComplete({
    instructions: 'Bind exact één veilige self-attention stap.',
    input: [msg('user', 'preempted-during-wait')], timeoutMs: 2_000,
    boundedIdenticalTransportRetry: { maxRetries: 1, assertCurrent: () => {
      if (!current) throw Object.assign(new Error('beurt ingehaald'), { code: 'EVA_TURN_SUPERSEDED' });
    } },
    request: oauthWireRequest(async () => {
      fetches += 1;
      return new Response('tijdelijk niet beschikbaar', { status: 503 });
    }),
    pause: async () => { pauses += 1; current = false; },
  }), (error) => error?.code === 'EVA_TURN_SUPERSEDED' && error?.providerAttempts === 1);
  assert.equal(fetches, 1);
  assert.equal(pauses, 1);
});

test('bounded retry weigert een niet-gate-aware tweede request gesloten', async () => {
  let current = true;
  let gateCalls = 0;
  const currentAtRequest = [];
  await assert.rejects(chatComplete({
    instructions: 'Bind exact één veilige self-attention stap.',
    input: [msg('user', 'Laat geen preemption-gap na de laatste gate.')],
    boundedIdenticalTransportRetry: { maxRetries: 1, assertCurrent: () => {
      gateCalls += 1;
      if (gateCalls === 2) queueMicrotask(() => { current = false; });
    } },
    request: (body, options) => {
      currentAtRequest.push(current);
      if (currentAtRequest.length === 1) {
        return oauthWireRequest(async () => new Response(
          'tijdelijk niet beschikbaar', { status: 503 },
        ))(body, options);
      }
      return Promise.resolve({
        output: [{ type: 'message', role: 'assistant',
          content: [{ type: 'output_text', text: 'Veilig hervat.' }] }],
        usage: null,
      });
    },
    pause: async () => {},
  }), (error) => error?.code === 'EVA_LLM_BOUNDED_TRANSPORT_UNVERIFIED'
    && error?.providerAttempts === 1);

  assert.equal(gateCalls, 2);
  assert.deepEqual(currentAtRequest, [true],
    'een onbekend transportpad mag request twee niet ontvangen');
  assert.equal(current, false);
});

test('bounded attempt twee herbevestigt authority na OAuth direct vóór de wire-fetch', async () => {
  let current = true;
  let accessCalls = 0;
  let fetches = 0;
  let gateCalls = 0;
  const bodies = [];
  const before = llmRuntimeStatus().provider_request_attempts;

  let failure = null;
  try {
    await chatComplete({
      instructions: 'Bind exact één veilige self-attention stap.',
      input: [msg('user', 'Laat een verlopen claim nooit wirecall twee bereiken.')],
      boundedIdenticalTransportRetry: { maxRetries: 1, assertCurrent: () => {
        gateCalls += 1;
        if (!current) throw Object.assign(
          new Error('doelclaim verlopen tijdens OAuth'),
          { code: 'EVA_SELF_ATTENTION_EXPIRED' },
        );
      } },
      request: __llmTest.requestOnceWith({
        authStatus: () => ({ status: 'ok' }),
        getAccess: async () => {
          accessCalls += 1;
          if (accessCalls === 2) {
            await Promise.resolve();
            current = false;
          }
          return { accessToken: 'test-oauth-token', accountId: 'test-account' };
        },
        fetch: async (_url, fetchOptions) => {
          fetches += 1;
          bodies.push(String(fetchOptions.body));
          if (fetches === 1) {
            return new Response('tijdelijk niet beschikbaar', { status: 503 });
          }
          return completedResponse([{ type: 'message', role: 'assistant',
            content: [{ type: 'output_text', text: 'Deze fetch mag niet starten.' }] }]);
        },
      }),
      pause: async () => {},
    });
  } catch (error) {
    failure = error;
  }

  assert.equal(accessCalls, 2);
  assert.equal(fetches, 1);
  assert.equal(bodies.length, 1);
  assert.equal(gateCalls, 3);
  assert.equal(llmRuntimeStatus().provider_request_attempts - before, 1);
  assert.equal(failure?.code, 'EVA_SELF_ATTENTION_EXPIRED');
  assert.equal(failure?.providerAttempts, 1);
});

test('bounded wire twee controleert de absolute deadline na synchrone OAuth-getters en na de authoritygate', async () => {
  for (const phase of ['oauth-getter', 'authority-gate']) {
    const timeoutMs = 700;
    let accessCalls = 0;
    let gateCalls = 0;
    let fetches = 0;
    let failure = null;
    try {
      await chatComplete({
        instructions: 'Start geen tweede fetch na de absolute deadline.',
        input: [msg('user', phase)], timeoutMs,
        boundedIdenticalTransportRetry: { maxRetries: 1, assertCurrent: () => {
          gateCalls += 1;
          if (phase === 'authority-gate' && gateCalls === 3) {
            assert.ok(advanceWallClockPast(Date.now() + timeoutMs + 10) > 0);
          }
        } },
        request: __llmTest.requestOnceWith({
          authStatus: () => ({ status: 'ok' }),
          getAccess: async () => {
            accessCalls += 1;
            if (phase === 'oauth-getter' && accessCalls === 2) {
              const access = {};
              Object.defineProperties(access, {
                accessToken: { enumerable: true, get() {
                  assert.ok(advanceWallClockPast(Date.now() + timeoutMs + 10) > 0);
                  return 'stable-access-token';
                } },
                accountId: { enumerable: true, value: 'stable-account' },
              });
              return access;
            }
            return { accessToken: 'stable-access-token', accountId: 'stable-account' };
          },
          fetch: async () => {
            fetches += 1;
            if (fetches === 1) return new Response('tijdelijk niet beschikbaar', { status: 503 });
            return completedResponse([{ type: 'message', role: 'assistant',
              content: [{ type: 'output_text', text: 'Te laat gestart.' }] }]);
          },
        }),
        pause: async () => {},
      });
    } catch (error) {
      failure = error;
    }
    assert.equal(failure?.code, 'EVA_LLM_TIMEOUT', phase);
    assert.equal(failure?.providerAttempts, 1, phase);
    assert.equal(fetches, 1, phase);
  }
});

test('een fout ná bounded wirecall twee opent nooit een derde providerrequest', async () => {
  let fetches = 0;
  await assert.rejects(chatComplete({
    instructions: 'Bind exact één veilige self-attention stap.',
    input: [msg('user', 'Tweede response is leeg.')],
    boundedIdenticalTransportRetry: { maxRetries: 1, assertCurrent: () => {} },
    request: oauthWireRequest(async () => {
      fetches += 1;
      if (fetches === 1) return new Response('tijdelijk niet beschikbaar', { status: 503 });
      return new Response('', { status: 200,
        headers: { 'content-type': 'text/event-stream' } });
    }),
    pause: async () => {},
  }), (error) => error?.providerAttempts === 2);
  assert.equal(fetches, 2);
});

test('de process-lokale providerteller volgt exact iedere echte wire-fetch, ook bij retries', async () => {
  for (const scenario of [
    { name: 'rate', status: 429, oauth: false,
      rateLimitRetries: 1, transientRetries: 0, authenticationRetries: 0 },
    { name: 'transient', status: 503, oauth: false,
      rateLimitRetries: 0, transientRetries: 1, authenticationRetries: 0 },
    { name: 'auth', status: 401, oauth: true,
      rateLimitRetries: 0, transientRetries: 0, authenticationRetries: 1 },
  ]) {
    const before = llmRuntimeStatus().provider_request_attempts;
    let fetches = 0;
    const result = await chatComplete({
      instructions: 'Antwoord kort.', input: [msg('user', `Herstel ${scenario.name}.`)],
      rateLimitRetries: scenario.rateLimitRetries,
      transientRetries: scenario.transientRetries,
      authenticationRetries: scenario.authenticationRetries,
      pause: async () => {},
      request: (body, options) => __llmTest.requestOnce(body, {
        ...options,
        authStatus: () => ({ status: scenario.oauth ? 'ok' : 'none' }),
        getAccess: async () => ({ accessToken: 'test-oauth-token', accountId: 'test-account' }),
        apiKey: 'test-api-key',
        fetch: async () => {
          fetches += 1;
          if (fetches === 1) return new Response(JSON.stringify({ error: {
            code: `test_${scenario.name}`, param: 'none', message: 'testfout',
          } }), { status: scenario.status, headers: { 'content-type': 'application/json' } });
          return completedResponse([{ type: 'message', role: 'assistant',
            content: [{ type: 'output_text', text: 'Hersteld.' }] }], {
            input_tokens: 20, output_tokens: 4, total_tokens: 24,
          });
        },
      }),
    });
    assert.equal(result.text, 'Hersteld.');
    assert.equal(fetches, 2, scenario.name);
    assert.equal(llmRuntimeStatus().provider_request_attempts - before, fetches,
      scenario.name);
  }
});

test('auth- en serialisatiefouten vóór de wire-fetch verhogen de providerteller niet', async () => {
  const before = llmRuntimeStatus().provider_request_attempts;
  let fetches = 0;
  await assert.rejects(__llmTest.requestOnce({ model: 'test' }, {
    authStatus: () => ({ status: 'ok' }),
    getAccess: async () => { throw new Error('auth vóór fetch geweigerd'); },
    fetch: async () => { fetches += 1; throw new Error('onbereikbaar'); },
  }), /auth vóór fetch geweigerd/u);

  const cyclic = /** @type {any} */ ({ model: 'test' });
  cyclic.self = cyclic;
  await assert.rejects(__llmTest.requestOnce(cyclic, {
    authStatus: () => ({ status: 'none' }), apiKey: 'test-api-key',
    fetch: async () => { fetches += 1; throw new Error('onbereikbaar'); },
  }), /circular|cyclic/iu);
  assert.equal(fetches, 0);
  assert.equal(llmRuntimeStatus().provider_request_attempts, before);
});

test('een optionele outputtokenlimiet blijft begrensd en ontbreekt op gewone requests', async () => {
  const bodies = [];
  const complete = (maxOutputTokens) => chatComplete({
    instructions: 'Antwoord kort.',
    input: [msg('user', 'Leg uitsluitend de requestvorm vast.')],
    ...(maxOutputTokens === undefined ? {} : { maxOutputTokens }),
    request: async (body) => {
      bodies.push(structuredClone(body));
      return {
        output: [{ type: 'message', role: 'assistant',
          content: [{ type: 'output_text', text: 'Vastgelegd.' }] }],
        usage: null,
      };
    },
  });

  await complete(undefined);
  await complete(8_000.9);
  await complete(999_999);
  await complete(Number.POSITIVE_INFINITY);

  assert.equal(Object.hasOwn(bodies[0], 'max_output_tokens'), false);
  assert.equal(bodies[1].max_output_tokens, 8_000);
  assert.equal(bodies[2].max_output_tokens, 128_000);
  assert.equal(Object.hasOwn(bodies[3], 'max_output_tokens'), false);
});

test('requestOnce laat de outputcap alleen door op de officiële API-key Responses-route', async () => {
  const requests = [];
  const body = { model: 'gpt-5.6-terra', stream: true, max_output_tokens: 8_000 };
  const fetch = async (url, options) => {
    requests.push({ url: String(url), body: JSON.parse(String(options.body || '{}')) });
    return completedResponse([{ type: 'message', role: 'assistant',
      content: [{ type: 'output_text', text: 'Vastgelegd.' }] }], {
      input_tokens: 20, output_tokens: 8, total_tokens: 28,
    });
  };

  await __llmTest.requestOnce(body, {
    authStatus: () => ({ status: 'ok' }),
    getAccess: async () => ({ accessToken: 'test-oauth-token', accountId: 'test-account' }),
    fetch,
  });
  await __llmTest.requestOnce(body, {
    authStatus: () => ({ status: 'none' }), apiKey: 'test-api-key', fetch,
  });

  assert.equal(requests[0].url, 'https://chatgpt.com/backend-api/codex/responses');
  assert.equal(Object.hasOwn(requests[0].body, 'max_output_tokens'), false);
  assert.equal(requests[1].url, 'https://api.openai.com/v1/responses');
  assert.equal(requests[1].body.max_output_tokens, 8_000);
  assert.equal(body.max_output_tokens, 8_000,
    'transportcompatibiliteit mag de interne sequentiegrens niet muteren');
});

test('een provider-contractfout bewaart alleen veilige status-, code- en parammetadata', async () => {
  await assert.rejects(chatComplete({
    instructions: 'Leg de requestvorm vast.', input: [msg('user', 'Test contractfout.')],
    rateLimitRetries: 0, transientRetries: 0, authenticationRetries: 0,
    request: (body, options) => __llmTest.requestOnce(body, {
      ...options,
      authStatus: () => ({ status: 'ok' }),
      getAccess: async () => ({ accessToken: 'test-oauth-token', accountId: 'test-account' }),
      fetch: async () => new Response(JSON.stringify({ error: {
        message: 'gevoelige providerdetails mogen niet in Eva haar fouttekst',
        type: 'invalid_request_error', code: 'unsupported_parameter',
        param: 'max_output_tokens',
      } }), { status: 400, headers: { 'content-type': 'application/json' } }),
    }),
  }), (error) => error?.status === 400
    && error?.code === 'EVA_LLM_PROVIDER_ERROR'
    && error?.providerCode === 'unsupported_parameter'
    && error?.providerParam === 'max_output_tokens'
    && !/gevoelige providerdetails/u.test(error.message));

  const state = kvGet('llm_runtime_status_v1', null);
  assert.deepEqual({
    failureKind: state.failureKind, statusCode: state.statusCode,
    providerCode: state.providerCode, providerParam: state.providerParam,
  }, {
    failureKind: 'request_contract', statusCode: 400,
    providerCode: 'unsupported_parameter', providerParam: 'max_output_tokens',
  });
});

test('een SSE response.failed houdt providertekst en secrets buiten fout en runtime', async () => {
  const secret = `sse-provider-secret-8642-${'X'.repeat(12_000)}`;
  let failure = null;
  try {
    await chatComplete({
      instructions: 'Laat providertekst niet ontsnappen.',
      input: [msg('user', 'SSE-foutcanary.')],
      rateLimitRetries: 0, transientRetries: 0, authenticationRetries: 0,
      request: oauthWireRequest(async () => new Response(`data: ${JSON.stringify({
        type: 'response.failed', response: { error: {
          status: 400, code: 'invalid_request_error', param: 'input', message: secret,
        } },
      })}\n\n`, { status: 200, headers: { 'content-type': 'text/event-stream' } })),
    });
  } catch (error) {
    failure = error;
  }

  assert.equal(failure?.code, 'EVA_LLM_PROVIDER_ERROR');
  assert.equal(failure?.status, 400);
  assert.equal(failure?.providerCode, 'invalid_request_error');
  assert.equal(failure?.providerParam, 'input');
  assert.equal(String(failure?.message || '').includes('sse-provider-secret-8642'), false);
  assert.ok(String(failure?.message || '').length < 160);
  const state = kvGet('llm_runtime_status_v1', null);
  assert.equal(JSON.stringify(state).includes('sse-provider-secret-8642'), false);
  assert.deepEqual({
    failureKind: state.failureKind, statusCode: state.statusCode,
    providerCode: state.providerCode, providerParam: state.providerParam,
  }, {
    failureKind: 'request_contract', statusCode: 400,
    providerCode: 'invalid_request_error', providerParam: 'input',
  });
});

test('SSE-foutmetadata accepteert geen controltekens, overmaat of objectcoercion', async () => {
  const secret = `unsafe-sse-metadata-secret-5921-${'Z'.repeat(200)}`;
  let failure = null;
  try {
    await chatComplete({
      instructions: 'Bewaar uitsluitend strak getypeerde foutmetadata.',
      input: [msg('user', 'Metadata-canary.')],
      rateLimitRetries: 0, transientRetries: 0, authenticationRetries: 0,
      request: oauthWireRequest(async () => new Response(`data: ${JSON.stringify({
        type: 'response.failed', response: { error: {
          status: 422, code: `invalid\n${secret}`, param: { secret }, message: secret,
        } },
      })}\n\n`, { status: 200, headers: { 'content-type': 'text/event-stream' } })),
    });
  } catch (error) {
    failure = error;
  }

  assert.equal(failure?.code, 'EVA_LLM_PROVIDER_ERROR');
  assert.equal(failure?.status, 422);
  assert.equal(failure?.providerCode, null);
  assert.equal(failure?.providerParam, null);
  assert.equal(JSON.stringify({ failure: {
    message: failure?.message, providerCode: failure?.providerCode,
    providerParam: failure?.providerParam,
  }, runtime: kvGet('llm_runtime_status_v1', null) }).includes(
    'unsafe-sse-metadata-secret-5921',
  ), false);
});

test('OAuth zonder geldig usagebewijs stopt een begrensde modelreeks vóór actie of vervolgcall', async () => {
  const handle = {
    alias: 'materialiseer', description: 'Materialiseer exact één tekst.',
    parameters: { type: 'object', additionalProperties: false,
      properties: { inhoud: { type: 'string' } }, required: ['inhoud'] },
  };
  const usageBefore = db.prepare("SELECT count(*) AS count FROM timeline WHERE kind='llm_usage'")
    .get().count;
  for (const scenario of [
    { name: 'ontbrekend', usage: undefined, code: 'EVA_LLM_OUTPUT_BUDGET_UNVERIFIED' },
    { name: 'overschreden', usage: { input_tokens: 20, output_tokens: 2_001,
      total_tokens: 2_021 }, code: 'EVA_LLM_OUTPUT_BUDGET_EXCEEDED' },
  ]) {
    let providerRequests = 0;
    let executions = 0;
    const complete = (request) => chatComplete({
      ...request,
      request: (requestBody, options) => __llmTest.requestOnce(requestBody, {
        ...options,
        authStatus: () => ({ status: 'ok' }),
        getAccess: async () => ({ accessToken: 'test-oauth-token', accountId: 'test-account' }),
        fetch: async (_url, fetchOptions) => {
          providerRequests += 1;
          const wireBody = JSON.parse(String(fetchOptions.body || '{}'));
          assert.equal(Object.hasOwn(wireBody, 'max_output_tokens'), false);
          return completedResponse([{ type: 'function_call', call_id: `call-${scenario.name}`,
            name: 'materialiseer', arguments: JSON.stringify({ inhoud: 'klaar' }) }], scenario.usage);
        },
      }),
    });
    await assert.rejects(runExecutionDriver({
      implementationSpec: 'Schrijf de tekst.', understandingSummary: { bounded: true },
      offeredHandles: [handle], maxRounds: 2, baseRounds: 2,
      maxOutputTokens: 2_000, rateLimitRetries: 0,
      transientRetries: 0, authenticationRetries: 0, fallbackModel: null,
      obligations: () => ['materialiseer'], done: () => false, complete,
      execute: async () => {
        executions += 1;
        return { contract: 'ActionResult/v1', ok: true, verified: true, changed: true };
      },
    }), (error) => error?.code === scenario.code);
    assert.equal(providerRequests, 1, scenario.name);
    assert.equal(executions, 0, scenario.name);
  }
  const usageRows = db.prepare("SELECT meta FROM timeline WHERE kind='llm_usage' ORDER BY id")
    .all();
  assert.equal(usageRows.length, usageBefore + 1,
    'alleen de oversized respons heeft werkelijk usagebewijs om te boeken');
  const oversizedUsage = JSON.parse(usageRows.at(-1).meta);
  assert.deepEqual({
    outputTokens: oversizedUsage.outputTokens,
    requestAttempts: oversizedUsage.requestAttempts,
  }, { outputTokens: 2_001, requestAttempts: 1 });
  assert.equal(kvGet('llm_runtime_status_v1', null).failureKind, 'output_budget');
});

test('de begrensde execution-driver draagt cap en uitgeschakelde retries per ronde', async () => {
  const handle = {
    alias: 'materialiseer', description: 'Materialiseer exact één tekst.',
    parameters: { type: 'object', additionalProperties: false,
      properties: { inhoud: { type: 'string' } }, required: ['inhoud'] },
  };
  const requests = [];
  const result = await runExecutionDriver({
    implementationSpec: 'Schrijf de tekst.', understandingSummary: { bounded: true },
    offeredHandles: [handle], maxRounds: 1, baseRounds: 1,
    maxOutputTokens: 2_000, rateLimitRetries: 0,
    transientRetries: 0, authenticationRetries: 0, fallbackModel: null,
    obligations: () => ['materialiseer'],
    done: ({ receipts }) => receipts.length === 1,
    complete: async (request) => {
      requests.push(request);
      return { output: [], text: '', toolCalls: [{ call_id: 'bounded-write',
        name: 'materialiseer', arguments: JSON.stringify({ inhoud: 'klaar' }) }] };
    },
    execute: async () => ({ contract: 'ActionResult/v1', ok: true,
      verified: true, changed: true }),
  });
  assert.equal(result.status, 'completed');
  assert.equal(requests.length, 1);
  assert.deepEqual({
    maxOutputTokens: requests[0].maxOutputTokens,
    rateLimitRetries: requests[0].rateLimitRetries,
    transientRetries: requests[0].transientRetries,
    authenticationRetries: requests[0].authenticationRetries,
  }, { maxOutputTokens: 2_000, rateLimitRetries: 0,
    transientRetries: 0, authenticationRetries: 0 });
});

test('de totale beurtdeadline omvat ook een hangende OAuth-tokenroute', async () => {
  let authCalls = 0;
  let responseFetches = 0;
  const started = Date.now();
  await assert.rejects(chatComplete({
    instructions: 'Antwoord kort.',
    input: [msg('user', 'Dit is de verkorte 24-seconden-voicecanary.')],
    timeoutMs: 40,
    rateLimitRetries: 0,
    transientRetries: 0,
    request: (body, options) => __llmTest.requestOnce(body, {
      ...options,
      authStatus: () => ({ status: 'ok' }),
      getAccess: () => {
        authCalls += 1;
        return new Promise(() => {});
      },
      fetch: async () => {
        responseFetches += 1;
        throw new Error('Responses-fetch mag vóór OAuth niet starten');
      },
    }),
  }), (error) => error?.code === 'EVA_LLM_TIMEOUT' && /tijdslimiet/i.test(error.message));

  assert.equal(authCalls, 1);
  assert.equal(responseFetches, 0);
  assert.ok(Date.now() - started < 500, 'de geïnjecteerde harde deadline moet de authwacht afbreken');
});
