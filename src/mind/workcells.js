import { db } from '../db.js';
import { searchWeb } from '../brain/llm.js';

const CELL_STATUSES = new Set(['pending', 'running', 'partial', 'completed', 'failed']);
const MAX_STREAMS = 20;
const DEFAULT_CONCURRENCY = 3;
const MAX_CONCURRENCY = 6;
const activeCellRuns = new Map();

db.exec(`
CREATE TABLE IF NOT EXISTS eva_work_cells (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_ts TEXT NOT NULL,
  updated_ts TEXT NOT NULL,
  started_ts TEXT,
  finished_ts TEXT,
  title TEXT NOT NULL,
  objective TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  concurrency INTEGER NOT NULL DEFAULT 3,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  last_error TEXT
);
CREATE INDEX IF NOT EXISTS idx_eva_work_cells_status
  ON eva_work_cells(status, updated_ts);

CREATE TABLE IF NOT EXISTS eva_work_cell_streams (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  cell_id INTEGER NOT NULL,
  ordinal INTEGER NOT NULL,
  created_ts TEXT NOT NULL,
  updated_ts TEXT NOT NULL,
  started_ts TEXT,
  finished_ts TEXT,
  title TEXT NOT NULL,
  query TEXT NOT NULL,
  primary_query TEXT NOT NULL,
  fallback_query TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  attempt_count INTEGER NOT NULL DEFAULT 0,
  research_phase TEXT NOT NULL DEFAULT 'primary',
  result_text TEXT,
  evidence_json TEXT NOT NULL DEFAULT '[]',
  sources_json TEXT NOT NULL DEFAULT '[]',
  errors_json TEXT NOT NULL DEFAULT '[]',
  last_error TEXT,
  UNIQUE(cell_id, ordinal),
  FOREIGN KEY(cell_id) REFERENCES eva_work_cells(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_eva_work_cell_streams_cell
  ON eva_work_cell_streams(cell_id, status, ordinal);

CREATE TABLE IF NOT EXISTS eva_work_cell_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  cell_id INTEGER NOT NULL,
  stream_id INTEGER NOT NULL,
  attempt INTEGER NOT NULL,
  phase TEXT NOT NULL,
  query TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'running',
  started_ts TEXT NOT NULL,
  finished_ts TEXT,
  result_text TEXT,
  evidence_json TEXT NOT NULL DEFAULT '{}',
  sources_json TEXT NOT NULL DEFAULT '[]',
  error TEXT,
  FOREIGN KEY(cell_id) REFERENCES eva_work_cells(id) ON DELETE CASCADE,
  FOREIGN KEY(stream_id) REFERENCES eva_work_cell_streams(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_eva_work_cell_runs_stream
  ON eva_work_cell_runs(stream_id, id);
CREATE INDEX IF NOT EXISTS idx_eva_work_cell_runs_cell
  ON eva_work_cell_runs(cell_id, id);
`);

const nowIso = () => new Date().toISOString();

function clean(value, limit = 2_000) {
  return String(value ?? '').replace(/\s+/g, ' ').trim().slice(0, limit);
}

function cleanText(value, limit = 40_000) {
  return String(value ?? '').trim().slice(0, limit);
}

function parseJson(value, fallback) {
  try {
    const parsed = JSON.parse(String(value ?? ''));
    return parsed == null ? fallback : parsed;
  } catch {
    return fallback;
  }
}

function concurrencyLimit(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return DEFAULT_CONCURRENCY;
  return Math.max(1, Math.min(MAX_CONCURRENCY, Math.floor(number)));
}

function uniqueSources(values) {
  const found = new Set();
  for (const value of values.flat(Infinity)) {
    const source = clean(value, 2_000);
    if (/^https?:\/\//i.test(source)) found.add(source);
  }
  return [...found].slice(0, 100);
}

function errorText(error) {
  return clean(error?.message || error || 'onbekende onderzoeksfout', 4_000);
}

function primaryResearchQuery(objective, query) {
  return [
    `Werkstroom binnen doel: ${objective}`,
    `Onderzoeksvraag: ${query}`,
    'Gebruik in deze eerste ronde uitsluitend officiële documentatie, oorspronkelijke publicaties, overheidsbronnen, leveranciersdocumentatie, repositories van de maker of andere directe primaire bronnen.',
    'Open de bronnen rechtstreeks. Scheid bewezen feiten, gevolgtrekkingen en ontbrekend bewijs. Geef directe bronlinks en zeg expliciet wanneer primaire bronnen onvoldoende of onderling onduidelijk zijn.',
  ].join('\n');
}

function fallbackResearchQuery(objective, query) {
  return [
    `Werkstroom binnen doel: ${objective}`,
    `Onderzoeksvraag: ${query}`,
    'De officiële/ primaire ronde was onvoldoende, onduidelijk of liep vast. Zoek nu aantoonbaar breder.',
    'Gebruik betrouwbare onafhankelijke vakbronnen, issue trackers, technische communityforums en waar nuttig ervaringsbronnen zoals Reddit of gebruikersdiscussies.',
    'Maak duidelijk wat onafhankelijke bevestiging, praktijkervaring, tegenspraak of slechts een onbevestigde melding is. Geef directe bronlinks en trek geen sterkere conclusie dan het gezamenlijke bewijs draagt.',
  ].join('\n');
}

function normalizeStream(value, index, objective) {
  const item = typeof value === 'string' ? { query: value } : value;
  if (!item || typeof item !== 'object' || Array.isArray(item)) {
    throw new Error(`werkstroom ${index + 1} moet tekst of een object zijn`);
  }
  const query = clean(item.query || item.question || item.objective, 4_000);
  const title = clean(item.title || query, 240);
  if (!query || !title) throw new Error(`werkstroom ${index + 1} mist een titel of onderzoeksvraag`);
  return {
    ordinal: index + 1,
    title,
    query,
    // Ook een door de aanroeper aangescherpte deelvraag blijft binnen de vaste
    // bronvolgorde: eerst primair/officieel, pas daarna de bredere controle.
    primaryQuery: primaryResearchQuery(
      objective,
      cleanText(item.primaryQuery, 4_000) || query,
    ),
    fallbackQuery: fallbackResearchQuery(
      objective,
      cleanText(item.fallbackQuery, 4_000) || query,
    ),
  };
}

function streamOut(row) {
  if (!row) return null;
  return {
    ...row,
    attempt_count: Number(row.attempt_count || 0),
    evidence: parseJson(row.evidence_json, []),
    sources: parseJson(row.sources_json, []),
    errors: parseJson(row.errors_json, []),
  };
}

function runOut(row) {
  if (!row) return null;
  return {
    ...row,
    attempt: Number(row.attempt || 0),
    evidence: parseJson(row.evidence_json, {}),
    sources: parseJson(row.sources_json, []),
  };
}

function cellRow(id) {
  return db.prepare('SELECT * FROM eva_work_cells WHERE id=?').get(Number(id)) || null;
}

function streamRows(cellId) {
  return db.prepare('SELECT * FROM eva_work_cell_streams WHERE cell_id=? ORDER BY ordinal')
    .all(Number(cellId));
}

function cellOut(row, { includeStreams = false, includeRuns = false } = {}) {
  if (!row) return null;
  const cell = {
    ...row,
    concurrency: concurrencyLimit(row.concurrency),
    metadata: parseJson(row.metadata_json, {}),
  };
  if (includeStreams) cell.streams = streamRows(row.id).map(streamOut);
  if (includeRuns) {
    cell.runs = db.prepare('SELECT * FROM eva_work_cell_runs WHERE cell_id=? ORDER BY id')
      .all(Number(row.id)).map(runOut);
  }
  return cell;
}

function researchAssessment(result) {
  const text = cleanText(result?.text, 40_000);
  const sources = uniqueSources(result?.sources || []);
  const unclear = /\b(?:onvoldoende|onduidelijk|onbevestigd|niet\s+(?:hard\s+)?bevestigen|geen\s+(?:actuele\s+)?(?:directe|offici[eë]le|primaire)\s+bron|bronnen\s+spreken\s+elkaar\s+tegen|unclear|insufficient|unconfirmed|could\s+not\s+confirm|no\s+(?:official|primary)\s+source|conflicting\s+sources)\b/i.test(text);
  const insufficient = result?.searched !== true || sources.length === 0 || text.length < 200 || unclear;
  return {
    text,
    sources,
    searched: result?.searched === true,
    searchCalls: Number(result?.searchCalls || 0),
    unclear,
    insufficient,
  };
}

function createRun(stream, phase, query) {
  const ts = nowIso();
  const info = db.prepare(`INSERT INTO eva_work_cell_runs
    (cell_id,stream_id,attempt,phase,query,status,started_ts)
    VALUES (?,?,?,?,?,'running',?)`).run(
    stream.cell_id,
    stream.id,
    Number(stream.attempt_count || 0),
    phase,
    query,
    ts,
  );
  return Number(info.lastInsertRowid);
}

function finishRun(runId, { status, result = null, assessment = null, error = null }) {
  db.prepare(`UPDATE eva_work_cell_runs SET
    status=?,finished_ts=?,result_text=?,evidence_json=?,sources_json=?,error=?
    WHERE id=?`).run(
    status,
    nowIso(),
    result == null ? null : cleanText(result, 40_000),
    JSON.stringify(assessment || {}),
    JSON.stringify(uniqueSources(assessment?.sources || [])),
    error == null ? null : errorText(error),
    Number(runId),
  );
}

function appendStreamEvidence(streamId, { evidence = null, sources = [], error = null, phase }) {
  const current = streamOut(db.prepare('SELECT * FROM eva_work_cell_streams WHERE id=?').get(Number(streamId)));
  if (!current) return;
  const evidenceRows = [...current.evidence];
  const errors = [...current.errors];
  if (evidence) evidenceRows.push({ ts: nowIso(), phase, ...evidence });
  if (error) errors.push({ ts: nowIso(), phase, error: errorText(error) });
  db.prepare(`UPDATE eva_work_cell_streams SET
    updated_ts=?,research_phase=?,evidence_json=?,sources_json=?,errors_json=?,last_error=?
    WHERE id=?`).run(
    nowIso(),
    phase,
    JSON.stringify(evidenceRows).slice(0, 120_000),
    JSON.stringify(uniqueSources(current.sources, sources)),
    JSON.stringify(errors).slice(0, 40_000),
    error == null ? current.last_error : errorText(error),
    Number(streamId),
  );
}

async function performResearchPhase(stream, phase, query, options) {
  const runId = createRun(stream, phase, query);
  try {
    const result = await searchWeb(query, {
      cacheKey: `eva-workcell-${phase}`,
      budgetClass: options.budgetClass || 'autonomous',
      timeoutMs: options.timeoutMs,
      ...(options.model ? { model: options.model } : {}),
      ...(options.effort ? { effort: options.effort } : {}),
    });
    const assessment = researchAssessment(result);
    finishRun(runId, {
      status: 'completed',
      result: assessment.text,
      assessment,
    });
    appendStreamEvidence(stream.id, {
      phase,
      evidence: {
        text: assessment.text,
        searched: assessment.searched,
        search_calls: assessment.searchCalls,
        insufficient: assessment.insufficient,
        unclear: assessment.unclear,
        sources: assessment.sources,
      },
      sources: assessment.sources,
    });
    return assessment;
  } catch (error) {
    finishRun(runId, { status: 'failed', error });
    appendStreamEvidence(stream.id, { phase, error });
    return {
      text: '',
      sources: [],
      searched: false,
      searchCalls: 0,
      unclear: true,
      insufficient: true,
      error: errorText(error),
    };
  }
}

function combinedResult(primary, fallback = null) {
  const parts = [];
  if (primary?.text) parts.push(`Primaire bronnen:\n${primary.text}`);
  if (fallback?.text) parts.push(`Bredere controle en praktijkbronnen:\n${fallback.text}`);
  return parts.join('\n\n').trim();
}

async function executeClaimedStream(stream, options) {
  const primary = await performResearchPhase(stream, 'primary', stream.primary_query, options);
  let fallback = null;
  if (primary.insufficient) {
    fallback = await performResearchPhase(stream, 'fallback', stream.fallback_query, options);
  }

  const result = combinedResult(primary, fallback);
  const sources = uniqueSources(primary.sources, fallback?.sources || []);
  // De fallback mag een negatieve of onzekere conclusie opleveren. Die is nog
  // steeds bruikbaar bewijs wanneer zij inhoud en een rechtstreeks bronspoor heeft.
  const usableFallback = fallback && fallback.text.length >= 160 && fallback.sources.length > 0;
  const completed = Boolean(result) && sources.length > 0
    && (!primary.insufficient || usableFallback);
  const errors = [primary.error, fallback?.error].filter(Boolean);
  const lastError = completed
    ? null
    : errors.at(-1) || 'Primaire en bredere zoekronde leverden onvoldoende controleerbaar bewijs op.';
  db.prepare(`UPDATE eva_work_cell_streams SET
    status=?,updated_ts=?,finished_ts=?,research_phase=?,result_text=?,sources_json=?,last_error=?
    WHERE id=?`).run(
    completed ? 'completed' : 'failed',
    nowIso(),
    nowIso(),
    fallback ? 'fallback' : 'primary',
    result || null,
    JSON.stringify(sources),
    lastError,
    Number(stream.id),
  );
  return streamOut(db.prepare('SELECT * FROM eva_work_cell_streams WHERE id=?').get(Number(stream.id)));
}

function refreshCellStatus(cellId) {
  const cell = cellRow(cellId);
  if (!cell) return null;
  const rows = streamRows(cellId);
  const counts = rows.reduce((out, stream) => {
    out[stream.status] = (out[stream.status] || 0) + 1;
    return out;
  }, {});
  let status = 'pending';
  if (counts.running) status = 'running';
  else if ((counts.completed || 0) === rows.length && rows.length) status = 'completed';
  else if ((counts.failed || 0) === rows.length && rows.length) status = 'failed';
  else if (counts.completed || counts.failed) status = 'partial';
  const terminal = ['completed', 'failed'].includes(status);
  const errors = rows.filter((stream) => stream.last_error).map((stream) => stream.last_error);
  db.prepare(`UPDATE eva_work_cells SET
    status=?,updated_ts=?,started_ts=coalesce(started_ts,?),finished_ts=?,last_error=?
    WHERE id=?`).run(
    status,
    nowIso(),
    status === 'pending' ? null : nowIso(),
    terminal ? nowIso() : null,
    errors.length ? clean(errors.at(-1), 4_000) : null,
    Number(cellId),
  );
  return getWorkCell(cellId);
}

const createCellTransaction = db.transaction(({ title, objective, concurrency, metadata, streams }) => {
  const ts = nowIso();
  const info = db.prepare(`INSERT INTO eva_work_cells
    (created_ts,updated_ts,title,objective,status,concurrency,metadata_json)
    VALUES (?,?,?,?,'pending',?,?)`).run(
    ts,
    ts,
    title,
    objective,
    concurrency,
    JSON.stringify(metadata || {}).slice(0, 20_000),
  );
  const cellId = Number(info.lastInsertRowid);
  const insert = db.prepare(`INSERT INTO eva_work_cell_streams
    (cell_id,ordinal,created_ts,updated_ts,title,query,primary_query,fallback_query,status)
    VALUES (?,?,?,?,?,?,?,?,'pending')`);
  for (const stream of streams) {
    insert.run(
      cellId,
      stream.ordinal,
      ts,
      ts,
      stream.title,
      stream.query,
      stream.primaryQuery,
      stream.fallbackQuery,
    );
  }
  return cellId;
});

export function createWorkCell({
  title,
  objective,
  streams,
  concurrency = DEFAULT_CONCURRENCY,
  metadata = {},
} = /** @type {any} */ ({})) {
  const cleanTitle = clean(title, 240);
  const cleanObjective = clean(objective, 4_000);
  if (!cleanTitle || !cleanObjective) throw new Error('een werkcel vereist een titel en concreet doel');
  if (!Array.isArray(streams) || streams.length < 1) {
    throw new Error('een werkcel vereist minstens één werkstroom');
  }
  if (streams.length > MAX_STREAMS) {
    throw new Error(`een werkcel ondersteunt maximaal ${MAX_STREAMS} werkstromen`);
  }
  const normalized = streams.map((stream, index) => normalizeStream(stream, index, cleanObjective));
  const id = createCellTransaction({
    title: cleanTitle,
    objective: cleanObjective,
    concurrency: concurrencyLimit(concurrency),
    metadata,
    streams: normalized,
  });
  return getWorkCell(id);
}

export function listWorkCells({ statuses = null, limit = 40 } = {}) {
  const selected = Array.isArray(statuses)
    ? statuses.map((status) => clean(status, 20)).filter((status) => CELL_STATUSES.has(status))
    : [];
  const where = selected.length ? `WHERE c.status IN (${selected.map(() => '?').join(',')})` : '';
  return db.prepare(`SELECT c.*,
      count(s.id) AS stream_count,
      sum(CASE WHEN s.status='pending' THEN 1 ELSE 0 END) AS pending_count,
      sum(CASE WHEN s.status='running' THEN 1 ELSE 0 END) AS running_count,
      sum(CASE WHEN s.status='completed' THEN 1 ELSE 0 END) AS completed_count,
      sum(CASE WHEN s.status='failed' THEN 1 ELSE 0 END) AS failed_count
    FROM eva_work_cells c
    LEFT JOIN eva_work_cell_streams s ON s.cell_id=c.id
    ${where}
    GROUP BY c.id
    ORDER BY c.updated_ts DESC
    LIMIT ?`).all(...selected, Math.max(1, Math.min(100, Number(limit) || 40)))
    .map((row) => ({
      ...cellOut(row),
      stream_count: Number(row.stream_count || 0),
      pending_count: Number(row.pending_count || 0),
      running_count: Number(row.running_count || 0),
      completed_count: Number(row.completed_count || 0),
      failed_count: Number(row.failed_count || 0),
    }));
}

export function getWorkCell(id, { includeRuns = true } = {}) {
  return cellOut(cellRow(id), { includeStreams: true, includeRuns });
}

function claimStreams(cellId, { retryFailed = false } = {}) {
  const eligible = retryFailed ? ['pending', 'failed'] : ['pending'];
  return db.transaction(() => {
    const rows = db.prepare(`SELECT * FROM eva_work_cell_streams
      WHERE cell_id=? AND status IN (${eligible.map(() => '?').join(',')})
      ORDER BY ordinal`).all(Number(cellId), ...eligible);
    const claimed = [];
    const ts = nowIso();
    for (const row of rows) {
      const changed = db.prepare(`UPDATE eva_work_cell_streams SET
        status='running',updated_ts=?,started_ts=?,finished_ts=NULL,
        attempt_count=attempt_count+1,research_phase='primary',last_error=NULL
        WHERE id=? AND status=?`).run(ts, ts, row.id, row.status);
      if (changed.changes) {
        claimed.push(streamOut(db.prepare('SELECT * FROM eva_work_cell_streams WHERE id=?').get(row.id)));
      }
    }
    if (claimed.length) {
      db.prepare(`UPDATE eva_work_cells SET
        status='running',updated_ts=?,started_ts=coalesce(started_ts,?),finished_ts=NULL,last_error=NULL
        WHERE id=?`).run(ts, ts, Number(cellId));
    }
    return claimed;
  })();
}

function failUnexpectedStream(stream, reason) {
  appendStreamEvidence(stream.id, { phase: 'internal', error: reason });
  db.prepare(`UPDATE eva_work_cell_streams SET
    status='failed',updated_ts=?,finished_ts=?,last_error=? WHERE id=?`).run(
    nowIso(),
    nowIso(),
    errorText(reason),
    Number(stream.id),
  );
}

async function runWorkCellInternal(id, {
  concurrency = null,
  retryFailed = false,
  budgetClass = 'autonomous',
  timeoutMs = undefined,
  model = null,
  effort = null,
} = {}) {
  const cell = cellRow(id);
  if (!cell) throw new Error(`werkcel ${id} bestaat niet`);
  const limit = concurrencyLimit(concurrency ?? cell.concurrency);
  const claimed = claimStreams(cell.id, { retryFailed });
  const options = { budgetClass, timeoutMs, model, effort };

  for (let offset = 0; offset < claimed.length; offset += limit) {
    const batch = claimed.slice(offset, offset + limit);
    const settled = await Promise.allSettled(
      batch.map((stream) => executeClaimedStream(stream, options)),
    );
    settled.forEach((result, index) => {
      if (result.status === 'rejected') failUnexpectedStream(batch[index], result.reason);
    });
    refreshCellStatus(cell.id);
  }

  return refreshCellStatus(cell.id);
}

export function runWorkCell(id, options = {}) {
  const key = Number(id);
  if (!Number.isInteger(key) || key < 1) throw new Error('een geldige werkcel-id is vereist');
  const active = activeCellRuns.get(key);
  if (active) return active;
  const run = runWorkCellInternal(key, options).finally(() => {
    if (activeCellRuns.get(key) === run) activeCellRuns.delete(key);
  });
  activeCellRuns.set(key, run);
  return run;
}

export async function resumeWorkCell(id, options = {}) {
  const cell = cellRow(id);
  if (!cell) throw new Error(`werkcel ${id} bestaat niet`);
  const active = activeCellRuns.get(Number(cell.id));
  if (active) return active;
  // Een proces kan tussen claim en opslag stoppen. Zo'n oude running-status is
  // hervatbaar, maar de onderbreking blijft als foutbewijs in de stream staan.
  const interrupted = streamRows(cell.id).filter((stream) => stream.status === 'running');
  for (const stream of interrupted) {
    const reason = 'Vorige uitvoering werd onderbroken voordat de werkstroom werd afgerond.';
    appendStreamEvidence(stream.id, { phase: 'resume', error: reason });
    db.prepare(`UPDATE eva_work_cell_streams SET
      status='failed',updated_ts=?,finished_ts=?,last_error=? WHERE id=?`).run(
      nowIso(),
      nowIso(),
      reason,
      stream.id,
    );
  }
  return runWorkCell(cell.id, { ...options, retryFailed: true });
}

export const WORKCELL_LIMITS = Object.freeze({
  maxStreams: MAX_STREAMS,
  defaultConcurrency: DEFAULT_CONCURRENCY,
  maxConcurrency: MAX_CONCURRENCY,
});
