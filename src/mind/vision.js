// Eva's ogen: door een camera kijken, beschrijven, en mensen herkennen.
// Herkenning werkt via "signalementen": korte uiterlijk-beschrijvingen die ze
// per persoon in haar geheugen bewaart en waartegen ze nieuwe beelden vergelijkt.
import db, { addMemory, logTimeline, upsertPerson } from '../db.js';
import { config } from '../config.js';
import { chatComplete, imageMsg } from '../brain/llm.js';
import { cachedStates, cameraSnapshot, findCamera, listCameras } from '../integrations/homeassistant.js';
import { learnFace, recognizeFaceDetailed } from './facerec.js';

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
`);
try { db.exec('ALTER TABLE nameable_photos ADD COLUMN suggested_person TEXT'); } catch { /* kolom bestaat al */ }
try { db.exec('ALTER TABLE nameable_photos ADD COLUMN candidate_similarity REAL'); } catch { /* kolom bestaat al */ }
try { db.exec('ALTER TABLE nameable_photos ADD COLUMN candidate_margin REAL'); } catch { /* kolom bestaat al */ }
try { db.exec('ALTER TABLE nameable_photos ADD COLUMN camera_key TEXT'); } catch { /* kolom bestaat al */ }

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

// Foto's waar het gezin op reageert met "dat is X". De Telegram-berichtkoppeling
// is de enige bron voor een duurzame identiteitsmutatie.
// Foto's zijn lokaal gezinsgeheugen, geen vluchtige gesprekscache. Bewaar ze
// lang genoeg om ook dagen later een letterlijke Telegram-reply te begrijpen.
const configuredArchiveDays = config.photoArchiveDays;
const configuredArchiveMax = config.photoArchiveMax;
const PHOTO_ARCHIVE_DAYS = Number.isFinite(configuredArchiveDays) && configuredArchiveDays > 0
  ? Math.max(1, configuredArchiveDays)
  : 180;
const PHOTO_ARCHIVE_TTL_MS = PHOTO_ARCHIVE_DAYS * 24 * 3600_000;
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
      'context'
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

const APPEARANCE = 'signalement'; // categorie in het geheugen

function canonicalPersonName(value) {
  const clean = String(value || '').trim();
  if (!clean) return '';
  const existing = db.prepare('SELECT name FROM people WHERE lower(name) = lower(?) LIMIT 1').get(clean);
  return existing?.name || clean.replace(/^\p{L}/u, (letter) => letter.toLocaleUpperCase('nl-NL'));
}

const FACE_IDENTITY_BINDINGS = new Set([
  'direct_reply', 'reply_chain', 'same_upload', 'nearby_photo_followup',
]);

function entityKey(value = '') {
  return String(value || '').normalize('NFKD').replace(/\p{Diacritic}/gu, '')
    .toLocaleLowerCase('nl-NL').replace(/[^\p{L}\p{N}]+/gu, ' ').trim();
}

function properNameShape(value = '') {
  const words = String(value || '').trim().split(/\s+/).filter(Boolean);
  if (!words.length || words.length > 5
    || words.some((word) => !/^[\p{L}][\p{L}'’.-]*$/u.test(word))) return false;
  const capitalized = (word) => /^\p{Lu}/u.test(word)
    && (word.length === 1 || /\p{Ll}/u.test(word.slice(1)));
  if (!capitalized(words[0]) || !capitalized(words.at(-1))) return false;
  return words.slice(1, -1).every((word) => capitalized(word)
    || (/^\p{Ll}+$/u.test(word) && word.length <= 4));
}

function registeredEntityKind(value = '') {
  const key = entityKey(value);
  if (!key) return 'unknown';
  const person = db.prepare('SELECT name FROM people WHERE lower(name)=lower(?) LIMIT 1').get(value);
  if (person && properNameShape(person.name)) return 'person';
  if (config.householdMembers.some((name) => entityKey(name) === key && properNameShape(name))) return 'person';

  const nonPersonLabels = config.voiceAssistSatelliteTargets
    .flatMap((target) => [target.alias, target.entityId]);
  if (nonPersonLabels.some((label) => entityKey(label) === key)) return 'non_person';

  let matchedNonPerson = false;
  for (const state of cachedStates()) {
    const domain = String(state?.entity_id || '').split('.', 1)[0];
    const labels = [
      state?.entity_id,
      String(state?.entity_id || '').split('.').at(-1)?.replaceAll('_', ' '),
      state?.attributes?.friendly_name,
      state?.attributes?.name,
    ];
    if (labels.some((label) => entityKey(label) === key)) {
      if (domain === 'person') return 'person';
      matchedNonPerson = true;
    }
  }
  if (matchedNonPerson) return 'non_person';

  const hasCapabilities = db.prepare(
    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='eva_capabilities'",
  ).get();
  if (hasCapabilities) {
    const capability = db.prepare('SELECT name,slug FROM eva_capabilities LIMIT 500').all()
      .some((row) => [row.name, row.slug].some((label) => entityKey(label) === key));
    if (capability) return 'non_person';
  }
  return 'unknown';
}

function identityAssertion(text = '', sender = '', replyText = '') {
  const value = String(text || '').replace(/\s+/g, ' ').trim().replace(/[.!?]+$/u, '');
  const scopedCorrection = value.match(/^nee\s*[,;:]?\s+(.+)$/iu);
  const clause = scopedCorrection?.[1]?.trim() || value;
  const confirmation = /^(?:ja\s*[,;:]?\s*)?(?:dat\s+)?klopt(?:\s+(?:inderdaad|precies))?$/iu
    .test(value);
  if (confirmation) {
    const anchored = String(replyText || '').split(/\r?\n/, 1)[0]
      .match(/^([\p{L}][\p{L}'’ .-]{0,78}?)\s+is\s+bij\b/iu)?.[1]?.trim();
    return anchored
      ? { polarity: 'affirmed', name: anchored, form: 'anchored_confirmation' }
      : { polarity: 'unknown', name: '', form: 'unanchored_confirmation' };
  }
  const self = clause.match(/^(?:dit|dat)\s+ben\s+ik$/iu);
  if (self) return { polarity: 'affirmed', name: sender, form: 'self_identity' };
  const named = clause.match(/^(?:dit|dat)\s+(?:is|heet)\s+(.+)$/iu);
  if (named) {
    const candidate = named[1].trim();
    if (/^(?:niet|geen)\b/iu.test(candidate)) {
      return { polarity: 'denied', name: candidate, form: 'negative_identity' };
    }
    return {
      polarity: 'affirmed', name: candidate,
      form: scopedCorrection ? 'scoped_correction' : 'identity',
    };
  }
  const introduced = clause.match(/^ik\s+ben\s+(.+)$/iu);
  if (introduced) return { polarity: 'affirmed', name: introduced[1].trim(), form: 'self_introduction' };
  return scopedCorrection
    ? { polarity: 'affirmed', name: clause, form: 'correction_shorthand' }
    : { polarity: 'unknown', name: '', form: 'not_identity' };
}

/**
 * @typedef {object} FaceIdentityClaim
 * @property {'FaceIdentityClaim/v1'} contract
 * @property {string} photoRef
 * @property {{kind:string}} binding
 * @property {{kind:'human_face',ref:string}} subject
 * @property {'human_identity'} predicate
 * @property {'affirmed'|'denied'|'unknown'} polarity
 * @property {{kind:'person',name:string,resolution:string}} object
 */
export function createFaceIdentityClaim({
  name = '', text = '', sender = '', photoRef = '', bindingKind = '', replyText = '',
} = {}) {
  const assertion = identityAssertion(text, sender, replyText);
  const candidate = canonicalPersonName(assertion.name);
  const resolution = registeredEntityKind(candidate);
  const sameName = entityKey(candidate) === entityKey(name);
  const reason = !photoRef
    ? 'exacte foto-ref ontbreekt'
    : !FACE_IDENTITY_BINDINGS.has(bindingKind)
      ? 'foto is niet rechtstreeks of aantoonbaar nabij aan deze uitspraak gebonden'
      : assertion.polarity !== 'affirmed'
        ? 'geen positieve menselijke identiteitsclaim'
        : !sameName
          ? 'toolnaam komt niet overeen met de uitgesproken identiteit'
          : !properNameShape(candidate) && resolution !== 'person'
            ? 'object is geen plausibele persoonsnaam'
            : resolution === 'non_person'
              ? 'object resolveert als apparaat, ruimte, kanaal of capability'
              : null;
  return {
    contract: 'FaceIdentityClaim/v1',
    ok: reason == null,
    reason,
    photoRef: String(photoRef || ''),
    binding: { kind: String(bindingKind || '') },
    subject: { kind: 'human_face', ref: String(photoRef || '') },
    predicate: 'human_identity',
    polarity: assertion.polarity,
    object: { kind: 'person', name: candidate, resolution },
    assertionForm: assertion.form,
    source: { text: String(text || ''), sender: String(sender || ''), replyText: String(replyText || '') },
  };
}

export function validateFaceIdentityClaim(claim, { name = '', photoRef = '' } = {}) {
  if (claim?.contract !== 'FaceIdentityClaim/v1') {
    return { ok: false, reason: 'getypepte identiteitsclaim ontbreekt' };
  }
  const rebuilt = createFaceIdentityClaim({
    name,
    photoRef,
    text: claim.source?.text,
    sender: claim.source?.sender,
    replyText: claim.source?.replyText,
    bindingKind: claim.binding?.kind,
  });
  if (!rebuilt.ok) return { ok: false, reason: rebuilt.reason };
  if (rebuilt.photoRef !== String(photoRef || '') || rebuilt.subject.ref !== String(photoRef || '')) {
    return { ok: false, reason: 'identiteitsclaim hoort bij een andere foto' };
  }
  return rebuilt;
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
    return { entity, meta, photo, dataUrl: `data:${contentType};base64,${frame.buffer.toString('base64')}` };
  }
  const entity = await findCamera(cameraArg);
  if (!entity) throw new Error('geen camera gevonden');
  const meta = cams.find((c) => c.entity_id === entity) || { name: entity, isDoorbell: false };
  const { buffer, contentType } = await cameraSnapshot(entity);
  const photo = { buffer, contentType, name: meta.name, entity, ts: Date.now() };
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
    ? `\n\nBekende huisgenoten/gezichten (naam: signalement):\n${bekenden.map((b) => `- ${b.person}: ${b.content}`).join('\n')}\n\nAls iemand op het beeld overeenkomt met een signalement, noem die naam. Twijfel je, zeg dan "lijkt op <naam>". Een onbekende noem je "een onbekende".`
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
export async function nameLastSeen(naam, {
  ref = null, identityClaim = null,
} = {}) {
  naam = canonicalPersonName(naam);
  if (!naam) throw new Error('ik heb een naam nodig');
  const authorization = validateFaceIdentityClaim(identityClaim, { name: naam, photoRef: ref });
  if (!authorization.ok) {
    return {
      ok: false,
      changed: false,
      reason: authorization.reason,
      message: `Ik koppel geen gezicht: ${authorization.reason}.`,
    };
  }
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
  return { ok: false, changed: false, reason: 'de vastgepinde leerfoto is niet beschikbaar' };
}
