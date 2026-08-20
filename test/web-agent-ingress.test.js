import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync('/tmp/bottie-web-agent-ingress-');
process.env.EVA_DATA_DIR = dataDir;
process.env.OPENAI_API_KEY = 'test-only';
process.env.WEB_HOST = '127.0.0.1';
process.env.WEB_PORT = '0';
process.env.NODE_ENV = 'test';

const { claimTurnToolExecution, db } = await import('../src/db.js');
const { canonicalJson } = await import('../src/util.js');
const {
  handleMessageDetailed,
  preparedUnderstandingIngressReplay,
  verifiedPreparedUnderstandingTurnForResult,
} = await import('../src/mind/agent.js');
const { startWeb } = await import('../src/web/server.js');

after(() => {
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

function completedModelResponse(output) {
  const event = { type: 'response.completed', response: { output } };
  return new Response(`data: ${JSON.stringify(event)}\n\n`, {
    status: 200,
    headers: { 'content-type': 'text/event-stream' },
  });
}

function replyUnderstanding(text, replyDraft) {
  return completedModelResponse([{
    type: 'function_call', call_id: 'web-ingress-understanding',
    name: 'commit_turn_understanding',
    arguments: JSON.stringify({
      dialogueAct: 'chat',
      goal: { statement: text, outcome: 'Geef een gewoon antwoord.', beneficiary: 'requester' },
      conditions: [],
      provenance: { sourceSpans: [{ start: 0, end: text.length }], contextHashes: [] },
      confidence: 0.99, actions: [], requestedEffects: [], deniedEffects: [],
      temporal: { mode: 'none', startSourceSpans: [] }, durability: 'turn',
      delivery: { mode: 'reply', target: 'origin' }, proofNeeds: [], ambiguities: [],
      firstDecision: { kind: 'reply', requirementIds: [], replyDraft },
    }),
  }]);
}

function textResponse(text) {
  return completedModelResponse([{
    type: 'message', role: 'assistant',
    content: [{ type: 'output_text', text }],
  }]);
}

function omittedMaterialUnderstanding(text) {
  return completedModelResponse([{
    type: 'function_call', call_id: 'web-material-omission',
    name: 'commit_turn_understanding',
    arguments: JSON.stringify({
      dialogueAct: 'command',
      goal: { statement: text, outcome: 'Een controleerbaar intern bestand.', beneficiary: 'requester' },
      conditions: [],
      provenance: { sourceSpans: [{ start: 0, end: text.length }], contextHashes: [] },
      confidence: 0.99, actions: [], requestedEffects: [], deniedEffects: [],
      temporal: { mode: 'now', startSourceSpans: [] }, durability: 'turn',
      delivery: { mode: 'reply', target: 'origin' }, proofNeeds: [], ambiguities: [],
      firstDecision: {
        kind: 'reply', requirementIds: [], replyDraft: 'Ik heb het bestand gecorrigeerd.',
      },
    }),
  }]);
}

test('webrequest krijgt vóór antwoord een stabiele duurzame agent-ingress en replayt zonder provider', async () => {
  const originalFetch = globalThis.fetch;
  const text = 'Hoe gaat het vandaag met je?';
  const reply = 'Goed. Ik ben aandachtig en rustig aanwezig.';
  const messageId = 'web-request:durable-agent:0001';
  const conversationId = 'web:durable-agent';
  let providerCalls = 0;
  globalThis.fetch = async (input, init) => {
    if (String(input).startsWith('http://127.0.0.1:')) return originalFetch(input, init);
    providerCalls += 1;
    return providerCalls % 2 === 1
      ? replyUnderstanding(text, reply)
      : textResponse(reply);
  };
  let first;
  let replay;
  try {
    first = await handleMessageDetailed({
      channel: 'web', sender: 'Jaap', text,
      messageContext: { conversationId, messageId, identityTrusted: true },
    });
    const callsAfterFirst = providerCalls;
    replay = await handleMessageDetailed({
      channel: 'web', sender: 'Jaap', text,
      messageContext: { conversationId, messageId, identityTrusted: true },
    });
    assert.equal(providerCalls, callsAfterFirst,
      'dezelfde request mag provider of tools niet opnieuw bereiken');
    await assert.rejects(() => handleMessageDetailed({
      channel: 'web', sender: 'Jaap', text: `${text} andere inhoud`,
      messageContext: { conversationId, messageId, identityTrusted: true },
    }), /andere handeling gebonden/iu);
    assert.equal(providerCalls, callsAfterFirst,
      'dezelfde request-id met andere inhoud faalt vóór de provider');
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.equal(replay.replayed, true);
  assert.equal(replay.reply, first.reply);
  assert.equal(replay.turnId, first.turnId);
  assert.equal(replay.originMessageId, messageId);
  assert.equal(db.prepare(`SELECT count(*) AS count FROM messages
    WHERE channel='web' AND conversation_id=? AND role='user'`).get(conversationId).count, 1);
  assert.equal(db.prepare(`SELECT count(*) AS count FROM timeline
    WHERE kind='chat_in' AND json_extract(meta,'$.conversationId')=?`).get(conversationId).count, 1);
  const ingress = db.prepare(`SELECT status,output FROM turn_tool_executions
    WHERE tool='@turn/understanding' AND json_extract(args,'$.channel')='web'
      AND json_extract(args,'$.conversationId')=?
      AND json_extract(args,'$.messageId')=?`).get(conversationId, messageId);
  assert.equal(ingress.status, 'done');
  const prepared = JSON.parse(ingress.output);
  assert.equal(prepared.contract, 'PreparedUnderstandingTurn/v1');
  assert.equal(prepared.turnId, first.turnId);
  assert.equal(prepared.reply, first.reply);
  assert.match(prepared.sha256, /^[a-f0-9]{64}$/u);
  assert.equal(verifiedPreparedUnderstandingTurnForResult(first, {
    channel: 'web', conversationId, messageId,
  })?.sha256, prepared.sha256);
  assert.equal(verifiedPreparedUnderstandingTurnForResult({
    ...first, reply: `${first.reply} vervalst`,
  }, { channel: 'web', conversationId, messageId }), null);
  assert.equal(preparedUnderstandingIngressReplay({
    channel: 'web', conversationId, messageId, sender: 'Jaap',
  })?.reply, first.reply);

  const restarted = spawnSync(process.execPath, ['--input-type=module', '-e', `
    let providerCalls=0;
    globalThis.fetch=async()=>{providerCalls+=1;throw new Error('provider mag niet starten')};
    const {handleMessageDetailed}=await import('./src/mind/agent.js');
    const replay=await handleMessageDetailed(${JSON.stringify({
      channel: 'web', sender: 'Jaap', text,
      messageContext: { conversationId, messageId, identityTrusted: true },
    })});
    const {db}=await import('./src/db.js');
    console.log(JSON.stringify({providerCalls,replay}));
    db.close();
    process.exit(0);
  `], {
    cwd: path.resolve(new URL('..', import.meta.url).pathname),
    encoding: 'utf8', timeout: 30_000,
    env: {
      ...process.env, EVA_DATA_DIR: dataDir, TMPDIR: '/tmp',
      EVA_RUNTIME_DISABLE_INFERENCE: 'true',
      EVA_RUNTIME_DISABLE_WORLD_LEARNING: 'true',
    },
  });
  assert.equal(restarted.status, 0, restarted.stderr || restarted.stdout);
  const restartedOutcome = JSON.parse(restarted.stdout.trim().split('\n').at(-1));
  assert.equal(restartedOutcome.providerCalls, 0);
  assert.equal(restartedOutcome.replay.replayed, true);
  assert.equal(restartedOutcome.replay.turnId, first.turnId);
  assert.equal(restartedOutcome.replay.reply, first.reply);
});

test('een ingehaalde webbeurt bewaart een exact Prepared supersession-resultaat', async () => {
  const originalFetch = globalThis.fetch;
  const conversationId = 'web:prepared-supersession';
  const firstText = 'Eerste vraag die wordt ingehaald.';
  const secondText = 'Tweede en actuele vraag.';
  let releaseFirst;
  let firstProviderStarted;
  const firstStarted = new Promise((resolve) => { firstProviderStarted = resolve; });
  const firstGate = new Promise((resolve) => { releaseFirst = resolve; });
  let calls = 0;
  globalThis.fetch = async (_input, options = {}) => {
    const request = JSON.parse(String(options.body || '{}'));
    calls += 1;
    if (calls === 1) {
      firstProviderStarted();
      await firstGate;
      return replyUnderstanding(firstText, 'Oud antwoord.');
    }
    if (request.tools?.some((tool) => tool.name === 'commit_turn_understanding')) {
      return replyUnderstanding(secondText, 'Actueel antwoord.');
    }
    return textResponse('Actueel antwoord.');
  };
  let first;
  let second;
  try {
    const firstPromise = handleMessageDetailed({
      channel: 'web', sender: 'Jaap', text: firstText,
      messageContext: {
        conversationId, messageId: 'web-superseded:0001', identityTrusted: true,
      },
    });
    await firstStarted;
    const secondPromise = handleMessageDetailed({
      channel: 'web', sender: 'Jaap', text: secondText,
      messageContext: {
        conversationId, messageId: 'web-superseded:0002', identityTrusted: true,
      },
    });
    releaseFirst();
    [first, second] = await Promise.all([firstPromise, secondPromise]);
  } finally {
    globalThis.fetch = originalFetch;
  }
  assert.equal(first.superseded, true);
  assert.equal(first.reply, '');
  assert.equal(second.reply, 'Actueel antwoord.');
  const prepared = JSON.parse(db.prepare(`SELECT output FROM main.turn_tool_executions
    WHERE tool='@turn/understanding' AND json_extract(args,'$.messageId')=?`)
    .get('web-superseded:0001').output);
  assert.equal(prepared.contract, 'PreparedUnderstandingTurn/v1');
  assert.equal(prepared.metadata.superseded, true);
  assert.equal(prepared.metadata.yielded, true);
  assert.equal(prepared.reply, '');
});

test('de echte HTTP-route verzegelt uitsluitend het duurzame agentantwoord en replayt exact', async () => {
  const originalFetch = globalThis.fetch;
  const text = 'Vertel me één rustige zin.';
  const reply = 'Ik ben hier en houd mijn aandacht bij wat werkelijk gebeurt.';
  const requestId = 'web-request:http-agent:0001';
  const conversationId = 'web:http-agent';
  let providerCalls = 0;
  globalThis.fetch = async (input, init) => {
    if (String(input).startsWith('http://127.0.0.1:')) return originalFetch(input, init);
    providerCalls += 1;
    return providerCalls % 2 === 1
      ? replyUnderstanding(text, reply)
      : textResponse(reply);
  };
  const server = await startWeb();
  const address = server.address();
  assert.ok(address && typeof address === 'object');
  const post = async () => {
    const response = await fetch(`http://127.0.0.1:${address.port}/api/chat`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Idempotency-Key': requestId,
      },
      body: JSON.stringify({ text, conversation_id: conversationId, request_id: requestId }),
    });
    return { response, body: await response.json() };
  };
  let firstBody;
  try {
    const first = await post();
    const callsAfterFirst = providerCalls;
    const replay = await post();
    assert.equal(first.response.status, 200);
    assert.equal(replay.response.status, 200);
    assert.equal(replay.response.headers.get('Idempotency-Replayed'), 'true');
    assert.deepEqual(replay.body, first.body);
    assert.equal(first.body.reply, reply);
    assert.equal(first.body.conversation_id, conversationId);
    assert.equal(first.body.request_id, requestId);
    assert.equal(first.body.request_replay_scope, 'durable_http_ingress');
    assert.equal(providerCalls, callsAfterFirst);
    firstBody = first.body;
  } finally {
    globalThis.fetch = originalFetch;
    await new Promise((resolve) => server.close(resolve));
  }
  const restarted = spawnSync(process.execPath, ['--input-type=module', '-e', `
    const nativeFetch=globalThis.fetch;
    let providerCalls=0;
    globalThis.fetch=async(input,init)=>{
      if(String(input).startsWith('http://127.0.0.1:')) return nativeFetch(input,init);
      providerCalls+=1;
      throw new Error('provider mag bij HTTP-replay niet starten');
    };
    const {startWeb}=await import('./src/web/server.js');
    const server=await startWeb();
    const address=server.address();
    const response=await nativeFetch('http://127.0.0.1:'+address.port+'/api/chat',{
      method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':${JSON.stringify(requestId)}},
      body:JSON.stringify({text:${JSON.stringify(text)},conversation_id:${JSON.stringify(conversationId)},request_id:${JSON.stringify(requestId)}}),
    });
    const body=await response.json();
    await new Promise(resolve=>server.close(resolve));
    const {db}=await import('./src/db.js');
    console.log(JSON.stringify({status:response.status,replayed:response.headers.get('Idempotency-Replayed'),body,providerCalls}));
    db.close();
  `], {
    cwd: path.resolve(new URL('..', import.meta.url).pathname),
    encoding: 'utf8', timeout: 30_000,
    env: {
      ...process.env, EVA_DATA_DIR: dataDir, TMPDIR: '/tmp', WEB_PORT: '0',
      EVA_RUNTIME_DISABLE_INFERENCE: 'true',
      EVA_RUNTIME_DISABLE_WORLD_LEARNING: 'true',
    },
  });
  assert.equal(restarted.status, 0, restarted.stderr || restarted.stdout);
  const restartedOutcome = JSON.parse(restarted.stdout.trim().split('\n').at(-1));
  assert.equal(restartedOutcome.status, 200);
  assert.equal(restartedOutcome.replayed, 'true');
  assert.equal(restartedOutcome.providerCalls, 0);
  assert.deepEqual(restartedOutcome.body, firstBody);
  assert.equal(db.prepare(`SELECT count(*) AS count FROM eva_http_ingress_requests
    WHERE channel='web' AND conversation_id=? AND request_id=?`)
    .get(conversationId, requestId).count, 1);
  assert.equal(db.prepare(`SELECT count(*) AS count FROM eva_http_prepared_responses
    WHERE request_ref=(SELECT request_ref FROM eva_http_ingress_requests
      WHERE channel='web' AND conversation_id=? AND request_id=?)`)
    .get(conversationId, requestId).count, 1);
  const storedPublic = JSON.parse(db.prepare(`SELECT public_body_json
    FROM eva_http_prepared_responses WHERE request_ref=(SELECT request_ref
      FROM eva_http_ingress_requests WHERE channel='web' AND conversation_id=?
        AND request_id=?)`).get(conversationId, requestId).public_body_json);
  assert.equal(storedPublic.runtimeCodeSha256, firstBody.runtime_code_sha256);
  assert.equal(db.prepare(`SELECT count(*) AS count FROM messages
    WHERE channel='web' AND conversation_id=? AND role='user'`).get(conversationId).count, 1);
  assert.equal(db.prepare(`SELECT count(*) AS count FROM messages
    WHERE channel='web' AND conversation_id=? AND role='assistant'`)
    .get(conversationId).count, 1);
});

test('HTTP-voice gebruikt vóór settlement hetzelfde duurzame Prepared-bewijs als web', async () => {
  const originalFetch = globalThis.fetch;
  const text = 'Geef een rustig antwoord zonder spraakuitvoer.';
  const reply = 'Eerste regel.\nTweede  regel blijft bytegelijk.';
  const requestId = 'voice-request:http-agent:0001';
  const conversationId = 'voice:http-agent';
  let providerCalls = 0;
  globalThis.fetch = async (input, init) => {
    if (String(input).startsWith('http://127.0.0.1:')) return originalFetch(input, init);
    providerCalls += 1;
    return providerCalls % 2 === 1
      ? replyUnderstanding(text, reply)
      : textResponse(reply);
  };
  const server = await startWeb();
  const address = server.address();
  assert.ok(address && typeof address === 'object');
  const post = async () => {
    const response = await fetch(`http://127.0.0.1:${address.port}/api/voice`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Idempotency-Key': requestId },
      body: JSON.stringify({
        text, speak: false, conversation_id: conversationId, request_id: requestId,
      }),
    });
    return { response, body: await response.json() };
  };
  try {
    const first = await post();
    const callsAfterFirst = providerCalls;
    const replay = await post();
    assert.equal(first.response.status, 200, JSON.stringify(first.body));
    assert.equal(first.body.reply, reply);
    assert.equal(replay.response.status, 200);
    assert.deepEqual(replay.body, first.body);
    assert.equal(providerCalls, callsAfterFirst);
    const row = db.prepare(`SELECT output FROM main.turn_tool_executions
      WHERE tool='@turn/understanding' AND json_extract(args,'$.channel')='voice'
        AND json_extract(args,'$.conversationId')=?
        AND json_extract(args,'$.messageId')=?`).get(conversationId, requestId);
    const prepared = JSON.parse(row.output);
    assert.equal(prepared.contract, 'PreparedUnderstandingTurn/v1');
    assert.equal(prepared.channel, 'voice');
    assert.equal(prepared.reply, reply);
    assert.equal(prepared.syntheticTest, false);
  } finally {
    globalThis.fetch = originalFetch;
    await new Promise((resolve) => server.close(resolve));
  }
});

test('een normale materiaalhandoff wordt als Prepared 202 verzegeld en niet generiek voltooid', async () => {
  const originalFetch = globalThis.fetch;
  const text = 'Corrigeer data/workspace/http-handoff.md en sla het controleerbaar op.';
  const requestId = 'web-request:http-handoff:0001';
  const conversationId = 'web:http-handoff';
  let providerCalls = 0;
  globalThis.fetch = async (input, init) => {
    if (String(input).startsWith('http://127.0.0.1:')) return originalFetch(input, init);
    providerCalls += 1;
    return omittedMaterialUnderstanding(text);
  };
  const server = await startWeb();
  const address = server.address();
  assert.ok(address && typeof address === 'object');
  const post = async () => {
    const response = await fetch(`http://127.0.0.1:${address.port}/api/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Idempotency-Key': requestId },
      body: JSON.stringify({ text, conversation_id: conversationId, request_id: requestId }),
    });
    return { response, body: await response.json() };
  };
  try {
    const first = await post();
    const callsAfterFirst = providerCalls;
    const replay = await post();
    assert.equal(first.response.status, 202, JSON.stringify(first.body));
    assert.equal(first.body.durable_handoff, true);
    assert.equal(first.body.execution_pending, true);
    assert.deepEqual(replay.body, first.body);
    assert.equal(providerCalls, callsAfterFirst);
    const row = db.prepare(`SELECT output FROM main.turn_tool_executions
      WHERE tool='@turn/understanding' AND json_extract(args,'$.channel')='web'
        AND json_extract(args,'$.conversationId')=?
        AND json_extract(args,'$.messageId')=?`).get(conversationId, requestId);
    const prepared = JSON.parse(row.output);
    assert.equal(prepared.contract, 'PreparedUnderstandingTurn/v1');
    assert.equal(prepared.metadata.durableHandoff, true);
    assert.equal(prepared.metadata.executionPending, true);
    assert.ok(prepared.workItemId);
    assert.ok(prepared.goalEpisodeId);
  } finally {
    globalThis.fetch = originalFetch;
    await new Promise((resolve) => server.close(resolve));
  }
});

test('een crash vóór agentantwoord wordt na restart één handoff en nooit een tweede providerbeurt', () => {
  const text = 'Werk dit veilig later voor me af.';
  const requestId = 'web-request:http-crash:0001';
  const conversationId = 'web:http-crash';
  const durableTurn = `web:${createHash('sha256').update(canonicalJson({
    conversationId, messageId: requestId,
  })).digest('hex')}`;
  const executionKey = `turn-understanding:${createHash('sha256').update(canonicalJson({
    channel: 'web', conversationId, messageId: requestId,
  })).digest('hex')}`;
  const seeded = claimTurnToolExecution({
    executionKey,
    turnId: `ingress:web:${conversationId}:${requestId}`,
    tool: '@turn/understanding',
    args: {
      channel: 'web', conversationId, messageId: requestId,
      requester: 'Jaap', originalText: text, sessionRef: durableTurn,
      syntheticTest: false,
      supersessionScope: `web:${conversationId}`, supersessionToken: requestId,
    },
  });
  assert.equal(seeded.claimed, true);

  const restarted = spawnSync(process.execPath, ['--input-type=module', '-e', `
    const nativeFetch=globalThis.fetch;
    let providerCalls=0;
    globalThis.fetch=async(input,init)=>{
      if(String(input).startsWith('http://127.0.0.1:')) return nativeFetch(input,init);
      providerCalls+=1;
      throw new Error('provider mag na crash niet starten');
    };
    const {startWeb}=await import('./src/web/server.js');
    const server=await startWeb();
    const address=server.address();
    const response=await nativeFetch('http://127.0.0.1:'+address.port+'/api/chat',{
      method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':${JSON.stringify(requestId)}},
      body:JSON.stringify({text:${JSON.stringify(text)},conversation_id:${JSON.stringify(conversationId)},request_id:${JSON.stringify(requestId)}}),
    });
    const body=await response.json();
    await new Promise(resolve=>server.close(resolve));
    const {db}=await import('./src/db.js');
    console.log(JSON.stringify({status:response.status,body,providerCalls,
      origins:db.prepare("SELECT count(*) count FROM eva_episode_origins WHERE origin_channel='web' AND origin_conversation_id=? AND origin_message_id=?").get(${JSON.stringify(conversationId)},${JSON.stringify(requestId)}).count}));
    db.close();
  `], {
    cwd: path.resolve(new URL('..', import.meta.url).pathname),
    encoding: 'utf8', timeout: 30_000,
    env: {
      ...process.env, EVA_DATA_DIR: dataDir, TMPDIR: '/tmp', WEB_PORT: '0',
      EVA_RUNTIME_DISABLE_INFERENCE: 'true',
      EVA_RUNTIME_DISABLE_WORLD_LEARNING: 'true',
    },
  });
  assert.equal(restarted.status, 0, restarted.stderr || restarted.stdout);
  const outcome = JSON.parse(restarted.stdout.trim().split('\n').at(-1));
  assert.equal(outcome.status, 202, JSON.stringify(outcome));
  assert.equal(outcome.providerCalls, 0);
  assert.equal(outcome.body.reply, 'De opdracht is veilig bewaard en wordt vervolgd.');
  assert.equal(outcome.origins, 1);
  assert.equal(db.prepare(`SELECT count(*) count FROM eva_http_prepared_responses
    WHERE target_state='handoff' AND request_ref=(SELECT request_ref
      FROM eva_http_ingress_requests WHERE conversation_id=? AND request_id=?)`)
    .get(conversationId, requestId).count, 1);
  const replay = spawnSync(process.execPath, ['--input-type=module', '-e', `
    const nativeFetch=globalThis.fetch;
    let providerCalls=0;
    globalThis.fetch=async(input,init)=>{
      if(String(input).startsWith('http://127.0.0.1:')) return nativeFetch(input,init);
      providerCalls+=1; throw new Error('provider mag handoff niet herhalen');
    };
    const {startWeb}=await import('./src/web/server.js');
    const server=await startWeb(); const address=server.address();
    const response=await nativeFetch('http://127.0.0.1:'+address.port+'/api/chat',{
      method:'POST',headers:{'Content-Type':'application/json','Idempotency-Key':${JSON.stringify(requestId)}},
      body:JSON.stringify({text:${JSON.stringify(text)},conversation_id:${JSON.stringify(conversationId)},request_id:${JSON.stringify(requestId)}}),
    });
    const body=await response.json(); await new Promise(resolve=>server.close(resolve));
    const {db}=await import('./src/db.js'); console.log(JSON.stringify({status:response.status,body,providerCalls,
      origins:db.prepare("SELECT count(*) count FROM eva_episode_origins WHERE origin_channel='web' AND origin_conversation_id=? AND origin_message_id=?").get(${JSON.stringify(conversationId)},${JSON.stringify(requestId)}).count}));
    db.close();
  `], {
    cwd: path.resolve(new URL('..', import.meta.url).pathname),
    encoding: 'utf8', timeout: 30_000,
    env: {
      ...process.env, EVA_DATA_DIR: dataDir, TMPDIR: '/tmp', WEB_PORT: '0',
      EVA_RUNTIME_DISABLE_INFERENCE: 'true', EVA_RUNTIME_DISABLE_WORLD_LEARNING: 'true',
    },
  });
  assert.equal(replay.status, 0, replay.stderr || replay.stdout);
  const replayOutcome = JSON.parse(replay.stdout.trim().split('\n').at(-1));
  assert.equal(replayOutcome.status, 202);
  assert.equal(replayOutcome.providerCalls, 0);
  assert.equal(replayOutcome.origins, 1);
  assert.deepEqual(replayOutcome.body, outcome.body);
});
