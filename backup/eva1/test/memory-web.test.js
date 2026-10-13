import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';

const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-memory-web-'));
process.env.EVA_DATA_DIR = dataDir;
process.env.WEB_HOST = '127.0.0.1';
process.env.WEB_PORT = '0';
process.env.OPENAI_API_KEY = 'test-only';

const { addMemory, db } = await import('../src/db.js');
const { startWeb } = await import('../src/web/server.js');

let server = null;

after(async () => {
  if (server) await new Promise((resolve) => server.close(resolve));
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

function baseUrl() {
  const address = server.address();
  assert.ok(address && typeof address === 'object');
  return `http://127.0.0.1:${address.port}`;
}

async function request(route, options = null) {
  const response = await fetch(`${baseUrl()}${route}`, options || undefined);
  const body = await response.json();
  assert.equal(response.status, 200, JSON.stringify(body));
  return body;
}

async function requestStatus(route, status, options = null) {
  const response = await fetch(`${baseUrl()}${route}`, options || undefined);
  const body = await response.json();
  assert.equal(response.status, status, JSON.stringify(body));
  return body;
}

function insertMemory({ content, archived = 0, archivedReason = null }) {
  return Number(db.prepare(`INSERT INTO memories
    (ts,category,content,importance,recall_count,archived,archived_reason)
    VALUES (?,?,?,?,?,?,?)`).run(
    new Date().toISOString(), 'feit', content, 0.5, 0, archived, archivedReason,
  ).lastInsertRowid);
}

test('dashboard scheidt actief, beoordeling en herstelbaar archief zonder recall te vervuilen', async () => {
  const preferenceId = addMemory({
    category: 'voorkeur', person: 'Jaap', importance: 0.9,
    content: 'Jaap houdt van korte concrete antwoorden met ruimte voor Eva haar eigen oordeel.',
  });
  const candidateId = addMemory({ category: 'feit', content: 'dummy', importance: 0.2 });
  server = await startWeb();

  const overview = await request('/api/memories/overview');
  assert.equal(overview.contract, 'MemoryHygieneOverview/v1');
  assert.equal(overview.active, 2);
  assert.equal(overview.archiveCandidates, 1);
  assert.equal(overview.archived, 0);

  const active = await request('/api/memories?scope=active');
  assert.equal(active.find((row) => row.id === preferenceId).status, 'PROTECT');
  assert.equal(active.find((row) => row.id === candidateId).status, 'ARCHIVE_CANDIDATE');
  const recallBefore = db.prepare('SELECT recall_count FROM memories WHERE id=?').get(candidateId).recall_count;
  const searched = await request('/api/memories?scope=active&q=dummy');
  assert.deepEqual(searched.map((row) => row.id), [candidateId]);
  assert.equal(db.prepare('SELECT recall_count FROM memories WHERE id=?').get(candidateId).recall_count,
    recallBefore, 'dashboardzoeken is geen modelrecall');

  const review = await request('/api/memories?scope=review');
  assert.deepEqual(review.map((row) => row.id), [candidateId]);
  const archived = await request('/api/memories/archive', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ids: [candidateId] }),
  });
  assert.deepEqual(archived.changedIds, [candidateId]);
  const archiveRows = await request('/api/memories?scope=archive');
  assert.equal(archiveRows.length, 1);
  assert.equal(archiveRows[0].id, candidateId);
  assert.equal(archiveRows[0].archiveProvenance.reason.code, 'exact_placeholder');
  assert.equal((await request('/api/memories?scope=active')).some((row) => row.id === candidateId), false);

  const restored = await request('/api/memories/restore', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ids: [candidateId] }),
  });
  assert.deepEqual(restored.changedIds, [candidateId]);
  assert.equal((await request('/api/memories?scope=active')).some((row) => row.id === candidateId), true);
});

test('pagina-API maakt oude review- en archiefregels volledig bereikbaar', async () => {
  const reviewIds = Array.from({ length: 124 }, (_, index) => insertMemory({
    content: `Doorzichtreeks noemt src/legacy/review-${index}.js als mogelijk spoor.`,
  }));
  const keepIds = Array.from({ length: 105 }, (_, index) => insertMemory({
    content: `Doorzichtreeks gewone tuindag nummer ${index} bleef rustig en droog.`,
  }));
  const archivedIds = Array.from({ length: 3 }, (_, index) => insertMemory({
    content: `Doorzichtarchief bewaarde regel nummer ${index}.`,
    archived: 1, archivedReason: 'handmatig bewaard archief',
  }));
  const sensitiveArchiveId = insertMemory({
    content: 'Doorzichtarchief: het wachtwoord is testgeheim123.',
    archived: 1, archivedReason: 'handmatig bewaard archief',
  });
  const before = db.prepare(`SELECT id,archived,recall_count,last_recalled
    FROM memories ORDER BY id`).all();

  const firstReview = await request(
    '/api/memories/page?scope=review&limit=100&q=Doorzichtreeks',
  );
  assert.equal(firstReview.contract, 'MemoryPage/v1');
  assert.equal(firstReview.matched, reviewIds.length);
  assert.equal(firstReview.items.length, 100);
  assert.deepEqual(firstReview.items.map((row) => row.id), reviewIds.slice(-100).reverse());
  assert.ok(firstReview.items.every((row) => row.status === 'REVIEW'));
  assert.equal(firstReview.nextBefore, reviewIds.at(-100));
  const legacyReview = await request('/api/memories?scope=review&q=Doorzichtreeks');
  assert.deepEqual(legacyReview.map((row) => row.id), reviewIds.slice(-100).reverse(),
    'ook de compatibele arrayroute filtert review vóór de limiet');

  const secondReview = await request(`/api/memories/page?scope=review&limit=100&q=Doorzichtreeks&before=${firstReview.nextBefore}`);
  assert.equal(secondReview.matched, reviewIds.length);
  assert.deepEqual(secondReview.items.map((row) => row.id), reviewIds.slice(0, 24).reverse());
  assert.equal(secondReview.nextBefore, null);

  const active = await request(
    '/api/memories/page?scope=active&limit=500&q=Doorzichtreeks',
  );
  assert.equal(active.items.length, 100);
  assert.equal(active.matched, reviewIds.length + keepIds.length);
  assert.deepEqual(active.items.map((row) => row.id), keepIds.slice(-100).reverse());

  const firstArchive = await request(
    '/api/memories/page?scope=archive&limit=2&q=Doorzichtarchief',
  );
  assert.equal(firstArchive.matched, archivedIds.length);
  assert.deepEqual(firstArchive.items.map((row) => row.id), archivedIds.slice(-2).reverse());
  assert.equal(firstArchive.items.some((row) => row.id === sensitiveArchiveId), false);
  const secondArchive = await request(`/api/memories/page?scope=archive&limit=2&q=Doorzichtarchief&before=${firstArchive.nextBefore}`);
  assert.deepEqual(secondArchive.items.map((row) => row.id), [archivedIds[0]]);
  assert.equal(secondArchive.nextBefore, null);

  for (const route of [
    '/api/memories/page?scope=onbekend',
    '/api/memories/page?limit=0',
    '/api/memories/page?before=geen-id',
  ]) {
    const response = await fetch(`${baseUrl()}${route}`);
    assert.equal(response.status, 400, route);
  }
  assert.deepEqual(db.prepare(`SELECT id,archived,recall_count,last_recalled
    FROM memories ORDER BY id`).all(), before,
  'API-paginering verandert archiefstatus en recallmetadata niet');
});

test('dashboard maakt actieve versus gearchiveerde status en herstelactie zichtbaar', () => {
  const html = fs.readFileSync(new URL('../src/web/public/index.html', import.meta.url), 'utf8');
  const script = fs.readFileSync(new URL('../src/web/public/app.js', import.meta.url), 'utf8');
  assert.match(html, /data-memory-scope="active"/);
  assert.match(html, /data-memory-scope="review"/);
  assert.match(html, /data-memory-scope="archive"/);
  assert.match(script, /veilig archiefvoorstel/);
  assert.match(script, /Herstel naar actief/);
  assert.match(script, /\/api\/memories\/overview/);
  assert.match(script, /\/api\/memories\/page/);
  assert.match(script, /memoryRequestGeneration/);
  assert.match(script, /requestGeneration !== memoryRequestGeneration/);
  assert.match(script, /Bewaar actief/);
  assert.match(script, /Bescherm persoonlijk/);
  assert.match(script, /Beoordeling heropenen/);
  assert.match(script, /opnieuw activeren voor Eva haar geheugen/);
  assert.match(script, /\/api\/memories\/review/);
  assert.match(html, /id="memory-shown"/);
  assert.match(html, /id="memory-more"[^>]*>Meer laden</);
});

test('dashboard reviewt exact één regel met revision, fingerprint en conflictstatus', async () => {
  const reviewId = insertMemory({
    content: 'De runtime-module en route vragen een handmatige gezinsbeoordeling.',
  });
  const review = await request('/api/memories/page?scope=review');
  const item = review.items.find((row) => row.id === reviewId);
  assert.equal(item.status, 'REVIEW');
  const input = {
    id: reviewId,
    action: 'protect',
    revision: item.revision,
    expectedFingerprint: item.exactFingerprint,
    operationKey: 'memory-review:web-test:00000001',
  };
  const protectedResult = await request('/api/memories/review', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  });
  assert.equal(protectedResult.status, 'PROTECT');
  assert.deepEqual(await request('/api/memories/review', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  }), protectedResult);
  const active = await request('/api/memories/page?scope=active');
  const protectedItem = active.items.find((row) => row.id === reviewId);
  assert.equal(protectedItem.reviewDecision.action, 'protect');
  assert.equal((await request('/api/memories/page?scope=review')).items
    .some((row) => row.id === reviewId), false);

  const conflict = await requestStatus('/api/memories/review', 409, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...input, action: 'keep' }),
  });
  assert.equal(conflict.reasonCode, 'idempotency_conflict');
  await requestStatus('/api/memories/review', 400, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...input, ids: [reviewId] }),
  });
  await requestStatus('/api/memories/archive', 400, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ids: [reviewId, reviewId + 1] }),
  });
  await requestStatus('/api/memories/restore', 400, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ids: [reviewId, reviewId + 1] }),
  });
  const stale = await requestStatus('/api/memories/review', 409, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      ...input,
      action: 'reopen',
      operationKey: 'memory-review:web-test:00000002',
      revision: 'a'.repeat(64),
    }),
  });
  assert.equal(stale.reasonCode, 'stale_revision');
  const missing = await requestStatus('/api/memories/review', 404, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      ...input,
      id: 999999,
      operationKey: 'memory-review:web-test:00000003',
    }),
  });
  assert.equal(missing.reasonCode, 'not_found');
});
