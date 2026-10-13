import { allPeople, db, resolveTelegramTargets } from '../db.js';
import { canonicalJson } from '../util.js';
import { householdMembers } from './household.js';
import { goalEpisodeById } from './portfolio.js';

const HANDOFF_CONTRACT = 'ExplorationEconomicHandoff/v1';
const GOAL_KEY = 'economic_value';
const EXTERNAL_AUTHORITY = Object.freeze({
  internalWork: true, familyDelivery: true,
  externalContact: false, publication: false, spend: false,
});

export function explorationEconomicWorkBoundary(context) {
  if (context?.mode !== 'autonomy') return null;
  const workId = Number(context.activeWorkId);
  const episodeId = Number(context.activeGoalEpisodeId);
  if (!Number.isSafeInteger(workId) || workId < 1
    || !Number.isSafeInteger(episodeId) || episodeId < 1) return null;
  const work = /** @type {any} */ (db.prepare(
    'SELECT * FROM eva_work_items WHERE id=?',
  ).get(workId));
  const episode = goalEpisodeById(episodeId);
  let state = /** @type {any} */ ({});
  try { state = JSON.parse(String(work?.state_json || '{}')); } catch {
    return { present: true, valid: false };
  }
  if (!state || typeof state !== 'object' || Array.isArray(state)
    || !Object.hasOwn(state, 'exploration_economic_handoff')) return null;
  const handoff = state.exploration_economic_handoff;
  const commitment = handoff?.commitment;
  const valid = [work, episode, handoff,
    handoff?.contract === HANDOFF_CONTRACT,
    /^[a-f0-9]{64}$/u.test(String(handoff?.sourceOutcomeKey || '')),
    /^[a-f0-9]{64}$/u.test(String(handoff?.evidenceSha256 || '')),
    handoff?.goalKey === GOAL_KEY,
    Number.isSafeInteger(Number(handoff?.explorationId)), Number(handoff?.explorationId) >= 1,
    Number.isSafeInteger(Number(handoff?.fromRevision)), Number(handoff?.fromRevision) >= 1,
    Number(handoff?.toRevision) === Number(handoff?.fromRevision) + 1,
    canonicalJson(handoff?.authority) === canonicalJson(EXTERNAL_AUTHORITY),
    commitment && typeof commitment === 'object' && !Array.isArray(commitment),
    canonicalJson(Object.keys(commitment || {}).sort()) === canonicalJson([
      'definitionOfDone', 'nextAction', 'outcome', 'title',
    ]),
    Object.values(commitment || {}).every((part) => typeof part === 'string'
      && part.trim().length >= 4),
    Number(episode?.work_item_id) === workId, episode?.goal_key === GOAL_KEY,
    episode?.origin_kind === 'self', episode?.origin_channel === 'autonomy',
    episode?.requester == null, episode?.delivery_policy === 'milestone',
    episode?.delivery_target == null, work?.source === 'autonomy', work?.owner == null,
  ].every(Boolean);
  return { present: true, valid };
}

export function explorationEconomicExternalPlanAllowed(name, args, context, metadata) {
  const plan = context?.issuedPlan;
  return [name === 'stuur_bericht', plan?.toolName === name, plan?.args === args,
    plan?.effect === 'external', plan?.capabilityId === 'message.deliver',
    plan?.operationId === 'send', metadata?.kind === 'parent',
    metadata?.provided?.capability === 'message.deliver', metadata?.operation?.id === 'send',
    context?.turnUnderstanding?.delivery?.target === 'family',
  ].every(Boolean);
}

export function explorationEconomicFamilyDelivery(context, args, metadata) {
  const boundary = explorationEconomicWorkBoundary(context);
  if (!boundary?.present) return { boundary, target: null };
  if (!boundary.valid
    || !explorationEconomicExternalPlanAllowed('stuur_bericht', args, context, metadata)) {
    return { boundary, target: null };
  }
  const target = resolveTelegramTargets(householdMembers(allPeople()), args?.aan, {
    broadcast: !String(args?.aan || '').trim(),
  });
  return { boundary, target: target.ok ? Object.freeze({
    recipient: target.recipient,
    broadcast: target.broadcast === true,
    chatIds: Object.freeze(target.targets.map((person) => String(person.telegram_id)).sort()),
  }) : null };
}

export function explorationEconomicExecutionPolicy(name, args, context, metadata, effect) {
  const delivery = name === 'stuur_bericht'
    ? explorationEconomicFamilyDelivery(context, args, metadata) : null;
  const boundary = delivery?.boundary || explorationEconomicWorkBoundary(context);
  const allowed = !boundary?.present || (boundary.valid
    && (effect !== 'external'
      || (explorationEconomicExternalPlanAllowed(name, args, context, metadata)
        && delivery?.target)));
  const reason = allowed ? '' : boundary?.valid
    ? 'deze exploration-handoff autoriseert geen extern contact, publicatie of uitgave buiten een exact gebonden familie-update'
    : 'de host-owned ExplorationEconomicHandoff/v1-grens is niet exact verifieerbaar';
  return { allowed, target: delivery?.target || null, reason };
}
