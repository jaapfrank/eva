const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { webcrypto, createHash } = require('node:crypto');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, '../../src/eva/voice_service/client.html'), 'utf8');
const start = html.indexOf('const savedResultParts=');
const end = html.indexOf('\nasync function', html.indexOf('async function receiveSavedResult', start) + 20);
function setup() {
  const rows = []; let active = 1;
  const context = vm.createContext({ crypto: webcrypto, TextEncoder, Uint8Array, Map,
    current: value => value === active, row: (...args) => rows.push(args) });
  vm.runInContext(html.slice(start, end), context);
  return { rows, receive: e => context.receiveSavedResult(e, 1), close: () => { active = 2; } };
}
function parts(text) {
  const hash = createHash('sha256').update(text).digest('hex');
  const chunks = Array.from({ length: Math.ceil(text.length / 2000) }, (_, i) => text.slice(i * 2000, (i + 1) * 2000));
  return chunks.map((text, part) => ({ message_id: 'background:' + 'a'.repeat(64), text, part,
    total: chunks.length, output_sha256: hash }));
}
test('saved result and sources reassemble out of order', async () => {
  const s = setup(), text = 'Bron: https://example.org/bron\n' + 'Resultaat. '.repeat(1200);
  for (const e of parts(text).reverse()) await s.receive(e);
  assert.equal(s.rows.length, 1); assert.equal(s.rows[0][2], text);
});
test('incomplete, forged and closed-session results never become visible', async () => {
  for (const mode of ['incomplete', 'forged', 'closed']) {
    const s = setup(), entries = parts('Resultaat '.repeat(400));
    if (mode === 'incomplete') entries.pop();
    if (mode === 'forged') entries[0].text += 'X';
    if (mode === 'closed') s.close();
    for (const e of entries) await s.receive(e);
    assert.equal(s.rows.length, 0);
  }
});
