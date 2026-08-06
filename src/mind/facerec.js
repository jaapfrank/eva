// Echte gezichtsherkenning — nu via de lokale brein-service (ArcFace, 512-dim).
//
// ArcFace (InsightFace) is duidelijk sterker dan het oude @vladmandic/human
// (~99,86% vs ~99,38% op LFW, en veel robuuster bij wisselend licht/hoek/leeftijd —
// belangrijk voor een gezinsarchief van jaren). De brein-service draait lokaal op de
// GPU. Human blijft als LAZY terugval: alleen als de service even niet bereikbaar is,
// zodat haar zicht nooit helemaal uitvalt.
//
// Embeddings van de twee backends leven in verschillende ruimtes (512 vs 1024 dim);
// daarom onthouden we per gezicht de backend en vergelijken we alleen binnen dezelfde.
import fs from 'node:fs';
import db, { logTimeline, kvGet, kvSet } from '../db.js';
import { config } from '../config.js';
import { vecToBuf, bufToVec } from './embeddings.js';
import { brainPost, brainServiceStatus } from './localbrain.js';

// ArcFace-scores uit echte buitencamera's zijn veel lager dan nette portretfoto's:
// oude live-data zat vaak rond 0.20-0.40 voor dezelfde persoon. Daarom werken we met
// twee regimes: legacy-rijen blijven streng, nieuwe kwaliteitsrijen mogen lager mits
// ze duidelijk boven de runner-up liggen.
const DREMPEL_ARC_LEGACY = 0.38;
const DREMPEL_ARC_GOED = 0.30;
const MARGE_ARC = 0.065;
const DREMPEL_HUMAN = 0.35;  // Human's eigen gekalibreerde gelijkenis
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
  // De ArcFace-service is de PRIMAIRE ruimte; een transiente hapering (traag/GPU-druk) liet faces terugvallen op
  // de 'human'-ruimte, die NIET matcht met arcface-embeddings -> Eva 'vergat' bekende gezichten en vroeg opnieuw.
  // Daarom: kort retryen (8s, dan nog eens) vóór we terugvallen, zodat de faces-DB consistent arcface blijft.
  for (let poging = 0; poging < 2; poging++) {
    try {
      const j = await brainPost('/faces_b64', { image_b64: buffer.toString('base64') }, { kind: 'faces_b64', timeoutMs: 8000 });
      serviceStatus = 'ok';
      return (j.gezichten || [])
        .filter((f) => Array.isArray(f.embedding) && f.embedding.length)
        .map((f) => ({ embedding: Float32Array.from(f.embedding), score: f.det_score || 0, oppervlak: f.oppervlak || 0, bbox: f.bbox || null, backend: 'arcface' }));
    } catch {
      if (poging === 0) { await new Promise((r) => setTimeout(r, 1200)); continue; }
      serviceStatus = 'onbereikbaar';
      return null; // pas na 2 pogingen: val terug op Human
    }
  }
  return null;
}

// ---------- terugval: @vladmandic/human (lazy — alleen geladen als de service uitvalt) ----------
function modelBase() {
  if (process.env.EVA_FACE_MODELS) return process.env.EVA_FACE_MODELS;
  const lokaal = `${config.root}/data/face-models`;
  if (fs.existsSync(`${lokaal}/faceres.json`) && fs.existsSync(`${lokaal}/blazeface.json`)) return `file://${lokaal}/`;
  return 'https://vladmandic.github.io/human-models/models/';
}

let human = null;
let humanStatus = 'koud'; // koud | laden | klaar | mislukt
let humanBelofte = null;

async function humanModel() {
  if (humanStatus === 'klaar') return human;
  if (humanStatus === 'mislukt') return null;
  if (humanBelofte) return humanBelofte;
  humanStatus = 'laden';
  humanBelofte = (async () => {
    try {
      const tf = (await import('@tensorflow/tfjs-node')).default;
      const HumanMod = await import('@vladmandic/human');
      const Human = HumanMod.Human || HumanMod.default;
      human = new Human({
        backend: 'tensorflow',
        modelBasePath: modelBase(),
        face: {
          enabled: true, detector: { rotation: false, maxDetected: 5, minConfidence: 0.4 }, mesh: { enabled: false },
          iris: { enabled: false }, emotion: { enabled: false }, description: { enabled: true },
          antispoof: { enabled: false }, liveness: { enabled: false },
        },
        body: { enabled: false }, hand: { enabled: false }, object: { enabled: false }, gesture: { enabled: false }, filter: { enabled: false },
      });
      human._tf = tf;
      await human.load();
      human._sim = (a, b) => {
        try {
          if (human.match?.similarity) return human.match.similarity(a, b);
          if (typeof human.similarity === 'function') return human.similarity(a, b);
        } catch { /* val terug op cosinus */ }
        return cosine(a, b);
      };
      humanStatus = 'klaar';
      logTimeline('system', 'Gezichtsherkenning-terugval (Human) geladen 👁️');
      return human;
    } catch (e) {
      humanStatus = 'mislukt';
      logTimeline('system', `Gezicht-terugval (Human) niet beschikbaar (${(e && e.message) || e}).`);
      return null;
    }
  })();
  return humanBelofte;
}

async function detectFacesHuman(buffer) {
  const m = await humanModel();
  if (!m || !buffer) return [];
  let t = null;
  try {
    t = m._tf.node.decodeImage(buffer, 3);
    const res = await m.detect(t);
    return (res.face || [])
      .filter((f) => f.embedding && f.embedding.length)
      .map((f) => ({ embedding: Float32Array.from(f.embedding), score: f.score || 0, oppervlak: (f.box?.[2] || 0) * (f.box?.[3] || 0), backend: 'human' }));
  } catch {
    return [];
  } finally {
    if (t) t.dispose();
  }
}

// ---------- publieke API (backend-bewust) ----------

// Zodra ArcFace-gezichten bestaan is ArcFace de ENIGE waarheid. Human-embeddings leven in een
// andere ruimte (1024 vs 512-dim) en matchen daar nooit mee — terugvallen op Human bij een
// service-hapering vervuilde juist de DB en brak de herkenning. We cachen of er arcface-rijen zijn.
let _arcfaceAanwezig = null;
function heeftArcfaceFaces() {
  if (_arcfaceAanwezig === null) {
    try { _arcfaceAanwezig = !!db.prepare("SELECT 1 FROM faces WHERE backend='arcface' LIMIT 1").get(); }
    catch { _arcfaceAanwezig = false; }
  }
  return _arcfaceAanwezig;
}

/** Detecteer alle gezichten in een beeld; geef embeddings + score + grootte + backend. */
export async function detectFaces(buffer) {
  if (!buffer) return [];
  const viaService = await serviceFaces(buffer);
  if (viaService) return viaService;       // ArcFace (primair)
  // Service hapert even. Bestaan er al ArcFace-gezichten? Dan NIET terugvallen op Human: die ruimte
  // matcht toch niet en zou de DB vervuilen. Liever deze frame niets herkennen; de volgende lukt wel.
  if (heeftArcfaceFaces()) return [];
  return detectFacesHuman(buffer);          // alleen bij een verse install waar ArcFace nog nooit liep
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
  const faces = await detectFaces(buffer);
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
  // Geen Human-gezicht opslaan als er al ArcFace-gezichten zijn: dat is een onmatchbare vervuiling
  // (precies waardoor Frank een dood 'human'-embedding kreeg). Dan liever niets leren dan fout leren.
  if (beste.backend !== 'arcface' && heeftArcfaceFaces()) {
    if (replaceSource) deactivateFaceSource(note);
    return false;
  }
  db.transaction(() => {
    deactivateFaceSource(note);
    db.prepare('INSERT INTO faces (person, vec, backend, ts, quality, det_score, oppervlak, active, note) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)')
      .run(person, vecToBuf(beste.embedding), beste.backend, new Date().toISOString(), leerbaar.quality, Number(beste.score || 0), Number(beste.oppervlak || 0), note);
  })();
  if (beste.backend === 'arcface') _arcfaceAanwezig = true;
  return true;
}

function acceptatieDrempel(row, backend) {
  if (backend !== 'arcface') return DREMPEL_HUMAN;
  const q = row.quality == null ? null : Number(row.quality);
  if (q == null || !Number.isFinite(q)) return DREMPEL_ARC_LEGACY;
  return q >= 0.5 ? DREMPEL_ARC_GOED : 0.33;
}

function rowScore(score, row, backend) {
  const q = row.quality == null ? 0.45 : Math.max(0.25, Math.min(1, Number(row.quality) || 0.45));
  return backend === 'arcface' ? score * (0.85 + 0.3 * q) : score;
}

/** Herken de meest prominente persoon op een beeld, inclusief diagnose voor inleren. */
export async function recognizeFaceDetailed(buffer) {
  const faces = await detectFaces(buffer);
  const leeg = {
    recognized: false,
    person: null,
    gelijkenis: null,
    marge: null,
    faceCount: faces.length,
    largestArea: 0,
    detScore: 0,
    backend: null,
    reason: 'geen gezicht',
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
  // alleen vergelijken binnen dezelfde embedding-ruimte (oude rijen zonder backend = 'human')
  const rijen = db.prepare("SELECT id, person, vec, quality FROM faces WHERE coalesce(backend, 'human') = ? AND coalesce(active, 1) != 0").all(backend);
  const sim = backend === 'arcface' ? cosine : ((a, b) => (human?._sim ? human._sim(a, b) : cosine(a, b)));
  let beste = null;
  for (const r of rijen) {
    const v = bufToVec(r.vec);
    if (!v || v.length !== e.length) continue;
    const score = sim(e, Array.from(v));
    const gewogen = rowScore(score, r, backend);
    if (!beste || gewogen > beste.gewogen) beste = { person: r.person, gelijkenis: score, gewogen, row: r };
  }
  if (!beste) return { ...basis, reason: 'geen actieve bekende gezichten in deze embeddingruimte' };
  let runner = null;
  for (const r of rijen) {
    if (r.person === beste.person) continue;
    const v = bufToVec(r.vec);
    if (!v || v.length !== e.length) continue;
    const score = sim(e, Array.from(v));
    const gewogen = rowScore(score, r, backend);
    if (!runner || gewogen > runner.gewogen) runner = { person: r.person, gelijkenis: score, gewogen };
  }
  const drempel = acceptatieDrempel(beste.row, backend);
  const marge = runner ? beste.gelijkenis - runner.gelijkenis : 1;
  const sterk = beste.gelijkenis >= 0.45;
  const accept = beste.gelijkenis >= drempel && (backend !== 'arcface' || sterk || marge >= MARGE_ARC);
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

/** Herken de meest prominente persoon op een beeld. Geeft {person, gelijkenis} of null. */
export async function recognizeFace(buffer) {
  const detail = await recognizeFaceDetailed(buffer);
  return detail?.recognized ? {
    person: detail.person,
    gelijkenis: detail.gelijkenis,
    marge: detail.marge,
  } : null;
}

function quarantineInconsistentLegacyFaces() {
  const key = 'faces_legacy_quarantine_v2';
  if (kvGet(key, false)) return;
  try {
    const rows = db.prepare("SELECT id, person, vec FROM faces WHERE coalesce(backend, 'human') = 'arcface' AND quality IS NULL AND coalesce(active, 1) != 0").all()
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
 *  deze module beheert alleen haar eigen embeddings en terugvalroute. */
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

export function faceRecStatus() {
  const brain = brainServiceStatus();
  if (brain.ok || serviceStatus === 'ok') return 'klaar';
  if (humanStatus === 'klaar') return 'klaar';
  if ((brain.bevestigdOnbereikbaar || serviceStatus === 'onbereikbaar') && humanStatus === 'mislukt') return 'mislukt';
  return 'laden';
}

export function knownFaceCount() {
  return db.prepare('SELECT COUNT(DISTINCT person) c FROM faces WHERE coalesce(active, 1) != 0').get().c;
}

export function faceRecDebugStatus() {
  quarantineInconsistentLegacyFaces();
  const brain = brainServiceStatus();
  const rows = db.prepare("SELECT person, coalesce(backend, 'human') backend, coalesce(active, 1) active, COUNT(*) n FROM faces GROUP BY person, coalesce(backend, 'human'), coalesce(active, 1) ORDER BY person, backend, active DESC").all();
  return {
    service: brain.ok ? 'ok' : brain.bevestigdOnbereikbaar ? 'onbereikbaar' : serviceStatus,
    human: humanStatus,
    bekendActief: knownFaceCount(),
    breinservice: brainServiceStatus(),
    rijen: rows,
  };
}
