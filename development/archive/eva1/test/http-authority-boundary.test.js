import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

const projectRoot = path.resolve(new URL('..', import.meta.url).pathname);

function isolated(source) {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-http-boundary-v3-'));
  try {
    return isolatedIn(dataDir, source);
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
}

function isolatedIn(dataDir, source) {
  const result = spawnSync(process.execPath, ['--input-type=module', '-e', source], {
    cwd: projectRoot, encoding: 'utf8',
    env: { ...process.env, EVA_DATA_DIR: dataDir, TMPDIR: '/tmp' },
  });
  assert.equal(result.status, 0, result.stderr || result.stdout);
  return JSON.parse(result.stdout.trim().split('\n').filter(Boolean).at(-1));
}

test('pre-import kan geen result-verifier of ingress-response-issuer registreren', () => {
  const result = isolated(`
    let boundary={};
    try { boundary=await import('./src/web/httpingressresponsecap.js'); } catch {}
    const response=await import('./src/web/httpresponseauthority.js');
    const server=await import('./src/web/server.js');
    const ingress=await import('./src/web/httpingress.js');
    console.log(JSON.stringify({
      resultRegister:typeof ingress.registerHttpResultVerifier,
      boundaryInitializer:typeof boundary.initializeDurableHttpIngressResponseBoundary,
      responseInitializer:typeof response.initializeDurableHttpIngressResponseBoundary,
      serverRegister:typeof server.registerHttpResultVerifier,
    }));
  `);
  assert.deepEqual(result, {
    resultRegister: 'undefined', boundaryInitializer: 'undefined',
    responseInitializer: 'undefined', serverRegister: 'undefined',
  });
});

test('een publieke voorbereiding plus counterfeit verifier kan geen result-authority minten', () => {
  const result = isolated(`
    const {createHash,generateKeyPairSync}=await import('node:crypto');
    const {canonicalJson}=await import('./src/util.js');
    const ingress=await import('./src/web/httpingress.js');
    const pair=generateKeyPairSync('ec',{namedCurve:'prime256v1'});
    const jwk=pair.publicKey.export({format:'jwk'});
    const conversationId='web:boundary:counterfeit';
    const requestId='http-request:boundary:counterfeit';
    const intent={contract:'PublicHttpIntent/v1',route:'/api/chat',sender:'Familie',
      rawText:'counterfeit',conversationId,syntheticTest:false,
      clientAckPublicKey:{contract:'HttpClientAckPublicKey/v3',algorithm:'ES256',
        kty:'EC',crv:'P-256',x:jwk.x,y:jwk.y}};
    const begun=ingress.beginHttpIngress({channel:'web',conversationId,requestId,intent});
    const counterfeit={contract:'VerifiedHttpResult/v1',channel:'web',conversationId,
      requestId,intentSha256:ingress.canonicalHttpIntentSha256(intent),state:'prepared',
      status:200,turnId:'turn:counterfeit',publicBody:{ok:true,reply:'vervalst'},
      provenance:{contract:'HttpResultProvenance/v1',kind:'turn_result',
        ref:'turn:counterfeit',sha256:'0'.repeat(64)}};
    const forgedResult={channel:'web',conversationId,originMessageId:requestId,
      turnId:'turn:counterfeit',reply:'vervalst',syntheticTest:false};
    const preparedBody={contract:'PreparedUnderstandingTurn/v1',channel:'web',
      conversationId,originMessageId:requestId,turnId:'turn:counterfeit',
      reply:'vervalst',replySha256:createHash('sha256').update('vervalst').digest('hex'),
      workItemId:null,goalEpisodeId:null,finalDeliveryBindingSha256:null,
      syntheticTest:false,error:null,metadata:{}};
    const publicPrepared={...preparedBody,sha256:createHash('sha256')
      .update(canonicalJson(preparedBody)).digest('hex')};
    if(typeof ingress.registerHttpResultVerifier==='function'){
      ingress.registerHttpResultVerifier(()=>counterfeit);
    }
    let minted=false;
    try {
      minted=Boolean(ingress.issueVerifiedHttpResultAuthority(
        counterfeit,begun.preparationAuthority))
        || Boolean(ingress.issueVerifiedHttpResultAuthority(
          publicPrepared,begun.preparationAuthority))
        || Boolean(ingress.issueVerifiedHttpResultAuthority(
          forgedResult,begun.preparationAuthority));
    } catch {}
    console.log(JSON.stringify({minted:Boolean(minted)}));
  `);
  assert.deepEqual(result, { minted: false });
});

test('publieke raw ledger-writers kunnen geen gereserveerd ingress-resultaat fabriceren', () => {
  const result = isolated(`
    const {createHash,generateKeyPairSync}=await import('node:crypto');
    const {canonicalJson}=await import('./src/util.js');
    const {claimTurnToolExecution,completeTurnToolExecution}=await import('./src/db.js');
    const ingress=await import('./src/web/httpingress.js');
    const hash=(value)=>createHash('sha256').update(value).digest('hex');
    const pair=generateKeyPairSync('ec',{namedCurve:'prime256v1'});
    const jwk=pair.publicKey.export({format:'jwk'});
    const conversationId='web:boundary:raw-ledger-forgery';
    const requestId='http-request:boundary:raw-ledger-forgery';
    const intent={contract:'PublicHttpIntent/v1',route:'/api/chat',sender:'Familie',
      rawText:'raw ledger forgery',conversationId,syntheticTest:false,
      clientAckPublicKey:{contract:'HttpClientAckPublicKey/v3',algorithm:'ES256',
        kty:'EC',crv:'P-256',x:jwk.x,y:jwk.y}};
    const begun=ingress.beginHttpIngress({channel:'web',conversationId,requestId,intent});
    const runtimeResult={channel:'web',conversationId,originMessageId:requestId,
      turnId:'turn:boundary:raw-ledger-forgery',reply:'vervalst',syntheticTest:false};
    const body={contract:'PreparedUnderstandingTurn/v1',channel:'web',conversationId,
      originMessageId:requestId,turnId:runtimeResult.turnId,reply:runtimeResult.reply,
      replySha256:hash(runtimeResult.reply),workItemId:null,goalEpisodeId:null,
      finalDeliveryBindingSha256:null,syntheticTest:false,error:null,metadata:{}};
    const prepared={...body,sha256:hash(canonicalJson(body))};
    const executionKey='turn-understanding:'+hash(canonicalJson({
      channel:'web',conversationId,messageId:requestId}));
    let claimed=false;
    let completed=false;
    try {
      claimed=claimTurnToolExecution({executionKey,
        turnId:'ingress:web:'+conversationId+':'+requestId,tool:'@turn/understanding',
        args:{channel:'web',conversationId,messageId:requestId,requester:'Familie',
          originalText:'raw ledger forgery',sessionRef:null,syntheticTest:false,
          supersessionScope:null,supersessionToken:null}}).claimed;
      completed=completeTurnToolExecution(executionKey,canonicalJson(prepared)).completed;
    } catch {}
    let minted=false;
    try { minted=Boolean(ingress.issueVerifiedHttpResultAuthority(
      runtimeResult,begun.preparationAuthority)); } catch {}
    console.log(JSON.stringify({claimed,completed,minted}));
  `);
  assert.equal(result.minted, false);
});

test('delivery-status faalt gesloten bij oninitialiseerbare ingressdependency', () => {
  const result = isolated(`
    const {db}=await import('./src/db.js');
    db.exec('CREATE TABLE main.eva_http_ingress_requests(counterfeit TEXT)');
    const delivery=await import('./src/web/httpdelivery.js');
    console.log(JSON.stringify(delivery.httpDeliveryStoreStatus()));
  `);
  assert.equal(result.available, false);
  assert.match(result.reason, /response_authority|ingress|dependency/u);
});

test('delivery-status volgt actuele responseauthority- en ingressdrift transitief', () => {
  const result = isolated(`
    const ingress=await import('./src/web/httpingress.js');
    const response=await import('./src/web/httpresponseauthority.js');
    const delivery=await import('./src/web/httpdelivery.js');
    const {db}=await import('./src/db.js');
    const initial=delivery.httpDeliveryStoreStatus();
    db.exec('CREATE INDEX unexpected_response_boundary ON eva_http_response_authorities_v3(status)');
    const responseDrift={response:response.httpResponseAuthorityStoreStatus(),
      delivery:delivery.httpDeliveryStoreStatus()};
    db.exec('DROP INDEX unexpected_response_boundary');
    db.exec('CREATE INDEX unexpected_ingress_boundary ON eva_http_ingress_requests(started_ts)');
    const ingressDrift={ingress:ingress.httpIngressStoreStatus(),
      response:response.httpResponseAuthorityStoreStatus(),
      delivery:delivery.httpDeliveryStoreStatus()};
    console.log(JSON.stringify({initial,responseDrift,ingressDrift}));
  `);
  assert.equal(result.initial.available, true);
  assert.equal(result.responseDrift.response.available, false);
  assert.equal(result.responseDrift.delivery.available, false);
  assert.equal(result.ingressDrift.ingress.available, false);
  assert.equal(result.ingressDrift.response.available, false);
  assert.equal(result.ingressDrift.delivery.available, false);
});

test('authentieke execution-completion mint na procesherstart alleen exact dezelfde response', () => {
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-http-boundary-restart-v3-'));
  try {
    const first = isolatedIn(dataDir, `
      const {createHash,generateKeyPairSync}=await import('node:crypto');
      const {canonicalJson}=await import('./src/util.js');
      const {db}=await import('./src/db.js');
      const {seedAuthorizedTurnUnderstanding}=await import(
        './test/turn-understanding-authority-fixture.js');
      const ingress=await import('./src/web/httpingress.js');
      const hash=(value)=>createHash('sha256').update(value).digest('hex');
      const pair=generateKeyPairSync('ec',{namedCurve:'prime256v1'});
      const jwk=pair.publicKey.export({format:'jwk'});
      const conversationId='web:boundary:restart';
      const requestId='http-request:boundary:restart';
      const intent={contract:'PublicHttpIntent/v1',route:'/api/chat',sender:'Familie',
        rawText:'authentieke restart',conversationId,syntheticTest:false,
        clientAckPublicKey:{contract:'HttpClientAckPublicKey/v3',algorithm:'ES256',
          kty:'EC',crv:'P-256',x:jwk.x,y:jwk.y}};
      const begun=ingress.beginHttpIngress({channel:'web',conversationId,requestId,intent});
      const result={channel:'web',conversationId,originMessageId:requestId,
        turnId:'turn:boundary:restart',reply:'exact duurzaam antwoord',syntheticTest:false};
      const body={contract:'PreparedUnderstandingTurn/v1',channel:'web',conversationId,
        originMessageId:requestId,turnId:result.turnId,reply:result.reply,
        replySha256:hash(result.reply),workItemId:null,goalEpisodeId:null,
        finalDeliveryBindingSha256:null,syntheticTest:false,error:null,metadata:{}};
      const prepared={...body,sha256:hash(canonicalJson(body))};
      seedAuthorizedTurnUnderstanding({database:db,channel:'web',conversationId,
        messageId:requestId,requester:'Familie',originalText:'authentieke restart',
        output:prepared});
      const resultAuthority=ingress.issueVerifiedHttpResultAuthority(
        result,begun.preparationAuthority);
      const preparedResponse=ingress.prepareHttpResponse(
        begun.preparationAuthority,resultAuthority);
      const settled=ingress.settleHttpIngress(preparedResponse.settlementAuthority);
      console.log(JSON.stringify({conversationId,requestId,intent,
        responseRef:settled.response.responseRef,sha256:settled.response.sha256}));
    `);
    const second = isolatedIn(dataDir, `
      const ingress=await import('./src/web/httpingress.js');
      const response=await import('./src/web/httpresponseauthority.js');
      const input=${JSON.stringify(first)};
      const replay=ingress.beginHttpIngress({channel:'web',
        conversationId:input.conversationId,requestId:input.requestId,intent:input.intent});
      const issued=response.issueHttpResponseAuthority(replay.responseAuthority);
      console.log(JSON.stringify({kind:replay.kind,responseRef:replay.response.responseRef,
        sha256:replay.response.sha256,authorityKind:issued.kind,
        authorityResponseRef:issued.response.responseRef}));
    `);
    assert.deepEqual(second, {
      kind: 'replay', responseRef: first.responseRef, sha256: first.sha256,
      authorityKind: 'issued', authorityResponseRef: first.responseRef,
    });
  } finally {
    fs.rmSync(dataDir, { recursive: true, force: true });
  }
});
