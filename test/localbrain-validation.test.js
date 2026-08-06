import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after, afterEach, beforeEach } from 'node:test';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-localbrain-validation-'));
process.env.EVA_DATA_DIR = dataDir;

const originalFetch = globalThis.fetch;
const { brainPost, brainServiceStatus } = await import('../src/mind/localbrain.js');

beforeEach(() => { globalThis.fetch = originalFetch; });
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
