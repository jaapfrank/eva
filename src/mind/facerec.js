// Echte gezichtsherkenning — nu via de lokale brein-service (ArcFace, 512-dim).
//
// ArcFace draait lokaal op de GPU. Dit is bewust de enige embeddingruimte: een
// tweede, onverenigbare fallback maakte een tijdelijke service-uitval vroeger tot
// foutief vergeten of vervuilde gezichtsvectoren.
import db, { logTimeline, kvGet, kvSet } from '../db.js';
import { vecToBuf, bufToVec } from './embeddings.js';
import { brainPost, brainServiceStatus } from './localbrain.js';

// ArcFace-scores uit echte buitencamera's zijn veel lager dan nette portretfoto's:
// oude live-data zat vaak rond 0.20-0.40 voor dezelfde persoon. Daarom werken we met
// twee regimes: legacy-rijen blijven streng, nieuwe kwaliteitsrijen mogen lager mits
// ze duidelijk boven de runner-up liggen.
const DREMPEL_ARC_LEGACY = 0.38;
const DREMPEL_ARC_GOED = 0.30;
const MARGE_ARC = 0.065;
const MIN_LEER_SCORE = 0.58;
const MIN_LEER_OPPERVLAK = 1200;
const MIN_HERKEN_OPPERVLAK = 700;
const AMBIGUOUS_AREA_RATIO = 0.68;

db.exec(`CREATE TABLE IF NOT EXISTS faces (
  id INTEGER PRIMARY KEY AUTOINCREMENT, person TEXT NOT NULL, vec BLOB NOT NULL, ts TEXT
);`);
try { db.exec('ALTER TABLE faces ADD COLUMN backend TEXT'); } catch { /* kolom bestaat al */ }
try { db.exec('ALTER TABLE faces ADD COLUMN quality REAL'); } catch { /* kolom bestaat al */ }
try { db.exec('ALTER TABLE faces ADD COLUMN det_score REAL'); } catch { /* kolom bestaat al */ }
try { db.exec('ALTER TABLE faces ADD COLUMN oppervlak INTEGER'); } catch { /* kolom bestaat al */ }
try { db.exec('ALTER TABLE faces ADD COLUMN active INTEGER DEFAULT 1'); } catch { /* kolom bestaat al */ }
try { db.exec('ALTER TABLE faces ADD COLUMN note TEXT'); } catch { /* kolom bestaat al */ }

let serviceStatus = 'onbekend'; // onbekend | ok | onbereikbaar

function cosine(a, b) {
  if (!a || !b || a.length !== b.length) return -1;
  let dot = 0, na = 0, nb = 0;
  for (let i = 0; i < a.length; i++) { dot += a[i] * b[i]; na += a[i] * a[i]; nb += b[i] * b[i]; }
  return dot / (Math.sqrt(na) * Math.sqrt(nb) || 1);
}

// ---------- primair: brein-service (ArcFace op de GPU) ----------
async function serviceFaces(buffer) {
  // Een transiente hapering mag de consistente ArcFace-ruimte niet veranderen.
  // Daarom: kort retryen (8s, dan nog eens) en daarna dit frame overslaan.
  for (let poging = 0; poging < 2; poging++) {
    try {
      const j = await brainPost('/faces_b64', { image_b64: buffer.toString('base64') }, { kind: 'faces_b64', timeoutMs: 8000 });
      serviceStatus = 'ok';
      return (j.gezichten || [])
        .filter((f) => Array.isArray(f.embedding) && f.embedding.length === 512)
        .map((f) => ({ embedding: Float32Array.from(f.embedding), score: f.det_score || 0, oppervlak: f.oppervlak || 0, bbox: f.bbox || null, backend: 'arcface' }));
    } catch (error) {
      // Een 4xx van de bereikbare brein-service is een fout in dit frame, geen
      // service-uitval. Nogmaals dezelfde bytes sturen geeft alleen dubbel lawaai.
      if (error?.retryable === false) {
        serviceStatus = 'ok';
        return [];
      }
      if (poging === 0) { await new Promise((r) => setTimeout(r, 1200)); continue; }
      serviceStatus = 'onbereikbaar';
      return null;
    }
  }
  return null;
}

// ---------- publieke API ----------

async function detectFacesResult(buffer) {
  if (!buffer || !Number(buffer.length)) return { available: true, faces: [] };
  const faces = await serviceFaces(buffer);
  return faces === null
    ? { available: false, faces: [], error: 'breinservice tijdelijk niet bereikbaar' }
    : { available: true, faces };
}

/** Detecteer alle gezichten in een beeld; geef embeddings + score + grootte + backend. */
export async function detectFaces(buffer) {
  return (await detectFacesResult(buffer)).faces;
}

function faceQuality(face) {
  const score = Number(face?.score || 0);
  const area = Number(face?.oppervlak || 0);
  const areaScore = Math.min(1, Math.sqrt(Math.max(0, area) / 12000));
  return Number((score * areaScore).toFixed(3));
}

function isLearnableFace(face, faces = []) {
  if (!face) return { ok: false, reden: 'geen gezicht' };
  if (Number(face.score || 0) < MIN_LEER_SCORE) return { ok: false, reden: `detectiescore te laag (${Number(face.score || 0).toFixed(2)})` };
  if (Number(face.oppervlak || 0) < MIN_LEER_OPPERVLAK) return { ok: false, reden: `gezicht te klein (${face.oppervlak || 0}px)` };
  const tweede = faces.filter((f) => f !== face).sort((a, b) => b.oppervlak - a.oppervlak)[0];
  if (tweede && Number(tweede.oppervlak || 0) >= Number(face.oppervlak || 0) * AMBIGUOUS_AREA_RATIO) {
    return { ok: false, reden: 'meerdere vergelijkbaar prominente gezichten; naamkoppeling is ambigu' };
  }
  return { ok: true, quality: faceQuality(face) };
}

function faceSourceNote(sourceRef = '') {
  const source = String(sourceRef || '').replace(/[^a-zA-Z0-9_.:-]/g, '').slice(0, 180);
  return source ? `kwaliteitsgezicht;foto-ref=${source}` : 'kwaliteitsgezicht';
}

function deactivateFaceSource(note) {
  if (note === 'kwaliteitsgezicht') return 0;
  return Number(db.prepare(
    'UPDATE faces SET active=0 WHERE active=1 AND note=?',
  ).run(note).changes || 0);
}

/** Leer een gezicht: bewaar de embedding van het meest prominente gezicht onder de naam. */
export async function learnFace(person, buffer, { sourceRef = null, replaceSource = false } = {}) {
  const note = faceSourceNote(sourceRef);
  const existing = note === 'kwaliteitsgezicht'
    ? []
    : db.prepare('SELECT id,person FROM faces WHERE active=1 AND note=?').all(note);
  const same = existing.filter((row) => String(row.person).toLowerCase() === String(person).toLowerCase());
  const wrong = existing.filter((row) => String(row.person).toLowerCase() !== String(person).toLowerCase());
  if (same.length && !wrong.length) return true;
  if (same.length && wrong.length) {
    db.prepare(`UPDATE faces SET active=0 WHERE active=1 AND note=? AND lower(person)<>lower(?)`)
      .run(note, person);
    return true;
  }
  const detection = await detectFacesResult(buffer);
  if (!detection.available) {
    logTimeline('system', `Gezicht niet geleerd voor ${person}: ${detection.error}.`, {
      faceLearn: false,
      person,
      reden: detection.error,
    });
    return false;
  }
  const faces = detection.faces;
  if (!faces.length) {
    if (replaceSource) deactivateFaceSource(note);
    return false;
  }
  const beste = faces.sort((a, b) => b.oppervlak - a.oppervlak)[0];
  const leerbaar = isLearnableFace(beste, faces);
  if (!leerbaar.ok) {
    if (replaceSource) deactivateFaceSource(note);
    logTimeline('system', `Gezicht niet geleerd voor ${person}: ${leerbaar.reden}.`, { faceLearn: false, person, reden: leerbaar.reden });
    return false;
  }
  db.transaction(() => {
    deactivateFaceSource(note);
    db.prepare('INSERT INTO faces (person, vec, backend, ts, quality, det_score, oppervlak, active, note) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)')
      .run(person, vecToBuf(beste.embedding), beste.backend, new Date().toISOString(), leerbaar.quality, Number(beste.score || 0), Number(beste.oppervlak || 0), note);
  })();
  return true;
}

function acceptatieDrempel(row) {
  const q = row.quality == null ? null : Number(row.quality);
  if (q == null || !Number.isFinite(q)) return DREMPEL_ARC_LEGACY;
  return q >= 0.5 ? DREMPEL_ARC_GOED : 0.33;
}

function rowScore(score, row) {
  const q = row.quality == null ? 0.45 : Math.max(0.25, Math.min(1, Number(row.quality) || 0.45));
  return score * (0.85 + 0.3 * q);
}

/** Herken de meest prominente persoon op een beeld, inclusief diagnose voor inleren. */
export async function recognizeFaceDetailed(buffer) {
  const detection = await detectFacesResult(buffer);
  const faces = detection.faces;
  const leeg = {
    recognized: false,
    person: null,
    gelijkenis: null,
    marge: null,
    faceCount: faces.length,
    largestArea: 0,
    detScore: 0,
    backend: null,
    reason: detection.available ? 'geen gezicht' : detection.error,
  };
  if (!faces.length) return leeg;
  const gezicht = faces.sort((a, b) => b.oppervlak - a.oppervlak)[0];
  const backend = gezicht.backend;
  const learnability = isLearnableFace(gezicht, faces);
  const basis = {
    ...leeg,
    faceCount: faces.length,
    bbox: Array.isArray(gezicht.bbox) ? gezicht.bbox.map(Number) : null,
    largestArea: Number(gezicht.oppervlak || 0),
    detScore: Number(gezicht.score || 0),
    backend,
    learnable: learnability.ok,
    learnReason: learnability.ok ? null : learnability.reden,
    quality: learnability.quality || null,
    reason: '',
  };
  if (backend === 'arcface' && Number(gezicht.oppervlak || 0) < MIN_HERKEN_OPPERVLAK) {
    return { ...basis, reason: `gezicht te klein (${gezicht.oppervlak || 0}px)` };
  }
  const e = Array.from(gezicht.embedding);
  const rijen = db.prepare("SELECT id, person, vec, quality FROM faces WHERE backend='arcface' AND coalesce(active, 1) != 0").all();
  let beste = null;
  for (const r of rijen) {
    const v = bufToVec(r.vec);
    if (!v || v.length !== e.length) continue;
    const score = cosine(e, Array.from(v));
    const gewogen = rowScore(score, r);
    if (!beste || gewogen > beste.gewogen) beste = { person: r.person, gelijkenis: score, gewogen, row: r };
  }
  if (!beste) return { ...basis, reason: 'geen actieve bekende gezichten in deze embeddingruimte' };
  let runner = null;
  for (const r of rijen) {
    if (r.person === beste.person) continue;
    const v = bufToVec(r.vec);
    if (!v || v.length !== e.length) continue;
    const score = cosine(e, Array.from(v));
    const gewogen = rowScore(score, r);
    if (!runner || gewogen > runner.gewogen) runner = { person: r.person, gelijkenis: score, gewogen };
  }
  const drempel = acceptatieDrempel(beste.row);
  const marge = runner ? beste.gelijkenis - runner.gelijkenis : 1;
  const sterk = beste.gelijkenis >= 0.45;
  const accept = beste.gelijkenis >= drempel && (sterk || marge >= MARGE_ARC);
  const detail = {
    ...basis,
    recognized: accept,
    person: beste.person,
    gelijkenis: Number(beste.gelijkenis.toFixed(2)),
    marge: Number(marge.toFixed(2)),
    runner: runner?.person || null,
    threshold: drempel,
    reason: accept ? 'herkend' : 'onder herkenningsdrempel of te weinig marge',
  };
  return detail;
}

function quarantineInconsistentLegacyFaces() {
  const key = 'faces_legacy_quarantine_v2';
  if (kvGet(key, false)) return;
  try {
    const rows = db.prepare("SELECT id, person, vec FROM faces WHERE backend='arcface' AND quality IS NULL AND coalesce(active, 1) != 0").all()
      .map((r) => ({ ...r, v: Array.from(bufToVec(r.vec) || []) }))
      .filter((r) => r.v.length);
    const bad = [];
    for (const r of rows) {
      const same = rows.filter((x) => x.id !== r.id && x.person === r.person).map((x) => cosine(r.v, x.v)).sort((a, b) => b - a)[0] ?? -1;
      const other = rows.filter((x) => x.person !== r.person).map((x) => cosine(r.v, x.v)).sort((a, b) => b - a)[0] ?? -1;
      if (same < 0.24 && other > same + 0.08) bad.push({ id: r.id, same, other });
    }
    const upd = db.prepare("UPDATE faces SET active = 0, note = ? WHERE id = ?");
    for (const b of bad) upd.run(`quarantaine: legacy-vector inconsistent (same ${b.same.toFixed(3)}, other ${b.other.toFixed(3)})`, b.id);
    if (bad.length) logTimeline('system', `Gezichtsherkenning: ${bad.length} oude, inconsistente face-vector(s) in quarantaine gezet; liever opnieuw leren dan verkeerd herkennen.`, { faceQuarantine: bad.length });
  } catch { /* audit mag zicht nooit breken */ }
  kvSet(key, true);
}

/** Bereid de gezichtslaag voor. De centrale localbrain-monitor bewaakt de service;
 *  deze module beheert alleen haar eigen embeddings. */
export function warmupFaceRec() {
  quarantineInconsistentLegacyFaces();
  // De centrale localbrain-monitor is de enige health-poller. Twee checks met
  // verschillende timeouts lieten dezelfde trage service afwisselend "uit" en
  // "aan" lijken, terwijl ArcFace nooit werkelijk herstartte.
  setTimeout(() => {
    const brain = brainServiceStatus();
    serviceStatus = brain.ok ? 'ok' : brain.bevestigdOnbereikbaar ? 'onbereikbaar' : serviceStatus;
  }, 9_000);
}
