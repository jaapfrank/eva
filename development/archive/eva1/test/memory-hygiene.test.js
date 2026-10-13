import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after, beforeEach } from 'node:test';

// Deze suite mag nooit de productie-database importeren: EVA_DATA_DIR staat
// procesbreed vast voordat config/db voor het eerst worden geladen.
const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), 'bottie-memory-hygiene-'));
process.env.EVA_DATA_DIR = dataDir;

const {
  MEMORY_HYGIENE_ARCHIVE_CONTRACT,
  MEMORY_HYGIENE_PREVIEW_CONTRACT,
  MEMORY_HYGIENE_STATUS,
  MEMORY_PAGE_CONTRACT,
  MEMORY_REVIEW_ARCHIVE_CONTRACT,
  MEMORY_REVIEW_MUTATION_CONTRACT,
  addMemory,
  allMemories,
  archiveMemoryHygieneCandidates,
  db,
  listArchivedMemories,
  memoriesForPerson,
  memoryHygienePreview,
  memoryPage,
  normalizeMemoryExactText,
  previewMemoryHygieneRows,
  reviewMemoryAction,
  restoreMemoryHygieneArchive,
  searchMemories,
} = await import('../src/db.js');

after(() => {
  db.close();
  fs.rmSync(dataDir, { recursive: true, force: true });
});

beforeEach(() => {
  db.prepare('DELETE FROM memories').run();
});

function insertMemory({
  ts = '2026-08-01T10:00:00.000Z', category = 'feit', person = null,
  content, importance = 0.5, feeling = null, recallCount = 0, lastRecalled = null,
  archived = 0, archivedReason = null,
}) {
  const result = db.prepare(`INSERT INTO memories
    (ts,category,person,content,importance,feeling,recall_count,last_recalled,archived,archived_reason)
    VALUES (?,?,?,?,?,?,?,?,?,?)`).run(
    ts, category, person, content, importance, feeling, recallCount, lastRecalled,
    archived, archivedReason,
  );
  return Number(result.lastInsertRowid);
}

function decisionById(preview, id) {
  return preview.items.find((item) => Number(item.id) === Number(id));
}

function exactFingerprint(row) {
  return createHash('sha256').update([
    normalizeMemoryExactText(row.category || 'feit'),
    normalizeMemoryExactText(row.person || ''),
    normalizeMemoryExactText(row.content || ''),
  ].join('\u0000')).digest('hex');
}

function reviewPageItem(id, scope = 'review') {
  return memoryPage({ scope, limit: 100 }).items
    .find((item) => Number(item.id) === Number(id));
}

test('handmatige geheugenbeoordeling is enkelvoudig, atomair, append-only en omkeerbaar', () => {
  const memoryId = insertMemory({
    category: 'afspraak', person: 'Jaap', recallCount: 4,
    content: 'De runtime-route is getest, maar de blijvende gezinsafspraak in deze regel moet handmatig worden beoordeeld.',
  });
  const first = reviewPageItem(memoryId);
  assert.equal(first.status, MEMORY_HYGIENE_STATUS.REVIEW);
  assert.match(first.revision, /^[a-f0-9]{64}$/u);
  const keepInput = {
    id: memoryId, action: 'keep', revision: first.revision,
    expectedFingerprint: first.exactFingerprint,
    operationKey: 'memory-review:test:keep:0001',
  };
  const kept = reviewMemoryAction(keepInput);
  assert.equal(kept.contract, MEMORY_REVIEW_MUTATION_CONTRACT);
  assert.equal(kept.status, MEMORY_HYGIENE_STATUS.KEEP);
  assert.equal(kept.archived, false);
  assert.deepEqual(reviewMemoryAction(keepInput), kept, 'exacte replay geeft exact de eerste response');
  assert.throws(() => reviewMemoryAction({ ...keepInput, action: 'protect' }), (error) => (
    error?.code === 'EVA_MEMORY_REVIEW_CONFLICT'
    && error?.reasonCode === 'idempotency_conflict'
  ));
  assert.equal(reviewPageItem(memoryId), undefined,
    'een behouden regel verdwijnt uit de reviewrij');
  const activeKept = reviewPageItem(memoryId, 'active');
  assert.equal(activeKept.reviewDecision.action, 'keep');
  assert.equal(activeKept.status, MEMORY_HYGIENE_STATUS.KEEP);
  db.prepare('UPDATE memories SET recall_count=recall_count+7,last_recalled=? WHERE id=?')
    .run('2026-08-12T16:00:00.000Z', memoryId);
  assert.equal(reviewPageItem(memoryId, 'active').revision, activeKept.revision,
    'recallboekhouding maakt een inhoudelijk ongewijzigde reviewklik niet stale');

  const reopened = reviewMemoryAction({
    id: memoryId, action: 'reopen', revision: activeKept.revision,
    expectedFingerprint: activeKept.exactFingerprint,
    operationKey: 'memory-review:test:reopen:0002',
  });
  assert.equal(reopened.status, MEMORY_HYGIENE_STATUS.REVIEW);
  const reviewAgain = reviewPageItem(memoryId);
  assert.equal(reviewAgain.reviewDecision.action, 'reopen');

  const protectedResult = reviewMemoryAction({
    id: memoryId, action: 'protect', revision: reviewAgain.revision,
    expectedFingerprint: reviewAgain.exactFingerprint,
    operationKey: 'memory-review:test:protect:0003',
  });
  assert.equal(protectedResult.status, MEMORY_HYGIENE_STATUS.PROTECT);
  const protectedItem = reviewPageItem(memoryId, 'active');
  assert.equal(protectedItem.reviewDecision.action, 'protect');
  const legacyArchiveAttempt = archiveMemoryHygieneCandidates([memoryId]);
  assert.deepEqual(legacyArchiveAttempt.changedIds, []);
  assert.equal(legacyArchiveAttempt.refused[0].status, MEMORY_HYGIENE_STATUS.PROTECT,
    'cleanup-protect kan niet via de veilige-kandidaatroute worden omzeild');
  const reopenProtected = reviewMemoryAction({
    id: memoryId, action: 'reopen', revision: protectedItem.revision,
    expectedFingerprint: protectedItem.exactFingerprint,
    operationKey: 'memory-review:test:reopen:0004',
  });
  assert.equal(reopenProtected.status, MEMORY_HYGIENE_STATUS.REVIEW);

  const archiveSource = reviewPageItem(memoryId);
  const archiveInput = {
    id: memoryId, action: 'archive', revision: archiveSource.revision,
    expectedFingerprint: archiveSource.exactFingerprint,
    operationKey: 'memory-review:test:archive:0005',
  };
  const archived = reviewMemoryAction(archiveInput);
  assert.equal(archived.archived, true);
  const archiveItem = reviewPageItem(memoryId, 'archive');
  assert.equal(archiveItem.archiveProvenance.contract, MEMORY_REVIEW_ARCHIVE_CONTRACT);
  assert.equal(archiveItem.reviewDecision.action, 'archive');
  assert.equal(allMemories(100).some((row) => row.id === memoryId), false);
  assert.equal(searchMemories('blijvende gezinsafspraak', 20, false)
    .some((row) => row.id === memoryId), false);
  assert.equal(memoriesForPerson('Jaap', 'blijvende gezinsafspraak', 20)
    .some((row) => row.id === memoryId), false);
  const restored = reviewMemoryAction({
    id: memoryId, action: 'reopen', revision: archiveItem.revision,
    expectedFingerprint: archiveItem.exactFingerprint,
    operationKey: 'memory-review:test:reopen:0006',
  });
  assert.equal(restored.archived, false);
  assert.ok(reviewPageItem(memoryId));
  assert.deepEqual(reviewMemoryAction(archiveInput), archived,
    'een oude exacte replay verandert de later heropende rij niet opnieuw');
  assert.equal(db.prepare('SELECT archived FROM memories WHERE id=?').get(memoryId).archived, 0);

  const events = db.prepare(`SELECT * FROM memory_review_events
    WHERE memory_id=? ORDER BY id`).all(memoryId);
  assert.deepEqual(events.map((row) => row.action),
    ['keep', 'reopen', 'protect', 'reopen', 'archive', 'reopen']);
  assert.ok(events.every((row) => !JSON.stringify(row).includes('blijvende gezinsafspraak')),
    'append-only audit bevat geen ruwe geheugentekst');
  assert.throws(() => db.prepare('UPDATE memory_review_events SET action=action WHERE id=?')
    .run(events[0].id), /append-only/u);
  assert.throws(() => db.prepare('DELETE FROM memory_review_events WHERE id=?')
    .run(events[0].id), /append-only/u);
});

test('duplicaatclassificatie negeert recall maar maakt een semantische siblingwijziging stale', () => {
  const content = 'De runtime-module met deze route vraagt een handmatige beoordeling.';
  const older = insertMemory({
    ts: '2026-08-01T10:00:00.000Z', content, recallCount: 1,
  });
  const newer = insertMemory({
    ts: '2026-08-02T10:00:00.000Z', content, recallCount: 0,
  });
  const candidate = reviewPageItem(older);
  assert.equal(candidate.status, MEMORY_HYGIENE_STATUS.ARCHIVE_CANDIDATE);
  assert.equal(candidate.replacementId, newer);
  db.prepare('UPDATE memories SET recall_count=99,last_recalled=? WHERE id=?')
    .run('2026-08-12T16:00:00.000Z', older);
  const afterRecall = reviewPageItem(older);
  assert.equal(afterRecall.revision, candidate.revision);
  assert.equal(afterRecall.replacementId, newer);
  db.prepare('UPDATE memories SET content=content||? WHERE id=?')
    .run(' Deze sibling is inhoudelijk gewijzigd.', newer);
  assert.throws(() => reviewMemoryAction({
    id: older, action: 'keep', revision: candidate.revision,
    expectedFingerprint: candidate.exactFingerprint,
    operationKey: 'memory-review:test:sibling-stale:0201',
  }), (error) => error?.reasonCode === 'stale_revision');
  assert.equal(db.prepare('SELECT count(*) AS count FROM memory_review_events WHERE memory_id=?')
    .get(older).count, 0);
});

test('stale, vreemde provenance en insertfout laten reviewstatus atomair ongemoeid', () => {
  const staleId = insertMemory({
    category: 'afspraak', person: 'Jaap',
    content: 'De runtime-route en module zijn tijdelijk actief; deze afspraak vraagt menselijke beoordeling.',
  });
  const stale = reviewPageItem(staleId);
  db.prepare('UPDATE memories SET feeling=? WHERE id=?').run('twijfel', staleId);
  assert.throws(() => reviewMemoryAction({
    id: staleId, action: 'keep', revision: stale.revision,
    expectedFingerprint: stale.exactFingerprint,
    operationKey: 'memory-review:test:stale:0101',
  }), (error) => error?.reasonCode === 'stale_revision');
  assert.equal(db.prepare('SELECT count(*) AS count FROM memory_review_events WHERE memory_id=?')
    .get(staleId).count, 0);

  const foreignId = insertMemory({ content: 'dummy' });
  assert.deepEqual(archiveMemoryHygieneCandidates([foreignId]).changedIds, [foreignId]);
  const foreign = reviewPageItem(foreignId, 'archive');
  assert.throws(() => reviewMemoryAction({
    id: foreignId, action: 'reopen', revision: foreign.revision,
    expectedFingerprint: foreign.exactFingerprint,
    operationKey: 'memory-review:test:foreign:0102',
  }), (error) => error?.reasonCode === 'not_reopenable');

  const rollbackId = insertMemory({
    category: 'afspraak', person: 'Eva',
    content: 'Deze runtime-module en route in Eva haar zelfafspraak moeten handmatig worden beoordeeld.',
  });
  const rollback = reviewPageItem(rollbackId);
  db.exec(`CREATE TEMP TRIGGER abort_memory_review_event
    BEFORE INSERT ON memory_review_events
    BEGIN SELECT RAISE(ABORT,'injected memory review event failure'); END;`);
  assert.throws(() => reviewMemoryAction({
    id: rollbackId, action: 'archive', revision: rollback.revision,
    expectedFingerprint: rollback.exactFingerprint,
    operationKey: 'memory-review:test:rollback:0103',
  }), /injected memory review event failure/u);
  db.exec('DROP TRIGGER abort_memory_review_event');
  const after = db.prepare('SELECT archived,archived_reason FROM memories WHERE id=?')
    .get(rollbackId);
  assert.deepEqual(after, { archived: 0, archived_reason: null });
  assert.equal(db.prepare('SELECT count(*) AS count FROM memory_review_events WHERE memory_id=?')
    .get(rollbackId).count, 0);
});

test('preview is read-only en scheidt beschermd, review, keep en veilige kandidaten', () => {
  const jaapTaste = insertMemory({
    category: 'voorkeur', person: 'Jaap',
    content: 'Jaap heeft liever één afgemaakt resultaat dan vijf losse ideeën.',
    importance: 9,
  });
  const evaTaste = insertMemory({
    category: 'voorkeur', person: 'Eva',
    content: 'Eva houdt van droge humor, speelsheid en een beetje eigenwijsheid.',
  });
  const homeFact = insertMemory({
    category: 'feit',
    content: 'De verwarming in de woonkamer is in Home Assistant climate.woonkamer_thermostaat.',
  });
  const ownedTechnicalAgreement = insertMemory({
    category: 'afspraak', person: 'Jaap',
    content: 'De directe Telegram-omroeproute heeft nu spreek_in_huis beschikbaar en de actieve vaardigheid voice-pe-direct-uitvoeren v1.',
  });
  const pureTechnicalChangelog = insertMemory({
    category: 'afspraak',
    content: 'De omroeproute en runtime-module zijn getest en actief als voice_delivery_v1.',
  });
  const placeholder = insertMemory({ category: 'feit', content: '  Dummy.  ' });
  const mixed = insertMemory({
    category: 'feit', person: 'Eva',
    content: 'Eva houdt van speelse grapjes, en src/mind/persona.js is hiervoor aangepast.',
  });
  const safetyLesson = insertMemory({
    category: 'feit', person: 'Eva',
    content: 'Veiligheidsles: src/config.js aanpassen bewijst nog niet dat de wijziging werkt.',
  });
  const durableModelLocation = insertMemory({
    category: 'feit',
    content: 'Het lokale wakewordmodel staat in /home/eva/models/wakeword.onnx.',
  });
  const customDoorbellConvention = insertMemory({
    category: 'feit',
    content: 'Het custom deurbel_relais werkt via de speaker in de woonkamer.',
  });
  const oldOrdinaryFact = insertMemory({
    ts: '2001-01-01T00:00:00.000Z', category: 'feit',
    content: 'De reservehuissleutel ligt in het afgesproken sleutelkastje.',
    importance: 0, recallCount: 0,
  });
  const sensitiveLegacy = insertMemory({
    category: 'feit', content: 'Het wachtwoord is testgeheim123.',
  });
  const before = db.prepare('SELECT * FROM memories ORDER BY id').all();

  const preview = memoryHygienePreview();
  const searchedPreview = memoryHygienePreview({ query: 'Telegram' });

  assert.equal(preview.contract, MEMORY_HYGIENE_PREVIEW_CONTRACT);
  assert.equal(decisionById(preview, jaapTaste).status, MEMORY_HYGIENE_STATUS.PROTECT);
  assert.equal(decisionById(preview, evaTaste).status, MEMORY_HYGIENE_STATUS.PROTECT);
  assert.equal(decisionById(preview, homeFact).status, MEMORY_HYGIENE_STATUS.PROTECT);
  assert.deepEqual(
    [decisionById(preview, pureTechnicalChangelog).status, decisionById(preview, placeholder).status],
    [MEMORY_HYGIENE_STATUS.ARCHIVE_CANDIDATE, MEMORY_HYGIENE_STATUS.ARCHIVE_CANDIDATE],
  );
  assert.equal(decisionById(preview, ownedTechnicalAgreement).status, MEMORY_HYGIENE_STATUS.REVIEW);
  assert.equal(decisionById(preview, mixed).status, MEMORY_HYGIENE_STATUS.REVIEW);
  assert.equal(decisionById(preview, safetyLesson).status, MEMORY_HYGIENE_STATUS.REVIEW);
  assert.equal(decisionById(preview, durableModelLocation).status, MEMORY_HYGIENE_STATUS.REVIEW);
  assert.equal(decisionById(preview, customDoorbellConvention).status, MEMORY_HYGIENE_STATUS.REVIEW);
  assert.equal(decisionById(preview, oldOrdinaryFact).status, MEMORY_HYGIENE_STATUS.KEEP);
  assert.equal(decisionById(preview, sensitiveLegacy), undefined,
    'legacygeheimen mogen via een hygiene-preview niet opnieuw uitlekken');
  assert.deepEqual(searchedPreview.items.map((item) => item.id), [ownedTechnicalAgreement]);
  assert.deepEqual(db.prepare('SELECT * FROM memories ORDER BY id').all(), before,
    'preview en previewzoeken wijzigen archive, recall_count en last_recalled niet');
});

test('geheugenpagina filtert scope voor limiet en bladert stabiel zonder recall', () => {
  const reviewIds = Array.from({ length: 4 }, (_, index) => insertMemory({
    content: `Zichtbaarheidspaginering noemt src/legacy/review-${index}.js als mogelijk spoor.`,
  }));
  const keepIds = Array.from({ length: 105 }, (_, index) => insertMemory({
    content: `Zichtbaarheidspaginering gewone tuindag nummer ${index} bleef rustig en droog.`,
  }));
  const archivedIds = Array.from({ length: 4 }, (_, index) => insertMemory({
    content: `Archiefpaginering bewaarde regel nummer ${index}.`, archived: 1,
    archivedReason: 'handmatig bewaard archief',
  }));
  const sensitiveArchiveId = insertMemory({
    content: 'Archiefpaginering: het wachtwoord is testgeheim123.', archived: 1,
    archivedReason: 'handmatig bewaard archief',
  });
  const before = db.prepare(`SELECT id,archived,recall_count,last_recalled
    FROM memories ORDER BY id`).all();

  const firstReview = memoryPage({
    scope: 'review', query: 'Zichtbaarheidspaginering', limit: 2,
  });
  assert.equal(firstReview.contract, MEMORY_PAGE_CONTRACT);
  assert.equal(firstReview.matched, 4);
  assert.deepEqual(firstReview.items.map((row) => row.id), reviewIds.slice(-2).reverse());
  assert.ok(firstReview.items.every((row) => row.status === MEMORY_HYGIENE_STATUS.REVIEW));
  assert.equal(firstReview.nextBefore, reviewIds.at(-2));

  const secondReview = memoryPage({
    scope: 'review', query: 'Zichtbaarheidspaginering', limit: 2,
    before: firstReview.nextBefore,
  });
  assert.equal(secondReview.matched, 4, 'matched blijft onafhankelijk van de cursor');
  assert.deepEqual(secondReview.items.map((row) => row.id), reviewIds.slice(0, 2).reverse());
  assert.equal(secondReview.nextBefore, null);

  const firstActive = memoryPage({
    scope: 'active', query: 'Zichtbaarheidspaginering', limit: 1_000,
  });
  assert.equal(firstActive.items.length, 100, 'de backend klemt pagina\'s op honderd regels');
  assert.equal(firstActive.matched, reviewIds.length + keepIds.length);
  assert.deepEqual(firstActive.items.map((row) => row.id), keepIds.slice(-100).reverse());
  assert.equal(firstActive.nextBefore, keepIds.at(-100));

  const firstArchive = memoryPage({
    scope: 'archive', query: 'Archiefpaginering', limit: 3,
  });
  assert.equal(firstArchive.matched, archivedIds.length);
  assert.deepEqual(firstArchive.items.map((row) => row.id), archivedIds.slice(-3).reverse());
  assert.equal(firstArchive.nextBefore, archivedIds.at(-3));
  assert.equal(firstArchive.items.some((row) => row.id === sensitiveArchiveId), false);
  const finalArchive = memoryPage({
    scope: 'archive', query: 'Archiefpaginering', limit: 3,
    before: firstArchive.nextBefore,
  });
  assert.deepEqual(finalArchive.items.map((row) => row.id), [archivedIds[0]]);
  assert.equal(finalArchive.nextBefore, null);

  assert.throws(() => memoryPage({ scope: 'onbekend' }), /Onbekende geheugenweergave/u);
  assert.throws(() => memoryPage({ before: 0 }), /Ongeldige geheugencursor/u);
  assert.deepEqual(db.prepare(`SELECT id,archived,recall_count,last_recalled
    FROM memories ORDER BY id`).all(), before,
  'pagina-inspectie verandert archiefstatus en recallmetadata niet');
});

test('alleen exact genormaliseerde duplicaten krijgen één deterministische overlevende', () => {
  const survivor = insertMemory({
    category: 'voorkeur', person: 'Eva',
    content: 'Eva houdt van scherpe humor bij het ontbijt.',
  });
  const exactDuplicate = insertMemory({
    category: ' VOORKEUR ', person: ' eva ',
    content: '  EVA HOUDT VAN SCHERPE HUMOR BIJ HET ONTBIJT.  ',
  });
  const meaningfulNegation = insertMemory({
    category: 'voorkeur', person: 'Eva',
    content: 'Eva houdt niet van scherpe humor bij het ontbijt.',
  });
  const otherCategory = insertMemory({
    category: 'feit', person: 'Eva',
    content: 'Eva houdt van scherpe humor bij het ontbijt.',
  });
  const metadataPoor = insertMemory({
    ts: '2026-08-01T10:00:00.000Z', category: 'feit', person: 'Eva',
    content: 'Eva zag een bijzondere regenboog.', importance: 0.2,
  });
  const metadataRich = insertMemory({
    ts: '2026-08-02T10:00:00.000Z', category: ' FEIT ', person: ' eva ',
    content: ' EVA ZAG EEN BIJZONDERE REGENBOOG. ', importance: 0.9,
    recallCount: 4, feeling: 'verwonderd',
  });
  const conflictingImportance = insertMemory({
    ts: '2026-08-03T10:00:00.000Z', category: 'feit', person: 'Eva',
    content: 'Eva zag een dubbele vallende ster.', importance: 0.9,
  });
  const conflictingFeeling = insertMemory({
    ts: '2026-08-04T10:00:00.000Z', category: ' FEIT ', person: ' eva ',
    content: ' EVA ZAG EEN DUBBELE VALLENDE STER. ', importance: 0.4,
    recallCount: 2, feeling: 'opgetogen',
  });
  const input = db.prepare('SELECT * FROM memories ORDER BY id DESC').all();
  const unchangedInput = structuredClone(input);

  const items = previewMemoryHygieneRows(input);
  const byId = new Map(items.map((item) => [Number(item.id), item]));

  assert.equal(byId.get(survivor).status, MEMORY_HYGIENE_STATUS.PROTECT);
  assert.equal(byId.get(exactDuplicate).status, MEMORY_HYGIENE_STATUS.ARCHIVE_CANDIDATE);
  assert.equal(byId.get(exactDuplicate).reasonCode, 'exact_normalized_duplicate');
  assert.equal(byId.get(exactDuplicate).replacementId, survivor);
  assert.equal(byId.get(meaningfulNegation).status, MEMORY_HYGIENE_STATUS.PROTECT);
  assert.equal(byId.get(otherCategory).status, MEMORY_HYGIENE_STATUS.PROTECT);
  assert.equal(byId.get(metadataPoor).status, MEMORY_HYGIENE_STATUS.ARCHIVE_CANDIDATE);
  assert.equal(byId.get(metadataPoor).replacementId, metadataRich);
  assert.equal(byId.get(metadataRich).status, MEMORY_HYGIENE_STATUS.KEEP);
  assert.equal(byId.get(conflictingImportance).status, MEMORY_HYGIENE_STATUS.REVIEW);
  assert.equal(byId.get(conflictingFeeling).status, MEMORY_HYGIENE_STATUS.REVIEW);
  assert.equal(byId.get(conflictingImportance).reasonCode, 'exact_duplicate_metadata_review');
  assert.deepEqual(input, unchangedInput, 'ook de pure rijclassifier muteert zijn invoer niet');
  const conflictArchive = archiveMemoryHygieneCandidates([conflictingImportance]);
  assert.deepEqual(conflictArchive.changedIds, []);
  assert.equal(conflictArchive.refused[0].status, MEMORY_HYGIENE_STATUS.REVIEW);

  const first = addMemory({
    category: 'voorkeur', person: 'Exacte Eva', importance: 0.4,
    content: 'Exacte Eva houdt van droge woordgrappen.',
  });
  const exactAgain = addMemory({
    category: ' VOORKEUR ', person: ' exacte eva ', importance: 0.4,
    content: '  EXACTE EVA HOUDT VAN DROGE WOORDGRAPPEN. ',
  });
  const notTheSame = addMemory({
    category: 'voorkeur', person: 'Exacte Eva', importance: 0.4,
    content: 'Exacte Eva houdt niet van droge woordgrappen.',
  });
  assert.equal(exactAgain, first);
  assert.notEqual(notTheSame, first, 'een betekenisvolle negatie mag nooit fuzzy verdwijnen');
});

test('expliciete archivering is redengebonden, omkeerbaar en idempotent', () => {
  const protectedTaste = insertMemory({
    category: 'voorkeur', person: 'Jaap',
    content: 'Jaap houdt van pizza met veel groente.',
  });
  const technical = insertMemory({
    category: 'afspraak', person: null,
    content: 'De omroeproute en runtime-module zijn getest en actief als voice_delivery_v1.',
  });
  const survivor = insertMemory({
    category: 'feit', person: 'Eva', content: 'Een exact bewaarde duplicaatregel.',
  });
  const duplicate = insertMemory({
    category: 'feit', person: ' eva ', content: '  EEN EXACT BEWAARDE DUPLICAATREGEL. ',
  });
  const foreignArchive = insertMemory({
    category: 'signalement', person: 'Iemand', content: 'Een gecorrigeerd fotolabel.',
    archived: 1, archivedReason: 'fotolabel handmatig gecorrigeerd',
  });
  const secretArchiveReason = insertMemory({
    category: 'feit', content: 'Een onschuldige handmatig gearchiveerde regel.',
    archived: 1, archivedReason: 'beheerwachtwoord: zeerlanggeheim123',
  });

  const archived = archiveMemoryHygieneCandidates({
    ids: [technical, duplicate, protectedTaste],
  });
  assert.deepEqual(archived.changedIds, [technical, duplicate]);
  assert.deepEqual(archived.refused, [{
    id: protectedTaste,
    reasonCode: 'durable_personal_or_household_memory',
    status: MEMORY_HYGIENE_STATUS.PROTECT,
  }]);

  const technicalRow = db.prepare('SELECT * FROM memories WHERE id=?').get(technical);
  const duplicateRow = db.prepare('SELECT * FROM memories WHERE id=?').get(duplicate);
  const technicalReason = JSON.parse(technicalRow.archived_reason);
  const duplicateReason = JSON.parse(duplicateRow.archived_reason);
  assert.equal(technicalRow.archived, 1);
  assert.equal(technicalReason.contract, MEMORY_HYGIENE_ARCHIVE_CONTRACT);
  assert.equal(technicalReason.version, 1);
  assert.equal(technicalReason.reason.code, 'pure_internal_implementation_or_changelog');
  assert.equal(technicalReason.replacement, null);
  assert.equal(duplicateReason.replacement.memoryId, survivor);
  assert.equal(duplicateReason.replacement.relation, 'exact_normalized_duplicate');
  const malformedId = insertMemory({
    category: 'feit', content: 'Een rij met vervalste hygiene-provenance.',
  });
  const malformedRow = db.prepare('SELECT * FROM memories WHERE id=?').get(malformedId);
  const malformedReason = {
    ...duplicateReason,
    reason: { code: 'exact_placeholder', status: MEMORY_HYGIENE_STATUS.ARCHIVE_CANDIDATE },
    memory: { id: malformedId, exactFingerprint: exactFingerprint(malformedRow) },
  };
  db.prepare('UPDATE memories SET archived=1,archived_reason=? WHERE id=?')
    .run(JSON.stringify(malformedReason), malformedId);

  const archivedList = listArchivedMemories(100);
  assert.deepEqual(archivedList.map((row) => row.id).sort((a, b) => a - b),
    [technical, duplicate, foreignArchive, secretArchiveReason, malformedId]);
  assert.equal(archivedList.find((row) => row.id === technical)
    .archiveProvenance.contract, MEMORY_HYGIENE_ARCHIVE_CONTRACT);
  assert.equal(archivedList.find((row) => row.id === foreignArchive).archiveProvenance, null);
  assert.doesNotMatch(archivedList.find((row) => row.id === secretArchiveReason).archived_reason,
    /zeerlanggeheim/iu);
  assert.match(archivedList.find((row) => row.id === secretArchiveReason).archived_reason,
    /AFGESCHERMD/iu);
  assert.equal(archivedList.find((row) => row.id === malformedId).archiveProvenance, null,
    'een niet-duplicaatreden met vervangingsprovenance is geen geldig hygiene-archief');
  assert.deepEqual(listArchivedMemories(100, 'omroeproute').map((row) => row.id), [technical]);
  assert.equal(db.prepare('SELECT SUM(recall_count) AS count FROM memories').get().count, 0,
    'archiefinspectie verhoogt recall_count niet');

  assert.equal(allMemories(100).some((row) => row.id === technical), false);
  assert.equal(searchMemories('omroeproute', 20, false).some((row) => row.id === technical), false);
  assert.equal(memoriesForPerson('Jaap', 20, 'omroeproute')
    .some((row) => row.id === technical), false);
  assert.equal(db.prepare(`SELECT COUNT(*) AS count FROM memories
    WHERE COALESCE(archived,0)=0 AND lower(trim(category))='feit'
      AND lower(trim(COALESCE(person,'')))='eva'
      AND lower(trim(content)) LIKE '%exact bewaarde duplicaatregel.%'`).get().count, 1);

  const reasonsBeforeReplay = db.prepare(`SELECT id,archived_reason FROM memories
    WHERE id IN (?,?) ORDER BY id`).all(technical, duplicate);
  const replay = archiveMemoryHygieneCandidates([technical, duplicate]);
  assert.deepEqual(replay.changedIds, []);
  assert.deepEqual(replay.unchangedIds, [technical, duplicate]);
  assert.deepEqual(db.prepare(`SELECT id,archived_reason FROM memories
    WHERE id IN (?,?) ORDER BY id`).all(technical, duplicate), reasonsBeforeReplay,
  'idempotente replay overschrijft tijdstip of provenance niet');

  const restored = restoreMemoryHygieneArchive({
    ids: [technical, duplicate, foreignArchive, malformedId],
  });
  assert.deepEqual(restored.changedIds, [technical, duplicate]);
  assert.deepEqual(restored.refused, [
    { id: foreignArchive, reasonCode: 'archive_owned_by_other_process' },
    { id: malformedId, reasonCode: 'archive_owned_by_other_process' },
  ]);
  assert.deepEqual(db.prepare(`SELECT id,archived,archived_reason FROM memories
    WHERE id IN (?,?) ORDER BY id`).all(technical, duplicate), [
    { id: technical, archived: 0, archived_reason: null },
    { id: duplicate, archived: 0, archived_reason: null },
  ]);
  assert.equal(listArchivedMemories(100).some((row) => (
    row.id === technical || row.id === duplicate
  )), false);

  const restoreReplay = restoreMemoryHygieneArchive([technical, duplicate]);
  assert.deepEqual(restoreReplay.changedIds, []);
  assert.deepEqual(restoreReplay.unchangedIds, [technical, duplicate]);
});

test('addMemory klemt importance en archiveert of versmelt niets op fuzzy gelijkenis', () => {
  const low = addMemory({ content: 'De blauwe vaas staat op de vensterbank.', importance: -8 });
  const high = addMemory({ content: 'De groene vaas staat op de boekenkast.', importance: 9 });
  const invalid = addMemory({ content: 'De witte vaas staat op de eettafel.', importance: Infinity });
  const decimal = addMemory({ content: 'De rode vaas staat in de gang.', importance: '0.25' });

  assert.deepEqual(db.prepare(`SELECT id,importance FROM memories
    WHERE id IN (?,?,?,?) ORDER BY id`).all(low, high, invalid, decimal), [
    { id: low, importance: 0 },
    { id: high, importance: 1 },
    { id: invalid, importance: 0.5 },
    { id: decimal, importance: 0.25 },
  ]);
  assert.equal(db.prepare(`SELECT COUNT(*) AS count FROM memories
    WHERE importance < 0 OR importance > 1`).get().count, 0);

  const oldest = insertMemory({
    category: 'voorkeur', person: 'Mila',
    content: 'Mila drinkt muntthee zonder suiker.',
  });
  for (let index = 0; index < 205; index += 1) {
    insertMemory({
      category: 'voorkeur', person: 'Mila',
      content: `Mila bewaart unieke voorkeur ${index}.`,
    });
  }
  assert.equal(addMemory({
    category: 'voorkeur', person: 'Mila',
    content: '  MILA DRINKT MUNTTHEE ZONDER SUIKER. ',
  }), oldest, 'exacte bronontdubbeling mag niet stoppen na 200 kandidaten');
});
