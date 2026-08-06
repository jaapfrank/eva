// Eva's ogen: door een camera kijken, beschrijven, en mensen herkennen.
// Herkenning werkt via "signalementen": korte uiterlijk-beschrijvingen die ze
// per persoon in haar geheugen bewaart en waartegen ze nieuwe beelden vergelijkt.
import db, { addMemory, logTimeline, upsertPerson } from '../db.js';
import { chatComplete, imageMsg } from '../brain/llm.js';
import { cameraSnapshot, findCamera, listCameras } from '../integrations/homeassistant.js';
import { learnFace, recognizeFaceDetailed } from './facerec.js';
import { noteError, updateError } from './errorbook.js';

db.exec(`
CREATE TABLE IF NOT EXISTS nameable_photos (
  id TEXT PRIMARY KEY,
  ts INTEGER NOT NULL,
  content_type TEXT,
  caption TEXT,
  description TEXT,
  camera TEXT,
  camera_key TEXT,
  suggested_person TEXT,
  candidate_similarity REAL,
  candidate_margin REAL,
  buffer BLOB NOT NULL,
  state TEXT NOT NULL DEFAULT 'context',
  named_person TEXT,
  used_ts INTEGER
);
CREATE INDEX IF NOT EXISTS idx_nameable_photos_state_ts ON nameable_photos(state, ts);
CREATE TABLE IF NOT EXISTS nameable_photo_messages (
  chat_id TEXT NOT NULL,
  message_id TEXT NOT NULL,
  photo_id TEXT NOT NULL,
  ts INTEGER NOT NULL,
  PRIMARY KEY (chat_id, message_id),
  FOREIGN KEY (photo_id) REFERENCES nameable_photos(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_nameable_photo_messages_photo ON nameable_photo_messages(photo_id);
CREATE TABLE IF NOT EXISTS observation_corrections (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  updated_ts TEXT NOT NULL,
  camera_key TEXT NOT NULL,
  photo_ref TEXT NOT NULL,
  source_channel TEXT,
  source_chat_id TEXT,
  source_message_id TEXT,
  source_person TEXT,
  raw_text TEXT NOT NULL,
  location TEXT CHECK (
    location IS NULL OR location IN ('own_property', 'neighbor_property', 'public_space', 'unclear')
  ),
  person_label TEXT,
  scene_kind TEXT CHECK (
    scene_kind IS NULL OR scene_kind IN (
      'delivery', 'service', 'social_visitor', 'resident_candidate', 'passerby', 'unknown'
    )
  ),
  organization TEXT,
  active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
  UNIQUE (camera_key, photo_ref)
);
CREATE INDEX IF NOT EXISTS idx_observation_corrections_camera
  ON observation_corrections(camera_key, active, updated_ts);
CREATE INDEX IF NOT EXISTS idx_observation_corrections_photo
  ON observation_corrections(photo_ref);
`);
try { db.exec('ALTER TABLE nameable_photos ADD COLUMN suggested_person TEXT'); } catch { /* kolom bestaat al */ }
try { db.exec('ALTER TABLE nameable_photos ADD COLUMN candidate_similarity REAL'); } catch { /* kolom bestaat al */ }
try { db.exec('ALTER TABLE nameable_photos ADD COLUMN candidate_margin REAL'); } catch { /* kolom bestaat al */ }
try { db.exec('ALTER TABLE nameable_photos ADD COLUMN camera_key TEXT'); } catch { /* kolom bestaat al */ }

// Laatste foto die Eva maakte (voor doorsturen via Telegram).
let lastPhoto = null;
export const getLastPhoto = () => lastPhoto;

/** Beschrijf een al opgehaald beeld zonder te doen alsof het een vaste HA-camera is. */
export async function describeImageBuffer(buffer, contentType = 'image/jpeg', prompt = 'Wat zie je? ', {
  budgetClass = 'perception',
  cacheKey = 'eva-generic-vision',
} = {}) {
  if (!buffer?.length) return { ok: false, reason: 'beeld ontbreekt' };
  const dataUrl = `data:${contentType};base64,${Buffer.from(buffer).toString('base64')}`;
  const result = await chatComplete({
    instructions: 'Beschrijf alleen wat het actuele beeld werkelijk ondersteunt. Benoem onzekerheid en verzin geen objecten, afstanden of veilige rijroute.',
    input: [imageMsg(prompt, dataUrl)],
    tools: [],
    cacheKey,
    budgetClass,
  });
  return { ok: true, verified: true, description: String(result.text || '').trim() };
}

// Foto's waar het gezin op reageert met "dat is X". Dit is bewust een aparte
// cache naast lastSeen: Telegram-replies verwijzen naar een specifiek bericht,
// terwijl lastSeen door nieuwe camera-events overschreven kan worden.
// Foto's zijn lokaal gezinsgeheugen, geen vluchtige gesprekscache. Bewaar ze
// lang genoeg om ook dagen later een letterlijke Telegram-reply te begrijpen.
// Een open identiteitsvraag blijft wel apart tot 24 uur begrensd, zodat een oud
// gezicht nooit een veel latere korte bevestiging kan overnemen.
const configuredArchiveDays = Number(process.env.EVA_PHOTO_ARCHIVE_DAYS || 180);
const configuredArchiveMax = Number(process.env.EVA_PHOTO_ARCHIVE_MAX || 2000);
const PHOTO_ARCHIVE_DAYS = Number.isFinite(configuredArchiveDays) && configuredArchiveDays > 0
  ? Math.max(1, configuredArchiveDays)
  : 180;
const PHOTO_ARCHIVE_TTL_MS = PHOTO_ARCHIVE_DAYS * 24 * 3600_000;
const PENDING_PHOTO_TTL_MS = 24 * 3600_000;
const MAX_NAMEABLE_PHOTOS = Number.isFinite(configuredArchiveMax) && configuredArchiveMax > 0
  ? Math.max(120, Math.floor(configuredArchiveMax))
  : 2000;

function pruneNameablePhotos() {
  const now = Date.now();
  db.prepare('DELETE FROM nameable_photo_messages WHERE ts < ?').run(now - PHOTO_ARCHIVE_TTL_MS);
  db.prepare('DELETE FROM nameable_photos WHERE ts < ?').run(now - PHOTO_ARCHIVE_TTL_MS);
  const excess = db.prepare('SELECT id FROM nameable_photos ORDER BY ts DESC LIMIT -1 OFFSET ?').all(MAX_NAMEABLE_PHOTOS);
  if (excess.length) {
    const remove = db.prepare('DELETE FROM nameable_photos WHERE id = ?');
    db.transaction((rows) => { for (const row of rows) remove.run(row.id); })(excess);
  }
}

export function rememberNameablePhoto({
  buffer,
  contentType = 'image/jpeg',
  caption = '',
  description = '',
  camera = null,
  cameraKey = null,
  pending = false,
  suggestedPerson = null,
  candidateSimilarity = null,
  candidateMargin = null,
  telegramMessages = [],
} = {}) {
  if (!buffer) return null;
  pruneNameablePhotos();
  const id = `f${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
  const ts = Date.now();
  const save = db.transaction(() => {
    db.prepare(`
      INSERT INTO nameable_photos
        (id, ts, content_type, caption, description, camera, camera_key, suggested_person,
         candidate_similarity, candidate_margin, buffer, state)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `).run(
      id,
      ts,
      contentType || 'image/jpeg',
      String(caption || '').slice(0, 500),
      String(description || '').slice(0, 700),
      camera || null,
      cameraKey || null,
      suggestedPerson || null,
      Number.isFinite(Number(candidateSimilarity)) ? Number(candidateSimilarity) : null,
      Number.isFinite(Number(candidateMargin)) ? Number(candidateMargin) : null,
      Buffer.from(buffer),
      pending ? 'pending' : 'context'
    );
    const link = db.prepare(`INSERT OR REPLACE INTO nameable_photo_messages
      (chat_id,message_id,photo_id,ts) VALUES (?,?,?,?)`);
    for (const message of Array.isArray(telegramMessages) ? telegramMessages : []) {
      if (message?.chatId == null || message?.messageId == null) continue;
      link.run(String(message.chatId), String(message.messageId), id, ts);
    }
  });
  save();
  return id;
}

export function updateNameablePhoto(ref, {
  pending = null,
  description = null,
  camera = null,
  cameraKey = null,
  suggestedPerson = null,
  candidateSimilarity = null,
  candidateMargin = null,
} = {}) {
  if (!ref) return null;
  const current = nameablePhoto(ref);
  if (!current) return null;
  const nextCamera = camera == null ? current.camera : camera;
  const nextCameraKey = cameraKey == null ? current.camera_key : cameraKey;
  db.transaction(() => {
    if (pending === true && (nextCameraKey || nextCamera)) {
      db.prepare(`
        UPDATE nameable_photos
        SET state='context'
        WHERE state='pending' AND id<>?
          AND (
            (? IS NOT NULL AND camera_key=?)
            OR (? IS NULL AND ? IS NOT NULL AND lower(camera)=lower(?))
          )
      `).run(
        String(ref),
        nextCameraKey || null,
        nextCameraKey || null,
        nextCameraKey || null,
        nextCamera || null,
        nextCamera || null,
      );
    }
    db.prepare(`UPDATE nameable_photos SET
      state=?, description=?, camera=?, camera_key=?, suggested_person=?,
      candidate_similarity=?, candidate_margin=? WHERE id=?`).run(
      pending == null ? current.state : pending ? 'pending' : 'context',
      description == null ? current.description : String(description).slice(0, 700),
      nextCamera,
      nextCameraKey,
      suggestedPerson == null ? current.suggested_person : suggestedPerson,
      candidateSimilarity == null ? current.candidate_similarity : Number(candidateSimilarity),
      candidateMargin == null ? current.candidate_margin : Number(candidateMargin),
      String(ref),
    );
  })();
  return nameablePhotoContext(ref);
}

export function nameablePhotoForTelegramMessage({ chatId, messageId } = {}) {
  if (chatId == null || messageId == null) return null;
  pruneNameablePhotos();
  const row = db.prepare(`SELECT p.* FROM nameable_photo_messages m
    JOIN nameable_photos p ON p.id=m.photo_id
    WHERE m.chat_id=? AND m.message_id=? LIMIT 1`).get(String(chatId), String(messageId));
  if (!row) return null;
  return {
    ...row,
    contentType: row.content_type || 'image/jpeg',
    buffer: row.buffer ? Buffer.from(row.buffer) : null,
  };
}

function nameablePhoto(ref) {
  if (!ref) return null;
  pruneNameablePhotos();
  const row = db.prepare('SELECT * FROM nameable_photos WHERE id = ?').get(String(ref));
  if (!row) return null;
  return {
    ...row,
    contentType: row.content_type || 'image/jpeg',
    buffer: row.buffer ? Buffer.from(row.buffer) : null,
  };
}

export function nameablePhotoContext(ref) {
  const row = nameablePhoto(ref);
  if (!row) return null;
  return {
    ref: row.id,
    ts: Number(row.ts),
    contentType: row.contentType,
    caption: row.caption || '',
    description: row.description || '',
    camera: row.camera || null,
    cameraKey: row.camera_key || null,
    suggestedPerson: row.suggested_person || null,
    candidateSimilarity: row.candidate_similarity == null ? null : Number(row.candidate_similarity),
    candidateMargin: row.candidate_margin == null ? null : Number(row.candidate_margin),
    state: row.state,
    namedPerson: row.named_person || null,
  };
}

export function latestPendingNameablePhoto({
  maxAgeMs = PENDING_PHOTO_TTL_MS,
  camera = null,
  cameraKey = null,
  requireUnique = false,
} = {}) {
  pruneNameablePhotos();
  const rows = db.prepare(`
    SELECT id, ts, content_type, caption, description, camera, camera_key,
           suggested_person, candidate_similarity, candidate_margin
    FROM nameable_photos
    WHERE state = 'pending' AND ts >= ?
      AND (? IS NULL OR lower(camera) = lower(?))
      AND (? IS NULL OR camera_key = ?)
    ORDER BY ts DESC
    LIMIT ?
  `).all(
    Date.now() - Math.max(60_000, Number(maxAgeMs) || PENDING_PHOTO_TTL_MS),
    camera || null,
    camera || null,
    cameraKey || null,
    cameraKey || null,
    requireUnique ? 2 : 1,
  );
  if (requireUnique && rows.length !== 1) return null;
  const row = rows[0];
  return row ? {
    ref: row.id,
    ts: Number(row.ts),
    contentType: row.content_type || 'image/jpeg',
    caption: row.caption || '',
    description: row.description || '',
    camera: row.camera || null,
    cameraKey: row.camera_key || null,
    suggestedPerson: row.suggested_person || null,
    candidateSimilarity: row.candidate_similarity == null ? null : Number(row.candidate_similarity),
    candidateMargin: row.candidate_margin == null ? null : Number(row.candidate_margin),
  } : null;
}

export function latestPendingNameablePhotoForTelegramChat({
  chatId,
  maxAgeMs = PENDING_PHOTO_TTL_MS,
  requireUnique = false,
} = {}) {
  if (chatId == null || String(chatId).trim() === '') return null;
  pruneNameablePhotos();
  const rows = db.prepare(`
    SELECT DISTINCT p.id, p.ts, p.content_type, p.caption, p.description,
           p.camera, p.camera_key, p.suggested_person,
           p.candidate_similarity, p.candidate_margin
    FROM nameable_photo_messages m
    JOIN nameable_photos p ON p.id=m.photo_id
    WHERE m.chat_id=? AND p.state='pending' AND p.ts >= ?
    ORDER BY p.ts DESC
    LIMIT ?
  `).all(
    String(chatId),
    Date.now() - Math.max(60_000, Number(maxAgeMs) || PENDING_PHOTO_TTL_MS),
    requireUnique ? 2 : 1,
  );
  if (requireUnique && rows.length !== 1) return null;
  const row = rows[0];
  return row ? {
    ref: row.id,
    ts: Number(row.ts),
    contentType: row.content_type || 'image/jpeg',
    caption: row.caption || '',
    description: row.description || '',
    camera: row.camera || null,
    cameraKey: row.camera_key || null,
    suggestedPerson: row.suggested_person || null,
    candidateSimilarity: row.candidate_similarity == null ? null : Number(row.candidate_similarity),
    candidateMargin: row.candidate_margin == null ? null : Number(row.candidate_margin),
  } : null;
}

function photoReplyContext(text = '') {
  const raw = String(text || '');
  if (!/\breageert op (?:een|mijn eerdere) foto/i.test(raw)) return null;
  const caption = raw.match(/fotobericht had bijschrift:\s*"([\s\S]*?)"\.\s*(?:Als je|Ik heb|Ik kan)/i)?.[1]
    || raw.match(/fotobericht had bijschrift:\s*"([\s\S]*?)"/i)?.[1]
    || '';
  const camera = caption.match(/\bCamera:\s*([^\n\r]+)/i)?.[1]?.trim() || null;
  const description = raw.match(/(?:zie je|daarop zie je):\s*([\s\S]*?)(?:\]\s*[\p{L}\p{N}' ._-]{1,40}:|$)/iu)?.[1]?.trim() || '';
  return {
    caption: caption.replace(/\s+/g, ' ').trim().slice(0, 500),
    camera,
    description: description.replace(/\s+/g, ' ').trim().slice(0, 700),
  };
}

// Laatst TOEGESTUURDE foto (via Telegram). Zo kan het gezin simpelweg een foto van
// iemand sturen met "dit is Lola" en leert Eva dat gezicht — veruit de makkelijkste manier.
let ontvangenFoto = null; // { buffer, contentType, ts }
export function onthoudOntvangenFoto(buffer, contentType = 'image/jpeg') {
  if (!buffer) return;
  ontvangenFoto = { buffer, contentType, ts: Date.now() };
}
/** Leer een gezicht uit de laatst toegestuurde foto (binnen 15 min) onder de naam. */
export async function leerGezichtVanFoto(naam) {
  if (!naam) return { ok: false, reden: 'ik heb een naam nodig' };
  if (!ontvangenFoto?.buffer || Date.now() - ontvangenFoto.ts > 15 * 60_000) {
    return { ok: false, reden: 'ik heb niet recent een foto van je gekregen om een gezicht uit te leren' };
  }
  upsertPerson(naam);
  let geleerd = false;
  try { geleerd = await learnFace(naam, ontvangenFoto.buffer); } catch { /* */ }
  if (!geleerd) return { ok: false, reden: 'ik zag geen duidelijk gezicht op die foto' };
  logTimeline('vision', `Ik heb het gezicht van ${naam} geleerd uit een toegestuurde foto 📸`, { soort: 'gezicht', persoon: naam });
  return { ok: true, naam };
}

// Het laatst waargenomen (nog naamloze) gezicht: zo kan Eva er later,
// als iemand haar de naam vertelt, alsnog een naam aan koppelen.
let lastSeen = null; // {signalement, naam(camera), ts}
export const getLastSeen = () => lastSeen;
export function rememberLastSeenFace({ signalement, naam, buffer, ts = Date.now() } = {}) {
  if (!buffer) return false;
  if (lastSeen && Number(lastSeen.ts || 0) > Number(ts || 0)) return false;
  lastSeen = {
    signalement: String(signalement || `onbekend gezicht op ${naam || 'camera'}`).trim(),
    naam: naam || 'camera',
    ts,
    buffer,
  };
  return true;
}

const APPEARANCE = 'signalement'; // categorie in het geheugen

function canonicalPersonName(value) {
  const clean = String(value || '').trim();
  if (!clean) return '';
  const existing = db.prepare('SELECT name FROM people WHERE lower(name) = lower(?) LIMIT 1').get(clean);
  return existing?.name || clean.replace(/^\p{L}/u, (letter) => letter.toLocaleUpperCase('nl-NL'));
}

export function knownAppearances() {
  return db
    .prepare("SELECT person, content FROM memories WHERE category = ? AND COALESCE(archived,0)=0 ORDER BY ts DESC")
    .all(APPEARANCE)
    .filter((r) => r.person);
}

async function grabFrame(cameraArg, frame = null) {
  // Is er al een beeld vastgelegd bij de trigger? Gebruik dát (vers, op het moment
  // van aanbellen/bewegen) i.p.v. nu een nieuw, te laat beeld te pakken.
  const cams = await listCameras().catch(() => []);
  if (frame?.buffer) {
    const entity = frame.entity || (await findCamera(cameraArg)) || cameraArg;
    const meta = cams.find((c) => c.entity_id === entity) || { name: entity, isDoorbell: false };
    const contentType = frame.contentType || 'image/jpeg';
    const photo = { buffer: frame.buffer, contentType, name: meta.name, entity, ts: Date.now() };
    lastPhoto = photo;
    return { entity, meta, photo, dataUrl: `data:${contentType};base64,${frame.buffer.toString('base64')}` };
  }
  const entity = await findCamera(cameraArg);
  if (!entity) throw new Error('geen camera gevonden');
  const meta = cams.find((c) => c.entity_id === entity) || { name: entity, isDoorbell: false };
  const { buffer, contentType } = await cameraSnapshot(entity);
  const photo = { buffer, contentType, name: meta.name, entity, ts: Date.now() };
  lastPhoto = photo;
  return { entity, meta, photo, dataUrl: `data:${contentType};base64,${buffer.toString('base64')}` };
}

/**
 * Kijk door een camera en beschrijf wat er te zien is.
 * Met recognize=true vergelijkt Eva personen met bekende signalementen.
 * Geeft {tekst, entity, naam, mensen:[{naam|null, signalement}]} terug.
 */
export function parseCameraSceneDescription(value = '') {
  const raw = String(value || '').trim();
  const marker = raw.match(/^SCENE_(EMPTY|RELEVANT)\s*:\s*/i);
  return {
    text: raw.replace(/^SCENE_(?:EMPTY|RELEVANT)\s*:\s*/i, '').trim(),
    observationEmpty: marker?.[1]?.toUpperCase() === 'EMPTY',
    classified: !!marker,
  };
}

export async function lookAndDescribe(cameraArg, {
  recognize = true,
  frame = null,
  budgetClass = 'perception',
} = {}) {
  const { entity, meta, photo, dataUrl } = await grabFrame(cameraArg, frame);
  const effectiveBudgetClass = budgetClass || 'perception';
  const cameraCacheKey = effectiveBudgetClass === 'interactive'
    ? 'eva-camera-interactive'
    : 'eva-camera-context';

  // beeld-herkenning: ken ik dit gezicht/deze persoon?
  let herkend = null;
  let faceDetail = null;
  if (recognize) {
    try {
      faceDetail = await recognizeFaceDetailed(photo.buffer);
      herkend = faceDetail?.recognized ? faceDetail : null;
    } catch { /* */ }
  }

  const bekenden = recognize ? knownAppearances() : [];
  const bekendBlok = bekenden.length
    ? `\n\nBekende huisgenoten/gezichten (naam — signalement):\n${bekenden.map((b) => `- ${b.person}: ${b.content}`).join('\n')}\n\nAls iemand op het beeld overeenkomt met een signalement, noem die naam. Twijfel je, zeg dan "lijkt op <naam>". Een onbekende noem je "een onbekende".`
    : '';

  const { text: rawText } = await chatComplete({
    instructions:
      'Je bent de ogen van huisbot Eva. Begin exact met SCENE_EMPTY: als er geen mens, dier, voertuig, pakket of andere bijzonderheid zichtbaar is; begin anders exact met SCENE_RELEVANT:. Beschrijf daarna nuchter en concreet wat er op het camerabeeld te zien is, in 2 à 4 Nederlandse zinnen. Let op mensen, dieren, voertuigen, pakketjes en bijzonderheden. Bij personen: beschrijf kort hun uiterlijk (geschat geslacht/leeftijd, haar, kleding, opvallende kenmerken).' +
      bekendBlok,
    input: [imageMsg(`Camera "${meta.name}".${herkend ? ` (Je beeldherkenning denkt dat dit ${herkend.person} is, gelijkenis ${herkend.gelijkenis}.)` : ''} Wat zie je nu?`, dataUrl)],
    tools: [],
    cacheKey: cameraCacheKey,
    budgetClass: effectiveBudgetClass,
  });
  const scene = parseCameraSceneDescription(rawText);
  const text = scene.text || String(rawText || '').trim();

  // bewaar de beschrijving bij de laatst gemaakte foto, zodat Eva later weet wat
  // ze zag/stuurde (en niet ontkent dat er een foto was).
  photo.beschrijving = text.trim();

  // Zie je een (onbekende) persoon? Bewaar stilletjes een signalement, zodat Eva
  // er later — als ze de naam te weten komt — alsnog een gezicht aan kan koppelen.
  if (/\b(persoon|man|vrouw|jongen|meisje|kind|iemand|bezorger|onbekende)\b/i.test(text)) {
    try {
      const { text: sig } = await chatComplete({
        instructions:
          'Beschrijf in één compacte Nederlandse zin de blijvende uiterlijke kenmerken van de meest prominente persoon op dit beeld (geschat geslacht/leeftijd, haar, bril, baard, huidskleur, postuur). Geen kleding. Geen persoon zichtbaar? Antwoord: GEEN.',
        input: [imageMsg('Signalement van de persoon?', dataUrl)],
        tools: [],
        cacheKey: cameraCacheKey,
        budgetClass: effectiveBudgetClass,
      });
      // Bewaar óók de foto van juist dít gezicht (niet de vluchtige lastPhoto, die wordt door elke
      // volgende camera-kijk overschreven). Zo kan Eva straks, als ze de naam hoort, het juiste
      // gezicht leren — ook al keek ze ondertussen ergens anders.
      if (!/^geen/i.test(sig.trim())) rememberLastSeenFace({
        signalement: sig.trim(),
        naam: meta.name,
        buffer: photo.buffer,
        ts: photo.ts,
      });
    } catch { /* signalement is optioneel */ }
  }

  if (!lastSeen && faceDetail?.faceCount && !herkend) {
    rememberLastSeenFace({
      signalement: `onbekend gezicht op ${meta.name}`,
      naam: meta.name,
      buffer: photo.buffer,
      ts: photo.ts,
    });
  }

  return {
    tekst: text.trim(),
    entity,
    naam: meta.name,
    isDoorbell: meta.isDoorbell,
    herkend: herkend?.person || null,
    face: faceDetail,
    observationEmpty: scene.observationEmpty,
    sceneClassified: scene.classified,
  };
}

/**
 * Koppel een naam aan het gezicht dat Eva zojuist zag (bv. nadat het gezin
 * via Telegram vertelde wie het was). Zo leert ze iemand kennen zonder dat die
 * persoon nóg een keer voor de camera hoeft te staan.
 */
export async function nameLastSeen(naam, { ref = null, contextText = '' } = {}) {
  naam = canonicalPersonName(naam);
  if (!naam) throw new Error('ik heb een naam nodig');
  const namedPhoto = nameablePhoto(ref);
  if (namedPhoto?.buffer) {
    upsertPerson(naam);
    const captionSuggestion = String(namedPhoto.caption || '')
      .split(/\r?\n/, 1)[0]
      .match(/^([\p{L}][\p{L}' -]{0,78}?)\s+is\s+bij\b/iu)?.[1]?.trim() || null;
    const previousName = String(
      namedPhoto.named_person || namedPhoto.suggested_person || captionSuggestion || '',
    ).trim() || null;
    const correctedFrom = previousName
      && previousName.toLocaleLowerCase('nl-NL') !== naam.toLocaleLowerCase('nl-NL')
      ? previousName
      : null;
    let geleerd = false;
    try {
      geleerd = await learnFace(naam, namedPhoto.buffer, {
        sourceRef: String(ref),
        replaceSource: !!correctedFrom,
      });
    } catch { /* gezicht-leren optioneel */ }
    const context = [
      namedPhoto.camera ? `op ${namedPhoto.camera}` : '',
      namedPhoto.caption && !correctedFrom
        ? `bijschrift: ${namedPhoto.caption.replace(/\s+/g, ' ').slice(0, 180)}`
        : '',
      correctedFrom ? `automatische herkenning ${correctedFrom} gecorrigeerd` : '',
    ].filter(Boolean).join('; ');
    if (correctedFrom) {
      db.prepare(`
        UPDATE memories
        SET archived=1, archived_reason=?
        WHERE COALESCE(archived,0)=0 AND content LIKE ?
      `).run(
        `fotolabel gecorrigeerd van ${correctedFrom} naar ${naam}`,
        `%Foto-ref ${String(ref)}.%`,
      );
    }
    const content = geleerd
      ? `Door het gezin bevestigd op een Eva-foto${context ? ` (${context})` : ''}; het lokale gezicht is als herkenningsvoorbeeld opgeslagen.`
      : `Door het gezin benoemd op een Eva-foto${context ? ` (${context})` : ''}; het beeld was niet leerbaar als betrouwbaar gezichtsvoorbeeld.`;
    addMemory({
      category: 'gebeurtenis',
      person: naam,
      content: `Foto-ref ${String(ref)}. Het gezin koppelde ${naam} aan een Eva-foto${context ? ` (${context})` : ''}.${namedPhoto.description ? ` Beeldcontext: ${namedPhoto.description}` : ''}`,
      importance: 0.7,
    });
    logTimeline('vision', `${correctedFrom ? `Naam op reply-foto gecorrigeerd van ${correctedFrom} naar` : 'Naam gekoppeld aan reply-foto:'} ${naam}.${geleerd ? ' (gezicht onthouden)' : ' (alleen context/signalement onthouden)'}`, {
      soort: 'gezicht',
      persoon: naam,
      photoRef: ref,
      geleerd,
      correctedFrom,
    });
    db.prepare(`
      UPDATE nameable_photos
      SET state = 'named', named_person = ?, used_ts = ?
      WHERE id = ?
    `).run(String(naam), Date.now(), String(ref));
    const message = correctedFrom
      ? geleerd
        ? `Je hebt gelijk, dat is ${naam}. Ik heb de automatische koppeling met ${correctedFrom} op deze foto vervangen en dit gezicht voor ${naam} opgeslagen.`
        : `Je hebt gelijk, dat is ${naam}. Ik heb de automatische koppeling met ${correctedFrom} op deze foto vervangen; het beeld is alleen niet scherp genoeg als nieuw gezichtsvoorbeeld.`
      : geleerd
        ? `Dank je, dat is ${naam}. Ik heb dit gezicht als herkenningsvoorbeeld opgeslagen.`
        : `Duidelijk, dat is ${naam}. Ik heb de naam aan deze foto gekoppeld, maar het beeld is niet goed genoeg als gezichtsvoorbeeld.`;
    return {
      ok: true,
      signalement: content,
      naam,
      geleerd,
      bron: 'reply_foto',
      correctedFrom,
      message,
    };
  }
  const replyContext = photoReplyContext(contextText);
  if (replyContext) {
    upsertPerson(naam);
    const context = [
      replyContext.camera ? `op ${replyContext.camera}` : '',
      replyContext.caption ? `bijschrift: ${replyContext.caption}` : '',
      replyContext.description ? `beeldcontext: ${replyContext.description}` : '',
    ].filter(Boolean).join('; ');
    addMemory({
      category: 'gebeurtenis',
      person: naam,
      content: `Het gezin koppelde ${naam} aan een Eva-foto${context ? ` (${context})` : ''}. De exacte leerfoto was niet beschikbaar, dus dit is context en geen gezichts-embedding.`,
      importance: 0.68,
    });
    logTimeline('vision', `Naam gekoppeld aan reply-context: ${naam}. (geen leerfoto beschikbaar)`, {
      soort: 'foto_context',
      persoon: naam,
      photoRef: ref || null,
    });
    return { ok: true, naam, geleerd: false, bron: 'foto_context' };
  }
  // Ruim venster (2 uur): het gezin reageert vaak pas later op "wie is dit?" — dan moet ze het nog
  // kunnen koppelen i.p.v. "ik weet niet meer wie je bedoelt".
  if (!lastSeen || Date.now() - lastSeen.ts > 120 * 60_000) {
    return { ok: false, reden: 'ik heb niet recent een gezicht gezien om een naam aan te hangen' };
  }
  upsertPerson(naam);
  addMemory({ category: APPEARANCE, person: naam, content: lastSeen.signalement, importance: 0.8 });
  // Leer meteen het ECHTE gezicht (beeld-vector) uit de bewaarde foto van dit gezicht — dat herkent
  // veel betrouwbaarder dan het tekst-signalement, en voorkomt dat ze het de volgende keer wéér vraagt.
  let geleerd = false;
  try { if (lastSeen.buffer) geleerd = await learnFace(naam, lastSeen.buffer); } catch { /* gezicht-leren optioneel */ }
  logTimeline('vision', `Nu weet ik wie dat was: ${naam}.${geleerd ? ' (gezicht onthouden 📸)' : ''} ${lastSeen.signalement}`, { soort: 'gezicht', persoon: naam });
  const sig = lastSeen.signalement;
  lastSeen = null;
  return { ok: true, signalement: sig, naam, geleerd };
}

export function notePositionLabelsForPhoto(labels = [], { ref = null, contextText = '' } = {}) {
  const namedPhoto = nameablePhoto(ref);
  const replyContext = namedPhoto ? null : photoReplyContext(contextText);
  const context = namedPhoto
    ? [
      namedPhoto.camera ? `camera ${namedPhoto.camera}` : '',
      namedPhoto.caption ? `bijschrift: ${namedPhoto.caption.replace(/\s+/g, ' ').slice(0, 180)}` : '',
      namedPhoto.description ? `beeld: ${namedPhoto.description.replace(/\s+/g, ' ').slice(0, 220)}` : '',
    ].filter(Boolean).join('; ')
    : [
      replyContext?.camera ? `camera ${replyContext.camera}` : '',
      replyContext?.caption ? `bijschrift: ${replyContext.caption.slice(0, 180)}` : '',
      replyContext?.description ? `beeld: ${replyContext.description.slice(0, 220)}` : '',
    ].filter(Boolean).join('; ');
  const opgeslagen = [];
  for (const item of labels) {
    const naam = String(item.naam || '').trim();
    const positie = String(item.positie || '').trim();
    if (!naam || !positie) continue;
    upsertPerson(naam);
    addMemory({
      category: 'gebeurtenis',
      person: naam,
      content: `${naam} stond ${positie} op een door Eva gestuurde foto${context ? ` (${context})` : ''}. Dit is positie/context, geen bewezen losse gezichts-embedding.`,
      importance: 0.7,
    });
    opgeslagen.push({ naam, positie });
  }
  if (opgeslagen.length) {
    logTimeline('vision', `Posities op reply-foto onthouden: ${opgeslagen.map((x) => `${x.positie}=${x.naam}`).join(', ')}`, {
      soort: 'foto_context',
      photoRef: ref || null,
      labels: opgeslagen,
    });
  }
  return { ok: !!opgeslagen.length, labels: opgeslagen, hadFoto: !!namedPhoto, hadContext: !!replyContext };
}

function parseJson(text) {
  try {
    const m = String(text).match(/\{[\s\S]*\}/);
    return m ? JSON.parse(m[0]) : null;
  } catch {
    return null;
  }
}

const PERSON_CONTEXT_KINDS = new Set([
  'delivery', 'service', 'social_visitor', 'resident_candidate', 'passerby', 'unknown',
]);
const PERSON_CONTEXT_LOCATIONS = new Set([
  'own_property', 'neighbor_property', 'public_space', 'unclear',
]);

function cleanObservationText(value, limit = 500) {
  return String(value || '').replace(/\s+/g, ' ').trim().slice(0, limit);
}

function lastPatternIndex(value, pattern) {
  let index = -1;
  for (const match of String(value || '').matchAll(pattern)) index = Math.max(index, match.index ?? -1);
  return index;
}

function correctedLocation(raw) {
  const locations = [
    {
      location: 'neighbor_property',
      index: lastPatternIndex(
        raw,
        /\b(?:terrein|erf|grond|zone|stuk|deel|daar|dat|dit|die)\b.{0,100}\b(?:buren|buurman|buurvrouw)\b|\b(?:bij|van|op)\s+(?:de|het)?\s*buren\b/giu,
      ),
    },
    {
      location: 'own_property',
      index: lastPatternIndex(
        raw,
        /\b(?:dit|dat|die|daar)\b.{0,80}\b(?:ons|onze|eigen)\s+(?:terrein|erf|grond|zone)\b/giu,
      ),
    },
    {
      location: 'public_space',
      index: lastPatternIndex(
        raw,
        /\b(?:dit|dat|die|daar|persoon)\b.{0,80}\b(?:op straat|openbare weg|trottoir|stoep|publieke ruimte)\b/giu,
      ),
    },
  ].filter((item) => item.index >= 0).sort((a, b) => b.index - a.index);
  return locations[0]?.location || null;
}

function correctedPersonLabel(raw) {
  const matches = [...String(raw || '').matchAll(
    /\b(?:dat|dit|die|deze)(?:\s+(?:persoon|man|vrouw|bezorger|klusser|bezoeker))?\s+(?:is|was)\s+([^.!?;\n\]]+)/giu,
  )];
  for (const match of matches.reverse()) {
    let candidate = cleanObservationText(match[1], 90)
      .replace(/\s+(?:bij|op|van)\s+(?:de|het)?\s*(?:buren|buurman|buurvrouw|straat|stoep|trottoir)\b.*$/iu, '')
      .replace(/\s+(?:de|onze)\s+(?:buurman|buurvrouw|bezorger|postbode|pakketbezorger|klusser|monteur)\b.*$/iu, '')
      .trim();
    if (!candidate || /^(?:weer|wel|niet|geen|een|de|het|ons|onze|eigen|terrein|erf|grond|zone|stuk|deel|daar|hier)\b/iu.test(candidate)) continue;
    if (/\b(?:postnl|dhl|dpd|ups|bezorger|postbode|pakketbezorger|klusser|bouwvakker|monteur)\b/iu.test(candidate)) continue;
    const words = candidate.split(/\s+/).filter(Boolean);
    if (!words.length || words.length > 4 || !words.every((word) => /^[\p{L}'-]+$/u.test(word))) continue;
    const known = db.prepare('SELECT name FROM people WHERE lower(name)=lower(?) LIMIT 1').get(candidate);
    if (!known?.name && !/^\p{Lu}/u.test(candidate)) continue;
    candidate = known?.name || canonicalPersonName(candidate);
    return candidate || null;
  }
  return null;
}

function correctedSceneLabels(raw) {
  const text = String(raw || '');
  const organizations = [
    [/\bpostnl\b/i, 'PostNL'],
    [/\bdhl\b/i, 'DHL'],
    [/\bdpd\b/i, 'DPD'],
    [/\bups\b/i, 'UPS'],
  ];
  const organization = organizations.find(([pattern]) => pattern.test(text))?.[1] || null;
  if (organization || /\b(?:pakket|post|maaltijd)?bezorger\b|\bpostbode\b|\bpakket(?:je)?\b.{0,30}\b(?:brengt|bezorgt|levert)\b/i.test(text)) {
    return { sceneKind: 'delivery', organization };
  }
  if (/\b(?:klusser|bouwvakker|monteur|installateur|schilder|hovenier)\b/i.test(text)) {
    return { sceneKind: 'service', organization: null };
  }
  return { sceneKind: null, organization: null };
}

/**
 * @typedef {Object} ObservationCorrection
 * @property {number} id
 * @property {string} cameraKey
 * @property {string} photoRef
 * @property {'own_property'|'neighbor_property'|'public_space'|'unclear'|null} location
 * @property {string|null} personLabel
 * @property {'delivery'|'service'|'social_visitor'|'resident_candidate'|'passerby'|'unknown'|null} sceneKind
 * @property {string|null} organization
 * @property {boolean} active
 */

export function parseObservationCorrection(text = '') {
  const raw = cleanObservationText(text, 900);
  if (!raw) return null;
  const location = correctedLocation(raw);
  const personLabel = correctedPersonLabel(raw);
  const { sceneKind, organization } = correctedSceneLabels(raw);
  if (!location && !personLabel && !sceneKind && !organization) return null;
  return { location, personLabel, sceneKind, organization };
}

function observationCorrectionOut(row) {
  if (!row) return null;
  return {
    id: Number(row.id),
    ts: row.ts,
    updatedTs: row.updated_ts,
    cameraKey: row.camera_key,
    photoRef: row.photo_ref,
    sourceChannel: row.source_channel || null,
    sourceChatId: row.source_chat_id || null,
    sourceMessageId: row.source_message_id || null,
    sourcePerson: row.source_person || null,
    rawText: row.raw_text,
    location: row.location || null,
    personLabel: row.person_label || null,
    sceneKind: row.scene_kind || null,
    organization: row.organization || null,
    active: row.active === 1,
  };
}

export function observationCorrectionsForCamera(cameraKey, { limit = 12 } = {}) {
  const key = cleanObservationText(cameraKey, 180);
  if (!key) return [];
  return db.prepare(`
    SELECT *
    FROM observation_corrections
    WHERE camera_key=? AND active=1
    ORDER BY updated_ts DESC, id DESC
    LIMIT ?
  `).all(key, Math.max(1, Math.min(100, Number(limit) || 12))).map(observationCorrectionOut);
}

function upsertObservationCorrection({
  cameraKey,
  photoRef,
  parsed,
  rawText,
  person = null,
  channel = null,
  messageContext = null,
} = {}) {
  const key = cleanObservationText(cameraKey, 180);
  const ref = cleanObservationText(photoRef, 120);
  if (!key || !ref || !parsed) return null;
  const existing = db.prepare(`
    SELECT id FROM observation_corrections WHERE camera_key=? AND photo_ref=? LIMIT 1
  `).get(key, ref);
  const now = new Date().toISOString();
  const sourceTs = Date.parse(messageContext?.ts || '');
  const ts = Number.isFinite(sourceTs) ? new Date(sourceTs).toISOString() : now;
  db.prepare(`
    INSERT INTO observation_corrections (
      ts, updated_ts, camera_key, photo_ref, source_channel, source_chat_id,
      source_message_id, source_person, raw_text, location, person_label,
      scene_kind, organization, active
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
    ON CONFLICT(camera_key, photo_ref) DO UPDATE SET
      updated_ts=excluded.updated_ts,
      source_channel=coalesce(excluded.source_channel, observation_corrections.source_channel),
      source_chat_id=coalesce(excluded.source_chat_id, observation_corrections.source_chat_id),
      source_message_id=coalesce(excluded.source_message_id, observation_corrections.source_message_id),
      source_person=coalesce(excluded.source_person, observation_corrections.source_person),
      raw_text=excluded.raw_text,
      location=coalesce(excluded.location, observation_corrections.location),
      person_label=coalesce(excluded.person_label, observation_corrections.person_label),
      scene_kind=coalesce(excluded.scene_kind, observation_corrections.scene_kind),
      organization=coalesce(excluded.organization, observation_corrections.organization),
      active=1
  `).run(
    ts,
    now,
    key,
    ref,
    channel || null,
    messageContext?.chatId == null ? null : String(messageContext.chatId),
    messageContext?.messageId == null ? null : String(messageContext.messageId),
    person || null,
    cleanObservationText(rawText, 900),
    parsed.location || null,
    parsed.personLabel || null,
    parsed.sceneKind || null,
    parsed.organization || null,
  );
  const row = db.prepare(`
    SELECT * FROM observation_corrections WHERE camera_key=? AND photo_ref=? LIMIT 1
  `).get(key, ref);
  return { correction: observationCorrectionOut(row), existing: !!existing };
}

export function cameraObservationGuidance(cameraKey, { corrections = null } = {}) {
  const rows = Array.isArray(corrections)
    ? corrections
    : observationCorrectionsForCamera(cameraKey, { limit: 4 });
  return rows.slice(0, 4).map((row) => {
    const guidance = [];
    if (row.location === 'neighbor_property') {
      guidance.push('het gecorrigeerde referentiebeeld toont terrein van de buren; meld die zone niet als activiteit bij ons huis');
    } else if (row.location === 'own_property') {
      guidance.push('het gecorrigeerde referentiebeeld toont ons eigen terrein');
    } else if (row.location === 'public_space') {
      guidance.push('het gecorrigeerde referentiebeeld toont openbare ruimte');
    }
    if (row.sceneKind) {
      guidance.push(`de zichtbare context op het referentiebeeld was ${row.sceneKind}${row.organization ? ` (${row.organization})` : ''}; neem dit alleen over als dezelfde zichtbare kenmerken terugkomen`);
    }
    if (row.personLabel) {
      guidance.push('de persoonsnaam geldt alleen voor de exacte bronfoto en mag niet naar een nieuw beeld worden overgedragen');
    }
    return `- ObservationCorrection #${row.id}: ${guidance.join('; ')}`;
  }).filter((line) => !line.endsWith(': ')).join('\n');
}

export function cameraBoundaryReferences(cameraKey, { limit = 1, corrections = null } = {}) {
  const references = [];
  const rows = Array.isArray(corrections)
    ? corrections
    : observationCorrectionsForCamera(cameraKey, { limit: 12 });
  for (const row of rows) {
    if (!row.location && !row.sceneKind && !row.organization) continue;
    const photo = db.prepare(`SELECT id,content_type,camera,camera_key,buffer
      FROM nameable_photos WHERE id=? AND camera_key=? LIMIT 1`).get(row.photoRef, row.cameraKey);
    if (!photo?.buffer) continue;
    references.push({
      correctionId: row.id,
      photoRef: photo.id,
      contentType: photo.content_type || 'image/jpeg',
      camera: photo.camera || null,
      cameraKey: photo.camera_key || null,
      location: row.location || 'unclear',
      sceneKind: row.sceneKind,
      organization: row.organization,
      buffer: Buffer.from(photo.buffer),
    });
    if (references.length >= Math.max(1, Number(limit) || 1)) return references;
  }
  return references;
}

function latestCameraPhotoForFeedback({
  chatId = null,
  replyTo = null,
  messageId = null,
  beforeTs = null,
} = {}) {
  const id = cleanObservationText(chatId || replyTo?.chatId, 120);
  if (!id) return null;
  let row = null;
  let exactReply = false;
  let anchorTs = Date.parse(beforeTs || '');
  try {
    if (replyTo?.isAssistant && replyTo?.messageId) {
      row = db.prepare(`
        SELECT t.ts, t.content, t.media_ref, p.camera, p.camera_key, p.description
        FROM telegram_messages t
        LEFT JOIN nameable_photos p ON p.id=t.media_ref
        WHERE t.chat_id=? AND t.message_id=? AND t.role='assistant' AND t.kind='photo'
        LIMIT 1
      `).get(id, String(replyTo.messageId));
      exactReply = !!row;
    }
    if (!row && messageId) {
      const current = db.prepare(`SELECT id,ts FROM telegram_messages
        WHERE chat_id=? AND message_id=? LIMIT 1`).get(id, String(messageId));
      if (current) {
        anchorTs = Date.parse(current.ts || '');
        row = db.prepare(`
          SELECT t.ts, t.content, t.media_ref, p.camera, p.camera_key, p.description
          FROM telegram_messages t
          LEFT JOIN nameable_photos p ON p.id=t.media_ref
          WHERE t.chat_id=? AND t.id<? AND t.role='assistant' AND t.kind='photo' AND t.deleted=0
          ORDER BY t.id DESC
          LIMIT 1
        `).get(id, current.id);
      }
    }
    if (!row) {
      const before = Number.isFinite(anchorTs) ? new Date(anchorTs).toISOString() : new Date().toISOString();
      row = db.prepare(`
        SELECT t.ts, t.content, t.media_ref, p.camera, p.camera_key, p.description
        FROM telegram_messages t
        LEFT JOIN nameable_photos p ON p.id=t.media_ref
        WHERE t.chat_id=? AND t.ts<=? AND t.role='assistant' AND t.kind='photo' AND t.deleted=0
        ORDER BY t.ts DESC, t.id DESC
        LIMIT 1
      `).get(id, before);
    }
  } catch {
    return null;
  }
  const anchor = Number.isFinite(anchorTs) ? anchorTs : Date.now();
  const age = anchor - Date.parse(row?.ts || '');
  if (!row?.media_ref) return null;
  if (!exactReply && (!Number.isFinite(age) || age < 0 || age > 6 * 3600_000)) return null;
  return row;
}

/** Zet een menselijke cameracorrectie synchroon om in één getypeerde regel.
 * Het foutenboek blijft auditcontext, maar de volgende waarneming leest uitsluitend
 * observation_corrections op de exacte camera_key. */
export function recordCameraObservationCorrection({
  person = null,
  text = '',
  channel = 'telegram',
  messageContext = null,
  replyTo = null,
} = {}) {
  if (channel !== 'telegram') return null;
  const raw = cleanObservationText(text, 900);
  if (/\?\s*$/.test(raw)) return null;
  const parsed = parseObservationCorrection(raw);
  if (!parsed) return null;

  const photo = latestCameraPhotoForFeedback({
    chatId: messageContext?.chatId,
    replyTo,
    messageId: messageContext?.messageId,
    beforeTs: messageContext?.ts,
  });
  const camera = cleanObservationText(photo?.camera, 120);
  const cameraKey = cleanObservationText(photo?.camera_key, 180);
  if (!photo?.media_ref || !cameraKey) return null;

  const stored = upsertObservationCorrection({
    cameraKey,
    photoRef: photo.media_ref,
    parsed,
    rawText: raw,
    person,
    channel,
    messageContext,
  });
  if (!stored?.correction) return null;
  if (stored.correction.personLabel) {
    upsertPerson(stored.correction.personLabel);
    db.prepare(`
      UPDATE nameable_photos
      SET state='named', named_person=?, used_ts=?
      WHERE id=? AND camera_key=?
    `).run(
      stored.correction.personLabel,
      Date.now(),
      photo.media_ref,
      cameraKey,
    );
  }

  const label = camera || cameraKey;
  const location = parsed.location;
  let errorId = null;
  if (location) {
    const neighbor = location === 'neighbor_property';
    const prevention = neighbor
      ? `Behandel de op deze ${label}-foto gecorrigeerde zone als terrein van de buren. Meld daar geen activiteit als gebeurtenis bij ons huis; alleen duidelijk eigen terrein is meldwaardig.`
      : location === 'public_space'
        ? `Behandel de op deze ${label}-foto gecorrigeerde zone als openbare ruimte en niet als activiteit op ons erf.`
        : `Behandel de op deze ${label}-foto gecorrigeerde zone als ons eigen terrein en beoordeel meldwaardigheid daarna nog steeds op context en noodzaak.`;
    const alreadyRecorded = db.prepare(`SELECT id,status FROM error_book
      WHERE status<>'verworpen' AND lower(area) IN ('zicht','camera','vision')
        AND evidence LIKE ? LIMIT 1`).get(`%Foto ${photo.media_ref}%`);
    if (alreadyRecorded) {
      errorId = alreadyRecorded.id;
      if (!['geleerd', 'hersteld'].includes(alreadyRecorded.status)) {
        updateError(alreadyRecorded.id, {
          status: 'geleerd',
          result: 'De menselijke correctie is actief in het getypeerde ObservationCorrection-register en wordt vóór de volgende scene-skip geraadpleegd.',
        });
      }
    } else {
      const recorded = noteError({
        key: `camera-grounding:${cameraKey}:${location}`,
        source: `cameracorrectie:${channel}`,
        severity: 'hoog',
        area: 'zicht',
        trigger: `${person || 'huisgenoot'}: ${raw}`,
        mistake: neighbor
          ? `Eva schreef via ${label} activiteit op het terrein van de buren aan ons huis toe.`
          : location === 'public_space'
            ? `Eva schreef via ${label} activiteit in de openbare ruimte aan ons huis toe.`
            : `Eva plaatste activiteit via ${label} ten onrechte buiten ons eigen terrein.`,
        effect: 'Een onjuiste ruimtelijke grens veroorzaakt irrelevante of misleidende camerameldingen.',
        cause: `Het vaste camerastandpunt werd niet gegrond met de correctie bij foto ${photo.media_ref}.`,
        prevention,
        evidence: [
          `Foto ${photo.media_ref}`,
          photo.description ? `eerdere beeldbeschrijving: ${cleanObservationText(photo.description, 240)}` : '',
          `menselijke correctie: ${raw}`,
        ].filter(Boolean).join('; '),
      });
      errorId = recorded?.error?.id || null;
      if (errorId) {
        updateError(errorId, {
          status: 'geleerd',
          result: 'De menselijke correctie is actief in het getypeerde ObservationCorrection-register en wordt vóór de volgende scene-skip geraadpleegd.',
        });
      }
    }
  }

  logTimeline('vision', `Cameracorrectie gekoppeld aan ${cameraKey} en foto ${photo.media_ref}.`, {
    internalOnly: true,
    observationCorrection: true,
    correctionId: stored.correction.id,
    cameraKey,
    photoRef: photo.media_ref,
    location: stored.correction.location,
    personLabel: stored.correction.personLabel,
    sceneKind: stored.correction.sceneKind,
    organization: stored.correction.organization,
  });

  return {
    ok: true,
    location: stored.correction.location,
    personLabel: stored.correction.personLabel,
    sceneKind: stored.correction.sceneKind,
    organization: stored.correction.organization,
    camera: camera || null,
    cameraKey,
    photoRef: photo.media_ref,
    correctionId: stored.correction.id,
    correction: stored.correction,
    errorId,
    existing: stored.existing,
  };
}

export function reconcileCameraObservationCorrections({ limit = 240 } = {}) {
  let rows = [];
  try {
    rows = db.prepare(`
      SELECT chat_id,message_id,ts,sender,content,reply_to_message_id
      FROM telegram_messages
      WHERE role='user' AND kind='text' AND deleted=0
        AND (
          lower(content) LIKE '%buren%'
          OR lower(content) LIKE '%eigen terrein%'
          OR lower(content) LIKE '%openbare weg%'
          OR lower(content) LIKE '%postnl%'
          OR lower(content) LIKE '%bezorger%'
          OR lower(content) LIKE '%dat is %'
          OR lower(content) LIKE '%dit is %'
        )
      ORDER BY id DESC
      LIMIT ?
    `).all(Math.max(1, Math.min(1000, Number(limit) || 240)));
  } catch {
    return [];
  }
  const repaired = [];
  for (const row of rows.reverse()) {
    const result = recordCameraObservationCorrection({
      person: row.sender,
      text: row.content,
      channel: 'telegram',
      messageContext: {
        chatId: row.chat_id,
        messageId: row.message_id,
        ts: row.ts,
      },
      replyTo: row.reply_to_message_id ? {
        isAssistant: true,
        chatId: row.chat_id,
        messageId: row.reply_to_message_id,
      } : null,
    });
    if (result?.ok && !result.existing) repaired.push(result);
  }
  return repaired;
}

export function parsePersonSceneContext(text) {
  const parsed = parseJson(text);
  if (!parsed || typeof parsed !== 'object') return null;
  const rawKind = String(parsed.kind || 'unknown').trim().toLowerCase();
  const rawLocation = String(parsed.location || 'unclear').trim().toLowerCase();
  const confidence = Math.max(0, Math.min(1, Number(parsed.confidence) || 0));
  const locationConfidence = Math.max(0, Math.min(1, Number(parsed.location_confidence) || 0));
  return {
    kind: PERSON_CONTEXT_KINDS.has(rawKind) ? rawKind : 'unknown',
    location: PERSON_CONTEXT_LOCATIONS.has(rawLocation) ? rawLocation : 'unclear',
    locationConfidence,
    role: String(parsed.role || '').replace(/\s+/g, ' ').trim().slice(0, 100),
    organization: String(parsed.organization || '').replace(/\s+/g, ' ').trim().slice(0, 80),
    activity: String(parsed.activity || '').replace(/\s+/g, ' ').trim().slice(0, 180),
    summary: String(parsed.summary || '').replace(/\s+/g, ' ').trim().slice(0, 240),
    confidence,
  };
}

/** Begrijp de rol en situatie rond een lokaal onbekend gezicht. ArcFace blijft
 * leidend voor persoonlijke identiteit; deze ene beeldbeurt voorkomt dat Eva
 * een duidelijk uniform of bezorgmoment als een onbekende huisbezoeker behandelt. */
export async function classifyPersonSceneContext(
  buffer,
  contentType = 'image/jpeg',
  cameraName = 'camera',
  { cameraKey = null, corrections = null } = {},
) {
  if (!buffer) return null;
  const dataUrl = `data:${contentType || 'image/jpeg'};base64,${Buffer.from(buffer).toString('base64')}`;
  const activeCorrections = Array.isArray(corrections)
    ? corrections
    : observationCorrectionsForCamera(cameraKey, { limit: 12 });
  const learnedGuidance = cameraObservationGuidance(cameraKey, { corrections: activeCorrections });
  const boundaryReferences = cameraBoundaryReferences(cameraKey, {
    limit: 1,
    corrections: activeCorrections,
  });
  const { text } = await chatComplete({
    instructions: `Bekijk het volledige beveiligingsbeeld en bepaal de zichtbare rol en activiteit van de meest prominente persoon.
Scheid persoonlijke identiteit strikt van context: raad nooit een persoonsnaam. Herken wel duidelijke uniformen, logo's, voertuigen, pakketjes, gereedschap en wat iemand aan het doen lijkt te zijn.
Bepaal los daarvan waar de persoon staat: own_property, neighbor_property, public_space of unclear. Een beroepsrol maakt iemand niet automatisch relevant voor dit huishouden.
  ${learnedGuidance ? `Bindende getypeerde correcties voor dit vaste camerastandpunt:\n${learnedGuidance}\nGebruik locatiecorrecties voor de terreingrens. Gebruik rolcorrecties alleen als dezelfde zichtbare kenmerken terugkomen. Neem nooit een persoonsnaam uit een correctie over.` : 'Er zijn geen specifieke ObservationCorrections voor deze camera_key.'}
  ${boundaryReferences.length ? 'Voor het huidige beeld staat één door een huisgenoot gecorrigeerd referentiebeeld. Gebruik de vaste zichtbare erfgrenzen om de locatieles toe te passen. Een eventueel rol- of organisatielabel is alleen een visueel voorbeeld; leid er nooit identiteit uit af.' : ''}
Antwoord uitsluitend met JSON:
{"kind":"delivery|service|social_visitor|resident_candidate|passerby|unknown","location":"own_property|neighbor_property|public_space|unclear","location_confidence":0.0,"role":"korte Nederlandse rol of leeg","organization":"zichtbare organisatie of leeg","activity":"korte zichtbare handeling","summary":"Nederlandse naamwoordgroep zoals 'een PostNL-bezorger die een pakket lijkt af te leveren'","confidence":0.0}
Gebruik delivery voor post/pakket/maaltijdbezorging, service voor monteurs of andere herkenbare beroepsbezoeken, passerby voor iemand die slechts langsloopt, en unknown als kleding en scène geen betrouwbare rol tonen.`,
    input: [
      ...boundaryReferences.map((reference) => imageMsg(
          `ObservationCorrection #${reference.correctionId} voor camera_key "${reference.cameraKey}": de zichtbare persoon/activiteit stond op ${reference.location}.${reference.sceneKind ? ` De zichtbare context was ${reference.sceneKind}${reference.organization ? ` (${reference.organization})` : ''}.` : ''} Leer alleen de vaste terreingrens en aantoonbare context, nooit de identiteit.`,
          `data:${reference.contentType};base64,${reference.buffer.toString('base64')}`,
        )),
      imageMsg(`Huidig beeld van camera "${cameraName}" (${cameraKey || 'camera_key onbekend'}). Welke rol, activiteit en terreinkant zijn werkelijk zichtbaar?`, dataUrl),
    ],
    tools: [],
    effort: 'low',
    cacheKey: 'eva-camera-context',
    budgetClass: 'perception',
    timeoutMs: 18_000,
    rateLimitRetries: 0,
  });
  const parsed = parsePersonSceneContext(text);
  return parsed ? {
    ...parsed,
    guidanceApplied: !!learnedGuidance,
    boundaryReferenceApplied: boundaryReferences.length > 0,
    observationCorrectionIds: activeCorrections.map((row) => row.id),
  } : null;
}

/**
 * Kijk KRITISCH of er echt iemand staat. Een bewegingsmelder gaat vaak vals af,
 * en Eva mag haar waarneming niet laten kleuren door de aanname dat er iemand is.
 * Geeft {persoon, waar, zekerheid, beschrijving}. Bias naar "niemand".
 */
export async function verifyPerson(cameraArg, { frame = null } = {}) {
  const { meta, dataUrl } = await grabFrame(cameraArg, frame);
  const { text } = await chatComplete({
    instructions:
      'Je kijkt door een beveiligingscamera van een huis. Een bewegingsmelder ging af, MAAR die gaat heel vaak vals af — door auto\'s, schaduwen, takken, regen, licht, of helemaal niets. Ga er dus NIET vanuit dat er iemand is. Kijk eerlijk en kritisch. Staat er werkelijk een duidelijk zichtbare persoon op dit beeld? Antwoord UITSLUITEND met JSON: {"persoon": true/false, "waar": "aan de deur"|"in de tuin"|"op de oprit"|"op straat"|"onduidelijk", "zekerheid": 0.0-1.0, "beschrijving": "kort wat je echt ziet"}. Zet "persoon" alleen op true als je écht een mens ziet; bij twijfel false. Een lege straat, een auto, een schaduw of een onduidelijk vlekje is GEEN persoon.',
    input: [imageMsg(`Camera "${meta.name}". Staat hier echt iemand?`, dataUrl)],
    tools: [],
  });
  const j = parseJson(text) || { persoon: false, zekerheid: 0, waar: 'onduidelijk', beschrijving: text.slice(0, 120) };
  if (lastPhoto) lastPhoto.beschrijving = j.beschrijving || lastPhoto.beschrijving;
  return {
    persoon: j.persoon === true,
    waar: String(j.waar || 'onduidelijk'),
    zekerheid: Number(j.zekerheid) || 0,
    beschrijving: String(j.beschrijving || '').slice(0, 200),
    camera: meta.name,
  };
}

/**
 * Leer iemand kennen: maak een foto, destilleer een herkenbaar signalement
 * en bewaar dat onder de persoon. Zo herkent Eva diegene later.
 */
export async function learnPerson(naam, cameraArg) {
  if (!naam) throw new Error('ik heb een naam nodig om iemand te onthouden');
  const { dataUrl, meta } = await grabFrame(cameraArg);

  const { text } = await chatComplete({
    instructions:
      'Je helpt huisbot Eva een persoon te leren herkennen. Beschrijf in één compacte Nederlandse zin de blijvende, herkenbare uiterlijke kenmerken van de meest prominente persoon op het beeld: geschat geslacht en leeftijd, haarkleur en -lengte, bril, baard/snor, huidskleur, lengte/postuur. Noem GEEN kleding of toevallige dingen (die veranderen). Als er niemand duidelijk in beeld is, antwoord precies: GEEN PERSOON.',
    input: [imageMsg(`Onthoud het uiterlijk van deze persoon (dit is ${naam}).`, dataUrl)],
    tools: [],
  });
  const signalement = text.trim();
  if (/geen persoon/i.test(signalement)) {
    return { ok: false, reden: `ik zie niemand duidelijk in beeld op ${meta.name} om te onthouden` };
  }
  upsertPerson(naam);
  addMemory({ category: APPEARANCE, person: naam, content: signalement, importance: 0.8 });
  // ook de beeld-vector opslaan — dat herkent veel betrouwbaarder dan tekst
  let geleerd = false;
  try { geleerd = await learnFace(naam, lastPhoto?.buffer); } catch { /* */ }
  logTimeline('vision', `Ik heb geleerd hoe ${naam} eruitziet${geleerd ? ' (gezicht onthouden)' : ''}: ${signalement}`, { soort: 'gezicht', persoon: naam });
  return { ok: true, signalement, naam };
}

/** Leer het gezicht van de laatst gemaakte foto onder een naam (na koppel_naam_aan_gezicht). */
export async function learnLastFace(naam) {
  if (!naam || !lastPhoto?.buffer) return false;
  try { return await learnFace(naam, lastPhoto.buffer); } catch { return false; }
}
