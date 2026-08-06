import fs from 'node:fs';
import path from 'node:path';
import Database from 'better-sqlite3';
import { config } from './config.js';
import { bus } from './bus.js';
import { isoNow, tekstOverlap } from './util.js';
import { sealThought, openThought } from './self/secretmind.js';

fs.mkdirSync(config.dataDir, { recursive: true, mode: 0o700 });
try { fs.chmodSync(config.dataDir, 0o700); } catch { /* beste poging */ }
fs.mkdirSync(config.workspaceDir, { recursive: true, mode: 0o700 });
try { fs.chmodSync(config.workspaceDir, 0o700); } catch { /* beste poging */ }
// De werkruimte is een zelfstandig uitvoergebied. Leg de JavaScript-runtime
// expliciet vast, zodat dezelfde .js-code binnen Eva's uitvoerder en rechtstreeks
// vanaf de projectmap niet anders wordt geïnterpreteerd door het bovenliggende
// ESM-project.
const workspacePackagePath = path.join(config.workspaceDir, 'package.json');
if (!fs.existsSync(workspacePackagePath)) {
  fs.writeFileSync(workspacePackagePath, `${JSON.stringify({ private: true, type: 'commonjs' }, null, 2)}\n`, {
    encoding: 'utf8',
    mode: 0o600,
    flag: 'wx',
  });
}
const databasePath = path.join(config.dataDir, 'eva.db');
export const db = new Database(databasePath);
try { fs.chmodSync(databasePath, 0o600); } catch { /* beste poging */ }
db.pragma('journal_mode = WAL');
db.pragma('synchronous = NORMAL');
db.pragma('busy_timeout = 5000');

db.exec(`
CREATE TABLE IF NOT EXISTS timeline (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  kind TEXT NOT NULL,          -- chat_in | chat_out | journal | dream | heartbeat | reminder | house | system
  content TEXT NOT NULL,
  meta TEXT
);
CREATE INDEX IF NOT EXISTS idx_timeline_ts ON timeline(ts);
CREATE INDEX IF NOT EXISTS idx_timeline_kind ON timeline(kind);

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

CREATE TABLE IF NOT EXISTS turn_tool_executions (
  execution_key TEXT PRIMARY KEY,
  turn_id TEXT NOT NULL,
  tool TEXT NOT NULL,
  args TEXT,
  status TEXT NOT NULL DEFAULT 'started',
  started_ts TEXT NOT NULL,
  finished_ts TEXT,
  output TEXT
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

try {
  const messageColumns = new Set(db.prepare('PRAGMA table_info(messages)').all().map((column) => column.name));
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

// Recente gespreksembeddings blijven over herstarts heen bewaard. Zonder deze
// kolom moest Eva na iedere start honderden identieke chatregels opnieuw door
// de lokale inferentieservice halen.
try {
  const cols = db.prepare('PRAGMA table_info(timeline)').all();
  if (!cols.some((c) => c.name === 'embedding')) db.exec('ALTER TABLE timeline ADD COLUMN embedding BLOB');
} catch { /* een oude database blijft zonder semantische chatcache bruikbaar */ }

// Geheugenkolommen (migratie; SQLite kent geen ADD COLUMN IF NOT EXISTS).
// Archiveren houdt oude ervaringen beschikbaar voor inspectie zonder dat ruis
// nog in gesprekken, planning of het eigen brein wordt opgehaald.
try {
  const cols = db.prepare('PRAGMA table_info(memories)').all();
  if (!cols.some((c) => c.name === 'embedding')) db.exec('ALTER TABLE memories ADD COLUMN embedding BLOB');
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
    const moved = db.prepare(`UPDATE timeline SET kind='innerlijk'
      WHERE kind='journal' AND meta IS NOT NULL
        AND COALESCE(json_extract(meta,'$.soort'),'') NOT IN ('dagafsluiting','eigen_dagboek','belevenis')
        AND COALESCE(json_extract(meta,'$.belevenis'),0) != 1`).run();
    db.prepare('INSERT INTO kv (key,value) VALUES (?,?)').run(
      migrationKey,
      JSON.stringify({ ts: isoNow(), moved: Number(moved.changes || 0) }),
    );
  }
} catch { /* oude SQLite-build of beschadigde meta mag starten niet blokkeren */ }

// Eva's privé-gedachten: alleen van haar. Komen NOOIT op de tijdlijn, het
// dashboard of in een API. Een hoekje van haar geest dat niet wordt opgevoerd.
db.exec(`
CREATE TABLE IF NOT EXISTS private_thoughts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  content TEXT NOT NULL,
  mood TEXT
);`);

// ---------- tijdlijn ----------

export function logTimeline(kind, content, meta = null) {
  const ts = isoNow();
  const info = db
    .prepare('INSERT INTO timeline (ts, kind, content, meta) VALUES (?, ?, ?, ?)')
    .run(ts, kind, content, meta ? JSON.stringify(meta) : null);
  const row = { id: Number(info.lastInsertRowid), ts, kind, content, meta };
  bus.emit('timeline', row);
  return row;
}

export function getTimeline({ limit = 50, kinds = null, before = null } = {}) {
  // De embedding-blob is intern indexmateriaal en hoort niet in dashboard- of
  // contextpayloads terecht te komen.
  let sql = 'SELECT id, ts, kind, content, meta FROM timeline';
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

export function claimTurnToolExecution({ executionKey, turnId, tool, args = null } = {}) {
  const key = String(executionKey || '').trim();
  if (!key) throw new Error('tooluitvoering mist een duurzame sleutel');
  const info = db.prepare(`
    INSERT OR IGNORE INTO turn_tool_executions
      (execution_key, turn_id, tool, args, status, started_ts)
    VALUES (?, ?, ?, ?, 'started', ?)
  `).run(
    key,
    String(turnId || ''),
    String(tool || ''),
    args == null ? null : JSON.stringify(args).slice(0, 20_000),
    isoNow(),
  );
  const row = db.prepare('SELECT * FROM turn_tool_executions WHERE execution_key = ?').get(key);
  return { claimed: Number(info.changes || 0) === 1, row };
}

export function completeTurnToolExecution(executionKey, output) {
  db.prepare(`
    UPDATE turn_tool_executions
    SET status = 'done', finished_ts = ?, output = ?
    WHERE execution_key = ?
  `).run(isoNow(), String(output ?? '').slice(0, 40_000), String(executionKey));
}

// ---------- gesprekken ----------

export function addMessage(channel, sender, role, content, conversationId = null) {
  const ts = isoNow();
  const info = db.prepare('INSERT INTO messages (ts, channel, conversation_id, sender, role, content) VALUES (?, ?, ?, ?, ?, ?)').run(
    ts, channel, conversationId || null, sender, role, content
  );
  const message = {
    id: Number(info.lastInsertRowid),
    ts,
    channel,
    conversation_id: conversationId || null,
    sender,
    role,
    content,
  };
  bus.emit('chat', message);
  return message;
}

export function recentMessages(channel = null, limit = 30) {
  const rows = channel
    ? db.prepare('SELECT * FROM messages WHERE channel = ? ORDER BY id DESC LIMIT ?').all(channel, limit)
    : db.prepare('SELECT * FROM messages ORDER BY id DESC LIMIT ?').all(limit);
  return rows.reverse();
}

export function recentConversation(conversationId, limit = 30) {
  const id = String(conversationId || '').trim();
  if (!id) return [];
  return db
    .prepare('SELECT * FROM messages WHERE conversation_id = ? ORDER BY id DESC LIMIT ?')
    .all(id, limit)
    .reverse();
}

export function recentOtherConversationsForPerson(
  sender,
  conversationId,
  limit = 8,
) {
  const person = String(sender || '').trim();
  if (!person) return [];
  const since = new Date(Date.now() - 48 * 3600_000).toISOString();
  return db.prepare(`
    WITH person_threads AS (
      SELECT DISTINCT conversation_id
      FROM messages
      WHERE role = 'user' AND lower(sender) = lower(?)
        AND conversation_id IS NOT NULL AND conversation_id != ''
    )
    SELECT m.*
    FROM messages m
    WHERE m.conversation_id IN (SELECT conversation_id FROM person_threads)
      AND m.conversation_id != ?
      AND m.ts >= ?
    ORDER BY m.id DESC
    LIMIT ?
  `).all(
    person,
    String(conversationId || ''),
    since,
    Math.max(1, Math.min(20, Number(limit) || 8)),
  ).reverse();
}

export function messagesSince(tsIso) {
  return db.prepare('SELECT * FROM messages WHERE ts >= ? ORDER BY id ASC').all(tsIso);
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
  let existingMeta = null;
  try { existingMeta = existing?.meta ? JSON.parse(existing.meta) : null; } catch { /* negeren */ }
  const mergedMeta = meta && typeof meta === 'object'
    ? { ...(existingMeta && typeof existingMeta === 'object' ? existingMeta : {}), ...meta }
    : existingMeta;
  const archivedTs = isoNow();
  const values = {
    ts: existing?.ts || telegramTimestamp(ts),
    sender: String(sender || existing?.sender || '').slice(0, 160),
    role: ['user', 'assistant'].includes(String(role)) ? String(role) : (existing?.role || 'user'),
    kind: String(kind || existing?.kind || 'text').slice(0, 40),
    content: String(content || existing?.content || '').slice(0, 16_000),
    replyToMessageId: replyToMessageId == null
      ? (existing?.reply_to_message_id || null)
      : String(replyToMessageId),
    mediaRef: mediaRef == null ? (existing?.media_ref || null) : String(mediaRef),
    fileId: fileId == null ? (existing?.file_id || null) : String(fileId),
    deleted: deleted == null ? Number(existing?.deleted || 0) : (deleted ? 1 : 0),
    processingStatus: existing?.processing_status || processingStatus || 'received',
    processingAttempts: Number(existing?.processing_attempts || 0),
    processedTs: existing?.processed_ts || null,
    processingError: existing?.processing_error || null,
    meta: mergedMeta ? JSON.stringify(mergedMeta) : null,
  };

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

/** Geef het eerstvolgende, nieuwere gebruikersbericht in dezelfde Telegram-chat. */
export function newerTelegramUserMessage(chatId, messageId) {
  if (chatId == null || messageId == null) return null;
  const currentMessageId = String(messageId).trim();
  if (!/^\d+$/.test(currentMessageId)) return null;
  return parseTelegramMessage(db.prepare(`
    SELECT * FROM telegram_messages
    WHERE chat_id=? AND role='user' AND deleted=0
      AND message_id GLOB '[0-9]*'
      AND CAST(message_id AS INTEGER) > CAST(? AS INTEGER)
    ORDER BY CAST(message_id AS INTEGER) ASC LIMIT 1
  `).get(String(chatId), currentMessageId));
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
  try {
    return db.prepare(`
      SELECT * FROM telegram_messages
      WHERE role = 'user'
        AND processing_status IN ('received', 'failed')
        AND processing_attempts < 5
        AND json_type(meta, '$.preparedReply') = 'object'
      ORDER BY id ASC
      LIMIT ?
    `).all(bounded).map(parseTelegramMessage);
  } catch {
    return recentTelegramMessages({
      role: 'user',
      includeDeleted: false,
      limit: 500,
    }).filter((message) => (
      ['received', 'failed'].includes(message.processing_status)
      && Number(message.processing_attempts || 0) < 5
      && message.meta?.preparedReply
    )).slice(0, bounded);
  }
}

export function hasDeliveredTurn(turnId) {
  const id = String(turnId || '').trim();
  if (!id) return false;
  try {
    return !!db.prepare(`
      SELECT 1 FROM timeline
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

export function claimTelegramMessage(chatId, messageId) {
  if (chatId == null || messageId == null) return { claimed: false, reason: 'missing_identity' };
  const chat = String(chatId);
  const message = String(messageId);
  return db.transaction(() => {
    const row = db.prepare(
      'SELECT processing_status,processing_attempts FROM telegram_messages WHERE chat_id=? AND message_id=?',
    ).get(chat, message);
    if (!row) return { claimed: false, reason: 'not_archived' };
    if (row.processing_status === 'completed') {
      return { claimed: false, reason: 'completed', attempts: Number(row.processing_attempts || 0) };
    }
    if (row.processing_status === 'processing') {
      return { claimed: false, reason: 'processing', attempts: Number(row.processing_attempts || 0) };
    }
    const attempts = Number(row.processing_attempts || 0) + 1;
    db.prepare(`UPDATE telegram_messages
      SET processing_status='processing', processing_attempts=?,
          processing_error=NULL, archived_ts=?
      WHERE chat_id=? AND message_id=?`).run(attempts, isoNow(), chat, message);
    return { claimed: true, reason: 'claimed', attempts };
  })();
}

export function completeTelegramMessage(chatId, messageId) {
  if (chatId == null || messageId == null) return false;
  const now = isoNow();
  return db.prepare(`UPDATE telegram_messages
    SET processing_status='completed', processed_ts=?, processing_error=NULL, archived_ts=?
    WHERE chat_id=? AND message_id=?`).run(
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
    WHERE chat_id=? AND message_id=?`).run(
    String(error || '').slice(0, 500),
    isoNow(),
    String(chatId),
    String(messageId),
  ).changes > 0;
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

// ---------- herinneringen (langetermijngeheugen) ----------

// ontdubbel-hulp: woord-overlap (Jaccard op woorden >3 letters), genormaliseerd.
const _memWoorden = (s) => new Set(String(s || '').toLowerCase().replace(/[^\p{L}\p{N} ]/gu, ' ').split(/\s+/).filter((w) => w.length > 3));
function _memOverlap(a, b) {
  const A = _memWoorden(a), B = _memWoorden(b);
  if (!A.size || !B.size) return 0;
  let n = 0; for (const w of A) if (B.has(w)) n++;
  return n / Math.max(A.size, B.size);
}

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
export function lowUtilityMemoryReason({ category = 'feit', person = null, content = '' } = {}) {
  const cat = String(category || '').toLowerCase();
  const owner = String(person || '').toLowerCase();
  const value = String(content || '');
  if (['persoon', 'voorkeur', 'gewoonte', 'afspraak', 'correctie', 'identiteit', 'signalement', 'belevenis'].includes(cat)) return null;
  if (!['', 'eva'].includes(owner)) return null;
  if (STALE_INTERNAL_STATE_MEMORY.test(value)) return 'verouderde_interne_status';
  if (INTERNAL_IMPLEMENTATION_MEMORY.test(value) || PROCEDURAL_SELF_REPORT.test(value)) return 'intern_implementatieverslag';
  if (REPETITIVE_SELF_PROCESS_MEMORY.test(value)) return 'herhaalde_zelfbeschouwing';
  return null;
}

function clearPersonPrefixMatch(content, people = []) {
  const value = String(content || '').trim();
  if (!value) return null;
  const matches = people.filter((person) => {
    const name = String(person?.name || '').trim();
    if (name.length < 2) return false;
    const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    return new RegExp(`^(?:over\\s+)?${escaped}(?:['’]s)?(?:\\s|:|,|\\.)`, 'i').test(value);
  });
  return matches.length === 1 ? String(matches[0].name).trim() : null;
}

/**
 * Oudere persoonsfeiten werden vaak zonder owner opgeslagen. Alleen een
 * ondubbelzinnige naam aan het begin van de zin wordt hersteld; vrije tekst en
 * gezichtsgissingen blijven ongemoeid.
 */
export function reassignUnownedPersonMemories() {
  const people = db.prepare("SELECT name FROM people WHERE name IS NOT NULL AND length(trim(name))>=2 ORDER BY length(name) DESC").all();
  if (!people.length) return [];
  const rows = db.prepare(`SELECT id,content FROM memories
    WHERE COALESCE(archived,0)=0 AND (person IS NULL OR trim(person)='')
      AND lower(category) IN ('persoon','voorkeur','gewoonte','gebeurtenis','feit','correctie','belevenis')
    ORDER BY id`).all();
  const changed = [];
  const update = db.prepare('UPDATE memories SET person=? WHERE id=? AND (person IS NULL OR trim(person)=\'\')');
  const tx = db.transaction(() => {
    for (const row of rows) {
      const person = clearPersonPrefixMatch(row.content, people);
      if (!person) continue;
      update.run(person, row.id);
      changed.push({ id: Number(row.id), person });
    }
  });
  tx();
  return changed;
}

export function archiveLowUtilityMemories() {
  const rows = db.prepare(`SELECT id,category,person,content FROM memories
    WHERE COALESCE(archived,0)=0 ORDER BY id`).all();
  const archive = db.prepare('UPDATE memories SET archived=1, archived_reason=? WHERE id=?');
  const changed = [];
  const tx = db.transaction(() => {
    for (const row of rows) {
      const reason = lowUtilityMemoryReason(row);
      if (!reason) continue;
      archive.run(reason, row.id);
      changed.push({ id: row.id, reason });
    }
  });
  tx();
  return changed;
}

function safeMemoryRows(rows, limit = rows.length) {
  return rows
    .filter((row) => Number(row?.archived || 0) !== 1)
    .filter((row) => !isSensitiveMemoryContent(row?.content))
    .slice(0, limit);
}

export function addMemory({ category = 'feit', person = null, content, importance = 0.5, feeling = null }) {
  if (!content || isSensitiveMemoryContent(content) || lowUtilityMemoryReason({ category, person, content })) return null;
  // Ontdubbelen bij de bron: bestaat er al een bijna-identieke herinnering in dezelfde
  // categorie, versterk die dan (importance omhoog) i.p.v. een kopie op te stapelen —
  // anders verwatert recall met 2-3× hetzelfde. Drempel hoog (0.8) zodat écht
  // verschillende lessen apart blijven.
  try {
    const kandidaten = db.prepare('SELECT id, content FROM memories WHERE category = ? AND COALESCE(archived,0)=0 ORDER BY id DESC LIMIT 200').all(category);
    for (const k of kandidaten) {
      if (_memOverlap(content, k.content) >= 0.8) {
        db.prepare('UPDATE memories SET importance = min(1, importance + 0.08), last_recalled = ? WHERE id = ?').run(isoNow(), k.id);
        return k.id; // de bestaande wint lading; geen nieuwe kopie
      }
    }
  } catch { /* ontdubbelen is niet kritiek */ }
  const info = db
    .prepare('INSERT INTO memories (ts, category, person, content, importance, feeling) VALUES (?, ?, ?, ?, ?, ?)')
    .run(isoNow(), category, person, content, importance, feeling);
  const id = Number(info.lastInsertRowid);
  bus.emit('memory_added', { id, content }); // associatief geheugen indexeert dit op de achtergrond
  return id;
}

// ---------- privé-gedachten (alleen voor Eva zelf) ----------

export function addPrivateThought(content, mood = null) {
  // versleuteld opslaan: op schijf staat alleen ruis
  db.prepare('INSERT INTO private_thoughts (ts, content, mood) VALUES (?, ?, ?)').run(isoNow(), sealThought(content), mood ? JSON.stringify(mood) : null);
}

export function recentPrivateThoughts(limit = 5) {
  return db
    .prepare('SELECT ts, content FROM private_thoughts ORDER BY id DESC LIMIT ?')
    .all(limit)
    .reverse()
    .map((r) => ({ ts: r.ts, content: openThought(r.content) }))
    .filter((r) => r.content != null);
}

// ---------- mensen samenvoegen/hernoemen (Eva beheert wie wie is) ----------

export function mergePeople(vanNaam, naarNaam) {
  const van = db.prepare('SELECT * FROM people WHERE lower(name) = lower(?)').get(vanNaam);
  const naar = db.prepare('SELECT * FROM people WHERE lower(name) = lower(?)').get(naarNaam);
  if (!van && !naar) return { ok: false, error: 'beide namen onbekend' };
  const doelNaam = naar?.name || naarNaam;
  // berichten en herinneringen overzetten
  db.prepare('UPDATE messages SET sender = ? WHERE lower(sender) = lower(?)').run(doelNaam, vanNaam);
  db.prepare('UPDATE memories SET person = ? WHERE lower(person) = lower(?)').run(doelNaam, vanNaam);
  // telegram-koppeling meenemen en de oude persoon opruimen
  if (van) {
    if (naar) {
      if (van.telegram_id && !naar.telegram_id) db.prepare('UPDATE people SET telegram_id = ? WHERE id = ?').run(van.telegram_id, naar.id);
      db.prepare('DELETE FROM people WHERE id = ?').run(van.id);
    } else {
      db.prepare('UPDATE people SET name = ? WHERE id = ?').run(doelNaam, van.id);
    }
  }
  return { ok: true, naar: doelNaam };
}

export function renamePerson(oudeNaam, nieuweNaam) {
  db.prepare('UPDATE people SET name = ? WHERE lower(name) = lower(?)').run(nieuweNaam, oudeNaam);
  db.prepare('UPDATE messages SET sender = ? WHERE lower(sender) = lower(?)').run(nieuweNaam, oudeNaam);
  db.prepare('UPDATE memories SET person = ? WHERE lower(person) = lower(?)').run(nieuweNaam, oudeNaam);
  return { ok: true };
}

// ---------- zelfbehoud: back-up van haar leven (de data) ----------

export function compactDb() {
  try { db.exec('PRAGMA wal_checkpoint(TRUNCATE);'); db.exec('VACUUM;'); } catch { /* niet kritiek */ }
}

const DAILY_BACKUP_RE = /^eva-\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}\.db$/;

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
      .sort((a, b) => b.mtimeMs - a.mtimeMs)[0] || null;
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
  for (const file of fs.readdirSync(dir).filter((name) => /^eva-\d{4}-.+\.db-journal$/.test(name))) {
    try { fs.unlinkSync(path.join(dir, file)); } catch { /* beste poging */ }
  }
  const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-');
  const dest = path.join(dir, `eva-${stamp}.db`);
  // better-sqlite3 kopieert standaard 100 pagina's per setImmediate. Bij Eva's
  // database hield dat de hoofdthread minutenlang warm. Grotere blokken maken
  // dezelfde consistente SQLite-back-up in veel minder scheduler-rondes.
  await db.backup(dest, { progress: () => 4096 });
  try { fs.chmodSync(dest, 0o600); } catch { /* beste poging */ }
  // Hou de laatste 10 automatische back-ups. Handmatige `eva-before-*` snapshots
  // hebben een ander doel en worden hier nooit stilletjes verwijderd.
  const backups = fs.readdirSync(dir).filter((file) => DAILY_BACKUP_RE.test(file)).sort();
  for (const oud of backups.slice(0, -10)) {
    try { fs.unlinkSync(path.join(dir, oud)); } catch { /* */ }
  }
  return dest;
}

// ---------- ruwe toegang voor associatief geheugen (embeddings) ----------

const updateMemoryEmbedding = db.prepare('UPDATE memories SET embedding = ? WHERE id = ?');
const updateMemoryEmbeddings = db.transaction((rows) => {
  for (const row of rows) updateMemoryEmbedding.run(row.embedding, row.id);
});
const updateTimelineEmbedding = db.prepare('UPDATE timeline SET embedding = ? WHERE id = ?');
const updateTimelineEmbeddings = db.transaction((rows) => {
  for (const row of rows) updateTimelineEmbedding.run(row.embedding, row.id);
});

export function setMemoryEmbedding(id, buf) {
  updateMemoryEmbedding.run(buf, id);
}

export function setMemoryEmbeddings(items = []) {
  const rows = items
    .map((item) => ({
      id: Number(item?.id),
      embedding: item?.embedding,
    }))
    .filter((item) => Number.isInteger(item.id) && item.id > 0 && Buffer.isBuffer(item.embedding));
  if (!rows.length) return 0;
  updateMemoryEmbeddings(rows);
  return rows.length;
}

export function setTimelineEmbedding(id, buf) {
  updateTimelineEmbedding.run(buf, id);
}

export function setTimelineEmbeddings(items = []) {
  const rows = items
    .map((item) => ({
      id: Number(item?.id),
      embedding: item?.embedding,
    }))
    .filter((item) => Number.isInteger(item.id) && item.id > 0 && Buffer.isBuffer(item.embedding));
  if (!rows.length) return 0;
  updateTimelineEmbeddings(rows);
  return rows.length;
}

export function recentTimelineChatRows(limit = 500) {
  return db.prepare(`SELECT id, ts, kind, content, meta, embedding
    FROM timeline
    WHERE kind IN ('chat_in', 'chat_out')
    ORDER BY id DESC LIMIT ?`).all(Math.max(1, Number(limit) || 500))
    .map((row) => {
      let meta = null;
      try { meta = row.meta ? JSON.parse(row.meta) : null; } catch { /* oude ongeldige meta negeren */ }
      return { ...row, meta };
    });
}

export function memoriesNeedingEmbedding(limit = 200) {
  const fetchLimit = Math.max(limit, Number(limit || 0) * 4);
  return safeMemoryRows(db.prepare('SELECT id, content, archived FROM memories WHERE embedding IS NULL AND COALESCE(archived,0)=0 LIMIT ?').all(fetchLimit), limit);
}

/** Alle herinneringen met hun embedding-blob (voor similariteits-zoeken). */
export function memoriesWithEmbeddings() {
  return safeMemoryRows(db.prepare('SELECT id, ts, category, person, content, importance, feeling, embedding, archived FROM memories WHERE embedding IS NOT NULL AND COALESCE(archived,0)=0').all());
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

export function memoriesForPerson(person, limit = 6) {
  const name = String(person || '').trim();
  if (!name) return [];
  return safeMemoryRows(db.prepare(`
    SELECT * FROM memories
    WHERE COALESCE(archived,0)=0
      AND lower(COALESCE(person,'')) = lower(?)
    ORDER BY importance DESC, ts DESC
    LIMIT ?
  `).all(name, Math.max(1, Math.min(20, Number(limit) || 6))), limit);
}

export function topMemories(limit = 15) {
  return safeMemoryRows(db.prepare('SELECT * FROM memories WHERE COALESCE(archived,0)=0 ORDER BY importance DESC, ts DESC LIMIT ?').all(Math.max(limit, limit * 4)), limit);
}

export function deleteMemory(id) {
  db.prepare('DELETE FROM memories WHERE id = ?').run(id);
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
      Number.isFinite(Number(cadenceHours)) ? Number(cadenceHours) : null,
      Number.isFinite(Number(priority)) ? Number(priority) : null,
    );
  return Number(info.lastInsertRowid);
}

export function ensureAspirationDirection({
  goalKey,
  text,
  why,
  beneficiary = 'het gezin',
  evidence,
  nextStep,
  cadenceHours = 4,
  priority = 0.72,
} = {}) {
  const key = String(goalKey || '').trim();
  if (!key || !String(text || '').trim() || !String(evidence || '').trim() || !String(nextStep || '').trim()) {
    return null;
  }
  const existing = db.prepare('SELECT * FROM aspirations WHERE goal_key=?').get(key);
  if (!existing) {
    const id = addAspiration({
      text, why, kind: 'development', goalKey: key, beneficiary, evidence,
      nextStep, cadenceHours, priority, touched: false,
    });
    return db.prepare('SELECT * FROM aspirations WHERE id=?').get(id) || null;
  }
  db.prepare(`UPDATE aspirations SET text=?,why=?,kind='development',beneficiary=?,evidence=?,
    next_step=?,cadence_hours=?,priority=? WHERE id=?`).run(
    text, why, beneficiary, evidence, nextStep,
    Math.max(1, Number(cadenceHours) || 4),
    Math.max(0, Math.min(1, Number(priority) || 0.72)),
    existing.id,
  );
  return db.prepare('SELECT * FROM aspirations WHERE id=?').get(existing.id) || null;
}

export function activeAspirations() {
  return db.prepare("SELECT * FROM aspirations WHERE status = 'actief' ORDER BY last_touched ASC").all();
}

export function allAspirations(limit = 50) {
  return db.prepare('SELECT * FROM aspirations ORDER BY created_ts DESC LIMIT ?').all(limit);
}

export function touchAspiration(id, progress = null) {
  if (progress) db.prepare('UPDATE aspirations SET last_touched = ?, progress = ? WHERE id = ?').run(isoNow(), progress, id);
  else db.prepare('UPDATE aspirations SET last_touched = ? WHERE id = ?').run(isoNow(), id);
}

export function closeAspiration(id, status = 'volbracht') {
  db.prepare('UPDATE aspirations SET status = ?, closed_ts = ? WHERE id = ?').run(status, isoNow(), id);
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
    db.prepare(`DELETE FROM timeline WHERE kind IN ('system','heartbeat') AND ts < ?`).run(grens);
  } catch { /* onderhoud is niet kritiek */ }
}

export default db;
