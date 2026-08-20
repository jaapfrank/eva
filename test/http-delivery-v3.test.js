import assert from 'node:assert/strict';
import { createHash, generateKeyPairSync, sign } from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-http-delivery-v3-'));
process.env.EVA_DATA_DIR = dataDir;

const { db } = await import('../src/db.js');
const { canonicalJson } = await import('../src/util.js');
const { seedAuthorizedTurnUnderstanding } = await import(
  './turn-understanding-authority-fixture.js'
);
const ingress = await import('../src/web/httpingress.js');
const responseAuthority = await import('../src/web/httpresponseauthority.js');
const delivery = await import('../src/web/httpdelivery.js');

after(() => {
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

const sha256 = (value) => createHash('sha256').update(value).digest('hex');

function clientKeyPair() {
  const pair = generateKeyPairSync('ec', { namedCurve: 'prime256v1' });
  const jwk = pair.publicKey.export({ format: 'jwk' });
  return {
    privateKey: pair.privateKey,
    publicKey: {
      contract: 'HttpClientAckPublicKey/v3', algorithm: 'ES256',
      kty: 'EC', crv: 'P-256', x: jwk.x, y: jwk.y,
    },
  };
}

function durableResponse(label, publicKey) {
  const conversationId = `web:v3:${label}`;
  const requestId = `http-request:v3:${label}`;
  const turnId = `turn:http:v3:${label}`;
  const intent = {
    contract: 'PublicHttpIntent/v1', route: '/api/chat', sender: 'Familie',
    rawText: `bericht ${label}`, conversationId, syntheticTest: false,
    clientAckPublicKey: publicKey,
  };
  const begun = ingress.beginHttpIngress({
    channel: 'web', conversationId, requestId, intent,
  });
  assert.equal(begun.kind, 'started');
  const runtimeResult = {
    channel: 'web', conversationId, originMessageId: requestId, turnId,
    reply: `antwoord ${label}`, syntheticTest: false,
  };
  const preparedBody = {
    contract: 'PreparedUnderstandingTurn/v1', channel: 'web', conversationId,
    originMessageId: requestId, turnId, reply: runtimeResult.reply,
    replySha256: sha256(runtimeResult.reply), workItemId: null, goalEpisodeId: null,
    finalDeliveryBindingSha256: null, syntheticTest: false, error: null, metadata: {},
  };
  const durableProof = {
    ...preparedBody, sha256: sha256(canonicalJson(preparedBody)),
  };
  seedAuthorizedTurnUnderstanding({
    database: db, channel: 'web', conversationId, messageId: requestId,
    requester: intent.sender, originalText: intent.rawText.trim(), output: durableProof,
  });
  const resultAuthority = ingress.issueVerifiedHttpResultAuthority(
    runtimeResult, begun.preparationAuthority,
  );
  const prepared = ingress.prepareHttpResponse(
    begun.preparationAuthority, resultAuthority,
  );
  return ingress.settleHttpIngress(prepared.settlementAuthority);
}

test('v3 vereist een vooraf duurzame P-256-clientsleutel voor een extern bewijsbare ACK', () => {
  const keys = clientKeyPair();
  const settled = durableResponse('signed', keys.publicKey);
  const issued = responseAuthority.issueHttpResponseAuthority(settled.responseAuthority);
  assert.equal(issued.contract, 'IssuedHttpResponseAuthority/v3');
  const begun = delivery.beginHttpDelivery(issued.authority);
  assert.equal(begun.delivery.contract, 'HttpDeliveryReceipt/v3');
  const claimed = delivery.claimHttpDeliveryWrite(begun.delivery);
  const body = JSON.parse(claimed.wire.bodyBytes.toString('utf8'));
  assert.equal(body._httpDelivery.contract, 'HttpClientAckChallenge/v3');
  assert.equal(body._httpDelivery.requestRef, begun.delivery.requestRef);
  assert.equal(body._httpDelivery.responseRef, begun.delivery.responseRef);
  assert.equal(body._httpDelivery.deliveryRef, begun.delivery.deliveryRef);
  assert.equal(Object.hasOwn(claimed, 'ackAuthority'), false);
  assert.equal(Object.hasOwn(claimed, 'privateKey'), false);

  delivery.finishHttpDeliveryWrite(claimed.writeAuthority);
  const ack = {
    contract: 'HttpClientAck/v3', requestRef: begun.delivery.requestRef,
    responseRef: begun.delivery.responseRef,
    responseSha256: begun.delivery.responseSha256,
    deliveryRef: begun.delivery.deliveryRef,
    challenge: body._httpDelivery.challenge,
    wireSha256: begun.delivery.wireSha256,
    writeAttemptRef: claimed.writeAttemptRef || claimed.attemptRef,
    writeGeneration: claimed.writeGeneration || claimed.generation,
  };
  const preimage = delivery.canonicalHttpClientAckPreimage(ack);
  const signature = sign('sha256', Buffer.from(preimage, 'utf8'), {
    key: keys.privateKey, dsaEncoding: 'ieee-p1363',
  }).toString('base64url');
  assert.throws(() => delivery.acknowledgeHttpDelivery({
    ...ack, signature: Buffer.alloc(64).toString('base64url'),
  }), /signature|handtekening|ACK/u);
  const acknowledged = delivery.acknowledgeHttpDelivery({ ...ack, signature });
  assert.equal(acknowledged.delivered, true);
  assert.equal(acknowledged.ack.contract, 'HttpClientAckReceipt/v3');
});
