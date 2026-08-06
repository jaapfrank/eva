import { getTimeline } from '../db.js';

const ACCOUNTABILITY = /\b(?:per\s+ongeluk|onbedoeld|vergissing|fout(?:je)?|niet\s+zeker|onzeker|mogelijk\s+effect|brand|rook|koolmonoxide|inbraak|lekkage|nood|alarm|privacy|betaling|betaald|kosten|geld|menselijke\s+keuze|jullie\s+moeten|je\s+moet)\b/i;
const OPERATIONAL_UPDATE = /\b(?:staat|stond|gezet|geschakeld|aangezet|uitgezet|hersteld|gecontroleerd|gevonden|afgerond|lamp|licht|deur|raam|robot|stofzuiger|verwarming|thermostaat|apparaat|bestand|rapport|test)\b/i;
const INTERNAL_LANGUAGE = /\b(?:aanwezigheidsbewijs|uitstand|bewijsgrens|delivery|receipt|werkpunt|werkboek|entity[_ -]?id|tool(?:call)?|gecontroleerd\s+(?:uit|aan)|op\s+naam\s+gegokt)\b/i;
const ROUTINE_MESSAGE_QUIET_MS = 90 * 60_000;

export function autonomousMessageNeedsAccountability(text = '') {
  return ACCOUNTABILITY.test(String(text || ''));
}

export function autonomousMessageNeedsRewrite(text = '') {
  return INTERNAL_LANGUAGE.test(String(text || ''));
}

function recentRealOutbound(limit = 8) {
  return getTimeline({ kinds: ['chat_out'], limit: 300 })
    .filter((row) => row.meta?.syntheticTest !== true)
    .filter((row) => row.meta?.quarantinedSyntheticContamination !== true)
    .slice(0, Math.max(1, limit))
    .reverse();
}

export function routineAutonomousMessageCoolingDown(text = '', {
  rows = recentRealOutbound(12),
  now = Date.now(),
} = {}) {
  if (!OPERATIONAL_UPDATE.test(String(text || '')) || autonomousMessageNeedsAccountability(text)) return false;
  return rows.some((row) => (
    row.meta?.autonomous === true
    && Number.isFinite(Date.parse(row.ts || ''))
    && now - Date.parse(row.ts) >= 0
    && now - Date.parse(row.ts) < ROUTINE_MESSAGE_QUIET_MS
  ));
}

// Alleen een pure afleverbeslissing. De hoofd-Eva heeft de tekst al geschreven;
// deze grens herschrijft haar stem niet met een tweede model.
export function reviewAutonomousMessage(text = '', { mustDeliver = false } = {}) {
  const draft = String(text || '').trim().slice(0, 4_000);
  if (!draft) return { decision: 'silent', text: '', reason: 'empty' };
  if (autonomousMessageNeedsRewrite(draft)) {
    return { decision: 'rewrite', text: draft, reason: 'internal_language' };
  }
  if (!mustDeliver && !autonomousMessageNeedsAccountability(draft)
    && routineAutonomousMessageCoolingDown(draft)) {
    return { decision: 'silent', text: '', reason: 'routine_contact_cooldown' };
  }
  return { decision: 'send', text: draft, reason: null };
}
