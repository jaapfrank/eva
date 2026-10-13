import { createHash } from 'node:crypto';
import { allPeople } from '../db.js';
import { announceOnDoorbellIntercom } from '../integrations/homeassistant.js';
import { householdMembers } from './household.js';
import {
  beginPerceptionIncidentEffect,
  claimPlannedPerceptionIncident,
  claimReadyPerceptionIncident,
  cleanupPerceptionFrames,
  migrateLegacyDoorbellFrameDirectory,
  failPerceptionIncidentClaim,
  issuePerceptionIncidentPlan,
  perceptionAttentionStatus,
  recoverPerceptionIncidents,
  settlePerceptionIncidentPlan,
} from './perceptionattention.js';
import { decidePerceptionIncident } from './perceptiondecision.js';
import { sendRegisteredMessage, sendRegisteredPhoto } from './tools.js';

const sha = (value) => createHash('sha256').update(String(value ?? '')).digest('hex');

function householdBindings() {
  const members = householdMembers(allPeople())
    .filter((person) => String(person.telegram_id || '').trim());
  const bindings = members.map((person) => Object.freeze({
    label: String(person.name),
    chatIds: Object.freeze([String(person.telegram_id).trim()]),
  }));
  const familyIds = [...new Set(members.map((person) => String(person.telegram_id).trim()))].sort();
  if (familyIds.length) bindings.unshift(Object.freeze({
    label: 'gezin', chatIds: Object.freeze(familyIds),
  }));
  return Object.freeze(bindings);
}

function deliveryOutcome(result) {
  if (result?.ok === true && result?.accepted === true) {
    return { result: 'delivered', receipt: sha(JSON.stringify(result)) };
  }
  if (result?.uncertain === true || result?.effect_uncertain === true
    || result?.partial === true || result?.visible === true) {
    return { result: 'effect_uncertain', error: result?.info || result?.error || 'onzeker effect' };
  }
  // beginPerceptionIncidentEffect staat reeds vóór de transportcall. Ook een
  // expliciete afwijzing kan na een crash niet veilig worden gereplayd; sluit
  // daarom terminal zonder automatisch tweede effect.
  return { result: 'failed_pre_effect', error: result?.info || result?.error || 'effect geweigerd' };
}

async function executePlan(capability, dependencies) {
  if (!dependencies.isCurrent()) {
    return Object.freeze({ ok: false, status: 'deferred' });
  }
  const plan = beginPerceptionIncidentEffect(capability);
  if (!plan) return null;
  if (plan.action === 'silent') {
    return settlePerceptionIncidentPlan(capability, { result: 'silent' });
  }
  const effectIsCurrent = () => dependencies.isCurrent()
    && dependencies.now() < Date.parse(plan.expiresAt);
  const options = {
    dedupeKey: `perception:${plan.incidentId}:${plan.planId}`,
    householdOnly: true,
    expectedTargetIds: plan.targetIds,
    broadcast: plan.recipient === 'gezin',
    directReply: false,
    isCurrent: effectIsCurrent,
    checkCurrentBeforeEachTransport: true,
  };
  let result;
  try {
    if (plan.action === 'household_text') {
      result = await dependencies.sendText(plan.text, plan.recipient, options);
    } else if (plan.action === 'household_photo') {
      result = await dependencies.sendPhoto(plan.frame.buffer, plan.frame.mime,
        plan.text, plan.recipient, options);
    } else if (plan.action === 'intercom') {
      result = await dependencies.announce(plan.text, plan.cameraEntity, {
        expectedSpeakerEntity: plan.speakerEntity,
        isCurrent: effectIsCurrent,
      });
    } else return null;
  } catch (error) {
    result = { ok: false, accepted: false, uncertain: true,
      error: String(error?.message || error) };
  }
  return settlePerceptionIncidentPlan(capability, deliveryOutcome(result));
}

/** Verwerk hoogstens één incident of één vóór-I/O bewaard plan. */
export async function runPerceptionIncidentOnce({
  decide = decidePerceptionIncident,
  sendText = sendRegisteredMessage,
  sendPhoto = sendRegisteredPhoto,
  announce = announceOnDoorbellIntercom,
  isCurrent = () => true,
  now = () => Date.now(),
} = /** @type {any} */ ({})) {
  try {
    const dependencies = { sendText, sendPhoto, announce, isCurrent, now };
    recoverPerceptionIncidents();
    const recovered = claimPlannedPerceptionIncident();
    if (recovered) return executePlan(recovered, dependencies);
    const claim = claimReadyPerceptionIncident({
      householdRecipients: [...householdBindings()],
    });
    if (!claim) return null;
    let decision;
    try { decision = await decide(claim); } catch { decision = null; }
    if (!decision) {
      failPerceptionIncidentClaim(claim, 'geen exact geldig modelbesluit');
      return Object.freeze({ ok: false, status: 'decision_failed' });
    }
    if (!isCurrent()) {
      failPerceptionIncidentClaim(
        claim,
        'ingehaald door een bewonersbeurt',
        'superseded_pre_effect',
      );
      return Object.freeze({ ok: false, status: 'superseded_pre_effect' });
    }
    let capability;
    try { capability = issuePerceptionIncidentPlan(claim, decision); } catch (error) {
      failPerceptionIncidentClaim(claim, error);
      return Object.freeze({ ok: false, status: 'decision_failed' });
    }
    return await executePlan(capability, dependencies);
  } finally {
    try { cleanupPerceptionFrames(); } catch { /* cleanup mag de effectuitkomst niet herschrijven */ }
  }
}

export function perceptionIncidentPending() {
  const status = perceptionAttentionStatus();
  if (!status.enabled) return false;
  return ['ready', 'claimed', 'planned', 'executing']
    .some((key) => Number(status.counts?.[key] || 0) > 0);
}

export function initializePerceptionIncidentRuntime() {
  const recovery = recoverPerceptionIncidents();
  const legacyFramesMigrated = migrateLegacyDoorbellFrameDirectory();
  cleanupPerceptionFrames();
  return Object.freeze({ ...recovery, legacyFramesMigrated });
}

export const __perceptionExecutorTest = Object.freeze({ householdBindings, deliveryOutcome });
