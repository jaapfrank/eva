import fs from 'node:fs';
import path from 'node:path';
import { createHash, randomUUID } from 'node:crypto';
import Database from 'better-sqlite3';
import {
  attestConfiguredDatabaseOpen,
  attestConfiguredDatabaseSidecars,
  config,
  configuredDatabaseFile,
  requireConfiguredDataRoot,
} from './config.js';
import { bus } from './bus.js';
import {
  canonicalJson,
  isoNow,
  normalizeUserFacingText,
  tekstOverlap,
} from './util.js';
import { redactSecrets, redactSensitiveText } from './security/redaction.js';
import {
  EXECUTION_LEDGER_TRIGGER_SQL,
  EXECUTION_RECORD_CONTRACT_V1,
  EXECUTION_RECORD_CONTRACT_V2,
  EXECUTION_VERIFICATION_LEGACY_UNVERIFIABLE,
  EXECUTION_VERIFICATION_VERIFIED,
  executionLedgerStatus,
  executionLedgerTriggersExact,
  executionLedgerUpgradePredecessorTriggersExact,
  requireExecutionLedger,
} from './executionledger.js';
import {
  initializeTurnUnderstandingAuthorityStore,
  turnUnderstandingAuthorityRecord,
  turnUnderstandingAuthorityStoreStatus as inspectTurnUnderstandingAuthorityStore,
} from './turnauthority.js';
import {
  initializeVoiceDeliveryAuthorityStore,
  voiceDeliveryAuthorityStoreStatus as inspectVoiceDeliveryAuthorityStore,
} from './voiceauthority.js';

export {
  EXECUTION_RECORD_CONTRACT_V1,
  EXECUTION_RECORD_CONTRACT_V2,
  EXECUTION_VERIFICATION_LEGACY_UNVERIFIABLE,
  EXECUTION_VERIFICATION_VERIFIED,
} from './executionledger.js';

// Deze guard staat bewust vóór iedere mkdir, chmod, workspace-write en
// SQLite-open. Een Node-test mag nooit stil op Eva's default/productiedata
// terugvallen, ook niet wanneer config.js eerder uit de modulecache kwam.
requireConfiguredDataRoot();
fs.mkdirSync(config.dataDir, { recursive: true, mode: 0o700 });
requireConfiguredDataRoot();
try { fs.chmodSync(config.dataDir, 0o700); } catch { /* beste poging */ }
requireConfiguredDataRoot();
fs.mkdirSync(config.workspaceDir, { recursive: true, mode: 0o700 });
requireConfiguredDataRoot();
try { fs.chmodSync(config.workspaceDir, 0o700); } catch { /* beste poging */ }
// De werkruimte is een zelfstandig uitvoergebied. Leg de JavaScript-runtime
// expliciet vast, zodat dezelfde .js-code binnen Eva's uitvoerder en rechtstreeks
// vanaf de projectmap niet anders wordt geïnterpreteerd door het bovenliggende
// ESM-project.
const workspacePackagePath = path.join(config.workspaceDir, 'package.json');
requireConfiguredDataRoot();
if (!fs.existsSync(workspacePackagePath)) {
  fs.writeFileSync(workspacePackagePath, `${JSON.stringify({ private: true, type: 'commonjs' }, null, 2)}\n`, {
    encoding: 'utf8',
    mode: 0o600,
    flag: 'wx',
  });
}
requireConfiguredDataRoot();
const databaseAuthority = configuredDatabaseFile();
const databasePath = databaseAuthority.accessPath;
export const db = new Database(databasePath);
try { fs.chmodSync(databasePath, 0o600); } catch { /* beste poging */ }
try {
  attestConfiguredDatabaseOpen(db, databaseAuthority);
} catch (error) {
  db.close();
  throw error;
}
db.pragma('journal_mode = WAL');
db.pragma('synchronous = NORMAL');
db.pragma('busy_timeout = 5000');
// Een lege write-lockcyclus maakt WAL/SHM zonder applicatie-DDL. De journal-
// pragma en deze cyclus zijn zelf wel SQLite-effecten; de garantie is daarom
// exact dat sidecar-attestatie vóór applicatie-DDL faalt, niet dat zij nul-effect is.
try {
  db.exec('BEGIN IMMEDIATE; ROLLBACK;');
  attestConfiguredDatabaseSidecars(databaseAuthority);
} catch (error) {
  db.close();
  throw error;
}

db.exec(`
CREATE TABLE IF NOT EXISTS timeline (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  kind TEXT NOT NULL,          -- chat_in | chat_out | journal | dream | heartbeat | reminder | house | system
  content TEXT NOT NULL,
  meta TEXT,
  outcome_key TEXT
);
CREATE INDEX IF NOT EXISTS idx_timeline_ts ON timeline(ts);
CREATE INDEX IF NOT EXISTS idx_timeline_kind ON timeline(kind);
CREATE INDEX IF NOT EXISTS idx_timeline_understanding_conversation
  ON timeline(kind, json_extract(meta, '$.conversationId'), id)
  WHERE kind='turn_understanding';

CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  channel TEXT NOT NULL,       -- telegram | web
  conversation_id TEXT,        -- exacte chat/sessie; voorkomt contextlek tussen personen
  sender TEXT NOT NULL,        -- naam van gezinslid of 'Eva'
  role TEXT NOT NULL,          -- user | assistant
  content TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages(ts);

-- De gewone messages-tabel is gesprekscontext. Telegram heeft daarnaast een
-- duurzaam transportarchief nodig: een reply verwijst naar chat_id+message_id
-- en media moeten na een herstart nog aan precies dat bericht te koppelen zijn.
CREATE TABLE IF NOT EXISTS telegram_messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  chat_id TEXT NOT NULL,
  message_id TEXT NOT NULL,
  ts TEXT NOT NULL,
  archived_ts TEXT NOT NULL,
  sender TEXT NOT NULL DEFAULT '',
  role TEXT NOT NULL,          -- user | assistant
  kind TEXT NOT NULL DEFAULT 'text',
  content TEXT NOT NULL DEFAULT '',
  reply_to_message_id TEXT,
  media_ref TEXT,
  file_id TEXT,
  deleted INTEGER NOT NULL DEFAULT 0,
  processing_status TEXT NOT NULL DEFAULT 'received',
  processing_attempts INTEGER NOT NULL DEFAULT 0,
  processed_ts TEXT,
  processing_error TEXT,
  meta TEXT,
  UNIQUE(chat_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_telegram_messages_chat_id
  ON telegram_messages(chat_id, id);
CREATE INDEX IF NOT EXISTS idx_telegram_messages_media
  ON telegram_messages(chat_id, kind, id);

CREATE TABLE IF NOT EXISTS eva_conversation_opportunity_cursors (
  opportunity_key TEXT PRIMARY KEY,
  spec_sha256 TEXT NOT NULL,
  committed_fingerprint TEXT,
  committed_ts TEXT,
  checked_ts TEXT,
  expires_ts TEXT,
  pending_token TEXT,
  pending_fingerprint TEXT,
  pending_ts TEXT,
  pending_ttl_ms INTEGER,
  updated_ts TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS turn_tool_executions (
  execution_key TEXT PRIMARY KEY,
  turn_id TEXT NOT NULL,
  tool TEXT NOT NULL,
  args TEXT,
  status TEXT NOT NULL DEFAULT 'started',
  started_ts TEXT NOT NULL,
  finished_ts TEXT,
  output TEXT,
  semantic_hash TEXT,
  semantic_binding TEXT,
  intent_record_sha256 TEXT,
  output_sha256 TEXT,
  material_sha256 TEXT,
  completion_record_sha256 TEXT,
  execution_seq INTEGER NOT NULL DEFAULT 0,
  execution_contract TEXT NOT NULL DEFAULT 'ExecutionRecord/v1',
  execution_verification TEXT NOT NULL DEFAULT 'legacy_unverifiable'
);
CREATE INDEX IF NOT EXISTS idx_turn_tool_executions_turn
  ON turn_tool_executions(turn_id, tool);

CREATE TABLE IF NOT EXISTS memories (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  category TEXT DEFAULT 'feit',  -- feit | voorkeur | gebeurtenis | persoon | afspraak
  person TEXT,
  content TEXT NOT NULL,
  importance REAL DEFAULT 0.5,
  recall_count INTEGER DEFAULT 0,
  last_recalled TEXT
);

CREATE TABLE IF NOT EXISTS memory_review_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_ref TEXT NOT NULL UNIQUE,
  operation_key TEXT NOT NULL UNIQUE,
  request_sha256 TEXT NOT NULL,
  memory_id INTEGER NOT NULL,
  ts TEXT NOT NULL,
  action TEXT NOT NULL CHECK(action IN ('keep','protect','archive','reopen')),
  authority TEXT NOT NULL CHECK(authority='local_household_dashboard'),
  exact_fingerprint TEXT NOT NULL,
  source_revision_sha256 TEXT NOT NULL,
  result_revision_sha256 TEXT NOT NULL,
  classifier_status TEXT NOT NULL,
  classifier_reason TEXT NOT NULL,
  classifier_replacement_id INTEGER,
  classifier_binding_sha256 TEXT NOT NULL,
  previous_status TEXT NOT NULL,
  previous_reason TEXT NOT NULL,
  result_status TEXT NOT NULL,
  result_archived INTEGER NOT NULL CHECK(result_archived IN (0,1)),
  response_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_review_events_memory
  ON memory_review_events(memory_id, id DESC);
CREATE TRIGGER IF NOT EXISTS memory_review_events_no_update
BEFORE UPDATE ON memory_review_events
BEGIN SELECT RAISE(ABORT, 'memory_review_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS memory_review_events_no_delete
BEFORE DELETE ON memory_review_events
BEGIN SELECT RAISE(ABORT, 'memory_review_events is append-only'); END;

CREATE TABLE IF NOT EXISTS emotion_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  mood TEXT NOT NULL,           -- JSON {geluk, energie, nieuwsgierigheid, eenzaamheid, stress}
  note TEXT
);

CREATE TABLE IF NOT EXISTS reminders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_ts TEXT NOT NULL,
  due_ts TEXT NOT NULL,
  recurrence TEXT,              -- null | 'daily' | 'weekly' | 'monthly'
  channel TEXT DEFAULT 'telegram',
  target TEXT,                  -- naam van persoon (of null = iedereen)
  text TEXT NOT NULL,
  status TEXT DEFAULT 'open'    -- open | done | cancelled
);

CREATE TABLE IF NOT EXISTS people (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT UNIQUE NOT NULL,
  telegram_id INTEGER,
  notes TEXT,
  last_seen TEXT
);

CREATE TABLE IF NOT EXISTS kv (
  key TEXT PRIMARY KEY,
  value TEXT
);

-- Eva's eigen voornemens/verlangens: dingen die zij zichzelf voorneemt.
CREATE TABLE IF NOT EXISTS aspirations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_ts TEXT NOT NULL,
  text TEXT NOT NULL,
  why TEXT,
  status TEXT DEFAULT 'actief',   -- actief | volbracht | losgelaten
  progress TEXT,                  -- laatste voortgangsnotitie
  last_touched TEXT,
  closed_ts TEXT,
  kind TEXT DEFAULT 'interest',   -- interest | development
  goal_key TEXT,
  beneficiary TEXT,
  evidence TEXT,
  next_step TEXT,
  cadence_hours REAL,
  priority REAL
);
`);

const conversationOpportunityColumns = new Set(
  db.prepare('PRAGMA table_info(eva_conversation_opportunity_cursors)')
    .all().map((column) => String(column.name)),
);
for (const [column, definition] of [
  ['spec_sha256', 'TEXT'],
  ['committed_fingerprint', 'TEXT'],
  ['committed_ts', 'TEXT'],
  ['checked_ts', 'TEXT'],
  ['expires_ts', 'TEXT'],
  ['pending_token', 'TEXT'],
  ['pending_fingerprint', 'TEXT'],
  ['pending_ts', 'TEXT'],
  ['pending_ttl_ms', 'INTEGER'],
  ['updated_ts', 'TEXT'],
]) {
  if (!conversationOpportunityColumns.has(column)) {
    db.exec(`ALTER TABLE eva_conversation_opportunity_cursors ADD COLUMN ${column} ${definition}`);
  }
}
db.prepare(`UPDATE eva_conversation_opportunity_cursors
  SET spec_sha256=coalesce(spec_sha256,opportunity_key),
      updated_ts=coalesce(updated_ts,checked_ts,?)
  WHERE spec_sha256 IS NULL OR updated_ts IS NULL`).run(isoNow());
db.exec(`CREATE UNIQUE INDEX IF NOT EXISTS idx_conversation_opportunity_pending_token
  ON eva_conversation_opportunity_cursors(pending_token)
  WHERE pending_token IS NOT NULL`);

const memoryReviewSchemaInfo = /** @type {any[]} */ (
  db.prepare('PRAGMA table_info(memory_review_events)').all()
);
const memoryReviewSchemaColumns = new Map(memoryReviewSchemaInfo
  .map((column) => [String(column.name), column]));
for (const required of [
  'id', 'event_ref', 'operation_key', 'request_sha256', 'memory_id', 'ts',
  'action', 'authority', 'exact_fingerprint', 'source_revision_sha256',
  'result_revision_sha256', 'classifier_status', 'classifier_reason',
  'classifier_replacement_id', 'classifier_binding_sha256',
  'previous_status', 'previous_reason', 'result_status', 'result_archived',
  'response_json',
]) {
  if (!memoryReviewSchemaColumns.has(required)) {
    throw new Error(`memory_review_events mist vereiste kolom ${required}`);
  }
}
for (const requiredNotNull of [
  'event_ref', 'operation_key', 'request_sha256', 'memory_id', 'ts', 'action',
  'authority', 'exact_fingerprint', 'source_revision_sha256',
  'result_revision_sha256', 'classifier_status', 'classifier_reason',
  'classifier_binding_sha256', 'previous_status', 'previous_reason',
  'result_status', 'result_archived', 'response_json',
]) {
  if (Number(memoryReviewSchemaColumns.get(requiredNotNull)?.notnull || 0) !== 1) {
    throw new Error(`memory_review_events mist NOT NULL op ${requiredNotNull}`);
  }
}
const memoryReviewIndexes = /** @type {any[]} */ (
  db.prepare('PRAGMA index_list(memory_review_events)').all()
);
const hasUniqueMemoryReviewIndex = (columns) => memoryReviewIndexes.some((index) => (
  Number(index.unique) === 1 && Number(index.partial || 0) === 0
  && canonicalJson(/** @type {any[]} */ (db.prepare(`PRAGMA index_info(${index.name})`).all())
    .map((row) => row.name)) === canonicalJson(columns)
));
if (!hasUniqueMemoryReviewIndex(['event_ref'])
  || !hasUniqueMemoryReviewIndex(['operation_key'])) {
  throw new Error('memory_review_events mist unieke event- of bewerkingssleutel');
}
const hasMemoryReviewLookupIndex = memoryReviewIndexes.some((index) => (
  Number(index.unique) === 0 && Number(index.partial || 0) === 0
  && canonicalJson(/** @type {any[]} */ (db.prepare(`PRAGMA index_info(${index.name})`).all())
    .map((row) => row.name)) === canonicalJson(['memory_id', 'id'])
));
if (!hasMemoryReviewLookupIndex) {
  throw new Error('memory_review_events mist de geheugen-eventindex');
}
const memoryReviewTableSql = String(/** @type {any} */ (db.prepare(
  "SELECT sql FROM sqlite_master WHERE type='table' AND name='memory_review_events'",
).get())?.sql || '');
if (!/CHECK\s*\(action IN \('keep','protect','archive','reopen'\)\)/u.test(memoryReviewTableSql)
  || !/CHECK\s*\(authority='local_household_dashboard'\)/u.test(memoryReviewTableSql)
  || !/result_archived\s+INTEGER\s+NOT\s+NULL\s+CHECK\s*\(result_archived IN \(0,1\)\)/u
    .test(memoryReviewTableSql)) {
  throw new Error('memory_review_events mist action- of authoritygrens');
}
for (const [requiredTrigger, operation] of [
  ['memory_review_events_no_update', 'UPDATE'],
  ['memory_review_events_no_delete', 'DELETE'],
]) {
  const triggerSql = String(/** @type {any} */ (db.prepare(
    "SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?",
  ).get(requiredTrigger))?.sql || '');
  const normalizedTriggerSql = triggerSql.replace(/["`[\]]/gu, '').replace(/\s+/gu, ' ');
  if (!new RegExp(
    `BEFORE\\s+${operation}\\s+ON\\s+memory_review_events(?:\\s|$)[\\s\\S]+RAISE\\s*\\(ABORT`,
    'u',
  ).test(normalizedTriggerSql)) {
    throw new Error(`memory_review_events mist geldige trigger ${requiredTrigger}`);
  }
}

// Een observation-contribution gebruikt de bestaande tijdlijn als canonieke
// lifecycle, maar heeft database-afgedwongen exact-once settlement nodig. Op
// een bestaande database moet de kolom vóó de index worden gemigreerd.
try {
  const timelineColumns = new Set(db.prepare('PRAGMA main.table_info(timeline)').all()
    .map((column) => column.name));
  if (!timelineColumns.has('outcome_key')) {
    db.exec('ALTER TABLE timeline ADD COLUMN outcome_key TEXT');
  }
  db.exec(`CREATE UNIQUE INDEX IF NOT EXISTS idx_timeline_contribution_outcome_key
    ON timeline(outcome_key)
    WHERE kind='contribution' AND outcome_key IS NOT NULL`);
} catch {
  // De gewone tijdlijn blijft startbaar. De idempotente contribution-helper
  // hieronder bewijst kolom + unieke index opnieuw en faalt anders gesloten.
}

try {
  const messageColumns = new Set(db.prepare('PRAGMA main.table_info(messages)').all().map((column) => column.name));
  if (!messageColumns.has('conversation_id')) {
    db.exec('ALTER TABLE messages ADD COLUMN conversation_id TEXT');
  }
  db.exec('CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, id)');
} catch {
  // Een oude database blijft leesbaar; de agent valt dan terug op de kanaalroute.
}

try {
  const cols = db.prepare('PRAGMA table_info(telegram_messages)').all();
  const names = new Set(cols.map((column) => column.name));
  if (!names.has('processing_status')) {
    db.exec("ALTER TABLE telegram_messages ADD COLUMN processing_status TEXT NOT NULL DEFAULT 'received'");
  }
  if (!names.has('processing_attempts')) {
    db.exec('ALTER TABLE telegram_messages ADD COLUMN processing_attempts INTEGER NOT NULL DEFAULT 0');
  }
  if (!names.has('processed_ts')) db.exec('ALTER TABLE telegram_messages ADD COLUMN processed_ts TEXT');
  if (!names.has('processing_error')) db.exec('ALTER TABLE telegram_messages ADD COLUMN processing_error TEXT');
} catch { /* berichtarchief blijft bruikbaar op een oude database */ }

try {
  const names = new Set(db.prepare('PRAGMA main.table_info(turn_tool_executions)').all()
    .map((column) => column.name));
  const migrateExecutionSequences = !names.has('execution_seq');
  const migrateExecutionContracts = !names.has('execution_contract');
  const migrateExecutionVerification = !names.has('execution_verification');
  db.transaction(() => {
    const triggerNames = db.prepare(`SELECT name FROM main.sqlite_master
      WHERE type='trigger' AND tbl_name='turn_tool_executions' ORDER BY name`).all()
      .map((row) => String(row.name));
    const expectedTriggerNames = Object.keys(EXECUTION_LEDGER_TRIGGER_SQL).sort();
    const exactTriggerManifest = canonicalJson(triggerNames)
        === canonicalJson(expectedTriggerNames)
      && executionLedgerTriggersExact(db);
    const exactUpgradePredecessor = canonicalJson(triggerNames)
        === canonicalJson(expectedTriggerNames)
      && executionLedgerUpgradePredecessorTriggersExact(db);
    if (triggerNames.length && !exactTriggerManifest && !exactUpgradePredecessor) {
      throw new Error('execution ledger has unrecognized live triggers');
    }
    if (!names.has('semantic_hash')) {
      db.exec('ALTER TABLE main.turn_tool_executions ADD COLUMN semantic_hash TEXT');
    }
    if (!names.has('semantic_binding')) {
      db.exec('ALTER TABLE main.turn_tool_executions ADD COLUMN semantic_binding TEXT');
    }
    if (!names.has('intent_record_sha256')) {
      db.exec('ALTER TABLE main.turn_tool_executions ADD COLUMN intent_record_sha256 TEXT');
    }
    if (!names.has('output_sha256')) {
      db.exec('ALTER TABLE main.turn_tool_executions ADD COLUMN output_sha256 TEXT');
    }
    if (!names.has('material_sha256')) {
      db.exec('ALTER TABLE main.turn_tool_executions ADD COLUMN material_sha256 TEXT');
    }
    if (!names.has('completion_record_sha256')) {
      db.exec('ALTER TABLE main.turn_tool_executions ADD COLUMN completion_record_sha256 TEXT');
    }
    if (!names.has('execution_seq')) {
      db.exec(`ALTER TABLE main.turn_tool_executions
        ADD COLUMN execution_seq INTEGER NOT NULL DEFAULT 0`);
    }
    if (migrateExecutionContracts) {
      db.exec(`ALTER TABLE main.turn_tool_executions ADD COLUMN execution_contract
        TEXT NOT NULL DEFAULT 'ExecutionRecord/v1'`);
    }
    if (migrateExecutionVerification) {
      db.exec(`ALTER TABLE main.turn_tool_executions ADD COLUMN execution_verification
        TEXT NOT NULL DEFAULT 'legacy_unverifiable'`);
    }
    if (migrateExecutionSequences) {
      const legacyRows = /** @type {Array<{execution_key:string}>} */ (db.prepare(
        `SELECT execution_key
        FROM main.turn_tool_executions ORDER BY started_ts,execution_key`,
      ).all());
      const updateSequence = db.prepare(`UPDATE main.turn_tool_executions
        SET execution_seq=? WHERE execution_key=? AND execution_seq=0`);
      legacyRows.forEach((row, index) => updateSequence.run(index + 1, row.execution_key));
    }
    db.exec(`CREATE UNIQUE INDEX IF NOT EXISTS idx_turn_tool_executions_sequence
      ON turn_tool_executions(execution_seq)`);
    if (migrateExecutionVerification) {
      const hashedRows = db.prepare(`SELECT * FROM main.turn_tool_executions
        WHERE execution_contract IN ('ExecutionRecord/v1','ExecutionRecord/v2')
          AND semantic_hash IS NOT NULL AND intent_record_sha256 IS NOT NULL`).all();
      const markVerified = db.prepare(`UPDATE main.turn_tool_executions
        SET execution_verification='verified'
        WHERE execution_key=? AND execution_verification='legacy_unverifiable'`);
      for (const raw of hashedRows) {
        const row = /** @type {any} */ (raw);
        try {
          if (row.execution_contract === EXECUTION_RECORD_CONTRACT_V1
            && String(row.args || '').includes('[AFGESCHERMD:')) continue;
          validateExecutionRecord({
            ...row, execution_verification: EXECUTION_VERIFICATION_VERIFIED,
          });
          markVerified.run(row.execution_key);
        } catch { /* historisch onvolledig of ambigu blijft expliciet onverifieerbaar */ }
      }
    }
    if (!exactTriggerManifest) {
      if (exactUpgradePredecessor) {
        for (const name of expectedTriggerNames) db.exec(`DROP TRIGGER main.${name}`);
      }
      for (const sql of Object.values(EXECUTION_LEDGER_TRIGGER_SQL)) db.exec(sql);
    }
    requireExecutionLedger(db);
  })();
  requireExecutionLedger(db);
} catch (error) {
  throw new Error(`execution ledger migration failed: ${error?.message || error}`, { cause: error });
}

try {
  initializeTurnUnderstandingAuthorityStore(db);
} catch (error) {
  throw new Error(
    `turn understanding authority store initialization failed: ${error?.message || error}`,
    { cause: error },
  );
}

try {
  initializeVoiceDeliveryAuthorityStore(db);
} catch (error) {
  throw new Error(
    `voice delivery authority store initialization failed: ${error?.message || error}`,
    { cause: error },
  );
}

export function executionLedgerStoreStatus() {
  return executionLedgerStatus(db);
}

export function turnUnderstandingAuthorityStoreStatus() {
  return inspectTurnUnderstandingAuthorityStore(db);
}

export function voiceDeliveryAuthorityStoreStatus() {
  return inspectVoiceDeliveryAuthorityStore(db);
}

// Een voornemen kan ook een duurzame ontwikkelrichting zijn. Dezelfde ledger
// bewaart dan niet alleen een wens, maar ook waarom die richting bestaat en wat
// de eerstvolgende concrete bijdrage is. Bestaande persoonlijke voornemens
// blijven door de defaults ongewijzigd werken.
try {
  const cols = db.prepare('PRAGMA table_info(aspirations)').all();
  const names = new Set(cols.map((column) => column.name));
  if (!names.has('kind')) db.exec("ALTER TABLE aspirations ADD COLUMN kind TEXT DEFAULT 'interest'");
  if (!names.has('goal_key')) db.exec('ALTER TABLE aspirations ADD COLUMN goal_key TEXT');
  if (!names.has('beneficiary')) db.exec('ALTER TABLE aspirations ADD COLUMN beneficiary TEXT');
  if (!names.has('evidence')) db.exec('ALTER TABLE aspirations ADD COLUMN evidence TEXT');
  if (!names.has('next_step')) db.exec('ALTER TABLE aspirations ADD COLUMN next_step TEXT');
  if (!names.has('cadence_hours')) db.exec('ALTER TABLE aspirations ADD COLUMN cadence_hours REAL');
  if (!names.has('priority')) db.exec('ALTER TABLE aspirations ADD COLUMN priority REAL');
  db.exec('CREATE UNIQUE INDEX IF NOT EXISTS idx_aspirations_goal_key ON aspirations(goal_key) WHERE goal_key IS NOT NULL');
  db.prepare("UPDATE aspirations SET kind='interest' WHERE kind IS NULL OR trim(kind)='' ").run();
} catch { /* een oude database mag door een optionele richtingkolom niet stoppen */ }

// Volledige-tekst-zoeken op herinneringen (FTS5), met fallback naar LIKE.
let hasFts = true;
try {
  db.exec(`
    CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(content, content='memories', content_rowid='id');
    CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
      INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
    END;
    CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
      INSERT INTO memories_fts(memories_fts, rowid, content) VALUES ('delete', old.id, old.content);
    END;
    CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
      INSERT INTO memories_fts(memories_fts, rowid, content) VALUES ('delete', old.id, old.content);
      INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
    END;
  `);
} catch {
  hasFts = false;
}

// Geheugenkolommen (migratie; SQLite kent geen ADD COLUMN IF NOT EXISTS).
// Archiveren houdt oude ervaringen beschikbaar voor inspectie zonder dat ruis
// nog in gesprekken, planning of het eigen brein wordt opgehaald.
try {
  const cols = db.prepare('PRAGMA table_info(memories)').all();
  if (!cols.some((c) => c.name === 'feeling')) db.exec('ALTER TABLE memories ADD COLUMN feeling TEXT'); // hoe een belevenis voelde
  if (!cols.some((c) => c.name === 'archived')) db.exec('ALTER TABLE memories ADD COLUMN archived INTEGER DEFAULT 0');
  if (!cols.some((c) => c.name === 'archived_reason')) db.exec('ALTER TABLE memories ADD COLUMN archived_reason TEXT');
} catch { /* kolom bestaat al */ }

// Oude actie-, feit- en diagnosefeeds werden als "dagboek" opgeslagen. Dat
// maakte duizenden technische regels tot schijnbare levenservaringen. Bewaar
// ze op de interne tijdlijn, maar laat alleen echte dagboekmomenten dagboek zijn.
try {
  const migrationKey = 'journal_semantics_v2';
  if (!db.prepare('SELECT 1 FROM kv WHERE key=?').get(migrationKey)) {
    const moved = db.prepare(`UPDATE main.timeline SET kind='innerlijk'
      WHERE kind='journal' AND meta IS NOT NULL
        AND COALESCE(json_extract(meta,'$.soort'),'') NOT IN ('dagafsluiting','eigen_dagboek','belevenis')
        AND COALESCE(json_extract(meta,'$.belevenis'),0) != 1`).run();
    db.prepare('INSERT INTO kv (key,value) VALUES (?,?)').run(
      migrationKey,
      JSON.stringify({ ts: isoNow(), moved: Number(moved.changes || 0) }),
    );
  }
} catch { /* oude SQLite-build of beschadigde meta mag starten niet blokkeren */ }

// ---------- tijdlijn ----------

export function persistTimelineWithoutPublication(kind, content, meta = null) {
  const ts = isoNow();
  const safeContent = redactSensitiveText(content);
  const safeMeta = meta == null ? null : redactSecrets(meta);
  const info = db
    .prepare('INSERT INTO main.timeline (ts, kind, content, meta) VALUES (?, ?, ?, ?)')
    .run(ts, kind, safeContent, safeMeta ? JSON.stringify(safeMeta) : null);
  const row = { id: Number(info.lastInsertRowid), ts, kind, content: safeContent, meta: safeMeta };
  return row;
}

export function publishPersistedTimeline(row) {
  try { bus.emit('timeline', row); } catch {
    // Tijdlijnpublicatie volgt na de duurzame insert. Een defecte listener mag
    // een reeds opgeslagen action/outcome nooit als teruggedraaid rapporteren.
  }
}

export function logTimeline(kind, content, meta = null) {
  const row = persistTimelineWithoutPublication(kind, content, meta);
  publishPersistedTimeline(row);
  return row;
}

const CONTRIBUTION_MUTATION_AUTHORITIES = new WeakMap();
const CONSUMED_CONTRIBUTION_MUTATION_AUTHORITIES = new WeakSet();

/** Eenmalige capability voor precies de mutation-callback van één outcome. */
export function consumeContributionMutationAuthority(authority, {
  outcomeKey, content, meta,
} = /** @type {any} */ ({})) {
  const issued = authority && typeof authority === 'object'
    ? CONTRIBUTION_MUTATION_AUTHORITIES.get(authority) : null;
  if (!issued || issued.active !== true
    || CONSUMED_CONTRIBUTION_MUTATION_AUTHORITIES.has(authority)
    || db.inTransaction !== true
    || issued.outcomeKey !== String(outcomeKey || '')
    || issued.content !== redactSensitiveText(content)
    || canonicalJson(issued.meta) !== canonicalJson(redactSecrets(meta))) return false;
  CONSUMED_CONTRIBUTION_MUTATION_AUTHORITIES.add(authority);
  return true;
}

function contributionOutcomeSchemaReady() {
  try {
    const columns = new Set(db.prepare('PRAGMA main.table_info(timeline)').all()
      .map((column) => column.name));
    if (!columns.has('outcome_key')) return false;
    const index = db.prepare("PRAGMA index_list('timeline')").all()
      .find((row) => row.name === 'idx_timeline_contribution_outcome_key');
    if (!index || Number(index.unique) !== 1 || Number(index.partial) !== 1) return false;
    const fields = db.prepare("PRAGMA index_info('idx_timeline_contribution_outcome_key')")
      .all().map((row) => row.name);
    const sql = String(db.prepare(`SELECT sql FROM sqlite_master
      WHERE type='index' AND name='idx_timeline_contribution_outcome_key'`).get()?.sql || '');
    return fields.length === 1 && fields[0] === 'outcome_key'
      && /WHERE\s+kind\s*=\s*'contribution'\s+AND\s+outcome_key\s+IS\s+NOT\s+NULL/iu.test(sql);
  } catch { return false; }
}

function exactContributionOutcomeRow(row, outcomeKey, content, meta) {
  if (!row || row.kind !== 'contribution' || row.outcome_key !== outcomeKey
    || row.content !== content) return null;
  let storedMeta;
  try { storedMeta = JSON.parse(row.meta); } catch { return null; }
  if (storedMeta?.outcomeKey !== outcomeKey
    || canonicalJson(storedMeta) !== canonicalJson(meta)) return null;
  return {
    id: Number(row.id), ts: row.ts, kind: row.kind,
    content: row.content, meta: storedMeta,
  };
}

/** Persist exact één contribution-lifecycle voor een vooraf bepaalde outcome.
 * Een optionele mutation draait vóór de eerste insert in dezelfde outer
 * transaction; haar resultaat wordt pas samen met de outcome zichtbaar. */
export function logContributionOutcome(outcomeKey, content, meta = {}, mutation = null) {
  if (db.inTransaction === true) {
    throw new Error('contribution-outcome vereist eigendom van de buitenste transactie');
  }
  const key = String(outcomeKey || '').trim();
  if (!/^[a-f0-9]{64}$/u.test(key) || !contributionOutcomeSchemaReady()) {
    throw new Error('duurzame contribution-outcomegrens is niet beschikbaar');
  }
  if (mutation !== null && typeof mutation !== 'function') {
    throw new Error('ongeldige contribution-outcome mutation');
  }
  const safeContent = redactSensitiveText(content);
  const safeMeta = redactSecrets({ ...(meta && typeof meta === 'object' ? meta : {}), outcomeKey: key });
  if (!safeContent.trim() || !safeMeta || typeof safeMeta !== 'object' || Array.isArray(safeMeta)) {
    throw new Error('ongeldige contribution-outcome');
  }
  const settled = db.transaction(() => {
    const prior = db.prepare(`SELECT id,ts,kind,content,meta,outcome_key FROM main.timeline
      WHERE kind='contribution' AND outcome_key=?`).get(key);
    if (prior) {
      const row = exactContributionOutcomeRow(prior, key, safeContent, safeMeta);
      if (!row) throw new Error('conflicterende contribution-outcome');
      const effect = mutation ? mutation({ replay: true, row }) : undefined;
      return { row, created: false, ...(mutation ? { effect } : {}) };
    }
    let effect;
    if (mutation) {
      const mutationAuthority = Object.freeze({});
      const issued = {
        active: true, outcomeKey: key, content: safeContent, meta: safeMeta,
      };
      CONTRIBUTION_MUTATION_AUTHORITIES.set(mutationAuthority, issued);
      try {
        effect = mutation({ replay: false, row: null, mutationAuthority });
      } finally {
        // Deze capability bestaat uitsluitend binnen deze ene synchrone
        // mutation-callback. Capturen voor een latere transactie levert niets op.
        issued.active = false;
        CONTRIBUTION_MUTATION_AUTHORITIES.delete(mutationAuthority);
      }
    }
    const ts = isoNow();
    const info = db.prepare(`INSERT OR IGNORE INTO main.timeline
      (ts,kind,content,meta,outcome_key) VALUES (?,'contribution',?,?,?)`).run(
      ts, safeContent, JSON.stringify(safeMeta), key,
    );
    const stored = db.prepare(`SELECT id,ts,kind,content,meta,outcome_key FROM main.timeline
      WHERE kind='contribution' AND outcome_key=?`).get(key);
    const row = exactContributionOutcomeRow(stored, key, safeContent, safeMeta);
    if (!row) throw new Error('contribution-outcome kon niet exact worden opgeslagen');
    return {
      row, created: Number(info.changes || 0) === 1,
      ...(mutation ? { effect } : {}),
    };
  }).immediate();
  if (settled.created) {
    try { bus.emit('timeline', settled.row); } catch {
      // De domeinmelding volgt na commit en mag een duurzame outcome nooit als
      // teruggedraaid rapporteren. Replay publiceert haar bewust niet opnieuw.
    }
  }
  return settled;
}

export function getTimeline({ limit = 50, kinds = null, before = null } = {}) {
  let sql = 'SELECT id, ts, kind, content, meta FROM main.timeline';
  const where = [];
  const params = [];
  if (kinds && kinds.length) {
    where.push(`kind IN (${kinds.map(() => '?').join(',')})`);
    params.push(...kinds);
  }
  if (before) {
    where.push('id < ?');
    params.push(before);
  }
  if (where.length) sql += ' WHERE ' + where.join(' AND ');
  sql += ' ORDER BY id DESC LIMIT ?';
  params.push(limit);
  return db
    .prepare(sql)
    .all(...params)
    .map((r) => ({ ...r, meta: r.meta ? JSON.parse(r.meta) : null }));
}

export const TELEGRAM_DELIVERY_STATUS = Object.freeze({
  TRANSPORT_SENDING: 'transport_sending',
  TRANSPORT_ACCEPTED: 'transport_accepted',
  TRANSPORT_RETRYABLE: 'transport_retryable',
  TRANSPORT_REJECTED: 'transport_rejected',
  TRANSPORT_UNCERTAIN: 'transport_uncertain',
  INBOUND_RECEIVED: 'received',
  INBOUND_PROCESSING: 'processing',
  INBOUND_FAILED: 'failed',
  INBOUND_COMPLETED: 'completed',
  INBOUND_REJECTED: 'delivery_rejected',
  INBOUND_UNCERTAIN: 'delivery_uncertain',
});
export const TELEGRAM_TRANSPORT_PROTOCOL = 'TelegramTransportIntent/v2';
export const TELEGRAM_RECEIPT_PROTOCOL = 'TelegramAcceptedReceipt/v3';
export const TELEGRAM_TRANSPORT_MAX_ATTEMPTS = 5;

export function canonicalTelegramText(value) {
  return normalizeUserFacingText(
    redactSensitiveText(String(value ?? '')),
  ).trim().slice(0, 64_000).trimEnd();
}

export function canonicalTelegramCaption(value) {
  return normalizeUserFacingText(
    redactSensitiveText(String(value ?? '')),
  ).slice(0, 1_024);
}

const TELEGRAM_FAMILY_TARGETS = new Set([
  'gezin', 'het gezin', 'familie', 'iedereen', 'allemaal',
]);

export function normalizeTelegramRecipient(value) {
  return String(value ?? '').normalize('NFKC').trim().replace(/\s+/g, ' ').toLowerCase();
}

/** Eén canonieke naam/id/gezin-resolutie voor transport én bewijscontrole. */
export function resolveTelegramTargets(people, requested, { broadcast = false } = {}) {
  const reachable = (Array.isArray(people) ? people : [])
    .filter((person) => String(person?.telegram_id || '').trim());
  const recipient = normalizeTelegramRecipient(requested);
  const family = TELEGRAM_FAMILY_TARGETS.has(recipient);
  const failure = (code, info) => ({
    ok: false, code, info, recipient: requested || null, broadcast: false, targets: [],
  });
  if (!recipient && broadcast !== true) {
    return failure(
      'TELEGRAM_RECIPIENT_REQUIRED',
      'Telegram-ontvanger ontbreekt; kies een persoon of vraag expliciet om het gezin.',
    );
  }
  if (!recipient || family) {
    const unique = new Map(reachable.map((person) => [String(person.telegram_id).trim(), person]));
    const targets = [...unique.values()];
    return targets.length ? {
      ok: true, code: 'TELEGRAM_BROADCAST_RESOLVED', recipient: family ? recipient : 'gezin',
      info: `expliciete gezinsontvanger met ${targets.length} uniek(e) chat-id(s)`,
      broadcast: true, targets,
    } : failure(
      'TELEGRAM_NO_RECIPIENTS',
      'Nog niemand heeft met Eva ge-Telegramd; er is geen chat-id bekend.',
    );
  }
  const byId = reachable.filter((person) => String(person.telegram_id).trim() === recipient);
  const byName = reachable.filter((person) => normalizeTelegramRecipient(person?.name) === recipient);
  const partial = reachable.filter((person) => normalizeTelegramRecipient(person?.name).includes(recipient));
  const unique = new Map((byId.length ? byId : byName.length ? byName : partial)
    .map((person) => [String(person.telegram_id).trim(), person]));
  const targets = [...unique.values()];
  if (!targets.length) {
    return failure(
      'TELEGRAM_RECIPIENT_UNKNOWN',
      `Onbekende Telegram-ontvanger "${String(requested).trim()}"; er is niets verstuurd.`,
    );
  }
  if (targets.length > 1) {
    return failure(
      'TELEGRAM_RECIPIENT_AMBIGUOUS',
      `Telegram-ontvanger "${String(requested).trim()}" past bij meerdere personen; gebruik de volledige naam.`,
    );
  }
  return {
    ok: true, code: 'TELEGRAM_RECIPIENT_RESOLVED', recipient: targets[0].name,
    info: `gerichte ontvanger ${targets[0].name}`, broadcast: false, targets,
  };
}

const SHA256_PATTERN = /^[a-f0-9]{64}$/u;
const positiveTelegramInteger = (value) => {
  const number = Number(value);
  return Number.isSafeInteger(number) && number > 0 ? number : null;
};

function telegramReceiptBaseValid({ core, receiptSha256, response, intent, operationKey }) {
  const expectedThread = positiveTelegramInteger(intent?.sendOptions?.message_thread_id);
  const expectedReply = positiveTelegramInteger(intent?.sendOptions?.reply_parameters?.message_id);
  const intentSha256 = createHash('sha256').update(canonicalJson(intent)).digest('hex');
  const computedReceiptSha256 = createHash('sha256').update(canonicalJson(core)).digest('hex');
  return intent?.contract === TELEGRAM_TRANSPORT_PROTOCOL
    && ['text', 'photo', 'audio'].includes(String(intent?.kind || ''))
    && String(intent?.chatId || '').trim() !== ''
    && String(intent?.deliveryKey || '').trim() !== ''
    && SHA256_PATTERN.test(String(intent?.payloadSha256 || ''))
    && core.contract === TELEGRAM_RECEIPT_PROTOCOL
    && core.accepted === true
    && core.operationKey === operationKey
    && core.intentSha256 === intentSha256
    && receiptSha256 === computedReceiptSha256
    && String(response.chatId || '') === String(intent.chatId)
    && positiveTelegramInteger(response.messageId) != null
    && positiveTelegramInteger(response.telegramDate) != null
    && positiveTelegramInteger(core.attempt) != null
    && Number(core.attempt) <= TELEGRAM_TRANSPORT_MAX_ATTEMPTS
    && positiveTelegramInteger(response.messageThreadId) === expectedThread
    && positiveTelegramInteger(response.replyToMessageId) === expectedReply;
}

function telegramTextReceiptValid(intent, response) {
  if (intent?.kind !== 'text') return true;
  return SHA256_PATTERN.test(String(intent?.wholeReplySha256 || ''))
    && intent?.segment?.splitVersion === 'split-3800/v1'
    && Number.isSafeInteger(Number(intent?.segment?.index))
    && Number(intent.segment.index) >= 0
    && Number.isSafeInteger(Number(intent?.segment?.count))
    && Number(intent.segment.count) > Number(intent.segment.index)
    && response.textSha256 === intent.payloadSha256
    && response.captionSha256 == null
    && response.mediaFileId == null;
}

function telegramMediaReceiptValid(intent, response) {
  if (intent?.kind === 'text') return true;
  return SHA256_PATTERN.test(String(intent?.captionSha256 || ''))
    && intent.captionSha256 === createHash('sha256')
      .update(String(intent?.sendOptions?.caption || '')).digest('hex')
    && response.textSha256 == null
    && String(response.mediaFileId || '').trim() !== ''
    && (!intent?.sendOptions?.caption
      ? response.captionSha256 == null || response.captionSha256 === intent.captionSha256
      : response.captionSha256 === intent.captionSha256);
}

/** Eén pure validator voor ieder immutable Telegram-acceptatiereceipt. */
export function validateTelegramAcceptedReceipt(receipt, operationKey, intent) {
  const { receiptSha256, ...core } = receipt && typeof receipt === 'object' ? receipt : {};
  const response = core.response && typeof core.response === 'object' ? core.response : {};
  if (!telegramReceiptBaseValid({ core, receiptSha256, response, intent, operationKey })
    || !telegramTextReceiptValid(intent, response)
    || !telegramMediaReceiptValid(intent, response)) {
    throw Object.assign(new Error(
      'Duurzaam Telegram-acceptatiebewijs is gewijzigd, onvolledig of hoort bij een andere payload.',
    ), { code: 'TELEGRAM_RECEIPT_INVALID' });
  }
  return receipt;
}

function telegramProofJson(value) {
  if (value && typeof value === 'object') return value;
  try { return JSON.parse(String(value || '')); } catch { return {}; }
}

function telegramProofError(message) {
  return Object.assign(new Error(message), { code: 'TELEGRAM_DELIVERY_PROOF_INVALID' });
}

function telegramArchiveProofValid(archived, expectedKind, operationKey, intent, receipt) {
  const meta = archived?.meta || {};
  return archived?.role === 'assistant'
    && archived?.kind === expectedKind
    && archived.deleted !== true
    && !meta.retractionPending
    && !meta.retractionUncertain
    && !meta.retracted
    && meta.operationKey === operationKey
    && meta.intentSha256 === receipt.intentSha256
    && meta.receiptSha256 === receipt.receiptSha256
    && meta.payloadSha256 === intent.payloadSha256
    && String(meta.messageThreadId || '') === String(receipt.response?.messageThreadId || '')
    && String(archived.reply_to_message_id || '') === String(receipt.response?.replyToMessageId || '');
}

function acceptedTelegramReference(reference, expectedKind) {
  const operationKey = String(reference?.operationKey || '').trim();
  const chatId = String(reference?.chatId || '').trim();
  const messageId = String(reference?.messageId || '').trim();
  if (!operationKey || !chatId || !messageId) {
    throw telegramProofError('Telegramaflevering bevat een onvolledige transportreferentie.');
  }
  const execution = /** @type {any} */ (validateExecutionIntent(db.prepare(`
    SELECT * FROM main.turn_tool_executions WHERE execution_key=?
  `).get(operationKey)));
  const intent = telegramProofJson(execution?.args);
  const receipt = telegramProofJson(execution?.output);
  const expectedTool = `@transport/telegram.${expectedKind === 'text'
    ? 'sendMessage' : expectedKind === 'photo' ? 'sendPhoto' : 'sendAudio'}`;
  if (execution?.status !== TELEGRAM_DELIVERY_STATUS.TRANSPORT_ACCEPTED
    || execution?.tool !== expectedTool || intent?.kind !== expectedKind) {
    throw telegramProofError('Telegramaflevering verwijst niet naar geaccepteerd transport.');
  }
  validateTelegramAcceptedReceipt(receipt, operationKey, intent);
  if (String(receipt.response?.chatId || '') !== chatId
    || String(receipt.response?.messageId || '') !== messageId) {
    throw telegramProofError('Telegramaflevering verwijst naar een ander bericht.');
  }
  const archived = telegramMessage(chatId, messageId);
  if (!telegramArchiveProofValid(archived, expectedKind, operationKey, intent, receipt)) {
    throw telegramProofError('Telegramaflevering mist een intact lokaal berichtbewijs.');
  }
  return { operationKey, chatId, messageId, execution, intent, receipt, archived };
}

function telegramRecoveryAnchorValid(anchor, { deliveryKey, signalRef }) {
  const meta = anchor?.meta || {};
  const chunkCount = Number(meta.chunkCount);
  return Boolean(anchor?.sender === 'Eva' && anchor?.kind === 'text'
    && anchor?.processing_status === TELEGRAM_DELIVERY_STATUS.INBOUND_COMPLETED
    && meta.outbound === true && meta.transportAccepted === true
    && meta.transportProtocol === TELEGRAM_TRANSPORT_PROTOCOL
    && meta.deliveryKey === deliveryKey && meta.directTurnSignalRef === signalRef
    && Number(meta.chunkIndex) === 0
    && Number.isSafeInteger(chunkCount) && chunkCount >= 1 && chunkCount <= 32);
}

function telegramRecoveryArchiveValid(archived, {
  deliveryKey, signalRef, chunkCount, indexes, operationKeys, messageIds,
}) {
  const meta = archived?.meta || {};
  const index = Number(meta.chunkIndex);
  const operationKey = String(meta.operationKey || '').trim();
  const messageId = String(archived?.message_id || '');
  return Boolean(archived?.sender === 'Eva' && archived?.kind === 'text'
    && archived?.processing_status === TELEGRAM_DELIVERY_STATUS.INBOUND_COMPLETED
    && meta.outbound === true && meta.transportAccepted === true
    && meta.transportProtocol === TELEGRAM_TRANSPORT_PROTOCOL
    && meta.deliveryKey === deliveryKey && meta.directTurnSignalRef === signalRef
    && Number.isSafeInteger(index) && index >= 0 && index < chunkCount
    && Number(meta.chunkCount) === chunkCount && !indexes.has(index)
    && Boolean(operationKey) && !operationKeys.has(operationKey)
    && !messageIds.has(messageId));
}

function telegramRecoveryTransportValid({
  archived, accepted, chat, deliveryKey, signalRef, principal, replyTo, chunkCount,
  wholeReplySha256,
}) {
  const meta = archived.meta || {};
  const { operationKey, execution, intent, receipt } = accepted;
  const index = Number(meta.chunkIndex);
  const expectedOperationKey = `telegram-text:${createHash('sha256').update(canonicalJson({
    contract: 'TelegramTextOperation/v1', chatId: chat, deliveryKey, index,
  })).digest('hex')}`;
  const currentWholeReplySha256 = String(intent.wholeReplySha256 || '');
  const expectedReplyTo = index === 0 ? replyTo : '';
  return Boolean(operationKey === expectedOperationKey && execution.turn_id === signalRef
    && execution.output === canonicalJson(receipt)
    && intent.deliveryKey === deliveryKey
    && intent.chunkKey === `${deliveryKey}:chunk:${index + 1}`
    && intent.directTurnSignalRef === signalRef && intent.originPrincipal === principal
    && Number(intent.segment?.index) === index && Number(intent.segment?.count) === chunkCount
    && meta.dedupeKey === intent.chunkKey && SHA256_PATTERN.test(currentWholeReplySha256)
    && (wholeReplySha256 == null || wholeReplySha256 === currentWholeReplySha256)
    && createHash('sha256').update(String(archived.content || '')).digest('hex')
      === intent.payloadSha256
    && String(receipt.response?.replyToMessageId || '') === expectedReplyTo);
}

/**
 * Bewijst één volledig direct Telegram-tekstantwoord uitsluitend met de
 * immutable transportledger en het exact daarmee gereconcilieerde archief.
 * Een los archiefrecord, een prefix of een ingetrokken bericht is geen bewijs.
 */
export function validateTelegramAcceptedReplyRecoveryEvidence({
  chatId, replyToMessageId, directTurnSignalRef, originPrincipal,
} = {}) {
  const chat = String(chatId || '').trim();
  const replyTo = String(replyToMessageId || '').trim();
  const signalRef = String(directTurnSignalRef || '').trim();
  const principal = String(originPrincipal || '').trim();
  const expectedSignalRef = `turn:telegram:${chat}:${replyTo}`;
  const deliveryKey = `user-reply:${expectedSignalRef}`;
  if (!chat || !replyTo || signalRef !== expectedSignalRef
    || !/^telegram:[1-9][0-9]*$/u.test(principal)) {
    throw telegramProofError('Telegram-herstelantwoord mist zijn exacte inboundbinding.');
  }
  const anchors = db.prepare(`SELECT * FROM main.telegram_messages
    WHERE chat_id=? AND role='assistant' AND reply_to_message_id=?
    ORDER BY id`).all(chat, replyTo).map(parseTelegramMessage);
  if (anchors.length !== 1) {
    throw telegramProofError('Telegram-herstelantwoord heeft geen uniek antwoordanker.');
  }
  const anchor = anchors[0];
  const anchorMeta = anchor.meta || {};
  const chunkCount = Number(anchorMeta.chunkCount);
  if (!telegramRecoveryAnchorValid(anchor, { deliveryKey, signalRef })) {
    throw telegramProofError('Telegram-herstelantwoord mist exact accepted Eva-archiefbewijs.');
  }
  const archives = db.prepare(`SELECT * FROM main.telegram_messages
    WHERE chat_id=? AND role='assistant' AND json_valid(meta)
      AND json_extract(meta,'$.deliveryKey')=? ORDER BY id`).all(
    chat, deliveryKey,
  ).map(parseTelegramMessage);
  if (archives.length !== chunkCount) {
    throw telegramProofError('Telegram-herstelantwoord mist één of meer geaccepteerde segmenten.');
  }
  const indexes = new Set();
  const operationKeys = new Set();
  const messageIds = new Set();
  let wholeReplySha256 = null;
  const receipts = [];
  for (const archived of archives) {
    const meta = archived.meta || {};
    const index = Number(meta.chunkIndex);
    const operationKey = String(meta.operationKey || '').trim();
    if (!telegramRecoveryArchiveValid(archived, {
      deliveryKey, signalRef, chunkCount, indexes, operationKeys, messageIds,
    })) {
      throw telegramProofError('Telegram-herstelantwoord bevat strijdig archivevidence.');
    }
    const accepted = acceptedTelegramReference({
      operationKey, chatId: chat, messageId: String(archived.message_id),
    }, 'text');
    const { execution, intent, receipt } = accepted;
    const currentWholeReplySha256 = String(intent.wholeReplySha256 || '');
    if (!telegramRecoveryTransportValid({
      archived, accepted, chat, deliveryKey, signalRef, principal, replyTo, chunkCount,
      wholeReplySha256,
    })) {
      throw telegramProofError('Telegram-herstelantwoord botst met zijn transportledger.');
    }
    wholeReplySha256 = currentWholeReplySha256;
    indexes.add(index);
    operationKeys.add(operationKey);
    messageIds.add(String(archived.message_id));
    receipts.push(Object.freeze({
      index,
      executionSeq: Number(execution.execution_seq),
      operationKey,
      intentRecordSha256: execution.intent_record_sha256,
      receiptSha256: receipt.receiptSha256,
      messageId: String(archived.message_id),
    }));
  }
  if (indexes.size !== chunkCount
    || [...Array(chunkCount).keys()].some((index) => !indexes.has(index))) {
    throw telegramProofError('Telegram-herstelantwoord is geen volledige segmentreeks.');
  }
  receipts.sort((left, right) => left.index - right.index);
  const evidence = Object.freeze({
    contract: 'TelegramAcceptedReplyRecoveryEvidence/v1',
    chatId: chat,
    replyToMessageId: replyTo,
    directTurnSignalRef: signalRef,
    originPrincipal: principal,
    deliveryKey,
    wholeReplySha256,
    receipts: Object.freeze(receipts),
  });
  return Object.freeze({
    ...evidence,
    evidenceSha256: createHash('sha256').update(canonicalJson(evidence)).digest('hex'),
  });
}

function telegramDeliveryExpectation(
  kind, value, caption, deliveryKey, messageThreadId, replyToMessageId,
) {
  const text = kind === 'text' ? canonicalTelegramText(value) : null;
  const safeCaption = kind === 'text' ? null : canonicalTelegramCaption(caption);
  return {
    kind,
    deliveryKey: deliveryKey == null ? null : String(deliveryKey),
    messageThreadId: positiveTelegramInteger(messageThreadId),
    replyToMessageId: positiveTelegramInteger(replyToMessageId),
    text,
    caption: safeCaption,
    payloadSha256: createHash('sha256').update(
      Buffer.isBuffer(value) ? value : String(text ?? value ?? ''),
    ).digest('hex'),
    captionSha256: kind === 'text' ? null
      : createHash('sha256').update(safeCaption).digest('hex'),
  };
}

function telegramReferencePayloadValid(accepted, expected) {
  const { intent, archived, receipt } = accepted;
  if (expected.deliveryKey != null && intent.deliveryKey !== expected.deliveryKey) return false;
  const expectedReply = expected.kind === 'text' && Number(intent.segment.index) > 0
    ? null : expected.replyToMessageId;
  if (positiveTelegramInteger(receipt.response?.messageThreadId) !== expected.messageThreadId
    || positiveTelegramInteger(receipt.response?.replyToMessageId) !== expectedReply) return false;
  if (expected.kind === 'text') {
    return intent.wholeReplySha256 === expected.payloadSha256
      && createHash('sha256').update(archived.content).digest('hex') === intent.payloadSha256;
  }
  return intent.payloadSha256 === expected.payloadSha256
    && intent.captionSha256 === expected.captionSha256
    && archived.content === expected.caption
    && archived.file_id === receipt.response.mediaFileId;
}

function addTelegramProofGroup(groups, accepted, kind) {
  const { intent, chatId } = accepted;
  const group = groups.get(chatId) || {
    count: null, indexes: new Set(), keys: new Set(), turnRefs: new Set(), principals: new Set(),
  };
  group.keys.add(String(intent.deliveryKey || ''));
  group.turnRefs.add(String(intent.directTurnSignalRef || ''));
  group.principals.add(String(intent.originPrincipal || ''));
  if (kind === 'text') {
    const index = Number(intent.segment.index);
    const count = Number(intent.segment.count);
    if (group.indexes.has(index) || (group.count != null && group.count !== count)) {
      throw telegramProofError('Telegramaflevering heeft dubbele of strijdige segmenten.');
    }
    group.count = count;
    group.indexes.add(index);
  } else if (group.count != null) {
    throw telegramProofError('Telegrammedia bevat meer dan één bewijs voor dezelfde chat.');
  } else {
    group.count = 1;
  }
  groups.set(chatId, group);
}

function telegramProofGroupSummary(groups, kind) {
  for (const group of groups.values()) {
    const incompleteText = kind === 'text' && (group.indexes.size !== group.count
      || [...Array(group.count).keys()].some((index) => !group.indexes.has(index)));
    if (group.keys.size !== 1 || group.turnRefs.size !== 1
      || group.principals.size !== 1 || incompleteText) {
      throw telegramProofError('Telegramaflevering bevat niet exact alle gebonden segmenten.');
    }
  }
  const values = (field) => new Set([...groups.values()].flatMap((group) => [...group[field]]));
  const keys = values('keys');
  const turnRefs = values('turnRefs');
  const principals = values('principals');
  if (keys.size !== 1 || turnRefs.size !== 1 || principals.size !== 1) {
    throw telegramProofError('Telegramfanout bevat onderling strijdige afleverbindings.');
  }
  return {
    chatIds: [...groups.keys()].sort(),
    deliveryKey: [...keys][0],
    directTurnSignalRef: [...turnRefs][0],
    originPrincipal: [...principals][0],
  };
}

function validateTelegramFanoutManifest(proof, expected, summary, messageKeys, flags) {
  if (flags.broadcast == null && flags.directReply == null) return;
  const execution = /** @type {any} */ (validateExecutionRecord(db.prepare(`
    SELECT * FROM main.turn_tool_executions WHERE execution_key=?
  `).get(String(proof.fanoutOperationKey || ''))));
  const intent = telegramProofJson(execution?.args);
  const output = telegramProofJson(execution?.output);
  const sameTargets = canonicalJson([...(intent.targetIds || [])].sort()) === canonicalJson(summary.chatIds);
  const sameMessages = canonicalJson([...(output.messageKeys || [])].sort())
    === canonicalJson([...messageKeys].sort());
  if (execution?.status !== 'done' || execution?.tool !== '@transport/telegram.fanout'
    || intent.contract !== 'TelegramFanoutIntent/v1' || intent.kind !== expected.kind
    || intent.deliveryKey !== expected.deliveryKey
    || intent.payloadSha256 !== expected.payloadSha256
    || intent.captionSha256 !== expected.captionSha256
    || intent.broadcast !== flags.broadcast || intent.directReply !== flags.directReply
    || !sameTargets || output.accepted !== true || !sameMessages) {
    throw telegramProofError('Telegramaflevering mist een passend duurzaam fanoutmanifest.');
  }
}

/** Valideer een volledige text/photo/audio-aflevering tegen ledger én berichtarchief. */
/** @param {any} options */
export function validateTelegramDeliveryProof({
  delivery, kind, value, caption = '', deliveryKey = null, expectedChatId = null,
  expectedChatIds = null, broadcast = null, directReply = null,
  messageThreadId = null, replyToMessageId = null,
  directTurnSignalRef = null, originPrincipal = null,
} = /** @type {any} */ ({})) {
  const proof = delivery && typeof delivery === 'object' ? delivery : {};
  const expectedKind = String(kind || '');
  const references = Array.isArray(proof.sent) ? proof.sent : [];
  if (proof.accepted !== true || proof.transport !== 'telegram' || proof.kind !== expectedKind
    || !['text', 'photo', 'audio'].includes(expectedKind) || !references.length) {
    throw telegramProofError('Extern transport gaf geen volledig Telegramacceptatiebewijs.');
  }
  const expected = telegramDeliveryExpectation(
    expectedKind, value, caption, deliveryKey, messageThreadId, replyToMessageId,
  );
  const operations = new Set();
  const messageKeys = new Set();
  const groups = new Map();
  const acceptedTimes = [];
  for (const reference of references) {
    const accepted = acceptedTelegramReference(reference, expectedKind);
    const acceptedAt = String(accepted.execution?.finished_ts || '');
    const acceptedMs = Date.parse(acceptedAt);
    if (!Number.isFinite(acceptedMs) || new Date(acceptedMs).toISOString() !== acceptedAt) {
      throw telegramProofError('Telegramaflevering mist een canonieke acceptatietijd.');
    }
    const messageKey = `${accepted.chatId}:${accepted.messageId}`;
    if (operations.has(accepted.operationKey) || messageKeys.has(messageKey)) {
      throw telegramProofError('Telegramaflevering hergebruikt een transport- of berichtbewijs.');
    }
    if (!telegramReferencePayloadValid(accepted, expected)) {
      throw telegramProofError('Telegramaflevering hoort bij een andere payload.');
    }
    operations.add(accepted.operationKey);
    messageKeys.add(messageKey);
    addTelegramProofGroup(groups, accepted, expectedKind);
    acceptedTimes.push(acceptedMs);
  }
  const summary = telegramProofGroupSummary(groups, expectedKind);
  const requestedChats = expectedChatIds == null
    ? (expectedChatId == null ? null : [String(expectedChatId)])
    : [...new Set(expectedChatIds.map(String))].sort();
  if (requestedChats && canonicalJson(requestedChats) !== canonicalJson(summary.chatIds)) {
    throw telegramProofError('Telegramaflevering hoort bij een andere bestemming.');
  }
  if ((directTurnSignalRef != null
    && summary.directTurnSignalRef !== String(directTurnSignalRef))
    || (originPrincipal != null && summary.originPrincipal !== String(originPrincipal))) {
    throw telegramProofError('Telegramaflevering hoort bij een andere beurt of principal.');
  }
  validateTelegramFanoutManifest(proof, expected, summary, messageKeys, { broadcast, directReply });
  return {
    proof, ...summary,
    maxAcceptedAt: new Date(Math.max(...acceptedTimes)).toISOString(),
  };
}
const DELIVERY_TERMINAL_STATUSES = new Set([
  TELEGRAM_DELIVERY_STATUS.INBOUND_COMPLETED,
  TELEGRAM_DELIVERY_STATUS.INBOUND_REJECTED,
  TELEGRAM_DELIVERY_STATUS.INBOUND_UNCERTAIN,
]);
const DURABLE_EXECUTION_STATUSES = new Set([
  'started', 'done', ...Object.values(TELEGRAM_DELIVERY_STATUS).filter((status) => status.startsWith('transport_')),
]);
const DURABLE_EXECUTION_INITIAL_STATUSES = new Set(['started', TELEGRAM_DELIVERY_STATUS.TRANSPORT_SENDING]);
const TELEGRAM_TRANSPORT_TOOL_PREFIX = '@transport/telegram.';
const DURABLE_EXECUTION_TRANSITIONS = Object.freeze({
  // Gewone tooluitvoer mag alleen via completeTurnToolExecution naar done.
  // Die route schrijft uitvoer en beide bewijs-hashes atomair.
  started: new Set(),
  done: new Set(),
  [TELEGRAM_DELIVERY_STATUS.TRANSPORT_SENDING]: new Set([
    TELEGRAM_DELIVERY_STATUS.TRANSPORT_ACCEPTED, TELEGRAM_DELIVERY_STATUS.TRANSPORT_RETRYABLE,
    TELEGRAM_DELIVERY_STATUS.TRANSPORT_REJECTED, TELEGRAM_DELIVERY_STATUS.TRANSPORT_UNCERTAIN,
  ]),
  [TELEGRAM_DELIVERY_STATUS.TRANSPORT_RETRYABLE]: new Set([
    TELEGRAM_DELIVERY_STATUS.TRANSPORT_SENDING, TELEGRAM_DELIVERY_STATUS.TRANSPORT_REJECTED,
  ]),
  [TELEGRAM_DELIVERY_STATUS.TRANSPORT_ACCEPTED]: new Set(),
  [TELEGRAM_DELIVERY_STATUS.TRANSPORT_REJECTED]: new Set(),
  [TELEGRAM_DELIVERY_STATUS.TRANSPORT_UNCERTAIN]: new Set(),
});

function reservedExecutionAuthorityError() {
  return Object.assign(
    new Error('de @turn-namespace is gereserveerd voor store-owned authority'),
    { code: 'EVA_RESERVED_EXECUTION_AUTHORITY' },
  );
}

function reservedTurnTool(value) {
  const tool = String(value || '');
  return tool === '@turn' || tool.startsWith('@turn/');
}

function executionOutputMetadata(storedOutput = '') {
  const output = String(storedOutput || '');
  let materialSha256 = null;
  try {
    const parsed = JSON.parse(output);
    const candidate = parsed?.data?.material_sha256 || parsed?.artifact_receipt?.sha256 || null;
    if (/^[a-f0-9]{64}$/u.test(String(candidate || ''))) materialSha256 = String(candidate);
  } catch { /* gewone uitvoer heeft alleen een outputhash */ }
  return {
    outputSha256: createHash('sha256').update(output).digest('hex'),
    materialSha256,
  };
}

/** @param {any} row */
function executionCompletionRecordSha(row) {
  const legacyRecord = {
    executionKey: row.execution_key,
    turnId: row.turn_id,
    tool: row.tool,
    args: row.args,
    semanticHash: row.semantic_hash,
    semanticBinding: row.semantic_binding,
    intentRecordSha256: row.intent_record_sha256,
    status: row.status,
    startedTs: row.started_ts,
    finishedTs: row.finished_ts,
    outputSha256: row.output_sha256,
    materialSha256: row.material_sha256,
  };
  if (row.execution_contract === EXECUTION_RECORD_CONTRACT_V1) {
    return createHash('sha256').update(canonicalJson(legacyRecord)).digest('hex');
  }
  if (row.execution_contract === EXECUTION_RECORD_CONTRACT_V2) {
    return createHash('sha256').update(canonicalJson({
      executionContract: row.execution_contract,
      executionSeq: Number(row.execution_seq),
      ...legacyRecord,
    })).digest('hex');
  }
  throw new Error('duurzame tooluitvoering heeft een onbekend uitvoeringscontract');
}

/** @param {any} row @returns {any} */
function validateExecutionOutput(row) {
  if (!row || row.status !== 'done') return row;
  const metadata = executionOutputMetadata(row.output || '');
  if (!row.output_sha256 || row.output_sha256 !== metadata.outputSha256) {
    throw new Error('duurzame tooluitvoer heeft een ongeldige outputhash');
  }
  if ((row.material_sha256 || null) !== metadata.materialSha256) {
    throw new Error('duurzame tooluitvoer heeft een ongeldige materiaalhash');
  }
  if (!row.completion_record_sha256
    || row.completion_record_sha256 !== executionCompletionRecordSha(row)) {
    throw new Error('duurzame tooluitvoer heeft een ongeldige eindrecordhash');
  }
  return row;
}

/** @param {any} value */
function executionIntentRecordSha({
  executionContract = EXECUTION_RECORD_CONTRACT_V1,
  executionSeq = null,
  turnId,
  tool,
  args,
  binding,
  semanticHash,
}) {
  const legacyRecord = {
    turnId,
    tool,
    args,
    binding,
    semanticHash,
  };
  if (executionContract === EXECUTION_RECORD_CONTRACT_V1) {
    return createHash('sha256').update(canonicalJson(legacyRecord)).digest('hex');
  }
  if (executionContract === EXECUTION_RECORD_CONTRACT_V2) {
    return createHash('sha256').update(canonicalJson({
      executionContract,
      executionSeq: Number(executionSeq),
      ...legacyRecord,
    })).digest('hex');
  }
  throw new Error('duurzame tooluitvoering heeft een onbekend uitvoeringscontract');
}

/** @param {any} row @returns {any} */
function validateExecutionIntent(row) {
  if (!row) return row;
  if (row.execution_contract !== EXECUTION_RECORD_CONTRACT_V1
    && row.execution_contract !== EXECUTION_RECORD_CONTRACT_V2) {
    throw new Error('duurzame tooluitvoering heeft een onbekend uitvoeringscontract');
  }
  if (!Number.isSafeInteger(Number(row.execution_seq)) || Number(row.execution_seq) <= 0) {
    throw new Error('duurzame toolintentie heeft geen stabiele uitvoeringsvolgorde');
  }
  if (row.execution_verification !== EXECUTION_VERIFICATION_VERIFIED) {
    throw new Error('duurzame legacy-tooluitvoering is onverifieerbaar');
  }
  let args;
  let binding;
  try {
    args = row.args == null ? null : JSON.parse(row.args);
    binding = row.semantic_binding == null ? null : JSON.parse(row.semantic_binding);
  } catch {
    throw new Error('duurzame toolintentie bevat ongeldige JSON');
  }
  if (canonicalJson(args) !== row.args
    || (binding == null ? row.semantic_binding != null : canonicalJson(binding) !== row.semantic_binding)) {
    throw new Error('duurzame toolintentie is niet canoniek opgeslagen');
  }
  const expected = executionIntentRecordSha({
    executionContract: row.execution_contract,
    executionSeq: row.execution_seq,
    turnId: row.turn_id,
    tool: row.tool,
    args,
    binding,
    semanticHash: row.semantic_hash,
  });
  if (!row.semantic_hash || !row.intent_record_sha256
    || row.intent_record_sha256 !== expected) {
    throw new Error('duurzame toolintentie heeft een ongeldige intentierecordhash');
  }
  return row;
}

/** @param {any} row @returns {any} */
export function validateExecutionRecord(row) {
  const validated = validateExecutionOutput(validateExecutionIntent(row));
  if (reservedTurnTool(validated?.tool)) {
    if (!turnUnderstandingAuthorityRecord(db, validated)) {
      throw new Error('duurzame @turn-uitvoering mist store-owned authority');
    }
  }
  return validated;
}

export function recentVerifiedTurnToolReceipts(conversationId, limit = 40) {
  requireExecutionLedger(db);
  const rows = db.prepare(`SELECT execution.* FROM main.turn_tool_executions execution
    WHERE execution.status='done' AND EXISTS (
      SELECT 1 FROM main.timeline turn WHERE turn.kind='turn_understanding'
        AND json_valid(turn.meta) AND json_extract(turn.meta, '$.conversationId')=?
        AND json_extract(turn.meta, '$.turnId')=execution.turn_id)
    ORDER BY execution.finished_ts DESC, execution.execution_key DESC LIMIT ?`).all(
    String(conversationId || ''), Math.max(1, Math.min(80, Number(limit) || 40)),
  );
  return rows.flatMap((row) => { try { return [validateExecutionRecord(row)]; } catch { return []; } });
}

/** @param {any} semanticBinding */
function normalizedExecutionBinding(semanticBinding) {
  const bindingFields = new Set([
    'understandingSha256', 'requirementId', 'addressesRequirementIds', 'basisReceiptIds',
    'capabilityId', 'operationId', 'role', 'executionType',
  ]);
  if (semanticBinding != null && (typeof semanticBinding !== 'object'
    || Array.isArray(semanticBinding)
    || Object.keys(semanticBinding).some((field) => !bindingFields.has(field)))) {
    throw new Error('ongeldige semantische uitvoeringsbinding');
  }
  const rawAddresses = Array.isArray(semanticBinding?.addressesRequirementIds)
    ? semanticBinding.addressesRequirementIds.map(String) : [];
  const rawBasis = Array.isArray(semanticBinding?.basisReceiptIds)
    ? semanticBinding.basisReceiptIds.map(String) : [];
  const binding = semanticBinding == null ? null : {
    understandingSha256: String(semanticBinding.understandingSha256 || ''),
    requirementId: String(semanticBinding.requirementId || ''),
    addressesRequirementIds: [...new Set(rawAddresses)],
    basisReceiptIds: [...new Set(rawBasis)],
    capabilityId: String(semanticBinding.capabilityId || ''),
    operationId: String(semanticBinding.operationId || ''),
    role: String(semanticBinding.role || ''),
    executionType: semanticBinding.executionType == null
      ? null : String(semanticBinding.executionType),
  };
  if (binding && (!/^[a-f0-9]{64}$/u.test(binding.understandingSha256)
    || !/^r(?:[1-9]|1[0-2])$/u.test(binding.requirementId)
    || rawAddresses.length !== binding.addressesRequirementIds.length
    || rawBasis.length !== binding.basisReceiptIds.length
    || !binding.addressesRequirementIds.length
    || binding.addressesRequirementIds.length > 12
    || binding.basisReceiptIds.length > 64
    || !binding.addressesRequirementIds.includes(binding.requirementId)
    || binding.addressesRequirementIds.some((id) => !/^r(?:[1-9]|1[0-2])$/u.test(id))
    || binding.basisReceiptIds.some((id) => !/^s[1-9][0-9]*$/u.test(id))
    || !/^[a-z][a-z0-9_.:-]{0,119}$/u.test(binding.capabilityId)
    || !/^[a-z][a-z0-9_.:-]{0,119}$/u.test(binding.operationId)
    || !/^[a-z][a-z0-9_:-]{0,79}$/u.test(binding.role)
    || (binding.executionType != null
      && !/^[a-z][a-z0-9_:-]{0,79}$/u.test(binding.executionType)))) {
    throw new Error('ongeldige semantische uitvoeringsbinding');
  }
  return binding;
}

/** @param {any} intent */
function claimStoredExecutionRecord(intent) {
  const {
    key, boundTurnId, boundTool, rawArgsCanonical, semanticArgs, storedArgs,
    binding, storedBinding, semanticHash, startingStatus,
  } = intent;
  let claimed = false;
  const row = db.transaction(() => {
    requireExecutionLedger(db);
    let storedRow = /** @type {any} */ (
      db.prepare('SELECT * FROM main.turn_tool_executions WHERE execution_key = ?').get(key)
    );
    if (!storedRow) {
      const nextSequence = /** @type {{value:number}} */ (db.prepare(
        `SELECT coalesce(max(execution_seq),0)+1 AS value
          FROM main.turn_tool_executions`,
      ).get());
      const sequence = Number(nextSequence.value);
      if (!Number.isSafeInteger(sequence) || sequence <= 0) {
        throw new Error('duurzame toolintentie kan geen veilige uitvoeringsvolgorde reserveren');
      }
      const intentRecordSha256 = executionIntentRecordSha({
        executionContract: EXECUTION_RECORD_CONTRACT_V2,
        executionSeq: sequence,
        turnId: boundTurnId,
        tool: boundTool,
        args: semanticArgs,
        binding,
        semanticHash,
      });
      db.prepare(`INSERT INTO main.turn_tool_executions
        (execution_key,turn_id,tool,args,semantic_hash,semantic_binding,
         intent_record_sha256,status,started_ts,execution_seq,execution_contract,
         execution_verification)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`).run(
        key,
        boundTurnId,
        boundTool,
        storedArgs,
        semanticHash,
        storedBinding,
        intentRecordSha256,
        startingStatus,
        isoNow(),
        sequence,
        EXECUTION_RECORD_CONTRACT_V2,
        EXECUTION_VERIFICATION_VERIFIED,
      );
      claimed = true;
      storedRow = db.prepare(
        'SELECT * FROM main.turn_tool_executions WHERE execution_key = ?',
      ).get(key);
    }
    if (storedRow.execution_contract === EXECUTION_RECORD_CONTRACT_V1
      && storedRow.execution_verification === EXECUTION_VERIFICATION_LEGACY_UNVERIFIABLE
      && !storedRow.semantic_hash && !storedRow.intent_record_sha256) {
      let legacyArgs = null;
      try {
        legacyArgs = canonicalJson(storedRow.args == null ? null : JSON.parse(storedRow.args));
      } catch { /* conflict */ }
      const sameLegacyIntent = storedRow.turn_id === boundTurnId
        && storedRow.tool === boundTool && storedRow.args === storedArgs
        && legacyArgs === storedArgs;
      const hashlessProof = storedRow.output_sha256 == null
        && storedRow.material_sha256 == null
        && storedRow.completion_record_sha256 == null;
      const replayableState = storedRow.status === 'started'
        ? storedRow.finished_ts == null && storedRow.output == null && hashlessProof
        : storedRow.status === 'done' && storedRow.finished_ts != null
          && storedRow.output != null && hashlessProof;
      const unambiguous = sameLegacyIntent && binding == null && storedRow.semantic_binding == null
        && !String(storedRow.args || '').includes('[AFGESCHERMD:')
        && rawArgsCanonical === storedArgs && replayableState;
      if (unambiguous) {
        const legacyIntentRecordSha256 = executionIntentRecordSha({
          executionContract: EXECUTION_RECORD_CONTRACT_V1,
          executionSeq: storedRow.execution_seq,
          turnId: boundTurnId,
          tool: boundTool,
          args: semanticArgs,
          binding,
          semanticHash,
        });
        let outputSha256 = storedRow.output_sha256;
        let materialSha256 = storedRow.material_sha256;
        let completionRecordSha256 = storedRow.completion_record_sha256;
        if (storedRow.status === 'done') {
          const metadata = executionOutputMetadata(storedRow.output || '');
          outputSha256 = metadata.outputSha256;
          materialSha256 = metadata.materialSha256;
          completionRecordSha256 = executionCompletionRecordSha({
            ...storedRow,
            semantic_hash: semanticHash,
            intent_record_sha256: legacyIntentRecordSha256,
            output_sha256: outputSha256,
            material_sha256: materialSha256,
          });
        }
        const replayTriggers = [
          'turn_tool_executions_intent_immutable',
          'turn_tool_executions_terminal_immutable',
        ];
        for (const name of replayTriggers) db.exec(`DROP TRIGGER main.${name}`);
        db.prepare(`UPDATE main.turn_tool_executions
          SET semantic_hash=?,intent_record_sha256=?,output_sha256=?,material_sha256=?,
            completion_record_sha256=?,execution_verification='verified'
          WHERE execution_key=? AND turn_id=? AND tool=? AND args=?
            AND semantic_binding IS NULL AND status=? AND started_ts=?
            AND finished_ts IS ? AND output IS ? AND output_sha256 IS NULL
            AND material_sha256 IS NULL AND completion_record_sha256 IS NULL
            AND execution_contract='ExecutionRecord/v1'
            AND semantic_hash IS NULL AND intent_record_sha256 IS NULL
            AND execution_verification='legacy_unverifiable'`).run(
          semanticHash, legacyIntentRecordSha256, outputSha256, materialSha256,
          completionRecordSha256, key, storedRow.turn_id, storedRow.tool, storedRow.args,
          storedRow.status, storedRow.started_ts, storedRow.finished_ts, storedRow.output,
        );
        for (const name of replayTriggers) db.exec(EXECUTION_LEDGER_TRIGGER_SQL[name]);
        storedRow = db.prepare(
          'SELECT * FROM main.turn_tool_executions WHERE execution_key = ?',
        ).get(key);
      }
    }
    if (storedRow.execution_verification !== EXECUTION_VERIFICATION_VERIFIED) {
      throw new Error('duurzame legacy-tooluitvoering is onverifieerbaar');
    }
    const expectedIntentRecordSha256 = executionIntentRecordSha({
      executionContract: storedRow.execution_contract,
      executionSeq: storedRow.execution_seq,
      turnId: boundTurnId,
      tool: boundTool,
      args: semanticArgs,
      binding,
      semanticHash,
    });
    if (storedRow.turn_id !== boundTurnId || storedRow.tool !== boundTool
      || storedRow.args !== storedArgs || storedRow.semantic_hash !== semanticHash
      || storedRow.intent_record_sha256 !== expectedIntentRecordSha256) {
      throw new Error('duurzame tooluitvoeringssleutel is al aan een andere handeling gebonden');
    }
    if ((storedRow.semantic_binding || null) !== storedBinding) {
      throw new Error('duurzame tooluitvoeringsbinding is gewijzigd of onvolledig');
    }
    return validateExecutionRecord(storedRow);
  }).immediate();
  return { claimed, row };
}

export function claimTurnToolExecution({
  executionKey = null, turnId = null, tool = null, args = null,
  semanticBinding = null, initialStatus = 'started',
} = {}) {
  const key = String(executionKey || '').trim();
  if (!key) throw new Error('tooluitvoering mist een duurzame sleutel');
  if (key.length > 512) throw new Error('duurzame tooluitvoeringssleutel is te lang');
  const boundTurnId = String(turnId || '').trim();
  const boundTool = String(tool || '').trim();
  if (!boundTurnId || !boundTool) throw new Error('tooluitvoering mist beurt of capability');
  if (key.startsWith('turn-understanding:') || reservedTurnTool(boundTool)) {
    throw reservedExecutionAuthorityError();
  }
  const startingStatus = String(initialStatus || 'started').trim();
  if (!DURABLE_EXECUTION_INITIAL_STATUSES.has(startingStatus)) {
    throw new Error(`ongeldige duurzame uitvoeringsstatus: ${startingStatus}`);
  }
  if (startingStatus.startsWith('transport_')
    && !boundTool.startsWith(TELEGRAM_TRANSPORT_TOOL_PREFIX)) {
    throw new Error('transportstatus is gereserveerd voor Telegram-transport');
  }
  const binding = normalizedExecutionBinding(semanticBinding);
  const rawArgs = args == null ? null : args;
  const semanticArgs = args == null ? null : redactSecrets(args);
  const rawArgsCanonical = canonicalJson(rawArgs);
  const storedArgs = canonicalJson(semanticArgs);
  const storedBinding = binding == null ? null : canonicalJson(binding);
  const semanticHash = createHash('sha256').update(canonicalJson({
    turnId: boundTurnId,
    tool: boundTool,
    args: rawArgs,
    binding,
  })).digest('hex');
  if (storedArgs.length > 20_000) throw new Error('tooluitvoeringsargumenten zijn te groot');
  return claimStoredExecutionRecord({
    key, boundTurnId, boundTool, rawArgs, rawArgsCanonical, semanticArgs, storedArgs,
    binding, storedBinding, semanticHash, startingStatus,
  });
}

/** CAS-overgang voor interne effectprotocollen die dezelfde semantische ledger gebruiken. */
export function transitionTurnToolExecution(executionKey, {
  from = null,
  status = null,
  output = null,
  finished = true,
} = {}) {
  requireExecutionLedger(db);
  const key = String(executionKey || '').trim();
  if (key.startsWith('turn-understanding:')) {
    throw reservedExecutionAuthorityError();
  }
  const rawCurrent = /** @type {any} */ (db.prepare(
    'SELECT * FROM main.turn_tool_executions WHERE execution_key=?',
  ).get(key));
  if (reservedTurnTool(rawCurrent?.tool)) {
    throw reservedExecutionAuthorityError();
  }
  const nextStatus = String(status || '').trim();
  const expected = [...new Set((Array.isArray(from) ? from : [from])
    .map((value) => String(value || '').trim()).filter(Boolean))];
  if (!key || !expected.length || !DURABLE_EXECUTION_STATUSES.has(nextStatus)
    || expected.some((value) => !DURABLE_EXECUTION_STATUSES.has(value))) {
    throw new Error('ongeldige duurzame uitvoerings-overgang');
  }
  if (expected.some((value) => !DURABLE_EXECUTION_TRANSITIONS[value]?.has(nextStatus))) {
    throw new Error(`verboden duurzame uitvoerings-overgang naar ${nextStatus}`);
  }
  const current = validateExecutionIntent(rawCurrent);
  if (!current) throw new Error('onbekende duurzame tooluitvoering');
  if ([current.status, nextStatus, ...expected].some((value) => value.startsWith('transport_'))
    && !current.tool.startsWith(TELEGRAM_TRANSPORT_TOOL_PREFIX)) {
    throw new Error('transportstatus is gereserveerd voor Telegram-transport');
  }
  const safeOutput = output == null
    ? null
    : redactSensitiveText(typeof output === 'string' ? output : canonicalJson(output)).slice(0, 40_000);
  const placeholders = expected.map(() => '?').join(',');
  const info = db.prepare(`
    UPDATE main.turn_tool_executions
    SET status=?, finished_ts=?, output=?
    WHERE execution_key=? AND status IN (${placeholders})
  `).run(nextStatus, finished ? isoNow() : null, safeOutput, key, ...expected);
  const row = validateExecutionIntent(
    db.prepare('SELECT * FROM main.turn_tool_executions WHERE execution_key=?').get(key),
  );
  if (Number(info.changes || 0) === 1) return { changed: true, row };
  if (row.status === nextStatus && row.output === safeOutput) return { changed: false, row };
  throw new Error(`duurzame uitvoerings-overgang botst met status ${row.status}`);
}

export function completeTurnToolExecution(executionKey, output) {
  requireExecutionLedger(db);
  const key = String(executionKey || '').trim();
  if (!key) throw new Error('tooluitvoering mist een duurzame sleutel');
  if (key.startsWith('turn-understanding:')) {
    throw reservedExecutionAuthorityError();
  }
  const safeOutput = redactSensitiveText(output).slice(0, 40_000);
  const { outputSha256, materialSha256 } = executionOutputMetadata(safeOutput);
  return db.transaction(() => {
    const current = /** @type {any} */ (
      db.prepare('SELECT * FROM main.turn_tool_executions WHERE execution_key = ?').get(key)
    );
    if (!current) throw new Error('onbekende duurzame tooluitvoering');
    if (reservedTurnTool(current.tool)) {
      throw reservedExecutionAuthorityError();
    }
    validateExecutionIntent(current);
    const opportunityCursor = opportunityCursorFromExecutionOutput(current.tool, safeOutput);
    const finishedTs = isoNow();
    const completionRecordSha256 = executionCompletionRecordSha({
      ...current,
      status: 'done',
      finished_ts: finishedTs,
      output_sha256: outputSha256,
      material_sha256: materialSha256,
    });
    const info = db.prepare(`
      UPDATE main.turn_tool_executions
      SET status = 'done', finished_ts = ?, output = ?, output_sha256 = ?, material_sha256 = ?,
          completion_record_sha256 = ?
      WHERE execution_key = ? AND status = 'started'
    `).run(
      finishedTs, safeOutput, outputSha256, materialSha256, completionRecordSha256, key,
    );
    if (Number(info.changes || 0) === 1 && opportunityCursor) {
      commitPreparedConversationOpportunity(opportunityCursor, finishedTs);
    }
    const row = /** @type {any} */ (
      db.prepare('SELECT * FROM main.turn_tool_executions WHERE execution_key = ?').get(key)
    );
    if (Number(info.changes || 0) === 1) {
      return { completed: true, row: validateExecutionRecord(row) };
    }
    if (row.status === 'done' && row.output === safeOutput) {
      if (opportunityCursor) commitPreparedConversationOpportunity(opportunityCursor, finishedTs);
      return { completed: false, row: validateExecutionRecord(row) };
    }
    throw new Error('duurzame tooluitvoering heeft een conflicterende eindstatus');
  }).immediate();
}

export function recordTurnToolObservation({
  turnId = null, tool = null, args = null, semanticBinding = null, output = '',
} = {}) {
  const executionKey = `observation:${String(turnId || '').slice(0, 180)}:${randomUUID()}`;
  const claimed = claimTurnToolExecution({
    executionKey, turnId, tool, args, semanticBinding,
  });
  if (!claimed.claimed) throw new Error('verse waarnemingsreceipt botste onverwacht');
  return completeTurnToolExecution(executionKey, output).row;
}

const VOICE_RETRY_TTL_MS = 5 * 60_000;
const VOICE_RETRY_FAILURES = new Set([
  'transient', 'network', 'network_error', 'netwerk', 'netwerkfout',
  'timeout', 'time_out', 'time-out', 'unavailable',
]);

function voiceRetryHash(value) {
  return createHash('sha256').update(canonicalJson(value)).digest('hex');
}

function freezeVoiceRetry(value, seen = new Set()) {
  if (!value || typeof value !== 'object' || seen.has(value)) return value;
  seen.add(value);
  Object.values(value).forEach((child) => freezeVoiceRetry(child, seen));
  return Object.freeze(value);
}

function voiceRetryOuterArgs(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const keys = Object.keys(value).sort();
  if (!['doel,tekst', 'tekst'].includes(keys.join(','))
    || typeof value.tekst !== 'string' || !value.tekst.trim()
    || (Object.hasOwn(value, 'doel') && (typeof value.doel !== 'string' || !value.doel.trim()))) {
    return null;
  }
  const args = { tekst: value.tekst, ...(value.doel == null ? {} : { doel: value.doel }) };
  const serialized = canonicalJson(args);
  if (/\[(?:afgeschermd|redacted)(?::[^\]]*)?\]/iu.test(serialized)
    || canonicalJson(redactSecrets(args)) !== serialized) return null;
  return args;
}

function voiceRetryDisposition(result) {
  if (!result || result.contract !== 'ActionResult/v1' || result.tool !== 'spreek_in_huis') return null;
  if (result.ok === true && result.verified === true) return 'already_completed';
  const safeFailure = result.ok === false && result.verified === false
    && result.changed === false && result.effect_uncertain === false
    && result.retryable === true && VOICE_RETRY_FAILURES.has(String(result.failure_class || ''));
  return safeFailure ? 'retryable_pre_effect' : null;
}

function voiceRetryUnderstandingValid(value) {
  if (!value || value.contract !== 'TurnUnderstandingRecord/v1'
    || !/^[a-f0-9]{64}$/u.test(String(value.sha256 || ''))
    || !/^[a-f0-9]{64}$/u.test(String(value.sourceSha256 || ''))) return false;
  const { sha256, ...body } = value;
  return voiceRetryHash(body) === sha256;
}

/**
 * Leidt een kortlevend voice-retryvoornemen af uit de nieuwste semantische
 * beurt en haar buitenste toolreceipt. Dit leest alleen; het voert niets uit.
 */
export function deriveVoiceRetryIntent({
  conversationId, now = new Date(),
} = /** @type {any} */ ({})) {
  const conversation = String(conversationId || '').trim();
  const nowMs = now instanceof Date ? now.getTime() : Number(now);
  if (!/^(?:voice|telegram):\S{1,230}$/u.test(conversation) || !Number.isFinite(nowMs)
    || redactSensitiveText(conversation) !== conversation) return null;
  const sourceRow = /** @type {any} */ (db.prepare(`SELECT id,ts,meta FROM main.timeline
    WHERE kind='turn_understanding' AND meta IS NOT NULL AND json_valid(meta)
      AND json_extract(meta, '$.conversationId')=?
    ORDER BY id DESC LIMIT 1`).get(conversation));
  if (!sourceRow) return null;
  const meta = telegramProofJson(sourceRow.meta);
  const turnId = String(meta.turnId || '').trim();
  const understanding = meta.understanding;
  if (meta.conversationId !== conversation || meta.interpretationSucceeded !== true
    || !turnId || redactSensitiveText(turnId) !== turnId
    || (conversation.startsWith('telegram:') && !turnId.startsWith(`${conversation}:`))
    || !voiceRetryUnderstandingValid(understanding)) return null;
  let candidates;
  try {
    candidates = db.prepare(`SELECT * FROM main.turn_tool_executions
      WHERE turn_id=? AND tool='spreek_in_huis' AND status='done'
      ORDER BY finished_ts DESC, execution_key DESC`).all(turnId)
      .map((row) => validateExecutionRecord(row))
      .map((raw) => {
      const row = /** @type {any} */ (raw);
      const args = voiceRetryOuterArgs(telegramProofJson(row.args));
      const result = telegramProofJson(row.output);
      return args && result?.contract === 'ActionResult/v1' && result.tool === 'spreek_in_huis'
        ? { row, args, result } : null;
      }).filter(Boolean);
  } catch {
    return null;
  }
  if (candidates.length !== 1) return null;
  const { row, args, result } = candidates[0];
  const disposition = voiceRetryDisposition(result);
  if (!disposition) return null;
  const finishedMs = Date.parse(row.finished_ts || '');
  if (!Number.isFinite(finishedMs) || nowMs < finishedMs || nowMs - finishedMs > VOICE_RETRY_TTL_MS) {
    return null;
  }
  const source = {
    timelineId: Number(sourceRow.id), timelineTs: sourceRow.ts,
    conversationId: conversation, turnId,
    understandingSha256: understanding.sha256,
    understandingSourceSha256: understanding.sourceSha256,
  };
  const effect = {
    executionKey: row.execution_key, turnId: row.turn_id, tool: row.tool,
    semanticHash: row.semantic_hash || null, status: row.status,
    startedTs: row.started_ts, finishedTs: row.finished_ts, args, result,
  };
  const body = {
    contract: 'VoiceRetryIntent/v1', disposition,
    conversationId: conversation, sourceTurnId: turnId,
    action: args, expiresAt: new Date(finishedMs + VOICE_RETRY_TTL_MS).toISOString(),
    sourceSha256: voiceRetryHash(source), effectSha256: voiceRetryHash(effect),
  };
  return freezeVoiceRetry({ ...body, sha256: voiceRetryHash(body) });
}

// ---------- gesprekken ----------

export function persistMessageWithoutPublication(
  channel, sender, role, content, conversationId = null,
) {
  const ts = isoNow();
  const safeContent = redactSensitiveText(content);
  const info = db.prepare('INSERT INTO main.messages (ts, channel, conversation_id, sender, role, content) VALUES (?, ?, ?, ?, ?, ?)').run(
    ts, channel, conversationId || null, sender, role, safeContent
  );
  const message = {
    id: Number(info.lastInsertRowid),
    ts,
    channel,
    conversation_id: conversationId || null,
    sender,
    role,
    content: safeContent,
  };
  return message;
}

export function publishPersistedMessage(message) {
  try { bus.emit('chat', message); } catch {
    // De message is al duurzaam gecommit. Een UI-listener mag die commit niet
    // als mislukt laten terugkomen bij de transportcaller.
  }
}

export function addMessage(channel, sender, role, content, conversationId = null) {
  const message = persistMessageWithoutPublication(
    channel, sender, role, content, conversationId,
  );
  publishPersistedMessage(message);
  return message;
}

/**
 * @typedef {object} ConversationMessageRow
 * @property {number} id
 * @property {string} ts
 * @property {string} channel
 * @property {string|null} conversation_id
 * @property {string} sender
 * @property {string} role
 * @property {string} content
 */

/** @returns {ConversationMessageRow[]} */
export function recentMessages(channel = null, limit = 30) {
  /** @type {ConversationMessageRow[]} */
  const rows = channel
    ? db.prepare('SELECT * FROM main.messages WHERE channel = ? ORDER BY id DESC LIMIT ?').all(channel, limit)
    : db.prepare('SELECT * FROM main.messages ORDER BY id DESC LIMIT ?').all(limit);
  return rows.reverse();
}

/** @returns {ConversationMessageRow[]} */
export function recentConversation(conversationId, limit = 30) {
  const id = String(conversationId || '').trim();
  if (!id) return [];
  /** @type {ConversationMessageRow[]} */
  const rows = db
    .prepare('SELECT * FROM main.messages WHERE conversation_id = ? ORDER BY id DESC LIMIT ?')
    .all(id, limit);
  return rows.reverse();
}

/**
 * Recente dialoog uit andere gesprekken van exact dezelfde bekende afzender.
 * De caller bepaalt of de actuele ingress die identiteit werkelijk vertrouwde;
 * deze query mint zelf bewust geen identiteit of uitvoerbevoegdheid.
 */
/** @returns {ConversationMessageRow[]} */
export function recentOtherConversationsForPerson(sender, conversationId, limit = 8) {
  const person = String(sender || '').trim();
  const currentConversation = String(conversationId || '').trim();
  if (!person || !currentConversation) return [];
  const since = new Date(Date.now() - 48 * 3600_000).toISOString();
  const boundedLimit = Math.max(1, Math.min(12, Number(limit) || 8));
  const tail = /** @type {ConversationMessageRow[]} */ (db.prepare(`
    SELECT * FROM main.messages ORDER BY id DESC LIMIT ?
  `).all(EPISODIC_RECALL_GLOBAL_TAIL_LIMIT));
  const personKey = person.normalize('NFKC').toLocaleLowerCase('nl-NL');
  const threadIds = [];
  const seenThreads = new Set();
  for (const row of tail) {
    const conversation = String(row.conversation_id || '');
    if (row.role !== 'user' || !conversation || conversation === currentConversation
      || row.ts < since || seenThreads.has(conversation)
      || String(row.sender || '').normalize('NFKC').toLocaleLowerCase('nl-NL') !== personKey) continue;
    seenThreads.add(conversation);
    threadIds.push(conversation);
    if (threadIds.length >= EPISODIC_RECALL_THREAD_LIMIT) break;
  }
  const threadRows = db.prepare(`SELECT * FROM main.messages INDEXED BY idx_messages_conversation
    WHERE conversation_id=? ORDER BY id DESC LIMIT ?`);
  const candidates = [];
  for (const conversation of threadIds) {
    const rows = /** @type {ConversationMessageRow[]} */ (threadRows.all(
      conversation, EPISODIC_RECALL_THREAD_ROW_LIMIT + 1,
    ));
    if (rows.length > EPISODIC_RECALL_THREAD_ROW_LIMIT) continue;
    const participants = rows.filter((row) => row.role === 'user');
    if (!participants.length || participants.some((row) => (
      String(row.sender || '').normalize('NFKC').toLocaleLowerCase('nl-NL') !== personKey
    ))) continue;
    candidates.push(...rows.filter((row) => row.ts >= since));
  }
  return candidates.sort((left, right) => Number(right.id) - Number(left.id))
    .slice(0, boundedLimit).reverse();
}

const EPISODIC_CONVERSATION_STOPWORDS = new Set([
  'aan', 'als', 'ben', 'bij', 'dan', 'dat', 'de', 'deze', 'die', 'dit', 'een',
  'en', 'er', 'heb', 'heeft', 'het', 'hoe', 'ik', 'in', 'is', 'je', 'jij', 'kan',
  'maar', 'me', 'met', 'mij', 'naar', 'niet', 'nog', 'nu', 'of', 'om', 'ons',
  'ook', 'op', 'over', 'te', 'van', 'voor', 'waar', 'wat', 'we', 'wel', 'wij',
  'wil', 'zijn', 'zou', 'weer', 'even', 'alweer', 'actueel', 'actuele', 'current',
  'huidig', 'huidige', 'momenteel', 'state', 'status', 'toestand',
]);
const EPISODIC_RECALL_CANDIDATE_LIMIT = 320;
const EPISODIC_RECALL_GLOBAL_TAIL_LIMIT = 640;
const EPISODIC_RECALL_THREAD_LIMIT = 64;
const EPISODIC_RECALL_THREAD_ROW_LIMIT = 24;
const EPISODIC_RECALL_MAX_AGE_MS = 365 * 24 * 3600_000;
const EPISODIC_RARE_TOKEN_MIN_LENGTH = 8;

function episodicConversationWords(value) {
  return String(value || '').normalize('NFKC').toLocaleLowerCase('nl-NL')
    .match(/[\p{L}\p{N}]{3,}/gu) || [];
}

function episodicConversationTokens(query) {
  return [...new Set(episodicConversationWords(String(query || '').slice(0, 800))
    .filter((word) => !EPISODIC_CONVERSATION_STOPWORDS.has(word)))]
    .slice(0, 16);
}

function episodicConversationRelevance(content, tokenText) {
  const tokens = String(tokenText || '').split(' ').filter(Boolean);
  if (!tokens.length) return 0;
  const words = new Set(episodicConversationWords(content));
  return tokens.reduce((score, token) => score + Number(words.has(token)), 0);
}

db.function('episodic_conversation_relevance', { deterministic: true },
  episodicConversationRelevance);

function olderConversationTailCandidate(row, {
  currentConversation, personKey, newerThan, olderThan, seenThreads,
}) {
  const conversation = String(row?.conversation_id || '');
  return row?.role === 'user' && Boolean(conversation) && conversation !== currentConversation
    && String(row?.sender || '').normalize('NFKC').toLocaleLowerCase('nl-NL') === personKey
    && row.ts >= newerThan && row.ts < olderThan && !seenThreads.has(conversation);
}

function singlePersonConversation(rows, personKey) {
  const participants = rows.filter((row) => row.role === 'user');
  return Boolean(participants.length) && participants.every((row) => (
    String(row.sender || '').normalize('NFKC').toLocaleLowerCase('nl-NL') === personKey
  ));
}

/**
 * Zoek een kleine, lexicaal relevante episode uit oudere één-op-ééngesprekken
 * met exact dezelfde bekende afzender. De caller bepaalt of de huidige ingress
 * die identiteit vertrouwt. Deze projectie is herinneringscontext en mint geen
 * actuele toestand, opdracht of uitvoerbevoegdheid.
 * @returns {ConversationMessageRow[]}
 */
export function relevantOlderConversationForPerson(
  sender, conversationId, query, limit = 3,
) {
  const person = String(sender || '').trim();
  const currentConversation = String(conversationId || '').trim();
  const tokens = episodicConversationTokens(query);
  if (!person || !currentConversation || !tokens.length) return [];
  const boundedLimit = Math.max(1, Math.min(6, Number(limit) || 3));
  const olderThan = new Date(Date.now() - 48 * 3600_000).toISOString();
  const newerThan = new Date(Date.now() - EPISODIC_RECALL_MAX_AGE_MS).toISOString();
  const tokenText = tokens.join(' ');
  // Begin altijd bij een harde newest-id-projectie. Daardoor kost een sparse
  // afzender nooit een jaar-/sender-scan: onder extreme berichtdruk missen we
  // liever een oude episode dan dat de interactieve beurt onbegrensd leest.
  const tail = /** @type {ConversationMessageRow[]} */ (db.prepare(`
    SELECT * FROM main.messages ORDER BY id DESC LIMIT ?
  `).all(EPISODIC_RECALL_GLOBAL_TAIL_LIMIT));
  const personKey = person.normalize('NFKC').toLocaleLowerCase('nl-NL');
  const threadIds = [];
  const seenThreads = new Set();
  for (const row of tail) {
    const conversation = String(row.conversation_id || '');
    if (!olderConversationTailCandidate(row, {
      currentConversation, personKey, newerThan, olderThan, seenThreads,
    })) continue;
    seenThreads.add(conversation);
    threadIds.push(conversation);
    if (threadIds.length >= EPISODIC_RECALL_THREAD_LIMIT) break;
  }
  const threadRows = db.prepare(`SELECT * FROM main.messages INDEXED BY idx_messages_conversation
    WHERE conversation_id=? ORDER BY id DESC LIMIT ?`);
  const candidates = new Map();
  for (const conversation of threadIds) {
    const rows = /** @type {ConversationMessageRow[]} */ (threadRows.all(
      conversation, EPISODIC_RECALL_THREAD_ROW_LIMIT + 1,
    ));
    // LIMIT+1 bewijst binnen een vaste indexseek dat de hele thread klein is.
    // Een langere thread valt volledig af; daardoor kan participantproof nooit
    // stilletjes de rest van een grote conversatie scannen of missen.
    if (rows.length > EPISODIC_RECALL_THREAD_ROW_LIMIT) continue;
    if (!singlePersonConversation(rows, personKey)) continue;
    for (const row of rows) {
      if (row.ts < newerThan || row.ts >= olderThan) continue;
      candidates.set(Number(row.id), row);
      if (candidates.size >= EPISODIC_RECALL_CANDIDATE_LIMIT) break;
    }
    if (candidates.size >= EPISODIC_RECALL_CANDIDATE_LIMIT) break;
  }
  const candidateIds = [...candidates.keys()];
  if (!candidateIds.length) return [];
  const minimumRelevance = tokens.length === 1 ? 1 : 2;
  const matches = /** @type {(ConversationMessageRow & {relevance:number})[]} */ (
    db.prepare(`
      WITH scored AS MATERIALIZED (
        SELECT m.*,episodic_conversation_relevance(m.content, ?) AS relevance
        FROM main.messages m
        WHERE m.id IN (${candidateIds.map(() => '?').join(',')})
      )
      SELECT * FROM scored WHERE relevance>=?
      ORDER BY relevance DESC,id DESC
      LIMIT 120
    `).all(
      tokenText, ...candidateIds, minimumRelevance,
    )
  );
  if (tokens.length === 1) {
    const conversations = new Set(matches.map((row) => String(row.conversation_id || '')));
    if (tokens[0].length < EPISODIC_RARE_TOKEN_MIN_LENGTH
      || conversations.size !== 1 || matches.length > 3) return [];
  }
  const chosen = [];
  const conversations = new Set();
  for (const match of matches) {
    const id = String(match.conversation_id || '');
    if (!id || conversations.has(id)) continue;
    conversations.add(id);
    chosen.push(match);
    if (chosen.length >= 2) break;
  }
  const rows = new Map();
  for (const match of chosen) {
    const before = /** @type {ConversationMessageRow[]} */ (db.prepare(`
      SELECT * FROM main.messages WHERE conversation_id=? AND id<=?
      ORDER BY id DESC LIMIT 2
    `).all(match.conversation_id, match.id)).reverse();
    const after = /** @type {ConversationMessageRow[]} */ (db.prepare(`
      SELECT * FROM main.messages WHERE conversation_id=? AND id>?
      ORDER BY id ASC LIMIT 1
    `).all(match.conversation_id, match.id));
    const matchTs = Date.parse(match.ts || '');
    for (const row of [...before, ...after]) {
      const rowTs = Date.parse(row.ts || '');
      if (!Number.isFinite(matchTs) || !Number.isFinite(rowTs)
        || row.ts < newerThan || row.ts >= olderThan
        || Math.abs(rowTs - matchTs) > 24 * 3600_000) continue;
      rows.set(Number(row.id), row);
    }
  }
  return [...rows.values()]
    .sort((left, right) => Number(left.id) - Number(right.id))
    .slice(-boundedLimit);
}

function parseTelegramMessage(row) {
  if (!row) return null;
  let meta = null;
  try { meta = row.meta ? JSON.parse(row.meta) : null; } catch { /* oude ongeldige meta blijft leeg */ }
  return {
    ...row,
    deleted: Number(row.deleted || 0) === 1,
    meta,
  };
}

function telegramTimestamp(value) {
  if (value instanceof Date && Number.isFinite(value.getTime())) return value.toISOString();
  if (typeof value === 'number' && Number.isFinite(value)) {
    const millis = value < 10_000_000_000 ? value * 1000 : value;
    const date = new Date(millis);
    if (Number.isFinite(date.getTime())) return date.toISOString();
  }
  const parsed = Date.parse(String(value || ''));
  return Number.isFinite(parsed) ? new Date(parsed).toISOString() : isoNow();
}

function telegramArchiveValues(existing, {
  ts, sender, role, kind, content, replyToMessageId, mediaRef, fileId,
  deleted, processingStatus, meta,
}) {
  let existingMeta = null;
  try { existingMeta = existing?.meta ? JSON.parse(existing.meta) : null; } catch { /* negeren */ }
  const mergedMeta = meta && typeof meta === 'object'
    ? { ...(existingMeta && typeof existingMeta === 'object' ? existingMeta : {}), ...meta }
    : existingMeta;
  return {
    ts: existing?.ts || telegramTimestamp(ts),
    sender: String(sender || existing?.sender || '').slice(0, 160),
    role: ['user', 'assistant'].includes(String(role)) ? String(role) : (existing?.role || 'user'),
    kind: String(kind || existing?.kind || 'text').slice(0, 40),
    content: redactSensitiveText(content || existing?.content || '').slice(0, 16_000),
    replyToMessageId: replyToMessageId == null
      ? (existing?.reply_to_message_id || null) : String(replyToMessageId),
    mediaRef: mediaRef == null ? (existing?.media_ref || null) : String(mediaRef),
    fileId: fileId == null ? (existing?.file_id || null) : String(fileId),
    deleted: deleted == null ? Number(existing?.deleted || 0) : (deleted ? 1 : 0),
    processingStatus: existing?.processing_status || processingStatus || 'received',
    processingAttempts: Number(existing?.processing_attempts || 0),
    processedTs: existing?.processed_ts || null,
    processingError: existing?.processing_error || null,
    meta: mergedMeta ? JSON.stringify(redactSecrets(mergedMeta)) : null,
  };
}

/** Bewaar of verrijk één werkelijk Telegram-bericht, idempotent op chat+bericht. */
export function archiveTelegramMessage({
  chatId,
  messageId,
  ts = null,
  sender = null,
  role = null,
  kind = null,
  content = null,
  replyToMessageId = null,
  mediaRef = null,
  fileId = null,
  deleted = null,
  processingStatus = null,
  meta = null,
} = {}) {
  if (chatId == null || messageId == null) return null;
  const chat = String(chatId).trim();
  const message = String(messageId).trim();
  if (!chat || !message) return null;

  const existing = db.prepare(
    'SELECT * FROM telegram_messages WHERE chat_id=? AND message_id=?',
  ).get(chat, message);
  const archivedTs = isoNow();
  const values = telegramArchiveValues(existing, {
    ts, sender, role, kind, content, replyToMessageId, mediaRef, fileId,
    deleted, processingStatus, meta,
  });

  db.prepare(`
    INSERT INTO telegram_messages
      (chat_id,message_id,ts,archived_ts,sender,role,kind,content,
       reply_to_message_id,media_ref,file_id,deleted,processing_status,
       processing_attempts,processed_ts,processing_error,meta)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    ON CONFLICT(chat_id,message_id) DO UPDATE SET
      ts=excluded.ts,
      archived_ts=excluded.archived_ts,
      sender=excluded.sender,
      role=excluded.role,
      kind=excluded.kind,
      content=excluded.content,
      reply_to_message_id=excluded.reply_to_message_id,
      media_ref=excluded.media_ref,
      file_id=excluded.file_id,
      deleted=excluded.deleted,
      processing_status=excluded.processing_status,
      processing_attempts=excluded.processing_attempts,
      processed_ts=excluded.processed_ts,
      processing_error=excluded.processing_error,
      meta=excluded.meta
  `).run(
    chat,
    message,
    values.ts,
    archivedTs,
    values.sender,
    values.role,
    values.kind,
    values.content,
    values.replyToMessageId,
    values.mediaRef,
    values.fileId,
    values.deleted,
    values.processingStatus,
    values.processingAttempts,
    values.processedTs,
    values.processingError,
    values.meta,
  );
  return telegramMessage(chat, message);
}

export function telegramMessage(chatId, messageId) {
  if (chatId == null || messageId == null) return null;
  return parseTelegramMessage(db.prepare(
    'SELECT * FROM telegram_messages WHERE chat_id=? AND message_id=?',
  ).get(String(chatId), String(messageId)));
}

/** Geef het eerstvolgende nieuwere bericht binnen dezelfde afzender- en topicscope. */
export function newerTelegramUserMessage(chatId, messageId, scope = null) {
  if (chatId == null || messageId == null) return null;
  const currentMessageId = String(messageId).trim();
  if (!/^\d+$/.test(currentMessageId)) return null;
  const current = telegramMessage(chatId, currentMessageId);
  const scopedFrom = String(
    scope?.fromTelegramId ?? current?.meta?.fromTelegramId ?? '',
  ).trim();
  const threadKnown = Object.hasOwn(scope || {}, 'messageThreadId') || current != null;
  const scopedThread = String(
    scope?.messageThreadId ?? current?.meta?.messageThreadId ?? '',
  ).trim();
  return parseTelegramMessage(db.prepare(`
    SELECT * FROM telegram_messages
    WHERE chat_id=? AND role='user' AND deleted=0
      AND message_id GLOB '[0-9]*'
      AND CAST(message_id AS INTEGER) > CAST(? AS INTEGER)
      AND (? = '' OR coalesce(json_extract(meta, '$.fromTelegramId'), '') = ?)
      AND (? = 0 OR coalesce(json_extract(meta, '$.messageThreadId'), '') = ?)
    ORDER BY CAST(message_id AS INTEGER) ASC LIMIT 1
  `).get(
    String(chatId), currentMessageId,
    scopedFrom, scopedFrom,
    threadKnown ? 1 : 0, scopedThread,
  ));
}

export function telegramOutboundByDedupeKey(dedupeKey, chatId = null) {
  const key = String(dedupeKey || '').trim();
  if (!key) return null;
  const chat = chatId == null ? null : String(chatId).trim();
  try {
    return parseTelegramMessage(db.prepare(`
      SELECT * FROM telegram_messages
      WHERE role = 'assistant' AND deleted = 0
        AND json_extract(meta, '$.dedupeKey') = ?
        AND (? IS NULL OR chat_id = ?)
      ORDER BY id DESC LIMIT 1
    `).get(key, chat || null, chat || null));
  } catch {
    return recentTelegramMessages({ role: 'assistant', limit: 1_000 })
      .reverse()
      .find((message) => message.meta?.dedupeKey === key
        && (!chat || String(message.chat_id) === chat)) || null;
  }
}

export function pendingPreparedTelegramMessages(limit = 30) {
  const bounded = Math.max(1, Math.min(100, Number(limit) || 30));
  const now = isoNow();
  try {
    return db.prepare(`
      SELECT * FROM telegram_messages
      WHERE role = 'user'
        AND processing_status IN ('received', 'failed')
        AND processing_attempts < 5
        AND json_type(meta, '$.preparedReply') = 'object'
        AND (json_extract(meta, '$.deliveryRetryAt') IS NULL
          OR json_extract(meta, '$.deliveryRetryAt') <= ?)
      ORDER BY id ASC
      LIMIT ?
    `).all(now, bounded).map(parseTelegramMessage);
  } catch {
    return recentTelegramMessages({
      role: 'user',
      includeDeleted: false,
      limit: 500,
    }).filter((message) => (
      ['received', 'failed'].includes(message.processing_status)
      && Number(message.processing_attempts || 0) < 5
      && message.meta?.preparedReply
      && (!message.meta?.deliveryRetryAt
        || Date.parse(message.meta.deliveryRetryAt) <= Date.parse(now))
    )).slice(0, bounded);
  }
}

export function hasDeliveredTurn(turnId) {
  const id = String(turnId || '').trim();
  if (!id) return false;
  try {
    return !!db.prepare(`
      SELECT 1 FROM main.timeline
      WHERE kind = 'chat_out' AND json_extract(meta, '$.turnId') = ?
      LIMIT 1
    `).get(id);
  } catch {
    return getTimeline({ limit: 5_000, kinds: ['chat_out'] })
      .some((entry) => entry.meta?.turnId === id);
  }
}

export function markTelegramMessageDeleted(chatId, messageId) {
  if (chatId == null || messageId == null) return false;
  return db.prepare(
    'UPDATE telegram_messages SET deleted=1, archived_ts=? WHERE chat_id=? AND message_id=?',
  ).run(isoNow(), String(chatId), String(messageId)).changes > 0;
}

export function claimTelegramMessage(chatId, messageId, { countAttempt = true } = {}) {
  if (chatId == null || messageId == null) return { claimed: false, reason: 'missing_identity' };
  const chat = String(chatId);
  const message = String(messageId);
  return db.transaction(() => {
    const row = db.prepare(
      'SELECT processing_status,processing_attempts FROM telegram_messages WHERE chat_id=? AND message_id=?',
    ).get(chat, message);
    if (!row) return { claimed: false, reason: 'not_archived' };
    if (DELIVERY_TERMINAL_STATUSES.has(row.processing_status)
      || row.processing_status === TELEGRAM_DELIVERY_STATUS.INBOUND_PROCESSING) {
      return {
        claimed: false,
        reason: row.processing_status,
        attempts: Number(row.processing_attempts || 0),
      };
    }
    const attempts = Number(row.processing_attempts || 0) + (countAttempt ? 1 : 0);
    if (!Number.isSafeInteger(attempts) || attempts < 0 || attempts > 64) {
      return { claimed: false, reason: 'attempt_limit', attempts };
    }
    const changed = db.prepare(`UPDATE telegram_messages
      SET processing_status='processing', processing_attempts=?,
          processing_error=NULL, archived_ts=?
      WHERE chat_id=? AND message_id=?
        AND processing_status=? AND processing_attempts=?`).run(
      attempts, isoNow(), chat, message,
      row.processing_status, Number(row.processing_attempts || 0),
    );
    if (Number(changed.changes) !== 1) {
      return { claimed: false, reason: 'claim_raced', attempts: Number(row.processing_attempts || 0) };
    }
    return { claimed: true, reason: 'claimed', attempts };
  }).immediate();
}

export function completeTelegramMessage(chatId, messageId) {
  if (chatId == null || messageId == null) return false;
  const now = isoNow();
  return db.prepare(`UPDATE telegram_messages
    SET processing_status='completed', processed_ts=?, processing_error=NULL, archived_ts=?
    WHERE chat_id=? AND message_id=? AND processing_status='processing'`).run(
    now,
    now,
    String(chatId),
    String(messageId),
  ).changes > 0;
}

export function failTelegramMessage(chatId, messageId, error = '') {
  if (chatId == null || messageId == null) return false;
  return db.prepare(`UPDATE telegram_messages
    SET processing_status='failed', processing_error=?, archived_ts=?
    WHERE chat_id=? AND message_id=? AND processing_status='processing'`).run(
    String(error || '').slice(0, 500),
    isoNow(),
    String(chatId),
    String(messageId),
  ).changes > 0;
}

export function markTelegramMessageDeliveryState(chatId, messageId, {
  status = null,
  error = '',
} = {}) {
  const nextStatus = String(status || '').trim();
  if (![TELEGRAM_DELIVERY_STATUS.INBOUND_UNCERTAIN,
    TELEGRAM_DELIVERY_STATUS.INBOUND_REJECTED].includes(nextStatus)) {
    throw new Error(`ongeldige Telegram-afleverstatus: ${nextStatus || '(leeg)'}`);
  }
  if (chatId == null || messageId == null) return false;
  return db.transaction(() => {
    const row = db.prepare(`SELECT processing_status FROM telegram_messages
      WHERE chat_id=? AND message_id=? AND role='user'`).get(String(chatId), String(messageId));
    if (!row) return false;
    if (row.processing_status === nextStatus) return true;
    if (DELIVERY_TERMINAL_STATUSES.has(row.processing_status)) {
      throw new Error(`terminale Telegram-afleverstatus botst met ${row.processing_status}`);
    }
    return db.prepare(`UPDATE telegram_messages
      SET processing_status=?, processing_error=?, archived_ts=?
      WHERE chat_id=? AND message_id=? AND role='user'
        AND processing_status NOT IN ('completed','delivery_uncertain','delivery_rejected')`).run(
      nextStatus, String(error || '').slice(0, 500), isoNow(),
      String(chatId), String(messageId),
    ).changes > 0;
  })();
}

export function recoverInterruptedTelegramMessages() {
  return Number(db.prepare(`UPDATE telegram_messages
    SET processing_status='received', processing_error='proces herstartte tijdens verwerking',
        archived_ts=?
    WHERE role='user' AND processing_status='processing'`).run(isoNow()).changes || 0);
}

export function recentTelegramMessages({
  chatId = null,
  limit = 30,
  beforeMessageId = null,
  kind = null,
  role = null,
  includeDeleted = false,
} = {}) {
  const where = [];
  const params = [];
  if (chatId != null) {
    where.push('chat_id=?');
    params.push(String(chatId));
  }
  if (beforeMessageId != null && chatId != null) {
    const pivot = db.prepare(
      'SELECT id FROM telegram_messages WHERE chat_id=? AND message_id=?',
    ).get(String(chatId), String(beforeMessageId));
    if (pivot?.id) {
      where.push('id < ?');
      params.push(Number(pivot.id));
    }
  }
  if (kind) {
    const kinds = Array.isArray(kind) ? kind : [kind];
    const clean = kinds.map(String).filter(Boolean);
    if (clean.length) {
      where.push(`kind IN (${clean.map(() => '?').join(',')})`);
      params.push(...clean);
    }
  }
  if (role) {
    where.push('role=?');
    params.push(String(role));
  }
  if (!includeDeleted) where.push('deleted=0');
  const boundedLimit = Math.max(1, Math.min(500, Number(limit) || 30));
  const sql = `SELECT * FROM telegram_messages${where.length ? ` WHERE ${where.join(' AND ')}` : ''}
    ORDER BY id DESC LIMIT ?`;
  params.push(boundedLimit);
  return db.prepare(sql).all(...params).reverse().map(parseTelegramMessage);
}

const TELEGRAM_SEARCH_STOPWORDS = new Set([
  'aan', 'als', 'dat', 'dan', 'de', 'dit', 'een', 'en', 'er', 'het', 'hoe',
  'ik', 'in', 'is', 'je', 'jij', 'maar', 'met', 'mij', 'naar', 'nog', 'of',
  'om', 'ons', 'op', 'over', 'te', 'van', 'wat', 'we', 'wel', 'werd', 'zijn',
]);

export function searchTelegramMessages(query, {
  chatId = null,
  sender = null,
  limit = 12,
} = {}) {
  const terms = [...new Set(String(query || '').toLocaleLowerCase('nl-NL')
    .replace(/[^\p{L}\p{N}]+/gu, ' ')
    .split(/\s+/)
    .filter((term) => term.length >= 3 && !TELEGRAM_SEARCH_STOPWORDS.has(term)))]
    .slice(0, 10);
  const where = ['deleted=0'];
  const params = [];
  if (chatId != null) {
    where.push('chat_id=?');
    params.push(String(chatId));
  }
  if (sender) {
    where.push('lower(sender)=lower(?)');
    params.push(String(sender));
  }
  const candidates = db.prepare(`SELECT * FROM telegram_messages
    WHERE ${where.join(' AND ')}
    ORDER BY id DESC LIMIT 5000`).all(...params).map(parseTelegramMessage);
  const scored = candidates.map((row) => {
    const kindWords = {
      photo: 'foto afbeelding beeld plaatje',
      video: 'video filmpje beeld',
      video_note: 'video filmpje videobericht',
      voice: 'spraakbericht stem audio',
      audio: 'audio spraakbericht geluid',
      document: 'document bestand',
    }[row.kind] || row.kind || '';
    const haystack = `${row.sender || ''} ${row.content || ''} ${kindWords} ${row.media_ref || ''}`
      .toLocaleLowerCase('nl-NL');
    const score = terms.reduce((total, term) => total + (haystack.includes(term) ? 1 : 0), 0);
    return { row, score };
  }).filter(({ score }) => !terms.length || score > 0);
  scored.sort((a, b) => b.score - a.score || Number(b.row.id) - Number(a.row.id));
  return scored.slice(0, Math.max(1, Math.min(50, Number(limit) || 12))).map(({ row }) => row);
}

const TELEGRAM_OPEN_LOOP_STATUSES = new Set([
  TELEGRAM_DELIVERY_STATUS.INBOUND_RECEIVED,
  TELEGRAM_DELIVERY_STATUS.INBOUND_PROCESSING,
  TELEGRAM_DELIVERY_STATUS.INBOUND_FAILED,
  TELEGRAM_DELIVERY_STATUS.INBOUND_REJECTED,
  TELEGRAM_DELIVERY_STATUS.INBOUND_UNCERTAIN,
]);
const TELEGRAM_RECENT_RESIDENT_MS = 14 * 24 * 3600_000;
const TELEGRAM_OPPORTUNITY_LIMIT = 6;
const TELEGRAM_OPPORTUNITY_CONTENT_LIMIT = 360;
const TELEGRAM_OPPORTUNITY_MEDIA_LIMIT = 120;
const TELEGRAM_OPPORTUNITY_CURSOR_TTL_MS = 6 * 3600_000;
const TELEGRAM_OPPORTUNITY_PENDING_TTL_MS = 15 * 60_000;
const TELEGRAM_OPPORTUNITY_CURSOR_LIMIT = 512;
const TELEGRAM_SEARCH_SPEC_CONTRACT = 'TelegramConversationSearchSpec/v1';
const TELEGRAM_OPPORTUNITY_CURSOR_CONTRACT = 'TelegramConversationOpportunityCursor/v1';

function telegramSearchTerms(query) {
  return [...new Set(String(query || '').normalize('NFKC').toLocaleLowerCase('nl-NL')
    .replace(/[^\p{L}\p{N}]+/gu, ' ')
    .split(/\s+/)
    .filter((term) => term.length >= 3 && !TELEGRAM_SEARCH_STOPWORDS.has(term)))]
    .sort().slice(0, 10);
}

function telegramOpportunityLimit(value) {
  const numeric = Number(value);
  const integer = Number.isFinite(numeric) && numeric !== 0
    ? Math.trunc(numeric) : TELEGRAM_OPPORTUNITY_LIMIT;
  return Math.max(1, Math.min(TELEGRAM_OPPORTUNITY_LIMIT, integer));
}

/** Maak de enige canonieke query-, afzender- en chatscope voor autonome historiezoeking. */
export function telegramConversationSearchSpec(query, {
  chatId = null, sender = null, limit = TELEGRAM_OPPORTUNITY_LIMIT,
} = {}) {
  const chat = String(chatId ?? '').trim();
  if (!chat || chat.length > 180) throw new Error('gesprekzoeking mist exact één Telegramchat');
  const body = {
    contract: TELEGRAM_SEARCH_SPEC_CONTRACT,
    chat_id: chat,
    sender: String(sender || '').normalize('NFKC').trim().toLocaleLowerCase('nl-NL')
      .slice(0, 160) || null,
    terms: telegramSearchTerms(query),
    limit: telegramOpportunityLimit(limit),
  };
  return Object.freeze({
    ...body,
    sha256: createHash('sha256').update(canonicalJson(body)).digest('hex'),
  });
}

function exactTelegramConversationSearchSpec(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || value.contract !== TELEGRAM_SEARCH_SPEC_CONTRACT
    || typeof value.chat_id !== 'string' || !value.chat_id.trim()
    || value.chat_id !== value.chat_id.trim() || value.chat_id.length > 180
    || (value.sender != null && (typeof value.sender !== 'string'
      || !value.sender || value.sender.length > 160
      || value.sender !== value.sender.trim().toLocaleLowerCase('nl-NL')))
    || !Array.isArray(value.terms) || value.terms.length > 10
    || value.terms.some((term) => typeof term !== 'string' || term.length < 3)
    || canonicalJson(value.terms) !== canonicalJson([...new Set(value.terms)].sort())
    || !Number.isSafeInteger(value.limit) || value.limit < 1
    || value.limit > TELEGRAM_OPPORTUNITY_LIMIT
    || !SHA256_PATTERN.test(String(value.sha256 || ''))) return null;
  const body = {
    contract: TELEGRAM_SEARCH_SPEC_CONTRACT,
    chat_id: value.chat_id,
    sender: value.sender,
    terms: value.terms,
    limit: value.limit,
  };
  const sha256 = createHash('sha256').update(canonicalJson(body)).digest('hex');
  return sha256 === value.sha256 ? value : null;
}

function telegramOpportunityRowRevision(row) {
  return createHash('sha256').update(canonicalJson({
    id: Number(row.id),
    message_id: String(row.message_id || ''),
    role: String(row.role || ''),
    processing_status: String(row.processing_status || ''),
    processing_attempts: Number(row.processing_attempts || 0),
    archived_ts: String(row.archived_ts || ''),
    processed_ts: String(row.processed_ts || ''),
    content_sha256: createHash('sha256').update(String(row.content || '')).digest('hex'),
    media_sha256: createHash('sha256').update(String(row.media_ref || '')).digest('hex'),
    deleted: Boolean(row.deleted),
  })).digest('hex');
}

/**
 * Autonome kanszoeking heeft een andere opdracht dan een expliciete
 * chatzoekvraag. Zij combineert een kleine, verse bewonerslane met de gewone
 * lexicale lane. De gewone searchTelegramMessages-route blijft daardoor exact
 * lexicaal en chatscope-gebonden.
 */
export function searchTelegramConversationOpportunity(searchSpec, { now = Date.now() } = {}) {
  const spec = exactTelegramConversationSearchSpec(searchSpec);
  if (!spec) throw new Error('gesprekzoeking heeft geen geldige canonieke zoekspecificatie');
  const terms = spec.terms;
  const where = ['deleted=0', 'chat_id=?'];
  const params = [spec.chat_id];
  if (spec.sender) {
    where.push('lower(sender)=lower(?)');
    params.push(spec.sender);
  }
  const candidates = db.prepare(`SELECT * FROM telegram_messages
    WHERE ${where.join(' AND ')}
    ORDER BY id DESC LIMIT 5000`).all(...params).map(parseTelegramMessage);
  const boundedLimit = spec.limit;
  const recencyRanks = new Map(candidates.map((row, index) => [Number(row.id), index + 1]));
  const lexical = candidates.map((row) => {
    const kindWords = {
      photo: 'foto afbeelding beeld plaatje',
      video: 'video filmpje beeld',
      video_note: 'video filmpje videobericht',
      voice: 'spraakbericht stem audio',
      audio: 'audio spraakbericht geluid',
      document: 'document bestand',
    }[row.kind] || row.kind || '';
    const haystack = `${row.sender || ''} ${row.content || ''} ${kindWords} ${row.media_ref || ''}`
      .toLocaleLowerCase('nl-NL');
    const score = terms.reduce((total, term) => total + (haystack.includes(term) ? 1 : 0), 0);
    return { row, score };
  }).filter(({ score }) => !terms.length || score > 0)
    .sort((left, right) => right.score - left.score
      || Number(right.row.id) - Number(left.row.id));
  const recentlyObserved = (row) => {
    const observed = Date.parse(row.ts || '');
    const age = Number(now) - observed;
    return Number.isFinite(age) && age >= 0 && age <= TELEGRAM_RECENT_RESIDENT_MS;
  };
  const openResident = candidates.filter((row) => (
    row.role === 'user'
    && TELEGRAM_OPEN_LOOP_STATUSES.has(String(row.processing_status || ''))
    && recentlyObserved(row)
  ));
  const recentResident = candidates.filter((row) => {
    if (row.role !== 'user' || TELEGRAM_OPEN_LOOP_STATUSES.has(String(row.processing_status || ''))) {
      return false;
    }
    return recentlyObserved(row);
  });
  const selected = [];
  const selectedIds = new Set();
  const append = (entries, lane, maximum = boundedLimit) => {
    let appended = 0;
    for (const entry of entries) {
      const row = entry?.row || entry;
      const id = Number(row?.id);
      if (!Number.isSafeInteger(id) || selectedIds.has(id)) continue;
      selectedIds.add(id);
      const content = String(row.content || '');
      selected.push({
        id,
        chat_id: row.chat_id,
        message_id: row.message_id,
        ts: row.ts,
        sender: row.sender,
        role: row.role,
        kind: row.kind,
        content: content.slice(0, TELEGRAM_OPPORTUNITY_CONTENT_LIMIT),
        ...(content.length > TELEGRAM_OPPORTUNITY_CONTENT_LIMIT
          ? { content_truncated: true } : {}),
        ...(row.reply_to_message_id
          ? { reply_to_message_id: row.reply_to_message_id } : {}),
        ...(row.media_ref
          ? { media_ref: String(row.media_ref).slice(0, TELEGRAM_OPPORTUNITY_MEDIA_LIMIT) }
          : {}),
        processing_status: row.processing_status,
        search_metadata: {
          lane,
          role: row.role,
          processing_status: row.processing_status,
          recency_rank: recencyRanks.get(id),
          row_revision: telegramOpportunityRowRevision(row),
        },
      });
      appended += 1;
      if (selected.length >= boundedLimit || appended >= maximum) break;
    }
    return appended;
  };
  const residentLimit = Math.min(3, Math.ceil(boundedLimit / 2));
  const openCount = append(openResident, 'resident_open_loop', residentLimit);
  if (selected.length < boundedLimit && openCount < residentLimit) {
    append(recentResident, 'recent_resident', residentLimit - openCount);
  }
  if (selected.length < boundedLimit) append(lexical, 'lexical');
  const fingerprintRows = selected.map((row) => ({
    id: row.id,
    message_id: row.message_id,
    lane: row.search_metadata.lane,
    role: row.search_metadata.role,
    processing_status: row.search_metadata.processing_status,
    row_revision: row.search_metadata.row_revision,
  }));
  return {
    strategy: 'resident_opportunity_plus_lexical',
    search_spec_sha256: spec.sha256,
    opportunity_fingerprint: createHash('sha256').update(canonicalJson({
      search_spec_sha256: spec.sha256,
      messages: fingerprintRows,
    })).digest('hex'),
    high_watermark: selected.reduce((highest, row) => Math.max(highest, row.id), 0),
    messages: selected,
  };
}

function opportunityCursorMillis(value, fallback = Date.now()) {
  const parsed = value instanceof Date ? value.getTime()
    : typeof value === 'number' ? value : Date.parse(String(value || ''));
  return Number.isFinite(parsed) ? parsed : fallback;
}

/**
 * Reserveer een kans zonder haar al te verbruiken. Dezelfde onvoltooide read
 * krijgt binnen de pending-TTL exact hetzelfde token terug.
 */
export function prepareTelegramConversationOpportunity({
  spec,
  fingerprint,
  highWatermark = 0,
  ttlMs = TELEGRAM_OPPORTUNITY_CURSOR_TTL_MS,
  now = Date.now(),
} = /** @type {any} */ ({})) {
  const exactSpec = exactTelegramConversationSearchSpec(spec);
  const exactFingerprint = String(fingerprint || '');
  if (!exactSpec || !SHA256_PATTERN.test(exactFingerprint)) {
    throw new Error('gesprekskans mist canonieke specificatie of fingerprint');
  }
  const at = opportunityCursorMillis(now);
  const timestamp = new Date(at).toISOString();
  const boundedTtl = Math.max(60_000, Math.min(24 * 3600_000, Number(ttlMs)
    || TELEGRAM_OPPORTUNITY_CURSOR_TTL_MS));
  return db.transaction(() => {
    const current = /** @type {any} */ (db.prepare(`SELECT *
      FROM eva_conversation_opportunity_cursors WHERE opportunity_key=?`).get(exactSpec.sha256));
    const committedCurrent = current?.committed_fingerprint === exactFingerprint
      && opportunityCursorMillis(current?.expires_ts, 0) > at;
    if (committedCurrent) {
      db.prepare(`UPDATE eva_conversation_opportunity_cursors
        SET checked_ts=?,updated_ts=? WHERE opportunity_key=? AND spec_sha256=?`).run(
        timestamp, timestamp, exactSpec.sha256, exactSpec.sha256,
      );
      return Object.freeze({
        allowed: false,
        reason: 'no_new_data',
        highWatermark: Number(highWatermark) || 0,
        expiresTs: current.expires_ts,
      });
    }
    const pendingAt = opportunityCursorMillis(current?.pending_ts, 0);
    const pendingCurrent = current?.pending_fingerprint === exactFingerprint
      && typeof current?.pending_token === 'string'
      && current.pending_token.length > 0
      && at - pendingAt >= 0
      && at - pendingAt <= TELEGRAM_OPPORTUNITY_PENDING_TTL_MS;
    const token = pendingCurrent ? current.pending_token : randomUUID();
    if (pendingCurrent) {
      db.prepare(`UPDATE eva_conversation_opportunity_cursors
        SET checked_ts=?,updated_ts=? WHERE opportunity_key=? AND spec_sha256=?`).run(
        timestamp, timestamp, exactSpec.sha256, exactSpec.sha256,
      );
    } else {
      db.prepare(`INSERT INTO eva_conversation_opportunity_cursors (
          opportunity_key,spec_sha256,committed_fingerprint,committed_ts,checked_ts,expires_ts,
          pending_token,pending_fingerprint,pending_ts,pending_ttl_ms,updated_ts
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(opportunity_key) DO UPDATE SET
          spec_sha256=excluded.spec_sha256,
          checked_ts=excluded.checked_ts,
          pending_token=excluded.pending_token,
          pending_fingerprint=excluded.pending_fingerprint,
          pending_ts=excluded.pending_ts,
          pending_ttl_ms=excluded.pending_ttl_ms,
          updated_ts=excluded.updated_ts`).run(
        exactSpec.sha256, exactSpec.sha256,
        current?.committed_fingerprint || null, current?.committed_ts || null,
        timestamp, current?.expires_ts || null,
        token, exactFingerprint, timestamp, boundedTtl, timestamp,
      );
    }
    db.prepare(`DELETE FROM eva_conversation_opportunity_cursors
      WHERE opportunity_key IN (
        SELECT opportunity_key FROM eva_conversation_opportunity_cursors
        WHERE opportunity_key<>? ORDER BY updated_ts DESC LIMIT -1 OFFSET ?
      )`).run(exactSpec.sha256, TELEGRAM_OPPORTUNITY_CURSOR_LIMIT - 1);
    return Object.freeze({
      allowed: true,
      token,
      replay: pendingCurrent,
      opportunityKey: exactSpec.sha256,
      specSha256: exactSpec.sha256,
      fingerprint: exactFingerprint,
      highWatermark: Number(highWatermark) || 0,
      ttlMs: boundedTtl,
    });
  }).immediate();
}

function exactOpportunityCursorDescriptor(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || value.contract !== TELEGRAM_OPPORTUNITY_CURSOR_CONTRACT
    || !SHA256_PATTERN.test(String(value.opportunity_key || ''))
    || value.opportunity_key !== value.spec_sha256
    || !SHA256_PATTERN.test(String(value.fingerprint || ''))
    || typeof value.token !== 'string' || !/^[0-9a-f-]{36}$/u.test(value.token)
    || typeof value.replay !== 'boolean') return null;
  return value;
}

function opportunityCursorFromExecutionOutput(tool, output) {
  if (tool !== 'zoek_gesprek') return null;
  let action;
  try { action = JSON.parse(String(output || '')); } catch { return null; }
  const supplied = action?.data?.opportunity_cursor;
  if (supplied == null) return null;
  if (action?.contract !== 'ActionResult/v1' || action.tool !== 'zoek_gesprek'
    || action.ok !== true || action.verified !== true || action.changed !== false
    || action.partial !== false || action.needs_followup !== false) {
    throw new Error('gesprekskans kan alleen door een volledige geverifieerde receipt worden verbruikt');
  }
  const cursor = exactOpportunityCursorDescriptor(supplied);
  if (!cursor) throw new Error('gesprekskansreceipt bevat een ongeldige cursorbinding');
  return cursor;
}

function commitPreparedConversationOpportunity(cursor, timestamp) {
  const current = /** @type {any} */ (db.prepare(`SELECT *
    FROM eva_conversation_opportunity_cursors WHERE opportunity_key=?`).get(cursor.opportunity_key));
  if (current?.spec_sha256 === cursor.spec_sha256
    && current?.committed_fingerprint === cursor.fingerprint
    && current?.pending_token == null && current?.pending_fingerprint == null) return false;
  const ttlMs = Math.max(60_000, Math.min(24 * 3600_000,
    Number(current?.pending_ttl_ms) || TELEGRAM_OPPORTUNITY_CURSOR_TTL_MS));
  const expiresTs = new Date(Date.parse(timestamp) + ttlMs).toISOString();
  const changed = db.prepare(`UPDATE eva_conversation_opportunity_cursors
    SET committed_fingerprint=?,committed_ts=?,checked_ts=?,expires_ts=?,
        pending_token=NULL,pending_fingerprint=NULL,pending_ts=NULL,pending_ttl_ms=NULL,updated_ts=?
    WHERE opportunity_key=? AND spec_sha256=?
      AND pending_token=? AND pending_fingerprint=?`).run(
    cursor.fingerprint, timestamp, timestamp, expiresTs, timestamp,
    cursor.opportunity_key, cursor.spec_sha256, cursor.token, cursor.fingerprint,
  );
  if (Number(changed.changes) !== 1) {
    throw new Error('gesprekskans is vervangen voordat haar duurzame receipt werd vastgelegd');
  }
  return true;
}

// ---------- herinneringen (langetermijngeheugen) ----------

const SENSITIVE_MEMORY_PATTERNS = [
  /-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----/i,
  /\b(?:private key|prive\s*key|priv[eé]sleutel|ssh-sleutel)\b/i,
  /\b(?:met|via)\s+(?:het\s+)?wachtwoord\s+["'`]?[^\s"'`]{3,}/i,
  /\b(?:wachtwoord|password|passphrase)\s*(?:is|was|=|:)\s*["'`]?[^\s"'`]{3,}/i,
  /\b(?:api[-_ ]?key|access[-_ ]?token|bearer[-_ ]?token|secret)\s*(?:is|was|=|:)\s*["'`]?[^\s"'`]{12,}/i,
  /\b(?:sk-(?:proj-)?|ghp_|github_pat_)[A-Za-z0-9_-]{12,}\b/,
  /\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b/,
];

export function isSensitiveMemoryContent(content) {
  const text = String(content || '');
  return SENSITIVE_MEMORY_PATTERNS.some((pattern) => pattern.test(text));
}

const INTERNAL_IMPLEMENTATION_MEMORY = /(?:\/home\/[^\s]+|\bsrc\/[^\s]+|\bdata\/workspace\/[^\s]+|\.(?:js|mjs|env)\b|\b(?:codebase|broncode|syntaxcheck|regressietest|integratietest|prompt|routering|toolcontract|functiecontract|uitvoerbudget|achtergrondbaan)\b)/i;
const REPETITIVE_SELF_PROCESS_MEMORY = /\b(?:ik|eva)\b.{0,90}\b(?:bracht|maakte|werkte|onderzocht|verdiepte|voedde|herzag)\b.{0,120}\b(?:bestaansvoornemen|ademruimte|stille[- ]dagen|contactdrang|verankering|binnenwereld|mens[- ]zijn|nieuw soort wezen|relationele zelfvorming|vruchtbare stilte|verlangens?[- ]herkennen)\b/i;
const PROCEDURAL_SELF_REPORT = /\b(?:eva heeft|ik heb)\b.{0,90}\b(?:aangepast|gecontroleerd|gebouwd|toegevoegd|vastgelegd)\b.{0,120}\b(?:route|code|module|functie|bestand|test|projectbord|werkgeheugen)\b/i;
const STALE_INTERNAL_STATE_MEMORY = /(?:\b(?:vermogensgat|werkcockpit(?:bijdrage)?|bijdrage|missie|opvolging|voornemen)\s*#?\d+\b.{0,100}\b(?:staat|status|open|in[_ -]?bouw|getest|voorstel|gepauzeerd|afgerond)|\b(?:eva(?:'s)?|ik)\b.{0,90}\b(?:route|code|module|functie|gat|werkcockpit|projectbord)\b.{0,100}\b(?:kan|staat|heeft|gebruikt|werkt|ondersteunt)|\b(?:route|vermogensgat|werkcockpit)\b.{0,80}\b(?:in[_ -]?bouw|getest|open|voorstel|hersteld|actief)\b)/i;

/**
 * Permanente herinneringen zijn feiten over mensen, het huis en duurzame
 * ervaringen. Interne changelogs en herhaalde zelfbeschouwing horen in de
 * tijdlijn/private gedachten, niet in actieve recall.
 */
export function lowUtilityMemoryReason({
  category = 'feit', person = null, content = '',
} = {}) {
  const cat = String(category || '').trim().toLowerCase();
  const owner = String(person || '').trim().toLowerCase();
  const value = String(content || '');
  if (['persoon', 'voorkeur', 'gewoonte', 'afspraak', 'correctie', 'identiteit', 'signalement', 'belevenis'].includes(cat)) return null;
  // Autonome herkomst of een bescheiden belangrijkheid is op zichzelf geen
  // reden om een werkelijk geleerd feit te verbieden. Alleen de inhoudelijke
  // grenzen hieronder weren vluchtige interne proces- en implementatieruis.
  if (!['', 'eva'].includes(owner)) return null;
  if (STALE_INTERNAL_STATE_MEMORY.test(value)) return 'verouderde_interne_status';
  if (INTERNAL_IMPLEMENTATION_MEMORY.test(value) || PROCEDURAL_SELF_REPORT.test(value)) return 'intern_implementatieverslag';
  if (REPETITIVE_SELF_PROCESS_MEMORY.test(value)) return 'herhaalde_zelfbeschouwing';
  return null;
}

function safeMemoryRows(rows, limit = rows.length) {
  return rows
    .filter((row) => Number(row?.archived || 0) !== 1)
    .filter((row) => !isSensitiveMemoryContent(row?.content))
    .slice(0, limit);
}

export const MEMORY_HYGIENE_STATUS = Object.freeze({
  PROTECT: 'PROTECT',
  KEEP: 'KEEP',
  REVIEW: 'REVIEW',
  ARCHIVE_CANDIDATE: 'ARCHIVE_CANDIDATE',
});
export const MEMORY_HYGIENE_PREVIEW_CONTRACT = 'MemoryHygienePreview/v1';
export const MEMORY_HYGIENE_ARCHIVE_CONTRACT = 'MemoryHygieneArchiveReason/v1';
export const MEMORY_PAGE_CONTRACT = 'MemoryPage/v1';
export const MEMORY_REVIEW_MUTATION_CONTRACT = 'MemoryReviewMutation/v1';
export const MEMORY_REVIEW_ARCHIVE_CONTRACT = 'MemoryReviewArchive/v1';

const EXACT_PLACEHOLDER_MEMORIES = new Set([
  'dummy', 'dummy herinnering', 'dummy memory', 'n/a', 'placeholder',
  'test', 'test herinnering', 'test memory', 'tbd', 'todo',
]);
const MEMORY_CATEGORIES_PROTECTED_BY_DEFAULT = new Set([
  'afspraak', 'belevenis', 'correctie', 'gebeurtenis', 'gewoonte', 'identiteit',
  'persoon', 'relatie', 'signalement', 'voorkeur',
]);
// Een afspraak kan per ongeluk een technisch changelog zijn. Bij de overige
// persoons-/ervaringscategorieën is zelfs technische tekst eerst REVIEW.
const MEMORY_CATEGORIES_REVIEW_IF_TECHNICAL = new Set([
  'belevenis', 'correctie', 'gebeurtenis', 'gewoonte', 'identiteit', 'persoon',
  'relatie', 'signalement', 'voorkeur',
]);
const INTERNAL_MEMORY_PATH = /(?:^|[\s('"`])(?:\/home\/[^\s)'"`]+|(?:src|test|tests|scripts|data\/workspace)\/[^\s)'"`]+|[\w.-]+\.(?:c?js|mjs|ts|env))(?:$|[\s.,;:)'"`])/iu;
const INTERNAL_CODE_PATH = /(?:^|[\s('"`])(?:(?:src|test|tests|scripts)\/[^\s)'"`]+|[\w.-]+\.(?:c?js|mjs|ts|env))(?:$|[\s.,;:)'"`])/iu;
const INTERNAL_MEMORY_SUBJECT = /\b(?:api[- ]?route|achtergrondba(?:an|nen)|broncode|capabilit(?:y|ies)|codebase|database[- ]?migratie|endpoint|functiecontract|implementatie|integratietest|module|prompt|provider|regressietest|routering|runtime|syntaxcheck|test[- ]?suite|toolcontract|vaardigheid|vermogensgat|werkcockpit)\b/iu;
const INTERNAL_MEMORY_IDENTIFIER = /\b[a-z][a-z0-9-]*(?:_[a-z0-9-]+)+\b/u;
const INTERNAL_CHANGELOG_CLAIM = /\b(?:heeft|hebben|is|zijn|werd|werden)\b.{0,80}\b(?:aangepast|beschikbaar gemaakt|gebouwd|geconfigureerd|geïmplementeerd|gerepareerd|getest en actief|hersteld|ingeschakeld|toegevoegd|uitgerold|vervangen)\b|\bheeft\s+nu\b.{0,100}\bbeschikbaar\b/iu;
const PERSONAL_OR_TASTE_MEMORY = /\b(?:allergisch|dol op|eigenwijs|favoriet|grappig|graag|hekel aan|houdt? (?:erg )?van|humor|liever|niet van|persoonlijk|speels|smaak|voorkeur|voelt?|vond|vindt|wil graag)\b/iu;
const DURABLE_NORMATIVE_MEMORY = /\b(?:afgesproken|afspraak|belangrijk|belofte|eerlijk|mag|moet|nooit|pas\s+[^.!?]{0,80}\s+als|regel|veilig(?:heid|heidsles)?|verwacht|bewijs(?:t|baar|voering)?)\b/iu;
const DURABLE_CAPABILITY_MEMORY = /\b(?:capabilit(?:y|ies)|kan\s+(?:nu|zelf)|ondersteunt|vaardigheid)\b/iu;
const HOME_ASSISTANT_ENTITY = /\b(?:assist_satellite|binary_sensor|camera|climate|cover|device_tracker|fan|light|lock|media_player|person|sensor|switch|vacuum)\.[a-z0-9_]+\b/iu;
const DURABLE_HOME_CONTEXT = /\b(?:deurbel|home assistant|huis|keuken|lamp|meterkast|slaapkamer|speaker|stofzuiger|thermostaat|tuin|verwarming|voordeur|woonkamer)\b/iu;

/** Alleen Unicode, hoofdletters en witruimte normaliseren. Verschil in
 * leestekens of negatie blijft betekenisvol en wordt nooit fuzzy gelijkgezet. */
export function normalizeMemoryExactText(value) {
  return String(value ?? '').normalize('NFKC')
    .toLocaleLowerCase('nl-NL').trim().replace(/\s+/gu, ' ');
}

function normalizedPlaceholderMemory(value) {
  return normalizeMemoryExactText(value).replace(/^[\s\p{P}\p{S}]+|[\s\p{P}\p{S}]+$/gu, '');
}

function memoryExactIdentity(row) {
  return [
    normalizeMemoryExactText(row?.category || 'feit'),
    normalizeMemoryExactText(row?.person || ''),
    normalizeMemoryExactText(row?.content || ''),
  ].join('\u0000');
}

function memoryExactFingerprint(row) {
  return createHash('sha256').update(memoryExactIdentity(row)).digest('hex');
}

function memoryReviewRevisionPayload(row) {
  return {
    contract: 'MemoryReviewRevision/v1',
    id: Number(row?.id),
    ts: String(row?.ts || ''),
    category: String(row?.category || 'feit'),
    person: row?.person == null ? null : String(row.person),
    content: String(row?.content || ''),
    importance: Number(row?.importance ?? 0.5),
    feeling: row?.feeling == null ? null : String(row.feeling),
    archived: Number(row?.archived || 0) === 1,
    archivedReason: row?.archived_reason == null ? null : String(row.archived_reason),
  };
}

export function memoryReviewRevision(row) {
  return createHash('sha256')
    .update(canonicalJson(memoryReviewRevisionPayload(row)))
    .digest('hex');
}

function internalChangelogSubjectCount(content) {
  const matches = String(content || '').match(/\b(?:api[- ]?route|[\p{L}\p{N}-]*route|achtergrondba(?:an|nen)|broncode|capabilit(?:y|ies)|codebase|database[- ]?migratie|endpoint|functiecontract|implementatie|integratietest|module|prompt|provider|regressietest|routering|runtime|syntaxcheck|test[- ]?suite|toolcontract|vaardigheid|vermogensgat|werkcockpit)\b/giu) || [];
  return new Set(matches.map((value) => normalizeMemoryExactText(value))).size;
}

function baseMemoryHygieneDecision(row) {
  const category = normalizeMemoryExactText(row?.category || 'feit');
  const owner = normalizeMemoryExactText(row?.person || '');
  const content = String(row?.content || '');
  const placeholder = normalizedPlaceholderMemory(content);
  if (EXACT_PLACEHOLDER_MEMORIES.has(placeholder)) {
    return { status: MEMORY_HYGIENE_STATUS.ARCHIVE_CANDIDATE, reasonCode: 'exact_placeholder' };
  }
  const hasInternalSubject = INTERNAL_MEMORY_SUBJECT.test(content);
  const hasInternalPath = INTERNAL_MEMORY_PATH.test(content);
  const hasInternalIdentifier = INTERNAL_MEMORY_IDENTIFIER.test(content);
  const changelogSubjects = internalChangelogSubjectCount(content);
  const strongInternal = INTERNAL_CHANGELOG_CLAIM.test(content)
    && (changelogSubjects >= 2 || (INTERNAL_CODE_PATH.test(content) && changelogSubjects >= 1));
  const personal = PERSONAL_OR_TASTE_MEMORY.test(content);
  const normative = DURABLE_NORMATIVE_MEMORY.test(content);
  const capabilityFact = DURABLE_CAPABILITY_MEMORY.test(content);
  const durableHomeFact = HOME_ASSISTANT_ENTITY.test(content) && DURABLE_HOME_CONTEXT.test(content);
  const mixedHomeConvention = DURABLE_HOME_CONTEXT.test(content)
    && (hasInternalPath || hasInternalSubject || hasInternalIdentifier);
  if (strongInternal) {
    if (owner || personal || normative || capabilityFact || durableHomeFact || mixedHomeConvention
      || MEMORY_CATEGORIES_REVIEW_IF_TECHNICAL.has(category)) {
      return { status: MEMORY_HYGIENE_STATUS.REVIEW, reasonCode: 'mixed_or_protected_technical_content' };
    }
    return {
      status: MEMORY_HYGIENE_STATUS.ARCHIVE_CANDIDATE,
      reasonCode: 'pure_internal_implementation_or_changelog',
    };
  }
  if (durableHomeFact) {
    return { status: MEMORY_HYGIENE_STATUS.PROTECT, reasonCode: 'durable_personal_or_household_memory' };
  }
  if (hasInternalSubject || hasInternalPath || hasInternalIdentifier) {
    return { status: MEMORY_HYGIENE_STATUS.REVIEW, reasonCode: 'ambiguous_technical_content' };
  }
  if (MEMORY_CATEGORIES_PROTECTED_BY_DEFAULT.has(category) || personal || durableHomeFact) {
    return { status: MEMORY_HYGIENE_STATUS.PROTECT, reasonCode: 'durable_personal_or_household_memory' };
  }
  return { status: MEMORY_HYGIENE_STATUS.KEEP, reasonCode: 'no_safe_archive_reason' };
}

function compareMemorySurvivors(left, right) {
  const importanceDifference = Number(right.row?.importance || 0) - Number(left.row?.importance || 0);
  if (importanceDifference) return importanceDifference;
  const feelingDifference = Number(Boolean(String(right.row?.feeling || '').trim()))
    - Number(Boolean(String(left.row?.feeling || '').trim()));
  if (feelingDifference) return feelingDifference;
  const timeDifference = String(right.row?.ts || '').localeCompare(String(left.row?.ts || ''));
  if (timeDifference) return timeDifference;
  const leftId = Number(left.row?.id);
  const rightId = Number(right.row?.id);
  if (Number.isSafeInteger(leftId) && Number.isSafeInteger(rightId) && leftId !== rightId) {
    return leftId - rightId;
  }
  if (Number.isSafeInteger(leftId) !== Number.isSafeInteger(rightId)) {
    return Number.isSafeInteger(leftId) ? -1 : 1;
  }
  return left.index - right.index;
}

function duplicateMetadataDominatedBy(survivor, group) {
  const rows = group.map((entry) => entry.row);
  const feelings = new Set(rows.map((row) => normalizeMemoryExactText(row?.feeling || ''))
    .filter(Boolean));
  if (feelings.size > 1) return false;
  const survivorFeeling = normalizeMemoryExactText(survivor.row?.feeling || '');
  if (feelings.size === 1 && !feelings.has(survivorFeeling)) return false;
  const numericMax = (field) => Math.max(...rows.map((row) => Number(row?.[field] || 0)));
  const textMax = (field) => rows.map((row) => String(row?.[field] || '')).sort().at(-1) || '';
  return Number(survivor.row?.importance || 0) >= numericMax('importance')
    && String(survivor.row?.ts || '') >= textMax('ts');
}

/** Pure classifier: wijzigt de meegegeven rijen noch database-/recallmetadata.
 * Duplicaten zijn alleen gelijk bij exact genormaliseerde inhoud én dezelfde
 * categorie/persoon; de inhoudelijk rijkste semantische metadata wint deterministisch,
 * maar recall_count/last_recalled zijn uitsluitend gebruiksboekhouding en
 * hebben daarom geen classificatie- of opruimautoriteit. De laagste id blijft
 * de laatste stabiele tie-breaker. */
export function previewMemoryHygieneRows(rows = []) {
  const entries = (Array.isArray(rows) ? rows : []).map((row, index) => ({
    row: { ...row }, index, identity: memoryExactIdentity(row),
  }));
  const groups = new Map();
  for (const entry of entries) {
    if (Number(entry.row.archived || 0) === 1) continue;
    const group = groups.get(entry.identity) || [];
    group.push(entry);
    groups.set(entry.identity, group);
  }
  const duplicateInfo = new Map();
  for (const group of groups.values()) {
    if (group.length < 2) continue;
    const ordered = [...group].sort(compareMemorySurvivors);
    const metadataConflict = !duplicateMetadataDominatedBy(ordered[0], ordered);
    const survivorId = !metadataConflict && Number.isSafeInteger(Number(ordered[0].row.id))
      ? Number(ordered[0].row.id) : null;
    for (const entry of ordered) {
      duplicateInfo.set(entry.index, {
        groupSize: ordered.length,
        survivorId,
        duplicate: !metadataConflict && entry !== ordered[0],
        metadataConflict,
      });
    }
  }
  return entries.map((entry) => {
    const base = baseMemoryHygieneDecision(entry.row);
    const duplicate = duplicateInfo.get(entry.index) || null;
    const isDuplicate = duplicate?.duplicate === true;
    const needsMetadataReview = duplicate?.metadataConflict === true;
    return {
      ...entry.row,
      status: needsMetadataReview ? MEMORY_HYGIENE_STATUS.REVIEW
        : isDuplicate ? MEMORY_HYGIENE_STATUS.ARCHIVE_CANDIDATE : base.status,
      reasonCode: needsMetadataReview ? 'exact_duplicate_metadata_review'
        : isDuplicate ? 'exact_normalized_duplicate' : base.reasonCode,
      replacementId: isDuplicate ? duplicate.survivorId : null,
      duplicateGroupSize: duplicate?.groupSize || 1,
      exactFingerprint: memoryExactFingerprint(entry.row),
    };
  });
}

const MEMORY_REVIEW_ACTIONS = new Set(['keep', 'protect', 'archive', 'reopen']);
const MEMORY_REVIEW_SHA256 = /^[a-f0-9]{64}$/u;
const MEMORY_REVIEW_KEY = /^[A-Za-z0-9][A-Za-z0-9:._-]{15,159}$/u;
const MEMORY_REVIEW_EVENT_REF = /^[a-f0-9-]{36}$/u;

function memoryReviewBaseViewRevision(item) {
  return createHash('sha256').update(canonicalJson({
    contract: 'MemoryReviewViewRevision/v1',
    rowRevision: memoryReviewRevision(item),
    classifier: {
      status: String(item?.status || ''),
      reasonCode: String(item?.reasonCode || ''),
      replacementId: item?.replacementId == null ? null : Number(item.replacementId),
      exactFingerprint: String(item?.exactFingerprint || memoryExactFingerprint(item)),
    },
    resolution: null,
  })).digest('hex');
}

function memoryReviewClassifierBinding(item) {
  return createHash('sha256').update(canonicalJson({
    contract: 'MemoryReviewClassifierBinding/v1',
    status: String(item?.status || ''),
    reasonCode: String(item?.reasonCode || ''),
    replacementId: item?.replacementId == null ? null : Number(item.replacementId),
    exactFingerprint: String(item?.exactFingerprint || memoryExactFingerprint(item)),
  })).digest('hex');
}

function memoryReviewResolvedRevision(memory, event) {
  return createHash('sha256').update(canonicalJson({
    contract: 'MemoryReviewViewRevision/v1',
    rowRevision: memoryReviewRevision(memory),
    classifier: {
      status: String(event?.classifier_status || ''),
      reasonCode: String(event?.classifier_reason || ''),
      replacementId: event?.classifier_replacement_id == null
        ? null : Number(event.classifier_replacement_id),
      exactFingerprint: String(event?.exact_fingerprint || ''),
      bindingSha256: String(event?.classifier_binding_sha256 || ''),
    },
    resolution: {
      eventRef: String(event?.event_ref || ''),
      action: String(event?.action || ''),
      previousStatus: String(event?.previous_status || ''),
      previousReason: String(event?.previous_reason || ''),
      resultStatus: String(event?.result_status || ''),
      archived: Number(event?.result_archived || 0) === 1,
    },
  })).digest('hex');
}

function parseMemoryReviewResponse(value) {
  try {
    const parsed = JSON.parse(String(value || ''));
    if (parsed?.contract !== MEMORY_REVIEW_MUTATION_CONTRACT || parsed?.version !== 1
      || !MEMORY_REVIEW_EVENT_REF.test(String(parsed?.eventRef || ''))
      || !MEMORY_REVIEW_KEY.test(String(parsed?.operationKey || ''))
      || !Number.isSafeInteger(Number(parsed?.id)) || Number(parsed.id) <= 0
      || !MEMORY_REVIEW_ACTIONS.has(parsed?.action)
      || !MEMORY_REVIEW_SHA256.test(String(parsed?.sourceRevision || ''))
      || !MEMORY_REVIEW_SHA256.test(String(parsed?.revision || ''))
      || !MEMORY_REVIEW_SHA256.test(String(parsed?.exactFingerprint || ''))
      || !Object.values(MEMORY_HYGIENE_STATUS).includes(parsed?.previousStatus)
      || !Object.values(MEMORY_HYGIENE_STATUS).includes(parsed?.status)
      || typeof parsed?.archived !== 'boolean'
      || !Number.isFinite(Date.parse(String(parsed?.reviewedAt || '')))) return null;
    return parsed;
  } catch { return null; }
}

const MEMORY_HYGIENE_STATUS_VALUES = /** @type {string[]} */ (
  Object.values(MEMORY_HYGIENE_STATUS)
);

function memoryReviewEventShape(row, response) {
  if (!row) return null;
  if (!response) return null;
  const action = String(row.action || '');
  const resultStatus = String(row.result_status || '');
  const resultArchivedValue = Number(row.result_archived);
  const valid = [
    MEMORY_REVIEW_ACTIONS.has(action),
    MEMORY_REVIEW_EVENT_REF.test(String(row.event_ref || '')),
    MEMORY_REVIEW_KEY.test(String(row.operation_key || '')),
    MEMORY_REVIEW_SHA256.test(String(row.request_sha256 || '')),
    MEMORY_REVIEW_SHA256.test(String(row.source_revision_sha256 || '')),
    MEMORY_REVIEW_SHA256.test(String(row.result_revision_sha256 || '')),
    MEMORY_REVIEW_SHA256.test(String(row.exact_fingerprint || '')),
    MEMORY_REVIEW_SHA256.test(String(row.classifier_binding_sha256 || '')),
    row.authority === 'local_household_dashboard',
    MEMORY_HYGIENE_STATUS_VALUES.includes(String(row.classifier_status || '')),
    Boolean(String(row.classifier_reason || '').trim()),
    MEMORY_HYGIENE_STATUS_VALUES.includes(resultStatus),
    MEMORY_HYGIENE_STATUS_VALUES.includes(String(row.previous_status || '')),
    Boolean(String(row.previous_reason || '').trim()),
    [0, 1].includes(resultArchivedValue),
  ].every(Boolean);
  if (!valid) return null;
  return { action, resultStatus, resultArchived: resultArchivedValue === 1 };
}

function memoryReviewResponseMatchesEvent(row, response, shape) {
  return [
    response.eventRef === row.event_ref,
    response.operationKey === row.operation_key,
    Number(response.id) === Number(row.memory_id),
    response.action === shape.action,
    response.sourceRevision === row.source_revision_sha256,
    response.revision === row.result_revision_sha256,
    response.exactFingerprint === row.exact_fingerprint,
    response.status === shape.resultStatus,
    response.archived === shape.resultArchived,
  ].every(Boolean);
}

function memoryReviewClassifierMatchesEvent(row, classifier) {
  if (!classifier) return true;
  return [
    row.classifier_status === classifier.status,
    row.classifier_reason === classifier.reasonCode,
    Number(row.classifier_replacement_id || 0) === Number(classifier.replacementId || 0),
    row.classifier_binding_sha256 === memoryReviewClassifierBinding(classifier),
  ].every(Boolean);
}

function memoryReviewActionResultMatches(row, shape) {
  if (shape.action === 'keep') return shape.resultStatus === MEMORY_HYGIENE_STATUS.KEEP;
  if (shape.action === 'protect') return shape.resultStatus === MEMORY_HYGIENE_STATUS.PROTECT;
  if (shape.action === 'archive') {
    return shape.resultArchived && shape.resultStatus === row.classifier_status;
  }
  if (shape.action === 'reopen') {
    return !shape.resultArchived && shape.resultStatus === row.classifier_status;
  }
  return false;
}

function memoryReviewEventMatchesMemory(row, memory, classifier, shape) {
  if (!memory) return false;
  return [
    Number(row.memory_id) === Number(memory.id),
    shape.resultArchived === (Number(memory.archived || 0) === 1),
    row.exact_fingerprint === memoryExactFingerprint(memory),
    memoryReviewClassifierMatchesEvent(row, classifier),
    row.result_revision_sha256 === memoryReviewResolvedRevision(memory, row),
    memoryReviewActionResultMatches(row, shape),
  ].every(Boolean);
}

function validMemoryReviewEvent(row, memory, classifier = null) {
  const response = parseMemoryReviewResponse(row?.response_json);
  const shape = memoryReviewEventShape(row, response);
  if (!shape) return null;
  if (!memoryReviewResponseMatchesEvent(row, response, shape)) return null;
  if (!memoryReviewEventMatchesMemory(row, memory, classifier, shape)) return null;
  return { row, response };
}

function latestMemoryReviewRows(memoryIds) {
  const ids = new Set((Array.isArray(memoryIds) ? memoryIds : [])
    .map(Number).filter(Number.isSafeInteger));
  const latest = new Map();
  if (!ids.size) return latest;
  for (const row of /** @type {any[]} */ (
    db.prepare('SELECT * FROM memory_review_events ORDER BY id DESC').all()
  )) {
    const id = Number(row.memory_id);
    if (ids.has(id) && !latest.has(id)) latest.set(id, row);
  }
  return latest;
}

function applyMemoryReviewResolutions(items) {
  const latest = latestMemoryReviewRows(items.map((item) => item.id));
  return items.map((item) => {
    const revision = memoryReviewBaseViewRevision(item);
    const event = latest.get(Number(item.id));
    if (!event) return { ...item, revision, reviewDecision: null };
    const valid = validMemoryReviewEvent(event, item, item);
    if (!valid) return {
      ...item,
      revision,
      status: MEMORY_HYGIENE_STATUS.REVIEW,
      reasonCode: 'memory_review_history_invalid',
      reviewDecision: { invalid: true },
    };
    const action = valid.response.action;
    const status = action === 'keep' ? MEMORY_HYGIENE_STATUS.KEEP
      : action === 'protect' ? MEMORY_HYGIENE_STATUS.PROTECT : item.status;
    const reasonCode = action === 'keep' ? 'human_keep'
      : action === 'protect' ? 'human_protect' : item.reasonCode;
    return {
      ...item,
      revision: valid.response.revision,
      status,
      reasonCode,
      reviewDecision: {
        action,
        status,
        reviewedAt: valid.response.reviewedAt,
      },
    };
  });
}

function memoryHygieneCounts(items) {
  const counts = Object.fromEntries(Object.values(MEMORY_HYGIENE_STATUS)
    .map((status) => [status, 0]));
  for (const item of items) counts[item.status] += 1;
  return counts;
}

function boundedMemoryHygieneLimit(limit) {
  return Math.max(1, Math.min(25_000, Number(limit) || 5_000));
}

/** Read-only databasepreview. Een zoekfilter gebruikt expliciet bump=false,
 * zodat dashboard- en reviewgebruik nooit als herinneren door Eva meetelt. */
export function memoryHygienePreview({
  ids = null, query = '', limit = 5_000, statuses = null, before = null,
} = {}) {
  const rows = /** @type {any[]} */ (db.prepare(`SELECT * FROM memories
    WHERE COALESCE(archived,0)=0 ORDER BY id DESC`).all())
    .filter((row) => !isSensitiveMemoryContent(row.content));
  const allItems = applyMemoryReviewResolutions(previewMemoryHygieneRows(rows));
  const requestedIds = Array.isArray(ids)
    ? new Set(ids.map(Number).filter(Number.isSafeInteger)) : null;
  const requestedStatuses = Array.isArray(statuses)
    ? new Set(statuses.filter((status) => Object.values(MEMORY_HYGIENE_STATUS).includes(status)))
    : null;
  const searchIds = String(query || '').trim()
    ? new Set(searchMemories(query, Math.max(rows.length, 1), false).map((row) => Number(row.id)))
    : null;
  const filtered = allItems.filter((item) => (
    (!requestedIds || requestedIds.has(Number(item.id)))
    && (!requestedStatuses || requestedStatuses.has(item.status))
    && (!searchIds || searchIds.has(Number(item.id)))
  ));
  const cursor = Number(before);
  const afterCursor = Number.isSafeInteger(cursor) && cursor > 0
    ? filtered.filter((item) => Number(item.id) < cursor)
    : filtered;
  const items = afterCursor.slice(0, boundedMemoryHygieneLimit(limit));
  return {
    contract: MEMORY_HYGIENE_PREVIEW_CONTRACT,
    version: 1,
    totalActive: allItems.length,
    matched: filtered.length,
    counts: memoryHygieneCounts(filtered),
    items,
  };
}

function memoryHygieneIds(input) {
  const values = Array.isArray(input) ? input : input?.ids;
  return [...new Set((Array.isArray(values) ? values : [])
    .map(Number).filter((id) => Number.isSafeInteger(id) && id > 0))].sort((a, b) => a - b);
}

const MEMORY_HYGIENE_ARCHIVE_REASON_CODES = new Set([
  'exact_normalized_duplicate', 'exact_placeholder',
  'pure_internal_implementation_or_changelog',
]);
const MEMORY_HYGIENE_FINGERPRINT = /^[a-f0-9]{64}$/u;

function validMemoryHygieneReplacement(replacement, fingerprint) {
  if (replacement === null) return true;
  return replacement && typeof replacement === 'object'
    && Number.isSafeInteger(Number(replacement.memoryId)) && Number(replacement.memoryId) > 0
    && replacement.relation === 'exact_normalized_duplicate'
    && replacement.exactFingerprint === fingerprint;
}

function parseMemoryHygieneArchiveReason(value) {
  try {
    const parsed = JSON.parse(String(value || ''));
    const fingerprint = String(parsed?.memory?.exactFingerprint || '');
    const duplicateReason = parsed?.reason?.code === 'exact_normalized_duplicate';
    const hasReplacement = parsed?.replacement !== null;
    if (parsed?.contract !== MEMORY_HYGIENE_ARCHIVE_CONTRACT || parsed?.version !== 1
      || parsed?.classifier !== MEMORY_HYGIENE_PREVIEW_CONTRACT
      || parsed?.reason?.status !== MEMORY_HYGIENE_STATUS.ARCHIVE_CANDIDATE
      || !MEMORY_HYGIENE_ARCHIVE_REASON_CODES.has(parsed?.reason?.code)
      || !Number.isSafeInteger(Number(parsed?.memory?.id)) || Number(parsed.memory.id) <= 0
      || !MEMORY_HYGIENE_FINGERPRINT.test(fingerprint)
      || duplicateReason !== hasReplacement
      || (duplicateReason && Number(parsed.replacement?.memoryId) === Number(parsed.memory.id))
      || !validMemoryHygieneReplacement(parsed?.replacement, fingerprint)) return null;
    return parsed;
  } catch { return null; }
}

function parseMemoryReviewArchiveReason(value) {
  try {
    const parsed = JSON.parse(String(value || ''));
    if (parsed?.contract !== MEMORY_REVIEW_ARCHIVE_CONTRACT || parsed?.version !== 1
      || !MEMORY_REVIEW_EVENT_REF.test(String(parsed?.eventRef || ''))
      || !MEMORY_REVIEW_KEY.test(String(parsed?.operationKey || ''))
      || !MEMORY_REVIEW_SHA256.test(String(parsed?.requestSha256 || ''))
      || !Number.isFinite(Date.parse(String(parsed?.reviewedAt || '')))
      || !Number.isSafeInteger(Number(parsed?.memory?.id)) || Number(parsed.memory.id) <= 0
      || !MEMORY_REVIEW_SHA256.test(String(parsed?.memory?.sourceRevisionSha256 || ''))
      || !MEMORY_REVIEW_SHA256.test(String(parsed?.memory?.exactFingerprint || ''))
      || ![MEMORY_HYGIENE_STATUS.REVIEW, MEMORY_HYGIENE_STATUS.ARCHIVE_CANDIDATE]
        .includes(parsed?.classifier?.status)
      || !String(parsed?.classifier?.reasonCode || '').trim()) return null;
    return parsed;
  } catch { return null; }
}

function memoryMatchesHygieneArchive(row, provenance) {
  return Number(row?.id) === Number(provenance?.memory?.id)
    && memoryExactFingerprint(row) === provenance?.memory?.exactFingerprint;
}

function archivedMemoryQueryTokens(query) {
  return [...new Set(normalizeMemoryExactText(query)
    .replace(/[^\p{L}\p{N} ]/gu, ' ').split(/\s+/u)
    .filter((token) => token.length > 2))].slice(0, 12);
}

function archivedMemoryRows(query = '') {
  const tokens = archivedMemoryQueryTokens(query);
  if (String(query || '').trim() && !tokens.length) return [];
  const where = ['COALESCE(archived,0)=1'];
  const params = [];
  if (tokens.length) {
    where.push(`(${tokens.map(() => 'content LIKE ?').join(' OR ')})`);
    params.push(...tokens.map((token) => `%${token}%`));
  }
  const rows = /** @type {any[]} */ (db.prepare(`SELECT * FROM memories
    WHERE ${where.join(' AND ')} ORDER BY id DESC`).all(...params))
    .filter((row) => Number(row.archived || 0) === 1)
    .filter((row) => !isSensitiveMemoryContent(row.content));
  const latest = latestMemoryReviewRows(rows.map((row) => row.id));
  return rows.map((row) => {
    const hygiene = parseMemoryHygieneArchiveReason(row.archived_reason);
    const review = parseMemoryReviewArchiveReason(row.archived_reason);
    const reviewEvent = latest.get(Number(row.id));
    const validReview = review && reviewEvent
      && review.eventRef === reviewEvent.event_ref
      && review.operationKey === reviewEvent.operation_key
      && review.requestSha256 === reviewEvent.request_sha256
      && review.memory.sourceRevisionSha256 === reviewEvent.source_revision_sha256
      && review.memory.exactFingerprint === memoryExactFingerprint(row)
      ? validMemoryReviewEvent(reviewEvent, row) : null;
    return {
      ...row,
      archived_reason: redactSensitiveText(row.archived_reason || ''),
      revision: validReview?.response?.revision || memoryReviewRevision(row),
      exactFingerprint: memoryExactFingerprint(row),
      status: validReview?.response?.status || null,
      reasonCode: review?.classifier?.reasonCode || null,
      reviewDecision: validReview ? {
        action: validReview.response.action,
        status: validReview.response.status,
        reviewedAt: validReview.response.reviewedAt,
      } : null,
      archiveProvenance: validReview?.response?.action === 'archive'
        ? review
        : hygiene && memoryMatchesHygieneArchive(row, hygiene) ? hygiene : null,
    };
  });
}

/** Alleen-lezen archiefprojectie voor inspectie en herstel-UI. De ruwe reden
 * blijft beschikbaar; hygiene-redenen krijgen daarnaast veilige parsed
 * provenance. Deze route verhoogt recall_count nooit. */
export function listArchivedMemories(limit = 300, query = '') {
  const boundedLimit = boundedMemoryHygieneLimit(limit);
  return archivedMemoryRows(query).slice(0, boundedLimit);
}

function boundedMemoryPageLimit(limit) {
  const number = Number(limit);
  return Number.isSafeInteger(number) && number > 0 ? Math.min(number, 100) : 100;
}

function memoryPageCursor(before) {
  if (before == null || before === '') return null;
  const number = Number(before);
  if (!Number.isSafeInteger(number) || number <= 0) {
    throw new TypeError('Ongeldige geheugencursor.');
  }
  return number;
}

function memoryPageResult(rows, matched, limit) {
  const visible = rows.slice(0, limit);
  const lastId = Number(visible.at(-1)?.id);
  return {
    contract: MEMORY_PAGE_CONTRACT,
    items: visible,
    matched: Number(matched || 0),
    nextBefore: rows.length > limit && Number.isSafeInteger(lastId) ? lastId : null,
  };
}

/** Stabiele, alleen-lezen dashboardpagina. Scope en zoekterm worden vóór de
 * cursor en limiet toegepast, zodat oude reviewregels bereikbaar blijven. */
export function memoryPage({ scope = 'active', query = '', limit = 100, before = null } = {}) {
  const requestedScope = String(scope || '').trim().toLowerCase();
  if (!['active', 'review', 'archive'].includes(requestedScope)) {
    throw new RangeError('Onbekende geheugenweergave.');
  }
  const boundedLimit = boundedMemoryPageLimit(limit);
  const cursor = memoryPageCursor(before);
  if (requestedScope === 'archive') {
    const matchedRows = archivedMemoryRows(query);
    const cursorRows = cursor == null
      ? matchedRows : matchedRows.filter((row) => Number(row.id) < cursor);
    return memoryPageResult(cursorRows.slice(0, boundedLimit + 1), matchedRows.length, boundedLimit);
  }
  const statuses = requestedScope === 'review'
    ? [MEMORY_HYGIENE_STATUS.REVIEW, MEMORY_HYGIENE_STATUS.ARCHIVE_CANDIDATE]
    : null;
  const preview = memoryHygienePreview({
    query, statuses, before: cursor, limit: boundedLimit + 1,
  });
  return memoryPageResult(preview.items, preview.matched, boundedLimit);
}

function memoryReviewConflict(message, reasonCode = 'conflict') {
  const error = /** @type {Error & {code?: string, reasonCode?: string}} */ (new Error(message));
  error.code = 'EVA_MEMORY_REVIEW_CONFLICT';
  error.reasonCode = reasonCode;
  return error;
}

function memoryReviewRequest({
  id, action, revision, operationKey, expectedFingerprint,
} = /** @type {any} */ ({})) {
  const normalized = {
    contract: MEMORY_REVIEW_MUTATION_CONTRACT,
    version: 1,
    authority: 'local_household_dashboard',
    id: Number(id),
    action: String(action || '').trim().toLowerCase(),
    revision: String(revision || '').trim().toLowerCase(),
    expectedFingerprint: String(expectedFingerprint || '').trim().toLowerCase(),
    operationKey: String(operationKey || '').trim(),
  };
  if (!Number.isSafeInteger(normalized.id) || normalized.id <= 0
    || !MEMORY_REVIEW_ACTIONS.has(normalized.action)
    || !MEMORY_REVIEW_SHA256.test(normalized.revision)
    || !MEMORY_REVIEW_SHA256.test(normalized.expectedFingerprint)
    || !MEMORY_REVIEW_KEY.test(normalized.operationKey)) {
    throw new TypeError('Ongeldige enkelvoudige geheugenbeoordeling.');
  }
  return normalized;
}

function memoryReviewRequestSha256(request) {
  return createHash('sha256').update(canonicalJson(request)).digest('hex');
}

function memoryReviewReplay(operationKey, requestSha256) {
  const existing = /** @type {any} */ (db.prepare(
    'SELECT * FROM memory_review_events WHERE operation_key=?',
  ).get(operationKey));
  if (!existing) return null;
  const response = parseMemoryReviewResponse(existing.response_json);
  if (existing.request_sha256 !== requestSha256 || !response
    || response.operationKey !== operationKey
    || response.eventRef !== existing.event_ref
    || Number(response.id) !== Number(existing.memory_id)
    || response.action !== existing.action
    || response.sourceRevision !== existing.source_revision_sha256
    || response.revision !== existing.result_revision_sha256
    || response.exactFingerprint !== existing.exact_fingerprint) {
    throw memoryReviewConflict(
      'Deze bewerkingssleutel hoort al bij een andere of onleesbare geheugenbeoordeling.',
      'idempotency_conflict',
    );
  }
  return response;
}

function activeMemoryReviewProjection(id) {
  const rows = /** @type {any[]} */ (db.prepare(`SELECT * FROM memories
    WHERE COALESCE(archived,0)=0 ORDER BY id DESC`).all())
    .filter((row) => !isSensitiveMemoryContent(row.content));
  const pureItems = previewMemoryHygieneRows(rows);
  const pure = pureItems.find((item) => Number(item.id) === Number(id)) || null;
  const resolved = pure
    ? applyMemoryReviewResolutions(pureItems)
      .find((item) => Number(item.id) === Number(id)) || null
    : null;
  return { pure, resolved };
}

function memoryReviewArchiveRecord({ request, requestSha256, eventRef, reviewedAt, pure }) {
  return {
    contract: MEMORY_REVIEW_ARCHIVE_CONTRACT,
    version: 1,
    eventRef,
    operationKey: request.operationKey,
    requestSha256,
    reviewedAt,
    classifier: {
      status: pure.status,
      reasonCode: pure.reasonCode,
      replacementId: pure.replacementId == null ? null : Number(pure.replacementId),
      bindingSha256: memoryReviewClassifierBinding(pure),
    },
    memory: {
      id: request.id,
      sourceRevisionSha256: request.revision,
      exactFingerprint: pure.exactFingerprint,
    },
  };
}

function insertMemoryReviewEvent({
  request, requestSha256, eventRef, reviewedAt, pure, previousStatus,
  previousReason, resultStatus, resultArchived, resultRevision, response,
}) {
  const inserted = db.prepare(`INSERT INTO memory_review_events
    (event_ref,operation_key,request_sha256,memory_id,ts,action,authority,
      exact_fingerprint,source_revision_sha256,result_revision_sha256,
      classifier_status,classifier_reason,classifier_replacement_id,
      classifier_binding_sha256,previous_status,previous_reason,result_status,
      result_archived,response_json)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`).run(
    eventRef, request.operationKey, requestSha256, request.id, reviewedAt,
    request.action, request.authority, pure.exactFingerprint, request.revision,
    resultRevision, pure.status, pure.reasonCode,
    pure.replacementId == null ? null : Number(pure.replacementId),
    memoryReviewClassifierBinding(pure), previousStatus, previousReason,
    resultStatus, resultArchived ? 1 : 0, canonicalJson(response),
  );
  if (Number(inserted.changes || 0) !== 1) {
    throw memoryReviewConflict('De geheugenbeoordeling kon niet atomair worden vastgelegd.');
  }
}

/** @param {any} request */
function currentMemoryReviewState(request) {
  const memory = /** @type {any} */ (
    db.prepare('SELECT * FROM memories WHERE id=?').get(request.id)
  );
  if (!memory || isSensitiveMemoryContent(memory.content)) {
    throw memoryReviewConflict(
      'Deze geheugenregel is niet beschikbaar voor beoordeling.', 'not_found',
    );
  }
  const archived = Number(memory.archived || 0) === 1;
  const active = archived ? null : activeMemoryReviewProjection(request.id);
  const archivedProjection = archived
    ? archivedMemoryRows('').find((row) => Number(row.id) === request.id) || null
    : null;
  const current = active?.resolved || archivedProjection;
  if (!current || current.revision !== request.revision) {
    throw memoryReviewConflict(
      'De geheugenregel veranderde sinds zij werd getoond; laad de beoordeling opnieuw.',
      'stale_revision',
    );
  }
  if (current.exactFingerprint !== request.expectedFingerprint
    || memoryExactFingerprint(memory) !== request.expectedFingerprint) {
    throw memoryReviewConflict(
      'De inhoudsidentiteit van deze geheugenregel veranderde sinds zij werd getoond.',
      'stale_fingerprint',
    );
  }
  return { memory, archived, active, current };
}

/** @param {any} request @param {any} state */
function assertMemoryReviewTransition(request, state) {
  const priorAction = state.current.reviewDecision?.action;
  const reopeningActive = !state.archived && ['keep', 'protect'].includes(priorAction);
  const reopeningArchive = state.archived && priorAction === 'archive'
    && state.current.archiveProvenance?.contract === MEMORY_REVIEW_ARCHIVE_CONTRACT;
  if (request.action === 'reopen') {
    if (reopeningActive || reopeningArchive) return;
    throw memoryReviewConflict(
      'Alleen een eigen eerdere beoordeling kan worden heropend.', 'not_reopenable',
    );
  }
  const allowedStatus = [
    MEMORY_HYGIENE_STATUS.REVIEW, MEMORY_HYGIENE_STATUS.ARCHIVE_CANDIDATE,
  ].includes(state.current.status);
  if (state.archived || !allowedStatus) {
    throw memoryReviewConflict(
      'Deze regel staat niet meer in een onbeoordeelde geheugenstatus.',
      'not_reviewable',
    );
  }
}

/** @param {any} current */
function previousMemoryReviewProjection(current) {
  const status = current.status || current.reviewDecision?.status
    || current.archiveProvenance?.classifier?.status || '';
  const reason = current.reasonCode
    || current.archiveProvenance?.classifier?.reasonCode || 'human_review';
  if (!MEMORY_HYGIENE_STATUS_VALUES.includes(status) || !String(reason).trim()) {
    throw memoryReviewConflict(
      'De bestaande geheugenbeoordeling is niet betrouwbaar leesbaar.', 'history_invalid',
    );
  }
  return { status, reason };
}

/** @param {any} input */
function applyMemoryReviewTransition(input) {
  const { request, requestSha256, eventRef, reviewedAt, state } = input;
  let { memory } = state;
  let pure = state.active?.pure || null;
  if (!state.archived && !pure) {
    throw memoryReviewConflict('De classifierbinding van deze geheugenregel ontbreekt.');
  }
  if (request.action === 'archive') {
    const archiveRecord = memoryReviewArchiveRecord({
      request, requestSha256, eventRef, reviewedAt, pure,
    });
    const changed = db.prepare(`UPDATE memories SET archived=1,archived_reason=?
      WHERE id=? AND COALESCE(archived,0)=0`).run(canonicalJson(archiveRecord), request.id);
    if (Number(changed.changes || 0) !== 1) {
      throw memoryReviewConflict('De geheugenregel veranderde tijdens archivering.');
    }
  }
  if (request.action === 'reopen' && state.archived) {
    const changed = db.prepare(`UPDATE memories SET archived=0,archived_reason=NULL
      WHERE id=? AND COALESCE(archived,0)=1 AND archived_reason=?`)
      .run(request.id, memory.archived_reason);
    if (Number(changed.changes || 0) !== 1) {
      throw memoryReviewConflict('De geheugenregel veranderde tijdens herstel.');
    }
    pure = activeMemoryReviewProjection(request.id).pure;
  }
  memory = /** @type {any} */ (
    db.prepare('SELECT * FROM memories WHERE id=?').get(request.id)
  );
  if (!pure || pure.exactFingerprint !== memoryExactFingerprint(memory)) {
    throw memoryReviewConflict('De classifierbinding van deze geheugenregel veranderde.');
  }
  return { memory, pure };
}

function memoryReviewResultProjection(action, pure) {
  if (action === 'keep') {
    return { status: MEMORY_HYGIENE_STATUS.KEEP, reasonCode: 'human_keep', archived: false };
  }
  if (action === 'protect') {
    return { status: MEMORY_HYGIENE_STATUS.PROTECT, reasonCode: 'human_protect', archived: false };
  }
  return { status: pure.status, reasonCode: pure.reasonCode, archived: action === 'archive' };
}

/** @param {any} input */
function recordMemoryReviewOutcome(input) {
  const {
    request, requestSha256, eventRef, reviewedAt, previous, memory, pure,
  } = input;
  const result = memoryReviewResultProjection(request.action, pure);
  const eventShape = {
    event_ref: eventRef,
    action: request.action,
    classifier_status: pure.status,
    classifier_reason: pure.reasonCode,
    classifier_replacement_id: pure.replacementId == null
      ? null : Number(pure.replacementId),
    classifier_binding_sha256: memoryReviewClassifierBinding(pure),
    exact_fingerprint: pure.exactFingerprint,
    previous_status: previous.status,
    previous_reason: previous.reason,
    result_status: result.status,
    result_archived: result.archived ? 1 : 0,
  };
  const resultRevision = memoryReviewResolvedRevision(memory, eventShape);
  const response = {
    contract: MEMORY_REVIEW_MUTATION_CONTRACT,
    version: 1,
    eventRef,
    operationKey: request.operationKey,
    id: request.id,
    action: request.action,
    sourceRevision: request.revision,
    revision: resultRevision,
    exactFingerprint: pure.exactFingerprint,
    previousStatus: previous.status,
    status: result.status,
    reasonCode: result.reasonCode,
    archived: result.archived,
    reviewedAt,
  };
  insertMemoryReviewEvent({
    request, requestSha256, eventRef, reviewedAt, pure,
    previousStatus: previous.status, previousReason: previous.reason,
    resultStatus: result.status, resultArchived: result.archived,
    resultRevision, response,
  });
  return response;
}

/** @param {any} request @param {string} requestSha256 */
function settleMemoryReviewAction(request, requestSha256) {
  const replay = memoryReviewReplay(request.operationKey, requestSha256);
  if (replay) return replay;
  const state = currentMemoryReviewState(request);
  assertMemoryReviewTransition(request, state);
  const eventRef = randomUUID();
  const reviewedAt = isoNow();
  const previous = previousMemoryReviewProjection(state.current);
  const stored = applyMemoryReviewTransition({
    request, requestSha256, eventRef, reviewedAt, state,
  });
  return recordMemoryReviewOutcome({
    request, requestSha256, eventRef, reviewedAt, previous, ...stored,
  });
}

/** Eén expliciete, omkeerbare dashboardbeslissing. Zij beïnvloedt uitsluitend
 * cleanupstatus: keep/protect worden geen promptopdracht en recallmetadata
 * wordt noch gelezen als autoriteit noch gewijzigd. */
export function reviewMemoryAction(input = {}) {
  const request = memoryReviewRequest(input);
  const requestSha256 = memoryReviewRequestSha256(request);
  return db.transaction(
    () => settleMemoryReviewAction(request, requestSha256),
  ).immediate();
}

function archivedMemoryHygieneResult(operation, requestedIds) {
  return {
    contract: 'MemoryHygieneMutation/v1', version: 1, operation, requestedIds,
    changedIds: [], unchangedIds: [], refused: [], records: [],
  };
}

function memoryArchiveRecord(decision, batchId, archivedAt) {
  const replacement = decision.replacementId == null ? null : {
    memoryId: Number(decision.replacementId),
    relation: 'exact_normalized_duplicate',
    exactFingerprint: decision.exactFingerprint,
  };
  return {
    contract: MEMORY_HYGIENE_ARCHIVE_CONTRACT,
    version: 1,
    classifier: MEMORY_HYGIENE_PREVIEW_CONTRACT,
    batchId,
    archivedAt,
    reason: { code: decision.reasonCode, status: decision.status },
    memory: { id: Number(decision.id), exactFingerprint: decision.exactFingerprint },
    replacement,
  };
}

/** Archiveert uitsluitend expliciet geselecteerde, opnieuw geclassificeerde
 * kandidaten. Een tweede identieke aanroep verandert reden/tijdstip niet. */
export function archiveMemoryHygieneCandidates(input = {}) {
  const requestedIds = memoryHygieneIds(input);
  const result = archivedMemoryHygieneResult('archive', requestedIds);
  if (!requestedIds.length) return result;
  return db.transaction(() => {
    const active = /** @type {any[]} */ (db.prepare(`SELECT * FROM memories
      WHERE COALESCE(archived,0)=0 ORDER BY id DESC`).all())
      .filter((row) => !isSensitiveMemoryContent(row.content));
    const decisions = new Map(applyMemoryReviewResolutions(previewMemoryHygieneRows(active))
      .map((decision) => [Number(decision.id), decision]));
    const batchId = randomUUID();
    const archivedAt = isoNow();
    for (const id of requestedIds) {
      const stored = /** @type {any} */ (
        db.prepare('SELECT * FROM memories WHERE id=?').get(id)
      );
      if (!stored) {
        result.refused.push({ id, reasonCode: 'not_found' });
        continue;
      }
      if (Number(stored.archived || 0) === 1) {
        const prior = parseMemoryHygieneArchiveReason(stored.archived_reason);
        if (prior && memoryMatchesHygieneArchive(stored, prior)) {
          result.unchangedIds.push(id);
          result.records.push(prior);
        } else result.refused.push({ id, reasonCode: 'already_archived_by_other_process' });
        continue;
      }
      const decision = decisions.get(id);
      if (decision?.status !== MEMORY_HYGIENE_STATUS.ARCHIVE_CANDIDATE) {
        result.refused.push({
          id, reasonCode: decision?.reasonCode || 'not_an_archive_candidate',
          status: decision?.status || null,
        });
        continue;
      }
      const record = memoryArchiveRecord(decision, batchId, archivedAt);
      const changed = db.prepare(`UPDATE memories SET archived=1, archived_reason=?
        WHERE id=? AND COALESCE(archived,0)=0`).run(canonicalJson(record), id);
      if (Number(changed.changes || 0) !== 1) {
        result.refused.push({ id, reasonCode: 'concurrent_change' });
        continue;
      }
      result.changedIds.push(id);
      result.records.push(record);
    }
    return result;
  }).immediate();
}

/** Herstelt alleen rijen die door bovenstaande hygienegrens zijn gearchiveerd.
 * Andere archiefredenen (bijvoorbeeld een gecorrigeerd fotolabel) blijven staan. */
export function restoreMemoryHygieneArchive(input = {}) {
  const requestedIds = memoryHygieneIds(input);
  const result = archivedMemoryHygieneResult('restore', requestedIds);
  if (!requestedIds.length) return result;
  return db.transaction(() => {
    for (const id of requestedIds) {
      const stored = /** @type {any} */ (
        db.prepare('SELECT * FROM memories WHERE id=?').get(id)
      );
      if (!stored) {
        result.refused.push({ id, reasonCode: 'not_found' });
        continue;
      }
      if (Number(stored.archived || 0) !== 1) {
        result.unchangedIds.push(id);
        continue;
      }
      const provenance = parseMemoryHygieneArchiveReason(stored.archived_reason);
      if (!provenance) {
        result.refused.push({ id, reasonCode: 'archive_owned_by_other_process' });
        continue;
      }
      if (!memoryMatchesHygieneArchive(stored, provenance)) {
        result.refused.push({ id, reasonCode: 'archive_provenance_mismatch' });
        continue;
      }
      const changed = db.prepare(`UPDATE memories SET archived=0, archived_reason=NULL
        WHERE id=? AND COALESCE(archived,0)=1 AND archived_reason=?`)
        .run(id, stored.archived_reason);
      if (Number(changed.changes || 0) !== 1) {
        result.refused.push({ id, reasonCode: 'concurrent_change' });
        continue;
      }
      result.changedIds.push(id);
      result.records.push(provenance);
    }
    return result;
  }).immediate();
}

function clampedMemoryImportance(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return 0.5;
  return Math.max(0, Math.min(1, numeric));
}

export function addMemory({
  category = 'feit', person = null, content, importance = 0.5, feeling = null, mode = null,
}) {
  const safeImportance = clampedMemoryImportance(importance);
  if (!content || isSensitiveMemoryContent(content)
    || lowUtilityMemoryReason({ category, person, content, importance: safeImportance, mode })) return null;
  // Alleen exacte normalisatie (Unicode/case/witruimte) mag automatisch
  // ontdubbelen. Een kleine parafrase of negatie kan een andere herinnering zijn.
  try {
    const kandidaten = /** @type {Array<{id: number, content: string}>} */ (db.prepare(`SELECT id, content FROM memories
      WHERE lower(trim(COALESCE(category,''))) = lower(trim(?))
      AND lower(trim(COALESCE(person,''))) = lower(trim(?))
      AND COALESCE(archived,0)=0 ORDER BY id ASC`).all(category, person || ''));
    for (const k of kandidaten) {
      if (normalizeMemoryExactText(content) === normalizeMemoryExactText(k.content)) {
        db.prepare('UPDATE memories SET importance = min(1, importance + 0.08), last_recalled = ? WHERE id = ?').run(isoNow(), k.id);
        return k.id; // de bestaande wint lading; geen nieuwe kopie
      }
    }
  } catch { /* ontdubbelen is niet kritiek */ }
  const info = db
    .prepare('INSERT INTO memories (ts, category, person, content, importance, feeling) VALUES (?, ?, ?, ?, ?, ?)')
    .run(isoNow(), category, person, content, safeImportance, feeling);
  return Number(info.lastInsertRowid);
}

// ---------- zelfbehoud: back-up van haar leven (de data) ----------

const DAILY_BACKUP_RE = /^eva-\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}\.db$/;

function sha256File(file) {
  const hash = createHash('sha256');
  const descriptor = fs.openSync(file, 'r');
  const chunk = Buffer.allocUnsafe(1024 * 1024);
  try {
    let bytes = 0;
    do {
      bytes = fs.readSync(descriptor, chunk, 0, chunk.length, null);
      if (bytes) hash.update(chunk.subarray(0, bytes));
    } while (bytes);
  } finally {
    fs.closeSync(descriptor);
  }
  return hash.digest('hex');
}

function validBackup(filePath) {
  try {
    const manifestPath = `${filePath}.sha256`;
    if (!fs.existsSync(manifestPath)) return false;
    const expected = String(fs.readFileSync(manifestPath, 'utf8')).trim().split(/\s+/)[0];
    if (!/^[a-f0-9]{64}$/i.test(expected) || sha256File(filePath) !== expected.toLowerCase()) return false;
    const candidate = new Database(filePath, { readonly: true, fileMustExist: true });
    try {
      if (candidate.pragma('quick_check', { simple: true }) !== 'ok') return false;
      if (candidate.pragma('foreign_key_check').length) return false;
      const tables = new Set(candidate.prepare("SELECT name FROM sqlite_master WHERE type='table'").all()
        .map((row) => row.name));
      return ['timeline', 'messages', 'kv'].every((table) => tables.has(table));
    } finally {
      candidate.close();
    }
  } catch {
    return false;
  }
}

export function latestDailyBackup(dir) {
  try {
    return fs.readdirSync(dir)
      .filter((file) => DAILY_BACKUP_RE.test(file))
      .map((file) => {
        const filePath = path.join(dir, file);
        const stat = fs.statSync(filePath);
        return { file, path: filePath, size: stat.size, mtimeMs: stat.mtimeMs };
      })
      .filter((item) => item.size > 0)
      .sort((a, b) => b.mtimeMs - a.mtimeMs)
      .find((item) => validBackup(item.path)) || null;
  } catch {
    return null;
  }
}

export function dailyBackupDue(dir, maxAgeMs = 20 * 3600_000, now = Date.now()) {
  const latest = latestDailyBackup(dir);
  return !latest || Number(now) - latest.mtimeMs >= Math.max(60_000, Number(maxAgeMs) || 20 * 3600_000);
}

export async function backupData(dir) {
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  try { fs.chmodSync(dir, 0o700); } catch { /* beste poging */ }
  // Een afgebroken SQLite-back-up laat alleen een journal achter. Zo'n restant
  // is geen herstelpunt en mag een volgende poging niet vervuilen.
  for (const file of fs.readdirSync(dir).filter((name) => (
    /^eva-\d{4}-.+\.db-(?:journal|wal|shm)$/.test(name)
    || /^\.eva-\d{4}-.+\.partial/.test(name)
  ))) {
    try { fs.unlinkSync(path.join(dir, file)); } catch { /* beste poging */ }
  }
  const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-');
  const dest = path.join(dir, `eva-${stamp}.db`);
  const partial = path.join(dir, `.eva-${stamp}.partial-${process.pid}.db`);
  const partialManifest = `${partial}.sha256`;
  // better-sqlite3 kopieert standaard 100 pagina's per setImmediate. Bij Eva's
  // database hield dat de hoofdthread minutenlang warm. Grotere blokken maken
  // dezelfde consistente SQLite-back-up in veel minder scheduler-rondes.
  try {
    await db.backup(partial, { progress: () => 4096 });
    fs.chmodSync(partial, 0o600);
    const candidate = new Database(partial, { fileMustExist: true });
    try {
      candidate.pragma('wal_checkpoint(TRUNCATE)');
      candidate.pragma('journal_mode = DELETE');
      if (candidate.pragma('quick_check', { simple: true }) !== 'ok'
        || candidate.pragma('foreign_key_check').length) {
        throw new Error('de nieuwe SQLite-back-up faalde haar integriteitscontrole');
      }
    } finally {
      candidate.close();
    }
    for (const suffix of ['-wal', '-shm', '-journal']) {
      try { fs.unlinkSync(`${partial}${suffix}`); } catch { /* beste poging */ }
    }
    const checksum = sha256File(partial);
    fs.writeFileSync(partialManifest, `${checksum}  ${path.basename(dest)}\n`, {
      encoding: 'utf8', mode: 0o600, flag: 'wx',
    });
    fs.renameSync(partial, dest);
    fs.renameSync(partialManifest, `${dest}.sha256`);
  } catch (error) {
    for (const file of [partial, partialManifest, `${partial}-wal`, `${partial}-shm`, `${partial}-journal`]) {
      try { fs.unlinkSync(file); } catch { /* beste poging */ }
    }
    throw error;
  }
  // Hou de laatste 10 automatische back-ups. Handmatige `eva-before-*` snapshots
  // hebben een ander doel en worden hier nooit stilletjes verwijderd.
  const backups = fs.readdirSync(dir)
    .filter((file) => DAILY_BACKUP_RE.test(file) && validBackup(path.join(dir, file)))
    .sort();
  for (const oud of backups.slice(0, -10)) {
    for (const file of [path.join(dir, oud), path.join(dir, `${oud}.sha256`)]) {
      try { fs.unlinkSync(file); } catch { /* beste poging */ }
    }
  }
  return dest;
}

export function bumpRecall(ids) {
  if (!ids.length) return;
  db.prepare(
    `UPDATE memories SET recall_count = recall_count + 1, last_recalled = ? WHERE id IN (${ids.map(() => '?').join(',')})`
  ).run(isoNow(), ...ids);
}

// bump=false voor intern gebruik (dedupe/fallback): dan vervuilt recall_count/
// belangrijkheid niet met herinneringen die nooit aan Eva zijn getoond.
export function searchMemories(query, limit = 8, bump = true) {
  const stopwoorden = new Set([
    'aan', 'als', 'ben', 'bij', 'daar', 'dan', 'dat', 'de', 'deze', 'die', 'dit',
    'een', 'en', 'er', 'gaat', 'gewoon', 'heb', 'heeft', 'het', 'hier', 'hoe', 'ik',
    'in', 'is', 'je', 'jij', 'kan', 'kun', 'maar', 'me', 'met', 'mij', 'moet', 'naar',
    'niet', 'nog', 'nu', 'of', 'om', 'ons', 'ook', 'op', 'over', 'te', 'van', 'voor',
    'waar', 'wat', 'we', 'wel', 'wij', 'wil', 'wordt', 'zijn', 'zou',
  ]);
  const tokens = String(query || '')
    .replace(/[^\p{L}\p{N} ]/gu, ' ')
    .split(/\s+/)
    .map((w) => w.toLowerCase())
    .filter((w) => w.length > 2 && !stopwoorden.has(w));
  if (!tokens.length) return [];
  let rows = [];
  if (hasFts) {
    try {
      const match = tokens.map((t) => `"${t}"`).join(' OR ');
      rows = db
        .prepare(
          `SELECT m.* FROM memories_fts f JOIN memories m ON m.id = f.rowid
           WHERE memories_fts MATCH ? AND COALESCE(m.archived,0)=0
           ORDER BY m.importance DESC, m.ts DESC LIMIT ?`
        )
        .all(match, Math.max(limit, limit * 4));
    } catch {
      rows = [];
    }
  }
  if (!rows.length) {
    const like = tokens.map(() => 'content LIKE ?');
    rows = db
      .prepare(`SELECT * FROM memories WHERE COALESCE(archived,0)=0 AND (${like.join(' OR ')}) ORDER BY importance DESC, ts DESC LIMIT ?`)
      .all(...tokens.map((t) => `%${t}%`), Math.max(limit, limit * 4));
  }
  rows = safeMemoryRows(rows, limit);
  if (bump && rows.length) {
    const ids = rows.map((r) => r.id);
    db.prepare(
      `UPDATE memories SET recall_count = recall_count + 1, last_recalled = ? WHERE id IN (${ids.map(() => '?').join(',')})`
    ).run(isoNow(), ...ids);
  }
  return rows;
}

export function allMemories(limit = 300) {
  return safeMemoryRows(db.prepare('SELECT * FROM memories WHERE COALESCE(archived,0)=0 ORDER BY ts DESC LIMIT ?').all(Math.max(limit, limit * 4)), limit);
}

const PERSON_MEMORY_QUERY_STOPWORDS = new Set([
  'aan', 'als', 'ben', 'bij', 'dan', 'dat', 'de', 'deze', 'die', 'dit', 'een',
  'en', 'er', 'heb', 'heeft', 'het', 'hoe', 'ik', 'in', 'is', 'je', 'jij',
  'kan', 'maar', 'me', 'met', 'mij', 'naar', 'niet', 'nog', 'nu', 'of', 'om',
  'ons', 'ook', 'op', 'over', 'te', 'van', 'voor', 'waar', 'wat', 'we', 'wel',
  'wij', 'wil', 'zijn', 'zou',
]);
const PERSON_MEMORY_CANDIDATE_LIMIT = 500;

function personMemoryQueryTokens(query) {
  return [...new Set(String(query || '').slice(0, 600)
    .toLocaleLowerCase('nl-NL')
    .replace(/[^\p{L}\p{N} ]/gu, ' ')
    .split(/\s+/)
    .filter((word) => word.length > 2 && !PERSON_MEMORY_QUERY_STOPWORDS.has(word)))]
    .slice(0, 16);
}

function personMemoryRelevance(row, tokens) {
  if (!tokens.length) return 0;
  const words = new Set(String(row?.content || '')
    .toLocaleLowerCase('nl-NL')
    .replace(/[^\p{L}\p{N} ]/gu, ' ')
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 160));
  return tokens.reduce((score, token) => score + Number(words.has(token)), 0);
}

db.function('person_memory_relevance', { deterministic: true }, (content, tokenText) => (
  personMemoryRelevance({ content }, String(tokenText || '').split(' ').filter(Boolean))
));

function preferredPersonMemory(row) {
  return ['voorkeur', 'afspraak'].includes(String(row?.category || '').toLowerCase()) ? 1 : 0;
}

function comparePersonMemoryCandidates(left, right) {
  if (left.relevance !== right.relevance) return right.relevance - left.relevance;
  if (left.relevance === 0) {
    const preferredDifference = preferredPersonMemory(right.row) - preferredPersonMemory(left.row);
    if (preferredDifference) return preferredDifference;
    if (preferredPersonMemory(left.row)) {
      const recentDifference = String(right.row.ts || '').localeCompare(String(left.row.ts || ''));
      if (recentDifference) return recentDifference;
    }
  }
  return Number(right.row.importance || 0) - Number(left.row.importance || 0)
    || String(right.row.ts || '').localeCompare(String(left.row.ts || ''))
    || Number(right.row.id || 0) - Number(left.row.id || 0);
}

/**
 * Projecteer alleen veilig persoonsgeheugen. De optionele derde parameter houdt
 * bestaande (person, limit)-aanroepen intact en verandert geen recall-metadata.
 */
export function memoriesForPerson(person, limit = 6, query = '') {
  const name = String(person || '').trim();
  if (!name) return [];
  const boundedLimit = Math.max(1, Math.min(20, Number(limit) || 6));
  const tokens = personMemoryQueryTokens(query);
  if (!tokens.length) return safeMemoryRows(db.prepare(`
    SELECT * FROM memories
    WHERE COALESCE(archived,0)=0
      AND lower(trim(COALESCE(person,''))) = lower(?)
    ORDER BY importance DESC, ts DESC
    LIMIT ?
  `).all(name, boundedLimit), boundedLimit);

  const tokenText = tokens.join(' ');
  const matching = safeMemoryRows(db.prepare(`
    SELECT * FROM memories
    WHERE COALESCE(archived,0)=0
      AND lower(trim(COALESCE(person,''))) = lower(?)
      AND person_memory_relevance(content, ?) > 0
    ORDER BY person_memory_relevance(content, ?) DESC, importance DESC, ts DESC, id DESC
    LIMIT ?
  `).all(name, tokenText, tokenText, PERSON_MEMORY_CANDIDATE_LIMIT),
  PERSON_MEMORY_CANDIDATE_LIMIT);
  const fallback = safeMemoryRows(db.prepare(`
    SELECT * FROM memories
    WHERE COALESCE(archived,0)=0
      AND lower(trim(COALESCE(person,''))) = lower(?)
    ORDER BY CASE WHEN lower(COALESCE(category,'')) IN ('voorkeur','afspraak')
      THEN 1 ELSE 0 END DESC,
      CASE WHEN lower(COALESCE(category,'')) IN ('voorkeur','afspraak') THEN ts ELSE '' END DESC,
      importance DESC, ts DESC, id DESC
    LIMIT ?
  `).all(name, PERSON_MEMORY_CANDIDATE_LIMIT), PERSON_MEMORY_CANDIDATE_LIMIT);
  const rows = [...new Map([...matching, ...fallback].map((row) => [row.id, row])).values()];
  return rows.map((row) => ({ row, relevance: personMemoryRelevance(row, tokens) }))
    .sort(comparePersonMemoryCandidates)
    .slice(0, boundedLimit)
    .map(({ row }) => row);
}

// ---------- emoties ----------

export function logEmotion(mood, note = null) {
  db.prepare('INSERT INTO emotion_log (ts, mood, note) VALUES (?, ?, ?)').run(isoNow(), JSON.stringify(mood), note);
  bus.emit('mood', { mood, note });
}

export function emotionHistory(hours = 24) {
  const since = new Date(Date.now() - hours * 3600_000).toISOString();
  return db
    .prepare('SELECT ts, mood FROM emotion_log WHERE ts >= ? ORDER BY id ASC')
    .all(since)
    .map((r) => ({ ts: r.ts, mood: JSON.parse(r.mood) }));
}

// ---------- geplande herinneringen (reminders) ----------

export function addReminder({ due_ts, text, channel = 'telegram', target = null, recurrence = null }) {
  // ontdubbel: bestaat er al een open herinnering die hier sterk op lijkt (zelfde
  // doel)? Maak er dan geen nieuwe — voorkomt tientallen bijna-gelijke meldingen.
  const open = db.prepare("SELECT id, text, target FROM reminders WHERE status = 'open'").all();
  // zelf-reminders (aan Eva) parafraseert ze net genoeg om ontdubbeling te ontwijken;
  // pak die veel strenger, en cap het aantal open zelf-reminders zodat ze niet opstapelen.
  const zelf = !target || /^\s*eva\s*$/i.test(target);
  const drempel = zelf ? 0.3 : 0.6;
  const dubbel = open.find((r) => (r.target || '') === (target || '') && tekstOverlap(r.text, text) >= drempel);
  if (dubbel) return dubbel.id;
  if (zelf && open.filter((r) => !r.target || /^\s*eva\s*$/i.test(r.target)).length >= 2) {
    return null; // al genoeg eigen voornemens open; geen nieuwe stapel
  }
  const info = db
    .prepare('INSERT INTO reminders (created_ts, due_ts, recurrence, channel, target, text) VALUES (?, ?, ?, ?, ?, ?)')
    .run(isoNow(), due_ts, recurrence, channel, target, text);
  return Number(info.lastInsertRowid);
}

export function dueReminders(nowIso = isoNow()) {
  return db.prepare("SELECT * FROM reminders WHERE status = 'open' AND due_ts <= ? ORDER BY due_ts ASC").all(nowIso);
}

export function openReminders() {
  return db.prepare("SELECT * FROM reminders WHERE status = 'open' ORDER BY due_ts ASC").all();
}

export function completeReminder(id, nextDueTs = null) {
  if (nextDueTs) db.prepare('UPDATE reminders SET due_ts = ? WHERE id = ?').run(nextDueTs, id);
  else db.prepare("UPDATE reminders SET status = 'done' WHERE id = ?").run(id);
}

export function cancelReminder(id) {
  db.prepare("UPDATE reminders SET status = 'cancelled' WHERE id = ?").run(id);
}

// ---------- mensen ----------

export function upsertPerson(name, { telegram_id = null, notes = null } = {}) {
  const existing = db.prepare('SELECT * FROM people WHERE lower(name) = lower(?)').get(name);
  if (existing) {
    db.prepare('UPDATE people SET telegram_id = COALESCE(?, telegram_id), notes = COALESCE(?, notes), last_seen = ? WHERE id = ?')
      .run(telegram_id, notes, isoNow(), existing.id);
    return existing.id;
  }
  const info = db
    .prepare('INSERT INTO people (name, telegram_id, notes, last_seen) VALUES (?, ?, ?, ?)')
    .run(name, telegram_id, notes, isoNow());
  return Number(info.lastInsertRowid);
}

export function personByTelegram(telegramId) {
  return db.prepare('SELECT * FROM people WHERE telegram_id = ?').get(telegramId);
}

export function allPeople() {
  return db.prepare('SELECT * FROM people ORDER BY name ASC').all();
}

// ---------- losse sleutels ----------

// ---------- voornemens (Eva's eigen verlangens) ----------

export function addAspiration({
  text,
  why = null,
  kind = 'interest',
  goalKey = null,
  beneficiary = null,
  evidence = null,
  nextStep = null,
  cadenceHours = null,
  priority = null,
  touched = true,
}) {
  const ts = isoNow();
  const info = db
    .prepare(`INSERT INTO aspirations
      (created_ts, text, why, last_touched, kind, goal_key, beneficiary, evidence,
       next_step, cadence_hours, priority)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`)
    .run(
      ts, text, why, touched ? ts : null,
      kind === 'development' ? 'development' : 'interest',
      goalKey, beneficiary, evidence, nextStep,
      cadenceHours === null || cadenceHours === undefined
        || String(cadenceHours).trim() === ''
        ? null
        : Number.isFinite(Number(cadenceHours)) ? Number(cadenceHours) : null,
      Number.isFinite(Number(priority)) ? Number(priority) : null,
    );
  return Number(info.lastInsertRowid);
}

export function activeAspirations() {
  return db.prepare("SELECT * FROM aspirations WHERE status = 'actief' ORDER BY last_touched ASC").all();
}

export function touchAspiration(id, progress = null) {
  if (progress) db.prepare('UPDATE aspirations SET last_touched = ?, progress = ? WHERE id = ?').run(isoNow(), progress, id);
  else db.prepare('UPDATE aspirations SET last_touched = ? WHERE id = ?').run(isoNow(), id);
}

// ---------- losse sleutels ----------

export function kvGet(key, def = null) {
  const row = db.prepare('SELECT value FROM kv WHERE key = ?').get(key);
  return row ? JSON.parse(row.value) : def;
}

export function kvSet(key, value) {
  db.prepare('INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value').run(
    key, JSON.stringify(value)
  );
}

// ---------- onderhoud (voorkomt onbegrensde groei) ----------

export function maintain() {
  try {
    // emotie-log: hou de laatste ~4000 metingen
    db.exec(`DELETE FROM emotion_log WHERE id NOT IN (SELECT id FROM emotion_log ORDER BY id DESC LIMIT 4000)`);
    // vluchtige tijdlijn-soorten ouder dan 30 dagen opruimen; haar verhaal
    // (dagboek, dromen, innerlijk, karakter, gesprekken, huis, herinneringen) blijft.
    const grens = new Date(Date.now() - 30 * 86400000).toISOString();
    db.prepare(`DELETE FROM main.timeline WHERE kind IN ('system','heartbeat') AND ts < ?`).run(grens);
  } catch { /* onderhoud is niet kritiek */ }
}

export default db;
