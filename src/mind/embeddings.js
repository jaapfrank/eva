// Associatief geheugen: betekenis-embeddings, lokaal berekend (geen API-key).
// Hiermee herinnert Eva zich dingen op gevoel/betekenis i.p.v. exacte woorden.
// Laadt het model lui; faalt het, dan valt het geheugen terug op trefwoorden.
import { logTimeline } from '../db.js';
import { embedWithLocalModel, warmupOwnModel } from './localinference.js';

let status = 'koud'; // koud | laden | klaar | mislukt
let laadBelofte = null;

function localInferenceBusy(error) {
  return /lokale inferentie bezig/i.test(String(error?.message || error || ''));
}

export function embeddingsReady() {
  return status === 'klaar';
}

export function embeddingsStatus() {
  return status; // koud | laden | klaar | mislukt
}

async function ensureModel() {
  if (status === 'klaar') return true;
  if (status === 'mislukt') return null;
  if (laadBelofte) return laadBelofte;
  status = 'laden';
  laadBelofte = (async () => {
    try {
      const result = await warmupOwnModel('embeddings');
      if (!result?.loaded) throw new Error('embeddingmodel gaf geen laadbevestiging');
      status = 'klaar';
      logTimeline('system', 'Associatief geheugen actief (betekenis-embeddings geladen) 🧠');
      return true;
    } catch (e) {
      if (localInferenceBusy(e)) {
        status = 'koud';
        laadBelofte = null;
        return null;
      }
      status = 'mislukt';
      logTimeline('system', `Associatief geheugen niet beschikbaar (${(e && e.message) || e}); ik val terug op trefwoord-geheugen.`);
      return null;
    }
  })();
  return laadBelofte;
}

/** Begin het model alvast op de achtergrond te laden (download bij eerste keer). */
export function warmupEmbeddings() {
  return ensureModel();
}

async function embedLoaded(text) {
  if (!text) return null;
  try {
    const output = await embedWithLocalModel(text);
    return Array.isArray(output) && output.length ? Float32Array.from(output) : null;
  } catch (error) {
    // De lokale inferentiepoort is bewust enkelvoudig. Gelijktijdige recall mag
    // daarom deze ene aanvraag overslaan, maar een reeds geladen model niet als
    // defect markeren en zo alle volgende gesprekken naar trefwoorden dwingen.
    if (localInferenceBusy(error)) return null;
    // Het child-proces wordt bij de volgende onderhoudsronde opnieuw gestart.
    status = 'koud';
    laadBelofte = null;
    return null;
  }
}

/** Gebruik semantiek alleen wanneer het model al warm is; start nooit vanuit
 * een gesprek of missiebeurt een zware modelinitialisatie. */
export async function embedIfReady(text) {
  return status === 'klaar' ? embedLoaded(text) : null;
}

/** Embedding (Float32Array) van een tekst, of null als embeddings niet werken. */
export async function embed(text) {
  const ready = await ensureModel();
  return ready ? embedLoaded(text) : null;
}

/** Cosine-similariteit tussen twee genormaliseerde vectoren (dan = dotproduct). */
export function cosine(a, b) {
  if (!a || !b || a.length !== b.length) return -1;
  let dot = 0;
  for (let i = 0; i < a.length; i++) dot += a[i] * b[i];
  return dot;
}

/** Serialiseren naar/van een buffer voor opslag in SQLite. */
export const vecToBuf = (vec) => (vec ? Buffer.from(vec.buffer.slice(0)) : null);
// Kopieer de bytes naar een VERSE, 0-aligned ArrayBuffer: SQLite/Node-buffers kunnen
// een byteOffset hebben die geen veelvoud van 4 is, en dan zou de Float32Array-
// constructor gooien (RangeError) — wat een leespad stil zou breken. Defensief.
export const bufToVec = (buf) => {
  if (!buf || buf.byteLength < 4) return null;
  try {
    const ab = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
    return new Float32Array(ab);
  } catch {
    return null;
  }
};

export const __embeddingsTest = { localInferenceBusy };
