import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-perception-web-'));
process.env.EVA_DATA_DIR = dataDir;

const { db } = await import('../src/db.js');
const { publicBusEventData } = await import('../src/web/server.js');

after(() => {
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

test('ruwe deurbel- en camerawaarneming bereikt nooit het dashboard-eventkanaal', () => {
  const frame = { buffer: Buffer.from('private pixels'), contentType: 'image/jpeg' };
  assert.equal(publicBusEventData('house', {
    type: 'doorbell', camera: 'camera.g4_doorbell', correlationId: 'private',
    frame, frames: [frame],
  }), null);
  assert.equal(publicBusEventData('house', {
    type: 'person_detected', frame, frames: [frame],
  }), null);
  assert.equal(publicBusEventData('house', {
    type: 'unknown_camera_event', frames: [frame],
  }), null);
  assert.deepEqual(publicBusEventData('house', {
    type: 'arrival', person: 'Jaap',
  }), { type: 'arrival', person: 'Jaap' });
});

