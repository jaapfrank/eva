import { createHash, createPublicKey, verify } from 'node:crypto';

import { canonicalJson } from '../util.js';

const BASE64URL_32 = /^[A-Za-z0-9_-]{43}$/u;
const SIGNATURE = /^[A-Za-z0-9_-]{86}$/u;
const SHA256 = /^[a-f0-9]{64}$/u;
const REQUEST_REF = /^http-ingress:[a-f0-9]{64}$/u;
const RESPONSE_REF = /^http-response:[a-f0-9]{64}$/u;
const DELIVERY_REF = /^http-delivery-v3:[a-f0-9]{64}$/u;
const WRITE_ATTEMPT_REF = /^http-write-attempt-v3:[a-f0-9]{64}$/u;
const CHALLENGE = SHA256;
const CONTENT_TYPE = 'application/json; charset=utf-8';

const hash = (value) => createHash('sha256').update(value).digest('hex');

export function canonicalHttpWireSha256({ status, contentType, bodyBytes }) {
  if (!Number.isSafeInteger(status) || status < 200 || status > 599
    || contentType !== CONTENT_TYPE || !Buffer.isBuffer(bodyBytes)
    || bodyBytes.length < 2 || bodyBytes.length > 24 * 1024) {
    throw new TypeError('invalid canonical HTTP wire');
  }
  const binding = canonicalJson({
    contract: 'CanonicalHttpWireBinding/v3', status, contentType,
    bodySha256: hash(bodyBytes), bodyLength: bodyBytes.length,
  });
  return hash(Buffer.concat([
    Buffer.from(binding, 'utf8'), Buffer.from('\n', 'utf8'), bodyBytes,
  ]));
}

function plainObject(value) {
  return value && typeof value === 'object' && !Array.isArray(value)
    && [Object.prototype, null].includes(Object.getPrototypeOf(value));
}

function exactKeys(value, keys) {
  return plainObject(value)
    && Object.keys(value).sort().join('\0') === [...keys].sort().join('\0');
}

function freeze(value) {
  if (!value || typeof value !== 'object' || Object.isFrozen(value)
    || ArrayBuffer.isView(value)) return value;
  Object.freeze(value);
  for (const child of Object.values(value)) freeze(child);
  return value;
}

function canonicalBase64Url(value, pattern, bytes) {
  if (typeof value !== 'string' || !pattern.test(value)) return false;
  try {
    const decoded = Buffer.from(value, 'base64url');
    return decoded.length === bytes && decoded.toString('base64url') === value;
  } catch { return false; }
}

/** Normalizeert uitsluitend browsercompatibele, canonieke P-256 verificatiesleutels. */
export function normalizeHttpClientAckPublicKey(value) {
  if (!exactKeys(value, ['contract', 'algorithm', 'kty', 'crv', 'x', 'y'])
    || value.contract !== 'HttpClientAckPublicKey/v3'
    || value.algorithm !== 'ES256' || value.kty !== 'EC' || value.crv !== 'P-256'
    || !canonicalBase64Url(value.x, BASE64URL_32, 32)
    || !canonicalBase64Url(value.y, BASE64URL_32, 32)) {
    throw new TypeError('invalid canonical HTTP client ACK public key');
  }
  const publicKey = freeze({
    contract: value.contract, algorithm: value.algorithm,
    kty: value.kty, crv: value.crv, x: value.x, y: value.y,
  });
  let keyObject;
  let exported;
  let spki;
  try {
    keyObject = createPublicKey({
      key: { kty: 'EC', crv: 'P-256', x: value.x, y: value.y }, format: 'jwk',
    });
    exported = keyObject.export({ format: 'jwk' });
    spki = keyObject.export({ format: 'der', type: 'spki' });
  } catch { throw new TypeError('invalid P-256 HTTP client ACK public key'); }
  if (exported.kty !== 'EC' || exported.crv !== 'P-256'
    || exported.x !== value.x || exported.y !== value.y) {
    throw new TypeError('non-canonical P-256 HTTP client ACK public key');
  }
  const publicKeyJson = canonicalJson(publicKey);
  const keyFingerprint = `p256-spki-sha256:${hash(spki)}`;
  return freeze({ publicKey, publicKeyJson, keyFingerprint, keyObject });
}

export function canonicalHttpClientAckPreimage(value) {
  if (!exactKeys(value, [
    'contract', 'requestRef', 'responseRef', 'responseSha256',
    'deliveryRef', 'challenge', 'wireSha256', 'writeAttemptRef', 'writeGeneration',
  ]) || value.contract !== 'HttpClientAck/v3'
    || !REQUEST_REF.test(value.requestRef) || !RESPONSE_REF.test(value.responseRef)
    || !SHA256.test(value.responseSha256)
    || value.responseRef !== `http-response:${value.responseSha256}`
    || !DELIVERY_REF.test(value.deliveryRef) || !CHALLENGE.test(value.challenge)
    || !SHA256.test(value.wireSha256) || !WRITE_ATTEMPT_REF.test(value.writeAttemptRef)
    || !Number.isSafeInteger(value.writeGeneration)
    || value.writeGeneration < 1 || value.writeGeneration > 64) {
    throw new TypeError('invalid canonical HTTP client ACK preimage');
  }
  return canonicalJson({
    contract: value.contract, requestRef: value.requestRef,
    responseRef: value.responseRef, responseSha256: value.responseSha256,
    deliveryRef: value.deliveryRef, challenge: value.challenge,
    wireSha256: value.wireSha256, writeAttemptRef: value.writeAttemptRef,
    writeGeneration: value.writeGeneration,
  });
}

export function canonicalLegacyHttpClientAckPreimage(value) {
  if (!exactKeys(value, [
    'contract', 'requestRef', 'responseRef', 'responseSha256',
    'deliveryRef', 'challenge', 'wireSha256',
  ]) || value.contract !== 'HttpClientAck/v3'
    || !REQUEST_REF.test(value.requestRef) || !RESPONSE_REF.test(value.responseRef)
    || !SHA256.test(value.responseSha256)
    || value.responseRef !== `http-response:${value.responseSha256}`
    || !DELIVERY_REF.test(value.deliveryRef) || !CHALLENGE.test(value.challenge)
    || !SHA256.test(value.wireSha256)) {
    throw new TypeError('invalid canonical legacy HTTP client ACK preimage');
  }
  return canonicalJson(value);
}

export function verifyLegacyHttpClientAck(input, storedPublicKey) {
  if (!exactKeys(input, [
    'contract', 'requestRef', 'responseRef', 'responseSha256',
    'deliveryRef', 'challenge', 'wireSha256', 'signature',
  ]) || !canonicalBase64Url(input?.signature, SIGNATURE, 64)) {
    throw new TypeError('invalid exact legacy HTTP client ACK signature');
  }
  const publicKey = normalizeHttpClientAckPublicKey(storedPublicKey);
  const ack = freeze({
    contract: input.contract, requestRef: input.requestRef,
    responseRef: input.responseRef, responseSha256: input.responseSha256,
    deliveryRef: input.deliveryRef, challenge: input.challenge,
    wireSha256: input.wireSha256,
  });
  const preimage = canonicalLegacyHttpClientAckPreimage(ack);
  const signature = Buffer.from(input.signature, 'base64url');
  const valid = verify('sha256', Buffer.from(preimage, 'utf8'), {
    key: publicKey.keyObject, dsaEncoding: 'ieee-p1363',
  }, signature);
  if (!valid) throw new Error('invalid legacy HTTP client ACK signature');
  return freeze({
    ack, preimage, preimageSha256: hash(preimage),
    signature: input.signature, signatureSha256: hash(signature),
    publicKey: publicKey.publicKey, publicKeyJson: publicKey.publicKeyJson,
    keyFingerprint: publicKey.keyFingerprint,
  });
}

export function verifyHttpClientAck(input, storedPublicKey) {
  if (!exactKeys(input, [
    'contract', 'requestRef', 'responseRef', 'responseSha256',
    'deliveryRef', 'challenge', 'wireSha256', 'writeAttemptRef', 'writeGeneration', 'signature',
  ]) || !canonicalBase64Url(input?.signature, SIGNATURE, 64)) {
    throw new TypeError('invalid exact HTTP client ACK signature');
  }
  const publicKey = normalizeHttpClientAckPublicKey(storedPublicKey);
  const ack = freeze({
    contract: input.contract, requestRef: input.requestRef,
    responseRef: input.responseRef, responseSha256: input.responseSha256,
    deliveryRef: input.deliveryRef, challenge: input.challenge,
    wireSha256: input.wireSha256, writeAttemptRef: input.writeAttemptRef,
    writeGeneration: input.writeGeneration,
  });
  const preimage = canonicalHttpClientAckPreimage(ack);
  const signature = Buffer.from(input.signature, 'base64url');
  let valid = false;
  try {
    valid = verify('sha256', Buffer.from(preimage, 'utf8'), {
      key: publicKey.keyObject, dsaEncoding: 'ieee-p1363',
    }, signature);
  } catch { valid = false; }
  if (!valid) throw new Error('invalid HTTP client ACK signature');
  return freeze({
    ack, preimage, preimageSha256: hash(preimage),
    signature: input.signature, signatureSha256: hash(signature),
    publicKey: publicKey.publicKey, publicKeyJson: publicKey.publicKeyJson,
    keyFingerprint: publicKey.keyFingerprint,
  });
}
