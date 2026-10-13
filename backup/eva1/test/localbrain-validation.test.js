import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after, afterEach, beforeEach } from 'node:test';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-localbrain-validation-'));
process.env.EVA_DATA_DIR = dataDir;

const originalFetch = globalThis.fetch;
const { brainPost, brainServiceStatus } = await import('../src/mind/localbrain.js');
const { db } = await import('../src/db.js');
const { detectFaces, learnFace, recognizeFaceDetailed } = await import('../src/mind/facerec.js');

function arcfaceVector() {
  return Array.from({ length: 512 }, (_, index) => (index % 17) / 17);
}

function facePayload(embedding = arcfaceVector()) {
  return {
    embedding,
    det_score: 0.98,
    oppervlak: 12_000,
    bbox: [10, 10, 110, 130],
  };
}

function embeddingBuffer(values = arcfaceVector()) {
  return Buffer.from(Float32Array.from(values).buffer);
}

beforeEach(() => {
  globalThis.fetch = originalFetch;
  db.prepare('DELETE FROM faces').run();
});
afterEach(() => { globalThis.fetch = originalFetch; });
after(() => {
  globalThis.fetch = originalFetch;
  fs.rmSync(dataDir, { recursive: true, force: true });
});

test('een gecontroleerde image-422 bewaart foutcode en is niet retrybaar', async () => {
  globalThis.fetch = async () => new Response(JSON.stringify({
    detail: {
      code: 'invalid_image',
      message: 'afbeeldingsgegevens bevatten geen ondersteund beeld',
    },
  }), { status: 422, headers: { 'Content-Type': 'application/json' } });

  await assert.rejects(
    brainPost('/faces_b64', { image_b64: 'ongeldig' }, { kind: 'faces_b64' }),
    (error) => {
      assert.equal(error.status, 422);
      assert.equal(error.code, 'invalid_image');
      assert.equal(error.retryable, false);
      assert.match(error.message, /geen ondersteund beeld/);
      return true;
    },
  );
  assert.equal(brainServiceStatus().bereikbaar, true);
});

test('een serverfout blijft retrybaar', async () => {
  globalThis.fetch = async () => new Response('tijdelijk stuk', { status: 503 });

  await assert.rejects(
    brainPost('/faces_b64', { image_b64: 'abc' }, { kind: 'faces_b64' }),
    (error) => {
      assert.equal(error.status, 503);
      assert.equal(error.retryable, true);
      assert.equal(error.message, 'HTTP 503');
      return true;
    },
  );
});

test('ArcFace accepteert alleen 512 dimensies en herkent een geldige vector', async () => {
  let calls = 0;
  globalThis.fetch = async () => {
    calls += 1;
    return new Response(JSON.stringify({
      gezichten: [facePayload(Array(511).fill(0.25)), facePayload()],
    }), { status: 200, headers: { 'Content-Type': 'application/json' } });
  };

  const faces = await detectFaces(Buffer.from('geldig-frame'));
  assert.equal(calls, 1);
  assert.equal(faces.length, 1);
  assert.equal(faces[0].backend, 'arcface');
  assert.equal(faces[0].embedding.length, 512);

  db.prepare(`INSERT INTO faces
    (person,vec,backend,ts,quality,det_score,oppervlak,active,note)
    VALUES (?,?,?,?,?,?,?,?,?)`)
    .run('Jaap', embeddingBuffer(), 'arcface', new Date().toISOString(), 0.8, 0.98, 12_000, 1, 'testvector');
  const recognized = await recognizeFaceDetailed(Buffer.from('zelfde-gezicht'));
  assert.equal(recognized.recognized, true);
  assert.equal(recognized.person, 'Jaap');
  assert.equal(recognized.backend, 'arcface');
  assert.equal(calls, 2);
});

test('een image-422 levert één gecontroleerd leeg ArcFace-resultaat', async () => {
  const urls = [];
  globalThis.fetch = async (url) => {
    urls.push(String(url));
    return new Response(JSON.stringify({
      detail: { code: 'invalid_image', message: 'ongeldig beeld' },
    }), { status: 422, headers: { 'Content-Type': 'application/json' } });
  };

  assert.deepEqual(await detectFaces(Buffer.from('ongeldig-frame')), []);
  assert.equal(urls.length, 1);
  assert.match(urls[0], /\/faces_b64$/);
});

test('twee 503-fouten deactiveren geen bestaande face en starten geen netwerkfallback', async () => {
  const sourceNote = 'kwaliteitsgezicht;foto-ref=bron-503';
  db.prepare(`INSERT INTO faces
    (person,vec,backend,ts,quality,det_score,oppervlak,active,note)
    VALUES (?,?,?,?,?,?,?,?,?)`)
    .run('Frank', embeddingBuffer(), 'arcface', new Date().toISOString(), 0.8, 0.98, 12_000, 1, sourceNote);

  const urls = [];
  globalThis.fetch = async (url) => {
    urls.push(String(url));
    return new Response('tijdelijk stuk', { status: 503 });
  };

  const learned = await learnFace('Jaap', Buffer.from('tijdelijk-onbereikbaar'), {
    sourceRef: 'bron-503',
    replaceSource: true,
  });
  assert.equal(learned, false);
  assert.equal(urls.length, 2);
  assert.deepEqual([...new Set(urls.map((url) => new URL(url).pathname))], ['/faces_b64']);
  assert.deepEqual(
    db.prepare('SELECT person,active,note FROM faces WHERE note=?').all(sourceNote),
    [{ person: 'Frank', active: 1, note: sourceNote }],
  );
});
