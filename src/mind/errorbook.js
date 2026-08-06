// Eva's foutenboek.
//
// Niet elke fout moet meteen code worden. Eerst moet Eva kunnen zeggen:
// wat ging er mis, waarom, wat was het effect, en hoe voorkom ik herhaling?
// Dit boek bewaart die lus duurzaam en maakt fouten later bruikbaar als ervaring.
import db, { logTimeline } from '../db.js';
import { isoNow, tekstOverlap } from '../util.js';

db.exec(`
CREATE TABLE IF NOT EXISTS error_book (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  updated_ts TEXT NOT NULL,
  failure_key TEXT,
  status TEXT DEFAULT 'open',
  source TEXT,
  severity TEXT DEFAULT 'laag',
  area TEXT DEFAULT 'algemeen',
  trigger TEXT,
  mistake TEXT NOT NULL,
  effect TEXT,
  cause TEXT,
  prevention TEXT,
  evidence TEXT,
  attempts INTEGER DEFAULT 0,
  occurrences INTEGER DEFAULT 1,
  result TEXT
);
CREATE INDEX IF NOT EXISTS idx_error_book_status ON error_book(status, updated_ts);
CREATE INDEX IF NOT EXISTS idx_error_book_area ON error_book(area);
`);

try {
  const columns = new Set(db.prepare('PRAGMA table_info(error_book)').all().map((column) => column.name));
  if (!columns.has('occurrences')) db.exec('ALTER TABLE error_book ADD COLUMN occurrences INTEGER DEFAULT 1');
  if (!columns.has('failure_key')) db.exec('ALTER TABLE error_book ADD COLUMN failure_key TEXT');
} catch { /* migratie mag een gesprek niet blokkeren */ }
try { db.exec('CREATE INDEX IF NOT EXISTS idx_error_book_failure_key ON error_book(failure_key, status)'); } catch { /* optionele index */ }

function clean(v, n = 900) {
  return String(v ?? '').replace(/\s+/g, ' ').trim().slice(0, n);
}

function rowOut(r) {
  return r ? { ...r, attempts: Number(r.attempts || 0), occurrences: Number(r.occurrences || 1) } : null;
}

function activeRows() {
  return db.prepare("SELECT * FROM error_book WHERE status IN ('open','in_onderzoek') ORDER BY updated_ts DESC LIMIT 120").all().map(rowOut);
}

function similar(a, b) {
  return tekstOverlap(`${a.area} ${a.mistake} ${a.cause} ${a.prevention}`, `${b.area} ${b.mistake} ${b.cause} ${b.prevention}`) >= 0.58;
}

export function noteError({
  key = '',
  source = 'eva',
  severity = 'laag',
  area = 'algemeen',
  trigger = '',
  mistake,
  effect = '',
  cause = '',
  prevention = '',
  evidence = '',
} = {}) {
  const item = {
    failure_key: clean(key, 120).toLowerCase(),
    source: clean(source, 80) || 'eva',
    severity: clean(severity, 40) || 'laag',
    area: clean(area, 80) || 'algemeen',
    trigger: clean(trigger, 1000),
    mistake: clean(mistake, 900),
    effect: clean(effect, 900),
    cause: clean(cause, 900),
    prevention: clean(prevention, 900),
    evidence: clean(evidence, 900),
  };
  if (!item.mistake) return { ok: false, reden: 'foutomschrijving ontbreekt' };

  const existing = activeRows().find((x) => (
    item.failure_key && x.failure_key === item.failure_key
  )) || activeRows().find((x) => similar(x, item));
  if (existing) {
    db.prepare(`UPDATE error_book
      SET updated_ts = ?, severity = ?, occurrences = coalesce(occurrences, 1) + 1,
          failure_key = coalesce(nullif(?, ''), failure_key),
          trigger = coalesce(nullif(?, ''), trigger),
          effect = coalesce(nullif(?, ''), effect),
          cause = coalesce(nullif(?, ''), cause),
          prevention = coalesce(nullif(?, ''), prevention),
          evidence = coalesce(nullif(?, ''), evidence)
      WHERE id = ?`)
      .run(isoNow(), item.severity, item.failure_key, item.trigger, item.effect, item.cause, item.prevention, item.evidence, existing.id);
    return { ok: true, nieuw: false, error: rowOut(db.prepare('SELECT * FROM error_book WHERE id = ?').get(existing.id)) };
  }

  const now = isoNow();
  const info = db.prepare(`INSERT INTO error_book
    (ts, updated_ts, failure_key, status, source, severity, area, trigger, mistake, effect, cause, prevention, evidence)
    VALUES (?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?)`)
    .run(now, now, item.failure_key || null, item.source, item.severity, item.area, item.trigger, item.mistake, item.effect, item.cause, item.prevention, item.evidence);
  const created = rowOut(db.prepare('SELECT * FROM error_book WHERE id = ?').get(info.lastInsertRowid));
  logTimeline('innerlijk', `Eva zette iets in haar foutenboek: ${created.mistake}`, { errorBook: true, id: created.id, area: created.area, severity: created.severity });
  return { ok: true, nieuw: true, error: created };
}

export function listErrors({ status = null, limit = 12 } = {}) {
  const lim = Math.min(Math.max(Number(limit) || 12, 1), 100);
  if (status) return db.prepare('SELECT * FROM error_book WHERE status = ? ORDER BY updated_ts DESC LIMIT ?').all(status, lim).map(rowOut);
  return db.prepare('SELECT * FROM error_book ORDER BY updated_ts DESC LIMIT ?').all(lim).map(rowOut);
}

export function updateError(id, {
  status = null,
  result = null,
  prevention = null,
  evidence = null,
} = {}) {
  const row = db.prepare('SELECT * FROM error_book WHERE id = ?').get(id);
  if (!row) return { ok: false, reden: 'onbekende fout' };
  const allowed = new Set(['open', 'in_onderzoek', 'geleerd', 'hersteld', 'verworpen']);
  const next = status && allowed.has(String(status)) ? String(status) : row.status;
  const attempts = (next === 'in_onderzoek' || next === 'geleerd' || next === 'hersteld')
    ? Number(row.attempts || 0) + 1
    : Number(row.attempts || 0);
  db.prepare(`UPDATE error_book
    SET status = ?, updated_ts = ?, attempts = ?,
        result = coalesce(?, result),
        prevention = coalesce(?, prevention),
        evidence = coalesce(?, evidence)
    WHERE id = ?`)
    .run(next, isoNow(), attempts, result == null ? null : clean(result, 1000),
      prevention == null ? null : clean(prevention, 1000),
      evidence == null ? null : clean(evidence, 1000), id);
  const updated = rowOut(db.prepare('SELECT * FROM error_book WHERE id = ?').get(id));
  if (next === 'geleerd' || next === 'hersteld') {
    logTimeline('innerlijk', `Foutenboek ${next}: ${updated.mistake}`, { errorBook: true, id: updated.id, status: next });
  }
  return { ok: true, error: updated };
}

export function errorBookStatus() {
  const rows = db.prepare('SELECT status, COUNT(*) c FROM error_book GROUP BY status').all();
  const counts = Object.fromEntries(rows.map((r) => [r.status, r.c]));
  const open = (counts.open || 0) + (counts.in_onderzoek || 0);
  const recent = listErrors({ limit: 8 }).map((e) => ({
    id: e.id,
    status: e.status,
    severity: e.severity,
    area: e.area,
    mistake: e.mistake,
    attempts: e.attempts,
    updated_ts: e.updated_ts,
  }));
  return { counts, open, recent };
}

export function errorContext(seed = '', { limit = 4 } = {}) {
  const text = clean(seed, 500);
  const rows = activeRows();
  if (!rows.length) return '';
  const relevant = rows
    .map((r) => ({
      ...r,
      score: text ? tekstOverlap(text, `${r.area} ${r.trigger} ${r.mistake} ${r.cause} ${r.prevention}`) : 0.1,
    }))
    .filter((r) => r.score >= 0.08)
    .sort((a, b) => b.score - a.score)
    .slice(0, limit);
  if (!relevant.length) return '';
  return relevant.map((e) =>
    `- #${e.id} [${e.area}/${e.severity}] ${e.mistake}${e.prevention ? `; voorkom door: ${e.prevention}` : ''}`
  ).join('\n');
}

export function formatError(e) {
  if (!e) return '';
  return [
    `#${e.id} [${e.status}/${e.area}/${e.severity}] ${e.mistake}`,
    e.effect ? `Effect: ${e.effect}` : '',
    e.cause ? `Oorzaak: ${e.cause}` : '',
    e.prevention ? `Volgende keer: ${e.prevention}` : '',
    e.evidence ? `Bewijs: ${e.evidence}` : '',
    e.result ? `Resultaat: ${e.result}` : '',
  ].filter(Boolean).join('\n');
}
