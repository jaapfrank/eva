import { performance } from 'node:perf_hooks';
import { createHash } from 'node:crypto';
import { redactSecrets, redactSensitiveText } from '../security/redaction.js';
import { bus } from '../bus.js';
import {
  addMessage,
  allMemories,
  canonicalTelegramText,
  claimTurnToolExecution,
  completeTurnToolExecution,
  recordTurnToolObservation,
  deriveVoiceRetryIntent,
  getTimeline,
  hasDeliveredTurn,
  bumpRecall,
  logContributionOutcome,
  logTimeline,
  persistMessageWithoutPublication,
  persistTimelineWithoutPublication,
  publishPersistedMessage,
  publishPersistedTimeline,
  newerTelegramUserMessage,
  recentConversation,
  recentTelegramMessages,
  recentOtherConversationsForPerson,
  relevantOlderConversationForPerson,
  recentVerifiedTurnToolReceipts,
  telegramMessage,
  validateTelegramAcceptedReplyRecoveryEvidence,
  validateExecutionRecord,
  upsertPerson,
  db,
} from '../db.js';
import { config } from '../config.js';
import { chatComplete, executionModelFailureText, msg, runExecutionDriver } from '../brain/llm.js';
import { applyInteractionFeedback, moodActionGuidance } from './emotions.js';
import { buildRedactedInstructions, renderHouseActionResult, semanticFailurePresentation, semanticFailureReplyFeedback } from './persona.js';
import {
  executeTool, bindContinuityContextPlan, bindExecutionHandle,
  bindExplorationTerminalContributionExecution, bindHomeContextRecoveryPlan, bindSignalAssessmentPlan,
  bindPriorVerifiedHomeContextPlan,
  priorVerifiedHomeContinuationResourceReceipt,
  capabilityCatalog,
  executionGroupConsumptions,
  executionHandlesForPlan,
  executionObligationForPlan,
  isIssuedExecutionProofReadback,
  planCapabilityAction,
  resolveIssuedSelfAttentionGoalPlan,
  toolEffect,
  toolHasTag,
  toolIsDurable,
  toolRequiresEffectReport,
} from './tools.js';
import { bindHomeContextEntityReferences, groundContextualHomeCommand } from './home.js';
import {
  issueMonitoringOutcomeReceipt,
  issueMonitoringPendingDelivery,
  monitoringContractReadBindings,
  monitoringDeliveryRetrySchedule,
  monitoringOutcomeRequestRecord,
  monitoringOutcomeResponseRecord,
  monitoringStateFitsDurableBudget,
  normalizeMonitoringOutcomeDecision,
  renderMonitoringTerminalText,
} from './monitoringcontract.js';
import { verifiedDoorbellTelegramDeliveryEvidence } from './perceptionattention.js';
import { SIGNAL_BATCH_MAX_ENTITY_KEYS, validSignalBatch } from './signals.js';
import {
  CONTINUATION_DIALOGUE_ACTS,
  bindIssuedExplorationTerminalContribution,
  consumeIssuedAspirationExplorationAnchor,
  continuityWatchForUnderstanding,
  consumeIssuedFreeAutonomyContinuation,
  createMonitoringContract,
  createTurnUnderstandingRecord,
  decideFreeAutonomyContinuation,
  decideTurnStep,
  hasUnresolvedReference,
  iterativeExecutionBudget,
  isIssuedTurnUnderstanding,
  normalizeMonitoringContract,
  projectContinuityWorkStart,
  retainedTurnResultMetadata,
  runMonitoringCycle,
  semanticFailureResultMetadata,
  turnUnderstandingSemanticAuthoritySha256,
  understandTurn,
  verifyTurnUnderstanding,
  verifyTurnUnderstandingRecord,
  verifyPreparedUnderstandingTurn,
} from './understanding.js';
import { projectTurnUnderstandingTerminalOutcome } from './turnunderstandingoutcome.js';
import {
  localProjectReadRequest,
  priorActionResultFollowup,
  projectMonitoringStartArgs,
  turnToolGuard,
} from './turnplan.js';
import { createWorkItem, durableWorkStatus } from './work.js';
import { householdContextText, recordRelationshipFeedback } from './household.js';
import {
  httpIngressAuthorityCurrent,
  verifiedHttpIngressExecutionAuthority,
} from '../web/httpingress.js';
import {
  behaviorCanaryLens,
  claimBehaviorCanaryForPerson,
  prepareBehaviorCanaryDelivery,
  recordBehaviorCanaryExposure,
  registerBehaviorDeliveryVerifier,
  registerBehaviorFeedbackVerifier,
  releaseBehaviorCanaryClaim,
  replayBehaviorExperiment,
  settleBehaviorCanaryFromFeedback,
  settleBehaviorCanaryInconclusive,
} from './learning.js';
import {
  relationshipFeedbackFromText,
  relationshipOutcomeFeedbackKind,
  RELATIONSHIP_STYLE_OUTCOME_CONTRACT,
  selfContinuityContextText,
} from './social.js';
import {
  autonomousMessageNeedsAccountability,
  boundedVerifiedHomePartialEffect,
  homeEffectClaimFeedback,
  homeReadbackProvesIntent,
  monitoringPromiseFeedback as monitoringFeedbackForRequest,
  reviewAutonomousMessage,
  scheduledMonitoringWork,
  selfRepairClaimFeedback,
  turnMonitoringProofOptions,
} from './communication.js';
import {
  beginCognitiveEpisode,
  closeCognitiveEpisode,
  recentFreeNoActionContextText,
  recordCognitiveAction,
  recordCognitiveOutcome,
} from './cognitive.js';
import {
  explorationById,
  issueFreeAutonomyExplorationAtomicStart,
  noteExplorationAction,
  noteIssuedExplorationAction,
  sleepingExplorationConstraintText,
} from './curiosity.js';
import {
  issueUnprivilegedAutonomyContext,
  issueAutonomySelfAttentionIngress,
  verifiedAutonomyInteractionEpoch,
} from './runtimecontroller.js';
import { issuedExplorationContinuationContext } from './selfattentioncontext.js';
import {
  actionData,
  buildMissionComplete,
  canonicalArtifactDisplayPath,
  cleanUserText,
  completedArtifact,
  executableWorkspaceArtifact,
  executedArtifactPath,
  issuedExecutionTraceMetadata,
  isTestArtifactPath,
  iterativeArtifactEvidence,
  iterativeArtifactStatus,
  iterativeExecutionObligation,
  pendingExecutableArtifactPath,
  phasedProgrammingBuildStatus,
  programmingAcceptanceStatus,
  readArtifactPath,
  successfulZeroExit,
  writtenArtifactPath,
} from './projectexecution.js';
import {
  durableMaterialProof,
  fixedMaterialIncompleteReply,
  fixedMaterialAuthorizationReply,
  materialCompletionObligation,
  materialRequestSentinel,
  sealMaterialTargetManifest,
} from './materialcompletion.js';
import {
  artifactSealAuthority,
  registerArtifactSealAuthorityVerifier,
  sealVerifiedWorkArtifacts,
  storeWorkspaceArtifactObject,
} from './artifacts.js';
import {
  consumeTelegramDeliveryAuthority,
  verifiedTelegramRecoveryOriginalText,
  verifiedTelegramDeliveryAuthority,
  verifiedTelegramIdentityContext,
  verifiedTelegramUnderstandingSource,
} from '../integrations/telegram.js';
import {
  consumeVoiceDeliveryAuthority,
  verifiedVoiceDeliveryAuthority,
  verifiedVoiceUnderstandingSource,
} from './voicebridge.js';
import {
  TURN_UNDERSTANDING_AUTHORITY_CONTRACT,
  TURN_UNDERSTANDING_AUTHORITY_TABLE,
  requireTurnUnderstandingAuthorityStore,
  turnUnderstandingAuthorityBody,
  turnUnderstandingAuthorityRecord,
} from '../turnauthority.js';
import {
  consumeVoiceFinalDeliveryRecoveryProof,
  recordVoiceDeliveredProjection,
  verifiedAcknowledgedVoiceFinalDeliveryRecovery,
} from '../voiceauthority.js';
import {
  classifyTurnEffectManifest,
  EXECUTION_RECORD_CONTRACT_V2,
  EXECUTION_VERIFICATION_VERIFIED,
  requireExecutionLedger,
  turnEffectManifestSpec,
  turnEffectPlanSpec,
} from '../executionledger.js';
import {
  abandonInterpretationRetry,
  acceptedMonitoringDeliveryReceiptCandidate,
  attachEpisodeOrigin,
  bindEpisodeToWork,
  cancelInterpretationRetryForConversation,
  completeInterpretationRetry,
  conversationWorkReceipt,
  createAutoBoundTurnWork,
  createDurableWork,
  createInterpretationRetryHandoff,
  createGoalEpisode,
  ensureEpisodeForWorkItem,
  adoptUnboundWorkEpisodes,
  episodeForWork,
  episodeHasFinalDelivery,
  goalEpisodeById,
  openEpisodeForConversation,
  recentEpisodeArtifactReceipts,
  reconcileAcceptedMonitoringDelivery,
  recordEpisodeDelivery,
  requiredTelegramOriginBinding,
  recoverDeliveredEpisodeReceipts,
  recoverStrandedDeliveryWork,
  sealFinalDeliveryReply,
  selectPortfolioCommitment,
  interpretationRetryResume,
  interpretationReplyDeliveryGate,
  interpretationReplyPreparationBinding,
  oneShotAutoBoundMemoryContribution,
  resolveInterpretationRetryFailure,
  transitionGoalEpisode,
  updateDurableWork,
  updateGoalEpisode,
  verifiedTelegramFinalDeliveryOrigin,
  verifyMonitoringLifecycleAuthority,
  verifyInterpretationRetryPendingDelivery,
} from './portfolio.js';
import {
  auditActionResultOutput as auditToolOutput, canonicalJson, errMsg,
  executionActionResult, normalizeUserFacingText, tekstOverlap, verifiedCompleteReceipt,
} from '../util.js';

const queues = new Map();
const interactiveIngressByScope = new Map();
const VOICE_FOREGROUND_TIMEOUT_MS = 8_000;
const VOICE_REPLY_COMPOSER_TIMEOUT_MS = 2_000;
const AUTONOMY_INTERPRETATION_TIMEOUT_MS = Math.max(250,
  Math.min(90_000, Number(config.autonomousLlmTimeoutMs) || 90_000));
const HIGH_IMPACT_HOUSE_EFFECT = /(?:slot|lock|alarm|beveilig|deur|poort|garage|cover|rolluik|gordijn|verwarming|thermostaat|climate|boiler|water|gas)/i;
const CONTINUITY_SNAPSHOT_FALLBACK = 'Ik heb alleen een momentopname kunnen lezen; de ene vervolgcontrole is niet vastgelegd. Ik let dus niet doorlopend op.';
const BOUNDED_SELF_ATTENTION_RESULTS = new WeakSet();
const BOUNDED_SELF_ATTENTION_TURNS = new WeakSet();
const SELF_ATTENTION_EXECUTION_PROFILES = new WeakMap();
const SETTLED_SELF_ATTENTION_PROVIDER_PLANS = new WeakSet();
const ISSUED_INTERPRETATION_REPLY_CANDIDATES = new WeakSet();
const ISSUED_FINAL_DELIVERY_CAPABILITIES = new WeakMap();
const ISSUED_VOICE_FINAL_RECOVERY_REQUESTS = new WeakMap();
const ISSUED_DELIVERABLE_RESULTS = new WeakMap();
const CONSUMED_DELIVERABLE_RESULTS = new WeakSet();
const ISSUED_BEHAVIOR_DELIVERY_CAPABILITIES = new WeakMap();
const ISSUED_BEHAVIOR_DELIVERY_CONTEXTS = new WeakMap();
const ISSUED_BEHAVIOR_FEEDBACK_CONTEXTS = new WeakMap();
const OBSERVATION_CONTRIBUTION_SETTLEMENTS = new WeakMap();
const ISSUED_FREE_AUTONOMY_START_PLANS = new WeakMap();
const CONSUMED_FREE_AUTONOMY_START_PLANS = new WeakSet();
const ISSUED_FREE_AUTONOMY_SELECTED_READS = new WeakMap();
const CONSUMED_FREE_AUTONOMY_SELECTED_READS = new WeakSet();
const SETTLED_FREE_AUTONOMY_SELECTED_READS = new WeakMap();
const ISSUED_ARTIFACT_SEAL_CONTEXTS = new WeakMap();
const ISSUED_IDENTITY_CONTEXT_CLAIMS = new WeakMap();
const DELIVERABLE_RESULT_PROVENANCE_CONTRACT = 'DeliverableTurnResultProvenance/v1';
const INTERPRETATION_REPLY_PROVENANCE_CONTRACT = 'InterpretationRetryReplyProvenance/v1';
const SELF_ATTENTION_PROVIDER_BACKED_DIRECT = new Set([
  'camera.frame.read\u0000capture', 'internet.research\u0000search',
  'research.workcell.manage\u0000start', 'research.workcell.manage\u0000hervat',
  'robot.control\u0000verken',
  'robot.observe\u0000kaart',
]);
const SELF_ATTENTION_PROVIDER_EXECUTION_CONTRACT = 'SelfAttentionProviderExecution/v1';
const SELF_ATTENTION_PROVIDER_WINDOW_MS = 2 * AUTONOMY_INTERPRETATION_TIMEOUT_MS;
const OBSERVATION_CONTRIBUTION_CONTRACT = 'ObservationContributionPrecommit/v1';

registerBehaviorDeliveryVerifier((context) => (
  ISSUED_BEHAVIOR_DELIVERY_CONTEXTS.get(context) || null
));
registerBehaviorFeedbackVerifier((context) => (
  ISSUED_BEHAVIOR_FEEDBACK_CONTEXTS.get(context) || null
));
registerArtifactSealAuthorityVerifier((context) => (
  ISSUED_ARTIFACT_SEAL_CONTEXTS.get(context) || null
));

function interpretationRetryOriginSha256(episode) {
  const origin = {
    channel: String(episode?.origin_channel || ''),
    conversationId: String(episode?.origin_conversation_id || ''),
    messageId: String(episode?.origin_message_id || ''),
    requester: String(episode?.requester || ''),
    deliveryPolicy: String(episode?.delivery_policy || ''),
    deliveryTarget: String(episode?.delivery_target || ''),
  };
  return createHash('sha256').update(JSON.stringify(origin), 'utf8').digest('hex');
}

function sealedInterpretationReplyText(value) {
  const raw = String(value ?? '').trim();
  if (!raw || raw.length > 4_000) {
    throw new Error('gebonden antwoordtekst ontbreekt of is te groot');
  }
  const canonical = canonicalTelegramText(raw);
  if (canonical !== raw) {
    throw new Error('gebonden antwoordtekst veranderde bij transportcanonicalisatie');
  }
  return canonical;
}

function issuedInterpretationReplyReceiptBasis({
  toolResults, issuedTurnId, executionTurnId, originalTextSha256,
  understandingSha256,
}) {
  const projections = toolResults.filter(verifiedCompleteReceipt).map((tool) => ({
    name: tool.name,
    args: tool.args,
    output: tool.output,
    semanticBinding: tool.semanticBinding || null,
  }));
  if (!projections.length) throw new Error('uitvoerantwoord vereist duurzame receipts');
  const projectedUnderstandingSha256s = [...new Set(projections.map((projection) => (
    String(projection.semanticBinding?.understandingSha256 || '')
  )))];
  if (projectedUnderstandingSha256s.length !== 1
    || ![originalTextSha256, understandingSha256]
      .includes(projectedUnderstandingSha256s[0])) {
    throw new Error('uitvoerantwoord heeft geen eenduidige understandingbinding');
  }
  const receiptUnderstandingSha256 = projectedUnderstandingSha256s[0];
  const rows = db.prepare(`SELECT execution_seq AS receipt_id,* FROM main.turn_tool_executions
    WHERE status='done' AND turn_id IN (?,?) ORDER BY execution_seq`).all(
    issuedTurnId, executionTurnId,
  ).flatMap((row) => {
    try {
      const verified = validateExecutionRecord(row);
      const semanticBinding = JSON.parse(String(verified.semantic_binding || 'null'));
      if (semanticBinding?.understandingSha256 !== receiptUnderstandingSha256) return [];
      const observation = String(verified.execution_key || '').startsWith('observation:');
      if ((observation && verified.turn_id !== issuedTurnId)
        || (!observation && verified.turn_id !== executionTurnId)) return [];
      return [{
        row: verified,
        projection: {
          name: verified.tool,
          args: JSON.parse(verified.args),
          output: verified.output,
          semanticBinding,
        },
      }];
    } catch { return []; }
  });
  const unused = [...rows];
  const receiptSha256s = projections.map((projection) => {
    const index = unused.findIndex((entry) => (
      canonicalJson(entry.projection) === canonicalJson(projection)
    ));
    if (index < 0) throw new Error('uitvoerantwoord mist een exact duurzaam receiptanker');
    const [matched] = unused.splice(index, 1);
    return String(matched.row.completion_record_sha256 || '');
  });
  if (receiptSha256s.some((sha) => !/^[a-f0-9]{64}$/u.test(sha))
    || new Set(receiptSha256s).size !== receiptSha256s.length) {
    throw new Error('uitvoerantwoord heeft geen unieke duurzame receiptset');
  }
  return Object.freeze({
    projections: Object.freeze(projections),
    receiptSha256s: Object.freeze(receiptSha256s),
  });
}

function issueInterpretationReplyCandidate({
  understanding, item, episode, source, executionTurnId,
  text = '', toolResults = [],
}) {
  if (!isIssuedTurnUnderstanding(understanding)) {
    throw new Error('antwoordkandidaat vereist een werkelijk uitgegeven understanding');
  }
  const retry = interpretationRetryResume(item, episode);
  if (!retry?.valid || retry.phase !== 'understood'
    || retry.understandingSha256 !== understanding.sha256) {
    throw new Error('antwoordkandidaat wijkt af van de duurzame understandingbinding');
  }
  const decisionKind = String(understanding.firstDecision?.kind || '');
  const issuedTurnId = String(understanding.source?.turnId || '');
  const durableExecutionTurnId = String(executionTurnId || '');
  if (!issuedTurnId) throw new Error('antwoordkandidaat mist haar uitgegeven runtimebeurt');
  if (!durableExecutionTurnId) throw new Error('antwoordkandidaat mist haar duurzame uitvoeringsbeurt');
  let replyText;
  let basisSha256;
  let basisReceiptSha256s = [];
  if (source === 'model_reply_draft') {
    if (understanding.source?.kind !== 'model' || decisionKind !== 'reply') {
      throw new Error('alleen een werkelijk modeluitgegeven reply kan als terminaal modelantwoord worden gepind');
    }
    replyText = sealedInterpretationReplyText(understanding.firstDecision.replyDraft);
    basisSha256 = understanding.sha256;
  } else if (source === 'verified_execution_reply') {
    if (!['act', 'inspect', 'schedule'].includes(decisionKind)) {
      throw new Error('uitvoerantwoord vereist een uitvoerend modelbesluit');
    }
    const basis = issuedInterpretationReplyReceiptBasis({
      toolResults, issuedTurnId, executionTurnId: durableExecutionTurnId,
      originalTextSha256: retry.originalTextSha256,
      understandingSha256: understanding.sha256,
    });
    replyText = sealedInterpretationReplyText(text);
    basisSha256 = createHash('sha256')
      .update(canonicalJson(basis.projections)).digest('hex');
    basisReceiptSha256s = [...basis.receiptSha256s];
  } else {
    throw new Error('onbekende antwoordprovenance');
  }
  const provenance = Object.freeze({
    contract: INTERPRETATION_REPLY_PROVENANCE_CONTRACT,
    source,
    decisionKind,
    episodeId: Number(episode.id),
    workItemId: Number(item.id),
    issuedTurnId,
    executionTurnId: durableExecutionTurnId,
    originalTextSha256: retry.originalTextSha256,
    originSha256: interpretationRetryOriginSha256(episode),
    planSha256: retry.planSha256,
    understandingSha256: understanding.sha256,
    basisSha256,
    basisReceiptSha256s,
    supersessionScopeSha256: retry.supersessionScope
      ? createHash('sha256').update(retry.supersessionScope).digest('hex') : null,
    supersessionTokenSha256: retry.supersessionToken
      ? createHash('sha256').update(retry.supersessionToken).digest('hex') : null,
    textSha256: createHash('sha256').update(replyText).digest('hex'),
  });
  const candidate = Object.freeze({ text: replyText, provenance });
  ISSUED_INTERPRETATION_REPLY_CANDIDATES.add(candidate);
  return candidate;
}

function prepareIssuedInterpretationRetryDelivery(item, episode, candidate, sessionRef = null) {
  if (!ISSUED_INTERPRETATION_REPLY_CANDIDATES.has(candidate)) {
    throw new Error('alleen een intern uitgegeven antwoordkandidaat kan worden gepind');
  }
  const retry = interpretationRetryResume(item, episode);
  const provenance = candidate.provenance;
  if (!retry?.valid || retry.phase !== 'understood'
    || String(sessionRef || '') !== provenance.executionTurnId
    || retry.originalTextSha256 !== provenance.originalTextSha256
    || retry.planSha256 !== provenance.planSha256
    || retry.understandingSha256 !== provenance.understandingSha256
    || (retry.supersessionScope
      ? createHash('sha256').update(retry.supersessionScope).digest('hex')
        !== provenance.supersessionScopeSha256
      : provenance.supersessionScopeSha256 != null)
    || (retry.supersessionToken
      ? createHash('sha256').update(retry.supersessionToken).digest('hex')
        !== provenance.supersessionTokenSha256
      : provenance.supersessionTokenSha256 != null)
    || interpretationRetryOriginSha256(episode) !== provenance.originSha256) {
    throw new Error('antwoordkandidaat verloor haar duurzame plan- of oorsprongsbinding');
  }
  const state = durableWorkState(item);
  delete state.interpretation_retry;
  const dedupeKey = `episode:${episode.id}:interpretation-retry-result`;
  const provenanceSha256 = createHash('sha256')
    .update(canonicalJson(provenance)).digest('hex');
  const replyBinding = {
    contract: INTERPRETATION_REPLY_PROVENANCE_CONTRACT,
    provenanceSha256,
    dedupeKey,
    supersessionScope: retry.supersessionScope || null,
    supersessionScopeSha256: provenance.supersessionScopeSha256,
    supersessionTokenSha256: provenance.supersessionTokenSha256,
  };
  state.pending_delivery = {
    kind: 'interpretation_reply', text: candidate.text, recipient: null,
    preparedAt: new Date().toISOString(), attempts: 0, dedupeKey,
    provenance, provenanceSha256,
  };
  state.interpretation_reply_binding = replyBinding;
  const wakeTs = new Date().toISOString();
  return transitionGoalEpisode(episode.id, {
    state: 'delivery_pending', wakeTs,
    checkpoint: `Gebonden modelantwoord voorbereid: ${provenanceSha256}.`,
    nextAction: 'Bezorg het antwoord aan de oorspronkelijke verzoeker.', sessionRef,
    eventKind: 'interpretation_reply_prepared',
    idempotencyKey: `interpretation-reply:${provenanceSha256}`,
    work: {
      state, nextWakeTs: wakeTs,
      wait: { reason: 'delivery_retry', resumeAfter: wakeTs, attempts: 0 },
      nextStep: 'Bezorg het antwoord aan de oorspronkelijke verzoeker.',
      lastAction: 'antwoord_voorbereid', lastError: '',
    },
  });
}

function registerInteractiveIngress(
  channel, conversationId, messageContext = null, verifiedNativeSource = null,
) {
  let scope = '';
  let token = '';
  if (['telegram', 'voice'].includes(channel)) {
    if (!verifiedNativeSource) return { scope, token };
    scope = String(verifiedNativeSource.ingressScope || '');
    token = String(verifiedNativeSource.ingressToken || '');
    if (!scope || !token
      || String(messageContext?.ingressScope || '') !== scope
      || String(messageContext?.ingressToken || '') !== token) {
      throw Object.assign(new Error(`${channel}-ingress mist haar geverifieerde supersessionbinding`), {
        code: 'EVA_TURN_AUTHORITY_INGRESS_MISMATCH',
      });
    }
  } else if (channel === 'web' && messageContext?.syntheticTest !== true) {
    const lane = String(conversationId || '').trim();
    if (!lane) return { scope, token };
    scope = `${channel}:${lane}`;
    token = String(messageContext?.messageId || '').trim()
      || `${Date.now()}:${Math.random().toString(36).slice(2, 12)}`;
  } else {
    return { scope, token };
  }
  interactiveIngressByScope.set(scope, token);
  if (interactiveIngressByScope.size > 2_000) {
    const oldest = interactiveIngressByScope.keys().next().value;
    if (oldest) interactiveIngressByScope.delete(oldest); }
  return { scope, token };
}

function autonomyEffectRequiresReport(toolOrName, args = null) {
  const name = typeof toolOrName === 'string' ? toolOrName : toolOrName?.name;
  const toolArgs = args || (typeof toolOrName === 'object' ? toolOrName?.args : null) || {};
  // Een normale, omkeerbare huisroutine is bewezen werk maar geen verplichte
  // Telegram-aflevering. Alleen effecten met duidelijke accountability worden
  // hier hard meldplichtig; Eva blijft vrij om iets werkelijk relevants zelf te
  // delen via haar afzonderlijke communicatie-oordeel.
  if (name === 'bedien_huis') {
    return HIGH_IMPACT_HOUSE_EFFECT.test(`${toolArgs.doel || ''} ${toolArgs.actie || ''}`);
  }
  if (!toolRequiresEffectReport(name)) return false;
  // Een eigen wekker is alleen Eva's interne planning. Die is geen resultaat
  // voor het gezin en mag een episode dus niet voortijdig afleverplichtig maken.
  if (name === 'zet_herinnering') {
    const target = String(toolArgs.voor_wie || 'Eva').trim().toLowerCase();
    if (!target || target === 'eva') return false;
  }
  return true;
}
function syntheticReplyNeedsPersistentState(text = '') {
  const value = String(text || '');
  return /\b(?:morgen(?:ochtend|middag|avond)?|vanavond|later|om\s+\d{1,2}(?::\d{2})?|volgende\s+(?:keer|week|dag))\b/i.test(value)
    && (/\b(?:ik|we)\b.{0,80}\b(?:ga|gaan|pak|pakken|doe|doen|hervat|vervolg|kijk|zoek|controleer|pas)\b/i.test(value)
      || /\b(?:pak|pakken|doe|doen|hervat|vervolg|kijk|zoek|controleer|pas)\b.{0,40}\b(?:ik|we)\b/i.test(value));
}
/** @param {{mode?: string, syntheticTest?: boolean, turnKind?: string, hasEpisode?: boolean, name?: string, recorded?: {ok?: boolean, verified?: boolean, changed?: boolean}}} [options] */
function shouldAutoBindDurableTool({
  mode, syntheticTest, turnKind, hasEpisode, name, recorded,
} = {}) {
  return mode === 'chat' && !syntheticTest && !hasEpisode
    && (turnKind === 'self_repair' || !String(turnKind).startsWith('self_'))
    && name !== 'werk_bijhouden'
    && toolIsDurable(name) && recorded?.ok && recorded?.verified && recorded?.changed;
}

const EXPLORATION_FINISH_TOOLS = new Set([
  'verkenning_bijhouden', 'signalen_afhandelen', 'stuur_bericht',
]);

function boundExplorationToolError(exploration, name = '') {
  if (!exploration || EXPLORATION_FINISH_TOOLS.has(name)) return '';
  if (exploration.status === 'evaluating') {
    return `Het zelfgekozen actiebudget van verkenning #${exploration.id} is bereikt. Beoordeel of sluit die verkenning nu; voer geen extra inhoudelijke stap uit.`;
  }
  return ['completed', 'abandoned', 'inconclusive'].includes(exploration.status)
    ? `Verkenning #${exploration.id} is in deze beurt afgesloten. Rond diezelfde focus nu af; een ander onderwerp krijgt een nieuwe aandachtbeurt.`
    : '';
}

function rejectedToolOutput(name, error, failureClass = 'policy') {
  return JSON.stringify({
    contract: 'ActionResult/v1', tool: name, ok: false, changed: false,
    verified: false, needs_followup: true, partial: false,
    failure_class: failureClass, retryable: false, effect_uncertain: false,
    execution_started: false, error,
  });
}

function closedAutonomyBoundaryOutput(name, error) {
  return rejectedToolOutput(name, error, 'policy');
}

function interpretationRetryPlanSha256(semanticTurn) {
  return createHash('sha256').update(canonicalJson({
    decision: semanticTurn?.understanding?.firstDecision?.kind || '',
    plans: (semanticTurn?.plans || []).map((plan) => ({
      requirementId: plan.requirementId,
      capabilityId: plan.capabilityId, operationId: plan.operationId,
      toolName: plan.toolName, effect: plan.effect, args: plan.args,
      execution: plan.execution || null,
      semanticBinding: {
        requirementId: plan.requirementId,
        addressesRequirementIds: [plan.requirementId],
        capabilityId: plan.capabilityId,
        operationId: plan.operationId,
        role: plan.execution?.lane || plan.role || 'primary',
        executionType: plan.execution?.type || null,
      },
    })),
  })).digest('hex');
}

function interpretationRetryLedgerBinding(binding, retry) {
  if (!retry?.valid || !binding) return binding;
  return {
    ...binding, understandingSha256: retry.originalTextSha256, basisReceiptIds: [],
  };
}

function resolveSelfAttentionGoalPlanStep({
  semanticTurn, plan, args, semanticBinding, receiptSemanticBinding,
  resumedInterpretationRetry, assertCurrent,
}) {
  if (!BOUNDED_SELF_ATTENTION_TURNS.has(semanticTurn)
    || plan.capabilityId !== 'goal.portfolio.manage') {
    return { args, receiptSemanticBinding };
  }
  assertCurrent();
  const resolution = resolveIssuedSelfAttentionGoalPlan(plan, Date.now());
  if (!resolution) throw new TypeError('missing_self_attention_goal_resolution');
  semanticBinding.targetResolution = resolution.provenance;
  return {
    args: resolution.args,
    receiptSemanticBinding: interpretationRetryLedgerBinding(
      semanticBinding, resumedInterpretationRetry,
    ),
  };
}

function prepareTurnToolCall({
  name, args, turnKind, workId, toolResults, userText,
  person, monitoring, monitoringIssuanceContext = null,
  supersedingMessage, semanticBinding = null,
}) {
  const newerMessage = supersedingMessage();
  if (newerMessage) {
    const error = Object.assign(
      new Error(`deze beurt is ingehaald door Telegram-bericht ${newerMessage.message_id}`),
      { code: 'EVA_TURN_SUPERSEDED' },
    );
    throw error;
  }
  // Een semantisch startplan heeft nog geen work-id, maar moet al wél een
  // concreet meetmoment, onderwerp en afleverdoel dragen.
  const boundWorkStart = name === 'werk_bijhouden'
    && semanticBinding?.capabilityId === 'work.episode.manage' && semanticBinding?.operationId === 'start';
  const boundMonitoringStart = turnKind === 'monitoring' && boundWorkStart;
  const boundContinuityStart = turnKind === 'continuity' && boundWorkStart;
  const projectedStartArgs = boundMonitoringStart || boundContinuityStart ? projectMonitoringStartArgs({ args, monitoring, now: Date.now() }) : args;
  const continuityStart = boundContinuityStart ? projectContinuityWorkStart(projectedStartArgs, monitoring, toolResults, person) : null;
  if (continuityStart && !continuityStart.accepted) return { args: projectedStartArgs,
    output: rejectedToolOutput(name, 'De begrensde hercontrole start niet zonder verse huiscontext uit dezelfde beurt.') };
  const monitoringContract = boundMonitoringStart
    ? createMonitoringContract(
      { monitoring, args: projectedStartArgs, toolResults }, monitoringIssuanceContext,
    ) : null;
  if (boundMonitoringStart
    && (!monitoringContract || !monitoringContractReadBindings(monitoringContract)?.length)) {
    return {
      args: projectedStartArgs,
      output: rejectedToolOutput(name,
        'Monitoring start niet: er is nog geen exacte, verse en geverifieerde Home Assistant-meetbinding.'),
    };
  }
  const suppliedState = objectFromJson(projectedStartArgs.toestand_json);
  const nonAuthoritativeState = { ...suppliedState };
  delete nonAuthoritativeState.monitoring;
  delete nonAuthoritativeState.monitoring_runtime;
  delete nonAuthoritativeState.pending_delivery;
  const boundArgs = boundMonitoringStart
    ? {
      ...projectedStartArgs,
      // De ontvanger is authority uit de oorspronkelijke beurt, geen vrije
      // formulering van het model. Zo kunnen “Jaap via Telegram” of een andere
      // beschrijving nooit een andere principal maken of de guard laten vallen.
      ...(monitoring?.deliveryRequired === false
        ? { afleveren_aan: '' }
        : { afleveren_aan: String(monitoring?.recipient || person || '').trim() }),
      // Ook de hervatcontext is host-owned. Modelstate mag nuttige niet-privilege
      // hints bewaren, maar kan geen meetbron of toekomstige delivery klaarzetten.
      ...(monitoringContract ? {
        toestand_json: JSON.stringify({
          ...nonAuthoritativeState,
          monitoring: monitoringContract,
        }),
      } : {}),
    }
    : continuityStart?.args || projectedStartArgs;
  const guardError = turnToolGuard({
    turnKind, name, args: boundArgs, workId, toolResults, userText, person,
    monitoring: boundMonitoringStart ? { ...monitoring, allowStart: true } : monitoring,
  });
  if (guardError) return { args: boundArgs, output: rejectedToolOutput(name, guardError) };
  return { args: boundArgs, output: null,
    trustedWorkState: monitoringContract ? {
      monitoring: monitoringContract,
      monitoring_start_authority: monitoringIssuanceContext?.startAuthority || null,
      monitoring_start_delivery_origin: monitoringIssuanceContext?.deliveryOrigin || null,
    } : null };
}

function autonomousMessageGate({
  mode, syntheticTest, name, args, toolResults, durableGoalEpisode,
}) {
  if (mode !== 'autonomy' || syntheticTest || name !== 'stuur_bericht') return null;
  if (toolResults.some((tool) => tool.name === 'stuur_bericht'
    && tool.ok === true && tool.verified === true && tool.changed === true)) {
    return JSON.stringify({
      contract: 'ActionResult/v1', tool: 'stuur_bericht', ok: false,
      changed: false, verified: false, needs_followup: false,
      error: 'In deze aandachtbeurt is al één gezinsbericht bezorgd.',
    });
  }
  const review = reviewAutonomousMessage(args.tekst, {
    mustDeliver: durableGoalEpisode?.delivery_policy === 'required'
      || autonomousMessageNeedsAccountability(args.tekst),
  });
  if (review.decision === 'silent') {
    return JSON.stringify({
      contract: 'ActionResult/v1',
      tool: 'stuur_bericht',
      ok: true,
      changed: false,
      verified: true,
      needs_followup: false,
      evidence: { source: 'communication_judgment', summary: review.reason },
      human_message: 'Dit resultaat blijft bewust intern; er is geen gezinsbericht verstuurd.',
      data: { suppressed: true, reason: review.reason },
    });
  }
  if (review.decision !== 'rewrite') return null;
  return JSON.stringify({
    contract: 'ActionResult/v1', tool: 'stuur_bericht', ok: false,
    changed: false, verified: false, needs_followup: true,
    error: 'Formuleer hetzelfde concrete gevolg opnieuw in gewone thuistaal, zonder interne bewijs-, tool-, registry- of entitytaal.',
  });
}

function exactStringList(value, expected) {
  return Array.isArray(value) && value.length === expected.length
    && value.every((item, index) => item === expected[index]);
}

function exactAuthenticFreeAutonomyContext({
  mode, messageContext, resumeContext = null, schedulerEpoch = null,
} = /** @type {any} */ ({})) {
  const signals = messageContext?.signalBatch?.signals;
  return mode === 'autonomy' && messageContext?.syntheticTest !== true
    && resumeContext == null && Number.isSafeInteger(schedulerEpoch) && schedulerEpoch >= 0
    && messageContext && Object.isFrozen(messageContext)
    && messageContext.attention === null && messageContext.workId === null
    && messageContext.goalEpisodeId === null
    && Array.isArray(messageContext.pendingSignals)
    && Object.isFrozen(messageContext.pendingSignals) && messageContext.pendingSignals.length === 0
    && validSignalBatch(messageContext.signalBatch)
    && Array.isArray(signals) && Object.isFrozen(signals) && signals.length === 0;
}

function inertObservationConditions(value) {
  return Array.isArray(value) && (value.length === 0 || (value.length === 1
    && value[0]?.role === 'guard' && value[0]?.state === 'required'
    && exactStringList(value[0]?.requirementIds, [])
    && exactStringList(value[0]?.afterRequirementIds, [])));
}

function exactObservationTemporalBoundary(understanding, plan) {
  const temporal = understanding?.temporal;
  const delivery = understanding?.delivery;
  return understanding?.durability === 'turn' && temporal?.mode === 'now'
    && temporal?.notBefore == null && temporal?.dueBy == null && temporal?.condition == null
    && exactStringList(temporal?.startSourceSpans, [])
    && ['none', 'silent'].includes(delivery?.mode) && delivery?.target === 'none'
    && inertObservationConditions(understanding.conditions)
    && understanding.firstDecision?.kind === 'inspect'
    && exactStringList(understanding.firstDecision?.requirementIds, ['r1'])
    && exactStringList(understanding.requestedEffects, [plan?.effect])
    && exactStringList(understanding.deniedEffects, []);
}

function exactObservationAction(action, plan) {
  return action?.requirementId === 'r1' && action?.capabilityId === 'internet.research'
    && action?.operationId === 'search' && action?.polarity === 'request'
    && action?.authorization === 'allowed' && action?.effect === plan.effect
    && exactStringList(action?.boundaryReasons, [])
    && Object.keys(action?.semanticInput || {}).join(',') === 'vraag'
    && canonicalJson(plan.args) === canonicalJson(action.semanticInput);
}

function exactObservationPlan(plan) {
  return ['read', 'discovery'].includes(plan?.effect)
    && plan?.requirementId === 'r1' && plan?.capabilityId === 'internet.research'
    && plan.operationId === 'search' && plan.toolName === 'zoek_internet'
    && plan.execution?.mode === 'direct' && plan.supportsDeferred === false
    && Object.isFrozen(plan) && Object.isFrozen(plan.args)
    && Object.keys(plan.args || {}).join(',') === 'vraag'
    && typeof plan.args.vraag === 'string' && plan.args.vraag.trim().length >= 8
    && !hasUnresolvedReference(plan.args);
}

function exactObservationContributionUnderstanding(semanticTurn, plan, turnId) {
  const understanding = semanticTurn?.understanding;
  const action = understanding?.actions?.[0];
  return semanticTurn?.ok === true && typeof turnId === 'string' && turnId.length > 0
    && isIssuedTurnUnderstanding(understanding) && Object.isFrozen(understanding)
    && semanticTurn?.plans?.length === 1 && semanticTurn.plans[0] === plan
    && understanding.source?.origin === 'self' && understanding.source?.turnId === turnId
    && understanding.actions?.length === 1
    && exactObservationTemporalBoundary(understanding, plan)
    && exactObservationAction(action, plan)
    && exactObservationPlan(plan);
}

function bindIssuedAspirationExplorationPlans(understanding, plans) {
  const issued = consumeIssuedAspirationExplorationAnchor(understanding);
  if (!issued) return null;
  const [startPlan, nextPlan] = Array.isArray(plans) ? plans : [];
  const startAction = issued.startAction, nextAction = issued.nextAction;
  const exact = plans.length === 2
    && startPlan?.requirementId === 'r1'
    && startPlan.capabilityId === 'exploration.manage'
    && startPlan.operationId === 'start'
    && startPlan.toolName === 'verkenning_bijhouden'
    && startPlan.effect === 'persistent' && startPlan.idempotency === 'cas'
    && startPlan.supportsDeferred === false && startPlan.execution?.mode === 'direct'
    && startPlan.args?.bewerking === 'start'
    && startPlan.args?.aspiratie_id === issued.aspirationId
    && !hasUnresolvedReference(startPlan.args)
    && nextPlan?.requirementId === 'r2'
    && ['read', 'discovery', 'workspace'].includes(nextPlan.effect)
    && nextPlan.supportsDeferred === false && !hasUnresolvedReference(nextPlan.args)
    && startAction === understanding.actions[0]
    && nextAction === understanding.actions[1]
    && startAction.requirementId === startPlan.requirementId
    && startAction.capabilityId === startPlan.capabilityId
    && startAction.operationId === startPlan.operationId
    && nextAction.requirementId === nextPlan.requirementId
    && nextAction.capabilityId === nextPlan.capabilityId
    && nextAction.operationId === nextPlan.operationId
    && nextAction.effect === nextPlan.effect;
  if (!exact) throw new TypeError('invalid_issued_aspiration_exploration_anchor');
  return Object.freeze({
    understandingSha256: understanding.sha256,
    aspirationId: issued.aspirationId,
    startPlan, nextPlan,
  });
}

function bindIssuedExplorationTerminalPlan(understanding, plans) {
  const action = understanding?.actions?.[0];
  const claim = bindIssuedExplorationTerminalContribution(understanding, action);
  if (!claim) return null;
  const plan = plans?.[0];
  const { bewerking: _operation, ...physicalInput } = plan?.args || {};
  const exact = plans.length === 1 && plan
    && plan.requirementId === 'r1'
    && plan.capabilityId === 'exploration.manage'
    && plan.operationId === 'afronden'
    && plan.toolName === 'verkenning_bijhouden'
    && plan.effect === 'persistent' && plan.idempotency === 'cas'
    && plan.supportsDeferred === false && plan.execution?.mode === 'direct'
    && plan.args?.bewerking === 'afronden'
    && Number(plan.args.id) === Number(action.semanticInput?.id)
    && Number(plan.args.verwachte_revisie)
      === Number(action.semanticInput?.verwachte_revisie)
    && canonicalJson(physicalInput) === canonicalJson(action.semanticInput)
    && Object.isFrozen(plan) && Object.isFrozen(plan.args)
    && !hasUnresolvedReference(plan.args);
  if (!exact) throw new TypeError('invalid_issued_exploration_terminal_contribution');
  return Object.freeze({
    understandingSha256: understanding.sha256,
    claim,
    plan,
    explorationId: Number(plan.args.id),
    fromRevision: Number(plan.args.verwachte_revisie),
  });
}

function exactAuthenticBoundExplorationTerminalContext({
  semanticTurn, mode, messageContext, resumeContext, schedulerEpoch,
  initialOpenWorkIds, boundExplorationId, goalEpisode,
} = /** @type {any} */ ({})) {
  const profile = semanticTurn?.explorationTerminalContribution;
  const context = issuedExplorationContinuationContext(messageContext?.attention);
  const signals = messageContext?.signalBatch?.signals;
  return profile && mode === 'autonomy' && semanticTurn.syntheticTest !== true
    && BOUNDED_SELF_ATTENTION_TURNS.has(semanticTurn)
    && resumeContext == null && Number.isSafeInteger(schedulerEpoch) && schedulerEpoch >= 0
    && messageContext && Object.isFrozen(messageContext)
    && messageContext.attention?.kind === 'exploration'
    && context && context.id === profile.explorationId
    && context.revision === profile.fromRevision
    && Number(boundExplorationId) === profile.explorationId
    && messageContext.workId === null && messageContext.goalEpisodeId === null
    && Array.isArray(initialOpenWorkIds) && initialOpenWorkIds.length === 0
    && goalEpisode == null
    && Array.isArray(messageContext.pendingSignals)
    && Object.isFrozen(messageContext.pendingSignals)
    && messageContext.pendingSignals.length === 0
    && validSignalBatch(messageContext.signalBatch)
    && Array.isArray(signals) && Object.isFrozen(signals) && signals.length === 0
    ? profile : null;
}

function terminalContributionReceiptMatches(profile, recorded) {
  return [profile, recorded,
    recorded?.name === 'verkenning_bijhouden',
    recorded?.args === profile?.plan?.args,
    verifiedCompleteReceipt(recorded), typeof recorded?.changed === 'boolean',
    recorded?.effect_uncertain !== true,
  ].every(Boolean);
}

function terminalContributionBindingMatches(profile, binding) {
  return [binding,
    binding?.understandingSha256 === profile?.understandingSha256,
    binding?.requirementId === 'r1',
    binding?.capabilityId === 'exploration.manage',
    binding?.operationId === 'afronden', binding?.role === 'primary',
    exactStringList(binding?.addressesRequirementIds, ['r1']),
    exactStringList(binding?.basisReceiptIds, []),
  ].every(Boolean);
}

function terminalEconomicHandoffMatches(profile, data, meta, outcomeKey) {
  const physical = profile?.plan?.args?.economisch_vervolg;
  const optedIn = physical && typeof physical === 'object' && !Array.isArray(physical);
  if (!optedIn) {
    return !Object.hasOwn(meta, 'economicHandoff')
      && !Object.hasOwn(data, 'economic_handoff');
  }
  const normalized = (value, limit) => String(value ?? '')
    .replace(/\s+/gu, ' ').trim().slice(0, limit);
  const safe = (value, limit) => normalized(redactSensitiveText(value), limit);
  const commitment = {
    title: safe(physical.titel, 240),
    outcome: safe(physical.uitkomst, 1_200),
    definitionOfDone: safe(physical.definitie_klaar, 1_200),
    nextAction: safe(physical.volgende_stap, 600),
  };
  const handoff = meta.economicHandoff;
  const projection = data.economic_handoff;
  const episode = goalEpisodeById(projection?.episode_id);
  let storedState = null;
  try {
    const row = /** @type {any} */ (db.prepare(
      'SELECT state_json FROM eva_work_items WHERE id=?',
    ).get(Number(projection?.work_id)));
    storedState = JSON.parse(String(row?.state_json || '{}'));
  } catch { return false; }
  return Boolean(handoff && projection && episode
    && handoff.contract === 'ExplorationEconomicHandoff/v1'
    && handoff.sourceOutcomeKey === outcomeKey
    && handoff.goalKey === 'economic_value'
    && Number(handoff.explorationId) === profile.explorationId
    && Number(handoff.fromRevision) === profile.fromRevision
    && Number(handoff.toRevision) === profile.fromRevision + 1
    && /^[a-f0-9]{64}$/u.test(String(handoff.evidenceSha256 || ''))
    && canonicalJson(handoff.authority) === canonicalJson({
      internalWork: true, familyDelivery: true,
      externalContact: false, publication: false, spend: false,
    })
    && canonicalJson(handoff.commitment) === canonicalJson(commitment)
    && canonicalJson(storedState?.exploration_economic_handoff) === canonicalJson(handoff)
    && projection.contract === 'ExplorationEconomicHandoff/v1'
    && projection.source_outcome_key === outcomeKey
    && typeof projection.created === 'boolean'
    && Number.isSafeInteger(Number(projection.work_id)) && Number(projection.work_id) >= 1
    && Number.isSafeInteger(Number(projection.episode_id)) && Number(projection.episode_id) >= 1
    && projection.goal_key === 'economic_value'
    && projection.delivery_policy === 'milestone' && projection.delivery_target == null
    && Number(episode.work_item_id) === Number(projection.work_id)
    && episode.goal_key === 'economic_value' && episode.origin_kind === 'self'
    && episode.origin_channel === 'autonomy' && episode.requester == null
    && episode.delivery_policy === 'milestone' && episode.delivery_target == null);
}

function terminalBehaviorExperimentMatches(profile, data, meta, outcomeKey) {
  const physical = profile?.plan?.args?.gedragsproef;
  const optedIn = physical && typeof physical === 'object' && !Array.isArray(physical);
  if (!optedIn) {
    return !Object.hasOwn(meta, 'behaviorExperiment')
      && !Object.hasOwn(data, 'behavior_experiment');
  }
  if (Object.hasOwn(profile?.plan?.args || {}, 'economisch_vervolg')) return false;
  const attestation = meta.behaviorExperiment;
  const projection = data.behavior_experiment;
  const stored = replayBehaviorExperiment(attestation);
  return Boolean(attestation && projection && stored
    && attestation.contract === 'BehaviorLearningProposal/v2'
    && attestation.sourceKind === 'self_initiated'
    && attestation.sourceOutcomeKey === outcomeKey
    && /^[a-f0-9]{64}$/u.test(String(attestation.experimentKey || ''))
    && /^[a-f0-9]{64}$/u.test(String(attestation.candidateSha256 || ''))
    && /^[a-f0-9]{64}$/u.test(String(attestation.sourceEvidenceSha256 || ''))
    && projection.contract === 'BehaviorLearningExperiment/v1'
    && projection.source_outcome_key === outcomeKey
    && Number.isSafeInteger(Number(projection.id)) && Number(projection.id) >= 1
    && Number.isSafeInteger(Number(projection.person_id)) && Number(projection.person_id) >= 1
    && projection.candidate_sha256 === attestation.candidateSha256
    && typeof projection.created === 'boolean'
    && Number(projection.id) === Number(stored.id)
    && Number(projection.person_id) === Number(stored.personId)
    && projection.candidate_sha256 === stored.candidateSha256
    // De receipt attesteert de immutable creatiesnapshot. Een latere echte
    // aflevering of feedback mag haar status/revisie rechtmatig veranderen
    // zonder de oorspronkelijke terminale toolreceipt ongeldig te maken.
    && projection.status === 'canary_pending' && Number(projection.revision) === 2
    && stored.sourceKind === 'self_initiated'
    && stored.sourceOutcomeKey === outcomeKey);
}

function terminalContributionOutput(profile, output) {
  const data = output?.data || {};
  const item = data.exploration || {};
  const outcome = data.contribution || {};
  const expectedOutcomeKey = createHash('sha256').update(canonicalJson({
    contract: 'ExplorationTerminalContribution/v1',
    explorationId: profile.explorationId,
    fromRevision: profile.fromRevision,
    toRevision: profile.fromRevision + 1,
    terminalStatus: 'completed',
  })).digest('hex');
  const storedOutcome = /** @type {any} */ (db.prepare(`SELECT content,meta,outcome_key
    FROM main.timeline WHERE kind='contribution' AND outcome_key=?`).get(expectedOutcomeKey));
  let meta = null;
  try { meta = storedOutcome ? JSON.parse(String(storedOutcome.meta || '')) : null; } catch {
    return null;
  }
  const replayed = outcome.created === false && output?.changed === false
    && data.changed === false;
  const created = outcome.created === true && output?.changed === true
    && data.changed === true;
  const exact = [output?.contract === 'ActionResult/v1',
    output?.tool === 'verkenning_bijhouden',
    output?.ok === true, output?.verified === true, created || replayed,
    output?.partial !== true, output?.needs_followup !== true,
    output?.effect_uncertain !== true, data.truncated !== true,
    data.ok === true, data.verified === true,
    outcome.contract === 'ExplorationTerminalContribution/v1',
    outcome.outcome_key === expectedOutcomeKey,
    storedOutcome?.outcome_key === expectedOutcomeKey,
    typeof storedOutcome?.content === 'string' && storedOutcome.content.length > 0,
    meta.outcomeKey === outcome.outcome_key,
    meta.source === 'bijdrage', meta.status === 'done',
    meta.lifecycleContract === 'ExplorationTerminalContribution/v1',
    meta.understandingSha256 === profile.understandingSha256,
    meta.requirementId === 'r1',
    Number(meta.explorationId) === profile.explorationId,
    Number(meta.fromRevision) === profile.fromRevision,
    Number(meta.toRevision) === profile.fromRevision + 1,
    meta.terminalStatus === 'completed',
    typeof meta.lastAction === 'string' && meta.lastAction
      && meta.lastAction !== 'verkenning_bijhouden',
    typeof meta.evidence === 'string' && meta.evidence.length >= 8,
    Number(item.id) === profile.explorationId,
    item.source === 'self', item.status === 'completed',
    Number(item.revision) === profile.fromRevision + 1,
    terminalEconomicHandoffMatches(profile, data, meta, expectedOutcomeKey),
    terminalBehaviorExperimentMatches(profile, data, meta, expectedOutcomeKey),
  ].every(Boolean);
  return exact ? { item, outcome, meta, content: storedOutcome.content, replayed } : null;
}

function terminalContributionCurrentMatches(profile, meta) {
  const current = explorationById(profile.explorationId);
  return [current, current?.status === 'completed',
    Number(current?.revision) === profile.fromRevision + 1,
    (current?.aspiration_id == null ? null : Number(current.aspiration_id))
      === (meta.aspirationId == null ? null : Number(meta.aspirationId)),
    String(current?.result || '') === String(meta.evidence),
  ].every(Boolean);
}

function terminalExecutionContext(profile, execution, plan) {
  return profile && profile.plan === plan
    ? { explorationTerminalContribution: execution } : {};
}

function verifiedExplorationTerminalContribution(profile, recorded) {
  if (!terminalContributionReceiptMatches(profile, recorded)
    || !terminalContributionBindingMatches(profile, recorded?.semanticBinding)) return null;
  let output;
  try { output = JSON.parse(String(recorded.output)); } catch { return null; }
  const projection = terminalContributionOutput(profile, output);
  if (!projection || !terminalContributionCurrentMatches(profile, projection.meta)) return null;
  const { outcome, meta } = projection;
  if (projection.replayed) return Object.freeze({ replayed: true, contribution: null });
  return Object.freeze({
    contribution: Object.freeze({
      ...recorded,
      evidence: Object.freeze({
        source: 'exploration_terminal', summary: meta.evidence,
      }),
      explorationTerminalContribution: true,
    }),
    outcomeKey: outcome.outcome_key,
    content: projection.content,
    meta: Object.freeze({ ...meta }),
  });
}

function verifiedAspirationExplorationStart(profile, recorded, cognitiveEpisodeId) {
  if (!profile) return null;
  if (!recorded) return null;
  const startPlan = profile.startPlan;
  if (!startPlan) return null;
  if (![
    recorded.name === 'verkenning_bijhouden',
    canonicalJson(recorded.args) === canonicalJson(startPlan.args),
    verifiedCompleteReceipt(recorded), recorded.changed === true,
    recorded.effect_uncertain !== true,
  ].every(Boolean)) return null;
  const binding = recorded.semanticBinding;
  if (!binding) return null;
  if (![
    binding.understandingSha256 === profile.understandingSha256,
    binding.requirementId === 'r1', binding.capabilityId === 'exploration.manage',
    binding.operationId === 'start', binding.role === 'primary',
    exactStringList(binding.addressesRequirementIds, ['r1']),
    exactStringList(binding.basisReceiptIds, []),
  ].every(Boolean)) return null;
  let output;
  try { output = JSON.parse(String(recorded.output)); } catch { return null; }
  if (!output) return null;
  if (typeof output !== 'object') return null;
  const data = output.data;
  if (!data) return null;
  if (typeof data !== 'object') return null;
  const item = data.exploration;
  if (!item) return null;
  if (typeof item !== 'object') return null;
  if (![
    output.contract === 'ActionResult/v1', output.tool === 'verkenning_bijhouden',
    output.ok === true, output.verified === true, output.changed === true,
    output.partial !== true, output.needs_followup !== true,
    output.effect_uncertain !== true, data.truncated !== true,
    data.ok === true, data.changed === true,
    Number.isSafeInteger(Number(item.id)), Number(item.id) >= 1,
    Number(item.aspiration_id) === profile.aspirationId,
    Number(item.source_episode_id) === Number(cognitiveEpisodeId),
    item.source === 'self', item.status === 'chosen', Number(item.action_count) === 0,
    String(item.question) === String(startPlan.args.vraag),
    String(item.topic_key) === String(startPlan.args.onderwerp_sleutel),
    canonicalJson(item.topic_anchors) === canonicalJson(startPlan.args.onderwerp_ankers),
  ].every(Boolean)) return null;
  const current = explorationById(item.id);
  if (!current) return null;
  if (![
    Number(current.id) === Number(item.id),
    Number(current.aspiration_id) === profile.aspirationId,
    Number(current.source_episode_id) === Number(cognitiveEpisodeId),
    current.source === 'self', current.status === 'chosen',
    Number(current.action_count) === 0,
    Number(current.revision) === Number(item.revision),
    String(current.question) === String(item.question),
    String(current.topic_key) === String(item.topic_key),
  ].every(Boolean)) return null;
  return Object.freeze({
    explorationId: Number(item.id),
    revision: Number(item.revision),
    expected: Object.freeze({
      id: Number(current.id), revision: Number(current.revision),
      status: String(current.status), actionCount: Number(current.action_count),
      maxActions: Number(current.max_actions), question: String(current.question),
    }),
  });
}

function aspirationExplorationAnchorStillCurrent(profile, settlement, boundExplorationId) {
  if (!profile || !settlement
    || Number(boundExplorationId) !== settlement.explorationId) return false;
  const current = explorationById(settlement.explorationId);
  const expected = settlement.expected;
  return Boolean(current && Number(current.aspiration_id) === profile.aspirationId
    && current.source === 'self' && current.status === expected?.status
    && Number(current.id) === expected?.id
    && Number(current.revision) === expected?.revision
    && Number(current.action_count) === expected?.actionCount
    && Number(current.max_actions) === expected?.maxActions
    && String(current.question) === expected?.question);
}

function issueObservationContributionPrecommit({
  semanticTurn, mode, messageContext, resumeContext, schedulerEpoch,
  initialOpenWorkIds, boundExplorationId, goalEpisode, turnId, turnCurrent,
} = /** @type {any} */ ({})) {
  const plan = semanticTurn?.plans?.[0];
  if (!exactAuthenticFreeAutonomyContext({
    mode, messageContext, resumeContext, schedulerEpoch,
  }) || initialOpenWorkIds?.length !== 0 || boundExplorationId != null
    || goalEpisode != null || turnCurrent !== true
    || !exactObservationContributionUnderstanding(semanticTurn, plan, turnId)) return null;
  const outcomeKey = createHash('sha256').update(canonicalJson({
    contract: OBSERVATION_CONTRIBUTION_CONTRACT,
    turnId,
    understandingSha256: semanticTurn.understanding.sha256,
    requirementId: plan.requirementId,
    capabilityId: plan.capabilityId,
    operationId: plan.operationId,
    args: plan.args,
  })).digest('hex');
  return Object.freeze({
    contract: OBSERVATION_CONTRIBUTION_CONTRACT,
    semanticTurn, understanding: semanticTurn.understanding,
    plan, args: plan.args, turnId, outcomeKey,
  });
}

function verifiedHttpSources(value) {
  if (!Array.isArray(value) || value.length < 1 || value.length > 20) return null;
  const sources = [];
  for (const item of value) {
    if (typeof item !== 'string' || item.length > 2_048) return null;
    try {
      const source = new URL(item);
      if (!['http:', 'https:'].includes(source.protocol)
        || source.username || source.password) return null;
      sources.push(source.href);
    } catch { return null; }
  }
  return sources.length ? Object.freeze([...new Set(sources)]) : null;
}

function exactObservationReceiptBinding(precommit, toolResults) {
  if (!precommit || toolResults?.length !== 1) return null;
  const receipt = toolResults[0];
  const semantic = receipt?.semanticBinding;
  if (receipt?.name !== precommit.plan.toolName || receipt?.args !== precommit.args
    || !verifiedCompleteReceipt(receipt) || receipt.changed === true
    || receipt.effect_uncertain === true
    || semantic?.understandingSha256 !== precommit.understanding.sha256
    || semantic?.requirementId !== 'r1'
    || !exactStringList(semantic?.addressesRequirementIds, ['r1'])
    || !exactStringList(semantic?.basisReceiptIds, [])
    || semantic?.capabilityId !== 'internet.research' || semantic?.operationId !== 'search'
    || semantic?.role !== 'primary' || semantic?.executionType != null) return null;
  return receipt;
}

function exactObservationActionResult(receipt) {
  let parsed;
  try { parsed = JSON.parse(String(receipt.output || '')); } catch { return null; }
  const data = parsed?.data;
  if (parsed?.contract !== 'ActionResult/v1' || parsed?.tool !== 'zoek_internet'
    || parsed?.ok !== true || parsed?.verified !== true || parsed?.changed !== false
    || parsed?.partial !== false || parsed?.needs_followup !== false
    || parsed?.audit_truncated === true || parsed?.evidence?.source !== 'zoek_internet'
    || !data || typeof data !== 'object' || Array.isArray(data)
    || data.truncated === true || data.audit_truncated === true) return null;
  return { parsed, data };
}

function materialObservationData(data) {
  const sources = verifiedHttpSources(data.sources);
  const message = String(data.message || '').replace(/\s+/gu, ' ').trim();
  const materialWords = message.match(/[\p{L}\p{N}]{3,}/gu) || [];
  if (data.ok !== true || data.verified !== true || data.searched !== true
    || !Number.isSafeInteger(data.searchCalls) || data.searchCalls < 1
    || data.searchCalls > 20 || !sources || message.length < 40
    || new Set(materialWords.map((word) => word.toLowerCase())).size < 6
    || /geen bruikbare resultaten gevonden/iu.test(message)) return null;
  const summary = redactSensitiveText(message).slice(0, 1_200).trim();
  if (summary.length < 40) return null;
  return { sources, summary };
}

function materialObservationReceipt(precommit, toolResults) {
  const receipt = exactObservationReceiptBinding(precommit, toolResults);
  if (!receipt) return null;
  const actionResult = exactObservationActionResult(receipt);
  if (!actionResult) return null;
  const material = materialObservationData(actionResult.data);
  return material ? Object.freeze({
    receipt, parsed: actionResult.parsed,
    sources: material.sources, summary: material.summary,
  }) : null;
}

function settleObservationContribution({
  precommit, toolResults, result, mode, turnCurrent,
} = /** @type {any} */ ({})) {
  const material = materialObservationReceipt(precommit, toolResults);
  if (!material || mode !== 'autonomy' || turnCurrent !== true
    || result?.toolFinal !== true || result?.understood !== true
    || result?.actionFailed === true || result?.durableProofMissing === true
    || result?.executionPending === true || result?.preparedRetryDelivery === true
    || result?.deliveryOnly === true || result?.superseded === true || result?.yielded === true
    || result?.error) return null;
  const prior = OBSERVATION_CONTRIBUTION_SETTLEMENTS.get(precommit);
  if (prior) return prior.receipt === material.receipt ? prior : null;
  const contribution = Object.freeze({
    ...material.receipt,
    evidence: Object.freeze({
      source: 'verified_web_research',
      summary: material.summary,
      sources: material.sources,
    }),
    observationContribution: true,
  });
  const content = `Zelfstandige onderzoeksnotitie: ${material.summary}`.slice(0, 500);
  const meta = Object.freeze({
    internalOnly: true,
    turnId: precommit.turnId,
    source: 'bijdrage',
    owner: null,
    evidence: material.summary,
    lastAction: contribution.name,
    status: 'done',
    syntheticTest: false,
    lifecycleContract: 'ObservationContribution/v1',
    observationKind: 'internet_research',
    understandingSha256: precommit.understanding.sha256,
    requirementId: precommit.plan.requirementId,
    sources: material.sources,
  });
  const persisted = logContributionOutcome(precommit.outcomeKey, content, meta);
  const settlement = Object.freeze({
    contribution, receipt: material.receipt, row: persisted.row,
    outcomeKey: precommit.outcomeKey,
  });
  OBSERVATION_CONTRIBUTION_SETTLEMENTS.set(precommit, settlement);
  return settlement;
}

const FREE_AUTONOMY_CONTINUATION_ADMIN_TOOLS = new Set([
  'verkenning_bijhouden', 'toon_werk', 'signalen_afhandelen',
]);

const FREE_AUTONOMY_CONTINUATION_ADMIN_CAPABILITIES = new Set([
  'exploration.read', 'exploration.manage', 'work.episode.read',
  'goal.portfolio.read', 'signal.assess',
]);

function autonomyAdministrativeRead(value) {
  const name = value?.toolName || value?.name || '';
  const args = value?.args || {};
  const effect = value?.effect || toolEffect(name, args);
  const capabilityId = value?.capabilityId || value?.semanticBinding?.capabilityId || '';
  const operationId = value?.operationId || value?.semanticBinding?.operationId || '';
  if (capabilityId === 'goal.portfolio.read') {
    return name === 'doel_bijhouden' && effect === 'read' && operationId === 'list';
  }
  return ['read', 'discovery'].includes(effect)
    && (FREE_AUTONOMY_CONTINUATION_ADMIN_TOOLS.has(name)
      || FREE_AUTONOMY_CONTINUATION_ADMIN_CAPABILITIES.has(capabilityId));
}

function autonomyTurnAdministrativeStep(value) {
  const name = value?.toolName || value?.name || '';
  return ['verkenning_bijhouden', 'toon_werk', 'signalen_afhandelen'].includes(name)
    || autonomyAdministrativeRead(value);
}

function materialFreeAutonomyRead(plan, receipt) {
  return ['read', 'discovery'].includes(plan?.effect)
    && plan?.execution?.mode === 'direct' && plan?.supportsDeferred === false
    && !autonomyAdministrativeRead(plan)
    && receipt?.name === plan.toolName && receipt?.args === plan.args
    && verifiedCompleteReceipt(receipt) && receipt.changed !== true
    && receipt.partial !== true && receipt.needs_followup !== true
    && receipt.effect_uncertain !== true
    && checkpointEvidence(receipt).length >= 8;
}

function freeAutonomyContinuationCandidates(plans) {
  return Object.freeze((Array.isArray(plans) ? plans : []).filter((plan) => (
    ['read', 'discovery'].includes(plan?.effect)
    && plan?.execution?.mode === 'direct' && plan?.supportsDeferred === false
    && !autonomyAdministrativeRead(plan)
    && !SELF_ATTENTION_PROVIDER_BACKED_DIRECT.has(
      `${plan?.capabilityId}\u0000${plan?.operationId}`,
    )
    && !hasUnresolvedReference(plan?.args)
    && Object.isFrozen(plan) && Object.isFrozen(plan.args)
  )));
}

function exactFreeAutonomyContinuationOpportunity({
  semanticTurn, plan, receipt, planIndex, mode, messageContext, resumeContext,
  schedulerEpoch, initialOpenWorkIds, boundExplorationId, goalEpisode, turnCurrent,
} = /** @type {any} */ ({})) {
  const candidates = freeAutonomyContinuationCandidates(
    semanticTurn?.plans?.slice(Number(planIndex) + 1),
  );
  const noIssuedStart = !(semanticTurn?.plans || []).some((item) => (
    item?.capabilityId === 'exploration.manage' && item?.operationId === 'start'
  ));
  const earlierSubstantive = (semanticTurn?.plans || []).slice(0, Number(planIndex))
    .some((item) => !autonomyAdministrativeRead(item));
  return !earlierSubstantive && noIssuedStart && candidates.length > 0
    && exactAuthenticFreeAutonomyContext({
      mode, messageContext, resumeContext, schedulerEpoch,
    })
    && initialOpenWorkIds?.length === 0 && boundExplorationId == null
    && goalEpisode == null && turnCurrent === true
    && materialFreeAutonomyRead(plan, receipt)
    ? candidates : null;
}

function bindIssuedFreeAutonomyStartPlan({
  claim, plan, remainingPlans, firstPlan, firstReceipt,
  messageContext, turnId, schedulerEpoch, basisReceiptId,
} = /** @type {any} */ ({})) {
  const semanticInput = claim?.action?.semanticInput;
  const { bewerking, ...physicalInput } = plan?.args || {};
  const exact = claim?.choice === 'start_exploration'
    && claim.action?.requirementId === 'continuation_exploration_start'
    && plan?.requirementId === claim.action.requirementId
    && plan?.capabilityId === 'exploration.manage' && plan?.operationId === 'start'
    && plan?.toolName === 'verkenning_bijhouden' && plan?.effect === 'persistent'
    && plan?.idempotency === 'cas' && plan?.supportsDeferred === false
    && plan?.execution?.mode === 'direct' && bewerking === 'start'
    && canonicalJson(physicalInput) === canonicalJson(semanticInput)
    && basisReceiptId === claim?.decision?.basisReceiptId
    && /^s[1-9][0-9]*$/u.test(String(basisReceiptId || ''))
    && Object.isFrozen(plan) && Object.isFrozen(plan.args)
    && Array.isArray(claim.selectedPlans) && claim.selectedPlans.length === 1
    && claim.selectedPlans.every((selected) => remainingPlans.includes(selected));
  if (!exact) return null;
  const binding = Object.freeze({
    claim, plan, args: plan.args, remainingPlans,
    selectedPlans: claim.selectedPlans, firstPlan, firstReceipt,
    messageContext, turnId, schedulerEpoch, basisReceiptId,
  });
  ISSUED_FREE_AUTONOMY_START_PLANS.set(plan, binding);
  return binding;
}

function consumeIssuedFreeAutonomyStartPlan(binding, context = {}) {
  const issued = binding?.plan && ISSUED_FREE_AUTONOMY_START_PLANS.get(binding.plan);
  if (!issued || issued !== binding || CONSUMED_FREE_AUTONOMY_START_PLANS.has(binding.plan)
    || issued.plan !== context.plan || issued.args !== context.args
    || issued.remainingPlans !== context.remainingPlans
    || issued.selectedPlans !== context.selectedPlans
    || issued.firstPlan !== context.firstPlan || issued.firstReceipt !== context.firstReceipt
    || issued.basisReceiptId !== context.basisReceiptId
    || issued.messageContext !== context.messageContext
    || issued.turnId !== context.turnId || issued.schedulerEpoch !== context.schedulerEpoch
    || context.turnCurrent !== true) return false;
  CONSUMED_FREE_AUTONOMY_START_PLANS.add(binding.plan);
  return true;
}

function consumeFreeAutonomyExecutionPrecommit({
  startBinding, selectedReadBinding, plan, args, messageContext, turnId,
  schedulerEpoch, turnCurrent, cognitiveEpisodeId,
} = /** @type {any} */ ({})) {
  let atomicStart = null;
  if (startBinding) {
    const consumedStart = consumeIssuedFreeAutonomyStartPlan(startBinding, {
      plan, args,
      remainingPlans: startBinding.remainingPlans,
      selectedPlans: startBinding.selectedPlans,
      firstPlan: startBinding.firstPlan,
      firstReceipt: startBinding.firstReceipt,
      basisReceiptId: startBinding.basisReceiptId,
      messageContext, turnId, schedulerEpoch, turnCurrent,
    });
    if (consumedStart) {
      atomicStart = issueFreeAutonomyExplorationAtomicStart({
        decision: startBinding.claim.decision,
        startArgs: args,
        basisPlan: startBinding.firstPlan,
        basisReceipt: startBinding.firstReceipt,
        basisReceiptId: startBinding.basisReceiptId,
        basisEvidence: checkpointEvidence(startBinding.firstReceipt),
        sourceEpisodeId: cognitiveEpisodeId,
        turnId,
      });
    }
    if (!consumedStart || !atomicStart) return Object.freeze({
      ok: false, atomicStart: null,
      reason: 'De zelfgekozen vervolgstart is niet meer exact aan deze actuele waarneming gebonden.',
    });
  }
  const selectedCurrent = !selectedReadBinding || consumeIssuedFreeAutonomySelectedRead(
    selectedReadBinding,
    { plan, args, messageContext, turnId, schedulerEpoch, turnCurrent },
  );
  return Object.freeze(selectedCurrent
    ? { ok: true, atomicStart, reason: '' }
    : {
        ok: false, atomicStart: null,
        reason: 'De geselecteerde vervolgwaarneming is niet meer exact aan de actuele exploration-revisie gebonden.',
      });
}

function freeAutonomyStartReceiptEnvelopeMatches(binding, recorded) {
  return Boolean(binding && recorded?.name === 'verkenning_bijhouden'
    && recorded.args === binding.args && verifiedCompleteReceipt(recorded)
    && recorded.changed === true && recorded.effect_uncertain !== true);
}

function freeAutonomyStartSemanticMatches(binding, semantic) {
  return semantic?.understandingSha256 === binding.claim?.decision?.sha256
    && semantic?.requirementId === 'continuation_exploration_start'
    && exactStringList(semantic?.addressesRequirementIds, ['continuation_exploration_start'])
    && exactStringList(semantic?.basisReceiptIds, [binding.basisReceiptId])
    && semantic?.capabilityId === 'exploration.manage'
    && semantic?.operationId === 'start'
    && semantic?.role === 'free_autonomy_continuation';
}

function freeAutonomyAtomicStartOutputMatches(output, binding, cognitiveEpisodeId) {
  const item = output?.data?.exploration;
  const atomicBasis = output?.data?.atomic_basis;
  return output?.contract === 'ActionResult/v1' && output.tool === 'verkenning_bijhouden'
    && output.ok === true && output.verified === true && output.changed === true
    && output.partial !== true && output.needs_followup !== true
    && output.effect_uncertain !== true && output.data?.truncated !== true
    && output.data?.ok === true && output.data?.changed === true
    && Number.isSafeInteger(Number(item?.id)) && Number(item.id) >= 1
    && item.source === 'self' && item.status === 'running'
    && item.aspiration_id == null
    && Number(item.source_episode_id) === Number(cognitiveEpisodeId)
    && Number(item.action_count) === 1 && Number(item.revision) >= 2
    && String(item.question) === String(binding.plan.args.vraag)
    && String(item.topic_key) === String(binding.plan.args.onderwerp_sleutel)
    && canonicalJson(item.topic_anchors) === canonicalJson(binding.plan.args.onderwerp_ankers)
    && Number(item.max_actions) === Number(binding.plan.args.max_acties)
    && atomicBasis?.contract === 'FreeAutonomyExplorationAtomicStart/v1'
    && atomicBasis?.decision_sha256 === binding.claim.decision.sha256
    && atomicBasis?.receipt_sha256 === binding.claim.decision.basisReceiptSha256
    && atomicBasis?.receipt_id === binding.basisReceiptId
    && atomicBasis?.tool === binding.firstReceipt.name
    && Number(atomicBasis?.episode_id) === Number(cognitiveEpisodeId);
}

function freeAutonomyAtomicStartCurrentMatches(current, item, cognitiveEpisodeId) {
  return Boolean(current && Number(current.id) === Number(item.id)
    && Number(current.revision) === Number(item.revision)
    && current.source === 'self' && current.status === 'running'
    && current.aspiration_id == null
    && Number(current.source_episode_id) === Number(cognitiveEpisodeId)
    && Number(current.action_count) === 1
    && Number(current.max_actions) === Number(item.max_actions)
    && String(current.question) === String(item.question)
    && String(current.topic_key) === String(item.topic_key));
}

function verifiedFreeAutonomyExplorationStart(binding, recorded, cognitiveEpisodeId) {
  if (!freeAutonomyStartReceiptEnvelopeMatches(binding, recorded)) return null;
  const semantic = recorded.semanticBinding;
  if (!freeAutonomyStartSemanticMatches(binding, semantic)) return null;
  let output;
  try { output = JSON.parse(String(recorded.output || '')); } catch { return null; }
  const item = output?.data?.exploration;
  if (!freeAutonomyAtomicStartOutputMatches(output, binding, cognitiveEpisodeId)) return null;
  const current = explorationById(item.id);
  if (!freeAutonomyAtomicStartCurrentMatches(current, item, cognitiveEpisodeId)) return null;
  return Object.freeze({
    explorationId: Number(current.id),
    expected: Object.freeze({
      id: Number(current.id), revision: Number(current.revision),
      status: String(current.status), actionCount: Number(current.action_count),
      maxActions: Number(current.max_actions), question: String(current.question),
    }),
  });
}

function exactFreeAutonomyExplorationSnapshot(explorationId, expected) {
  const current = explorationById(explorationId);
  return Boolean(current && expected
    && Number(current.id) === Number(explorationId)
    && Number(current.id) === Number(expected.id)
    && Number(current.revision) === Number(expected.revision)
    && String(current.status) === String(expected.status)
    && Number(current.action_count) === Number(expected.actionCount)
    && Number(current.max_actions) === Number(expected.maxActions)
    && String(current.question) === String(expected.question));
}

function bindIssuedFreeAutonomySelectedRead({
  claim, plan, startBinding, startSettlement, turnId, messageContext, schedulerEpoch,
} = /** @type {any} */ ({})) {
  const exact = claim?.choice === 'start_exploration'
    && claim.selectedPlans?.length === 1 && claim.selectedPlans[0] === plan
    && startBinding?.claim === claim && startSettlement?.explorationId >= 1
    && plan && Object.isFrozen(plan) && Object.isFrozen(plan.args)
    && ['read', 'discovery'].includes(plan.effect)
    && plan.execution?.mode === 'direct' && plan.supportsDeferred === false
    && !autonomyAdministrativeRead(plan)
    && !SELF_ATTENTION_PROVIDER_BACKED_DIRECT.has(
      `${plan.capabilityId}\u0000${plan.operationId}`,
    )
    && !hasUnresolvedReference(plan.args)
    && exactFreeAutonomyExplorationSnapshot(
      startSettlement.explorationId, startSettlement.expected,
    );
  if (!exact) return null;
  const binding = Object.freeze({
    claim, plan, args: plan.args, startBinding, startSettlement,
    explorationId: startSettlement.explorationId,
    expected: startSettlement.expected,
    turnId, messageContext, schedulerEpoch,
  });
  ISSUED_FREE_AUTONOMY_SELECTED_READS.set(plan, binding);
  return binding;
}

function consumeIssuedFreeAutonomySelectedRead(binding, context = /** @type {any} */ ({})) {
  const issued = binding?.plan && ISSUED_FREE_AUTONOMY_SELECTED_READS.get(binding.plan);
  if (!issued || issued !== binding || CONSUMED_FREE_AUTONOMY_SELECTED_READS.has(binding.plan)
    || binding.plan !== context.plan || binding.args !== context.args
    || binding.messageContext !== context.messageContext
    || binding.turnId !== context.turnId || binding.schedulerEpoch !== context.schedulerEpoch
    || context.turnCurrent !== true
    || !exactFreeAutonomyExplorationSnapshot(binding.explorationId, binding.expected)) return false;
  CONSUMED_FREE_AUTONOMY_SELECTED_READS.add(binding.plan);
  return true;
}

function settleIssuedFreeAutonomySelectedRead(
  binding, recorded, semanticBinding, continuation, episodeId,
) {
  const issued = binding?.plan && ISSUED_FREE_AUTONOMY_SELECTED_READS.get(binding.plan);
  if (!issued || issued !== binding
    || !CONSUMED_FREE_AUTONOMY_SELECTED_READS.has(binding.plan)
    || SETTLED_FREE_AUTONOMY_SELECTED_READS.has(binding.plan)
    || recorded?.name !== binding.plan.toolName || recorded?.args !== binding.args
    || semanticBinding?.understandingSha256
      !== binding.startBinding.claim.decision.understandingSha256
    || semanticBinding?.requirementId !== binding.plan.requirementId
    || continuation?.contract !== binding.claim.decision.contract
    || continuation?.decisionSha256 !== binding.claim.decision.sha256
    || continuation?.basisReceiptSha256 !== binding.claim.decision.basisReceiptSha256
    || continuation?.basisReceiptId !== binding.claim.decision.basisReceiptId
    || Number(continuation?.explorationId) !== Number(binding.explorationId)
    || !exactStringList(continuation?.selectedRequirementIds,
      binding.claim.decision.remainingRequirementIds)
    || !exactFreeAutonomyExplorationSnapshot(binding.explorationId, binding.expected)) return null;
  const noted = noteIssuedExplorationAction(binding.explorationId, binding.expected, {
    tool: recorded.name,
    ok: recorded.ok,
    verified: recorded.verified,
    evidence: checkpointEvidence(recorded),
    episodeId,
  });
  if (!noted.accepted || Number(noted.exploration?.revision) !== binding.expected.revision + 1
    || Number(noted.exploration?.action_count) !== binding.expected.actionCount + 1
    || Number(noted.exploration?.max_actions) !== binding.expected.maxActions
    || String(noted.exploration?.question) !== binding.expected.question) return null;
  const settlement = Object.freeze({
    explorationId: binding.explorationId,
    fromRevision: binding.expected.revision,
    toRevision: noted.exploration.revision,
    actionCount: noted.exploration.action_count,
    status: noted.exploration.status,
  });
  SETTLED_FREE_AUTONOMY_SELECTED_READS.set(binding.plan, settlement);
  return settlement;
}

function issuedFreeAutonomySelectedReadSettlement(binding) {
  return binding?.plan ? SETTLED_FREE_AUTONOMY_SELECTED_READS.get(binding.plan) || null : null;
}

function governedPlanSemanticBinding(plan, executionContext, understanding) {
  return {
    understandingSha256: executionContext?.understandingSha256
      || understanding?.sha256 || null,
    requirementId: plan.requirementId,
    addressesRequirementIds: executionContext?.addressesRequirementIds
      || [plan.requirementId],
    basisReceiptIds: executionContext?.basisReceiptIds || [],
    capabilityId: plan.capabilityId,
    operationId: plan.operationId,
    role: executionContext?.semanticRole
      || plan.execution?.lane || plan.role || 'primary',
    executionType: plan.execution?.type || null,
  };
}

function primaryHomeReceiptShape(receipt, parentPlan) {
  return receipt?.name === 'huis_status' && receipt?.args === parentPlan?.args
    && receipt?.ok === true && receipt?.verified === true && receipt?.partial === true
    && receipt?.needs_followup === true && receipt?.changed === false
    && receipt?.effect_uncertain === false && receipt?.retryable === false
    && receipt?.failure_class === 'partial_result';
}

function primaryHomeResultShape(result) {
  return result?.contract === 'ActionResult/v1' && result.tool === 'huis_status'
    && result.ok === true && result.verified === true && result.partial === true
    && result.needs_followup === true && result.changed === false
    && result.effect_uncertain === false && result.retryable === false
    && result.failure_class === 'partial_result' && result.data?.history_required === true;
}

function boundedHomeResultEntities(result) {
  const entities = Array.isArray(result?.data?.entities) ? result.data.entities : [];
  const ids = entities.map((item) => String(item?.entity_id || '').trim());
  return ids.length > 0 && ids.length < 24 && new Set(ids).size === ids.length
    && ids.every((id) => /^[a-z0-9_]+\.[a-z0-9_]+$/u.test(id));
}

function primaryHomeReceiptSemantic(receipt, understanding) {
  const semantic = receipt?.semanticBinding;
  return semantic?.understandingSha256 === understanding?.sha256
    && semantic?.requirementId === 'r1'
    && exactStringList(semantic?.addressesRequirementIds, ['r1'])
    && exactStringList(semantic?.basisReceiptIds, [])
    && semantic?.capabilityId === 'home.context.read' && semantic?.operationId === 'read'
    && semantic?.role === 'primary';
}

function safePrimaryHomeContextPartialReceipt(receipt, parentPlan, understanding) {
  let result = null;
  try { result = JSON.parse(String(receipt?.output || '')); } catch { return false; }
  return primaryHomeReceiptShape(receipt, parentPlan) && primaryHomeResultShape(result)
    && boundedHomeResultEntities(result) && primaryHomeReceiptSemantic(receipt, understanding);
}

function homeContextRecoveryHasDependency(understanding) {
  return (understanding?.conditions || []).some((condition) => (
    condition?.role === 'action'
    && Array.isArray(condition.requirementIds)
    && condition.requirementIds.includes('r1')
    && Array.isArray(condition.afterRequirementIds)
    && condition.afterRequirementIds.length > 0
  ));
}

function exactFreeHomeRecoveryTurn(context) {
  return context.mode === 'autonomy' && context.syntheticTest !== true
    && Number.isSafeInteger(context.schedulerEpoch) && context.schedulerEpoch >= 0
    && typeof context.turnId === 'string' && context.turnId.length > 0
    && context.binding?.turnId === context.turnId
    && context.turnCurrent === true && context.attention == null && context.explorationId == null
    && context.goalEpisode == null && context.initialOpenWorkIds?.length === 0
    && Array.isArray(context.pendingSignals) && context.pendingSignals.length === 0;
}

function exactIssuedHomeRecoveryParent(parentPlan, issuedPlans) {
  return Array.isArray(issuedPlans) && issuedPlans.length === 1
    && issuedPlans[0] === parentPlan && parentPlan?.requirementId === 'r1'
    && parentPlan?.capabilityId === 'home.context.read' && parentPlan?.operationId === 'read'
    && parentPlan?.toolName === 'huis_status' && parentPlan?.effect === 'read'
    && parentPlan?.execution?.mode === 'direct' && parentPlan?.supportsDeferred === false
    && parentPlan?.args?.historie === true && !hasUnresolvedReference(parentPlan?.args);
}

function exactHomeRecoveryUnderstanding(binding, understanding) {
  const action = understanding?.actions?.[0];
  return binding?.understanding === understanding
    && understanding?.source?.origin === 'self' && understanding?.durability === 'turn'
    && understanding?.temporal?.mode === 'now'
    && exactStringList(understanding?.requestedEffects, ['read'])
    && understanding?.actions?.length === 1
    && action?.requirementId === 'r1' && action?.capabilityId === 'home.context.read'
    && action?.operationId === 'read' && understanding?.firstDecision?.kind === 'inspect'
    && exactStringList(understanding?.firstDecision?.requirementIds, ['r1'])
    && !homeContextRecoveryHasDependency(understanding);
}

function exactHomeRecoverySequence(context, parentPlan, receipt, entry) {
  const { binding, plan, args, plannedResults, toolResults } = context;
  return binding?.plan === plan && binding?.args === args
    && plan !== parentPlan && plan?.args === args && plan?.args !== parentPlan?.args
    && canonicalJson(plan?.args) === canonicalJson(parentPlan?.args)
    && entry?.plan === parentPlan && entry?.recorded === receipt
    && entry?.completed === false && entry?.deferredBinding === false
    && binding?.pendingSnapshot?.length === 1 && binding.pendingSnapshot[0] === entry
    && plannedResults === binding?.plannedResults && plannedResults?.length === 1
    && plannedResults[0] === entry && toolResults?.length === 1 && toolResults[0] === receipt;
}

function exactHomeRecoverySemantic(name, semanticBinding, understanding) {
  return name === 'huis_status'
    && semanticBinding?.understandingSha256 === understanding?.sha256
    && semanticBinding?.requirementId === 'r1'
    && exactStringList(semanticBinding?.addressesRequirementIds, ['r1'])
    && exactStringList(semanticBinding?.basisReceiptIds, ['s1'])
    && semanticBinding?.capabilityId === 'home.context.read'
    && semanticBinding?.operationId === 'read' && semanticBinding?.role === 'recovery';
}

function homeContextRecoveryPrecommitValid(context = /** @type {any} */ ({})) {
  const { binding, issuedPlans, understanding, name, semanticBinding } = context;
  const parentPlan = binding?.parentPlan;
  const receipt = binding?.parentReceipt;
  const entry = binding?.entry;
  return exactFreeHomeRecoveryTurn(context)
    && exactIssuedHomeRecoveryParent(parentPlan, issuedPlans)
    && exactHomeRecoveryUnderstanding(binding, understanding)
    && exactHomeRecoverySequence(context, parentPlan, receipt, entry)
    && exactHomeRecoverySemantic(name, semanticBinding, understanding)
    && safePrimaryHomeContextPartialReceipt(receipt, parentPlan, understanding);
}

function prepareAutonomyPlanStep({
  mode, name, args, plan, lane, toolResults, initialOpenWorkIds, pendingSignals,
  explorationId, syntheticTest, goalEpisode, proofOnly = false,
  consumeHomeContextRecoveryPrecommit = null,
} = /** @type {any} */ ({})) {
  if (mode !== 'autonomy') return { lane, command: null, episode: goalEpisode, output: null };
  const exploration = Number.isFinite(Number(explorationId))
    ? explorationById(explorationId) : null;
  const explorationError = proofOnly ? '' : boundExplorationToolError(exploration, name);
  if (explorationError) return { lane, command: null, episode: goalEpisode,
    output: rejectedToolOutput(name, explorationError) };
  const traceExempt = initialOpenWorkIds.length > 0
    || (Array.isArray(pendingSignals) && pendingSignals.length > 0);
  const substantiveCount = toolResults.filter((tool) => !autonomyTurnAdministrativeStep(tool)
    && !(tool.semanticBinding?.role === 'proof'
      && tool.semanticBinding?.executionType === 'readback')).length;
  const exactHomeContextRecovery = !traceExempt && !explorationId && !proofOnly
    && !autonomyTurnAdministrativeStep(plan || { name, args }) && substantiveCount === 1
    && typeof consumeHomeContextRecoveryPrecommit === 'function'
    && consumeHomeContextRecoveryPrecommit() === true;
  if (!traceExempt && !explorationId && !proofOnly
    && !autonomyTurnAdministrativeStep(plan || { name, args })
    && substantiveCount >= 1 && !exactHomeContextRecovery) {
    return { lane, command: null, episode: goalEpisode, output: rejectedToolOutput(name,
      'Leg nu eerst je eigen vraag, hypothese, verwachte waarneming, actiebudget en stopregel vast met verkenning_bijhouden. Het onderwerp blijft jouw keuze.') };
  }
  const nextLane = lane || autonomyLaneForTool(name);
  if (!autonomyLaneAllows(name, nextLane, toolResults)) return {
    lane: nextLane, command: null, episode: goalEpisode,
    output: closedAutonomyBoundaryOutput(name,
      `Deze zelfstandige beurt werkt al binnen ${nextLane}; rond die richting eerst af.`),
  };
  if (!autonomyDiscoveryAllowed(name, toolResults)) return {
    lane: nextLane, command: null, episode: goalEpisode,
    output: closedAutonomyBoundaryOutput(name,
      'De informatiegrens van deze zelfstandige beurt is bereikt. Rond het gekozen onderwerp nu af met bestaand bewijs of blijf stil.'),
  };
  if (syntheticTest || goalEpisode || !autonomyEffectRequiresReport(name, args)) {
    return { lane: nextLane, command: null, episode: goalEpisode, output: null };
  }
  const intendedOutcome = exploration?.question
    || `Voer de zelfgekozen ${name}-handeling alleen uit als het actuele bewijs en de eigen stopregel dat dragen.`;
  const durable = db.transaction(() => {
    const command = createWorkItem({
      source: 'autonomy', owner: null,
      title: `Zelfgekozen bedoeling: ${intendedOutcome.slice(0, 180)}`,
      goal: intendedOutcome,
      nextStep: `Voer ${name} gecontroleerd uit, lees het effect terug en verander bij twijfel van aanpak.`,
      definitionOfDone: exploration?.expected_observation
        || 'De bedoeling is uitgevoerd of bewust verworpen, met gecontroleerd bewijs en een passend eindbesluit.',
      priority: 80, visibleToFamily: true,
    });
    const episode = createGoalEpisode({
      goalKey: 'household_value', title: command.title,
      outcome: command.goal, definitionOfDone: command.definition_of_done,
      originKind: 'self', originChannel: 'autonomy', deliveryPolicy: 'milestone',
      nextAction: command.next_step,
    });
    const bound = bindEpisodeToWork(episode.id, command.id);
    const transition = transitionGoalEpisode(bound.id, {
      state: bound.state,
      work: exploration ? { state: {
        exploration_id: exploration.id,
        hypothesis: exploration.hypothesis,
        stop_rule: exploration.stop_rule,
      } } : {},
    });
    return { command: transition.workItem, episode: transition.episode };
  })();
  return { lane: nextLane, ...durable, output: null };
}

// Oud duurzaam werk en het nieuwe doelportfolio vormen vanaf nu één boog. Dit
// is een eenmalige, idempotente adoptie; open beloften verdwijnen niet omdat ze
// vóór de portfolio-migratie zijn ontstaan.
adoptUnboundWorkEpisodes();
recoverDeliveredEpisodeReceipts();
recoverStrandedDeliveryWork();
const AUTONOMY_DISCOVERY_LIMIT = SIGNAL_BATCH_MAX_ENTITY_KEYS;
const runtime = {
  active: 0,
  activity: 'luistert',
  current: null,
  lastTurn: null,
  lastInteractiveTurn: null,
  lastAutonomyTurn: null,
  interactivePending: 0,
  interactionEpoch: 0,
};

function queueFor(key, work) {
  const previous = queues.get(key) || Promise.resolve();
  const current = previous.catch(() => {}).then(work);
  queues.set(key, current);
  current.finally(() => {
    if (queues.get(key) === current) queues.delete(key);
  }).catch(() => {});
  return current;
}

function commandText(value) {
  return cleanUserText(value)
    .replace(/^\s*(?:(?:kun|kan|wil|zou)\s+(?:je|jij)|wil\s+je)\s+(?:(?:even|eens|alsjeblieft)\s+)*(?:gaan\s+)?/i, '')
    .trim();
}

function directHomeCommand(text = '') {
  const value = commandText(text).trim();
  let match = value.match(/^\s*(?:zet|schakel|doe)\s+(.{1,100}?)\s+(?:weer\s+)?(aan|uit)\s+(?:in|onder|op|bij)\s+(?:de\s+|het\s+)?(.{1,60}?)\s*[.!?]*$/i);
  let target;
  let action;
  if (match) {
    target = `${match[1]} in ${match[3]}`;
    action = match[2];
  } else {
    match = value.match(/^\s*(?:zet|schakel|doe)\s+(.{1,160}?)\s+(?:weer\s+)?(aan|uit)\s*[.!?]*$/i);
    if (match) {
      [, target, action] = match;
    } else {
      match = value.match(/^\s*(open|sluit)\s+(.{1,160}?)\s*[.!?]*$/i);
      if (match) {
        action = /^open$/i.test(match[1]) ? 'open' : 'dicht';
        target = match[2];
      } else {
        match = value.match(/^\s*(.{1,160}?)\s+(aan|uit)\s*(?:zetten|doen|schakelen)\s*[.!?]*$/i);
        if (!match) return null;
        [, target, action] = match;
      }
    }
  }
  target = String(target || '').replace(/^\s*(?:de|het|die|dat)\s+/i, '').trim();
  if (!target || /(?:,|\b(?:en|maar|niet)\b)/i.test(target) || /^(?:alle|alles)\b/i.test(target)) return null;
  return { doel: target, actie: String(action).toLowerCase() };
}

const MEMORY_STOP_WORDS = new Set([
  'aan', 'als', 'ben', 'bij', 'dat', 'de', 'deze', 'die', 'dit', 'een', 'en',
  'het', 'hier', 'hoe', 'ik', 'in', 'is', 'je', 'jij', 'maar', 'met', 'mij',
  'mijn', 'naar', 'niet', 'nog', 'nu', 'of', 'om', 'ons', 'ook', 'op', 'over',
  'te', 'van', 'voor', 'waar', 'wat', 'we', 'wel', 'wij', 'wil', 'zijn', 'zou',
]);

function recallWords(value) {
  return String(value || '')
    .normalize('NFD')
    .replace(/\p{Diacritic}/gu, '')
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, ' ')
    .split(/\s+/)
    .filter((word) => word.length > 2 && !MEMORY_STOP_WORDS.has(word));
}

function autonomousMemoryContextText() {
  const candidates = allMemories(500).filter((row) => {
    if (String(row.person || '').trim().toLowerCase() === 'eva') return false;
    const content = String(row.content || '');
    if (/^\s*Eva wil\b/i.test(content)
      || /\bwil dat (?:Eva|ik)\b.{0,120}\b(?:opdracht|routing|doorpak|achtergrondwerk)\w*/i.test(content)
      || /\bbedoelt met doorpakken\b/i.test(content)) return false;
    return ['afspraak', 'voorkeur'].includes(String(row.category || '').toLowerCase());
  });
  const projects = candidates.filter((row) => (
    String(row.category || '').toLowerCase() === 'afspraak'
    && /\b(?:staat nog open|open klus|proef|rapport|onderzoek\w*|uitzoek\w*|verifi(?:eer|eren))\b/i.test(row.content)
  ));
  const preferences = candidates.filter((row) => String(row.category || '').toLowerCase() === 'voorkeur');
  const distinct = (rows, limit) => {
    const selected = [];
    for (const row of rows) {
      const content = String(row.content || '');
      const candidate = new Set(recallWords(content));
      if (!candidate.size) continue;
      const duplicate = selected.some((existing) => {
        const words = new Set(recallWords(existing.content));
        const overlap = [...candidate].filter((word) => words.has(word)).length;
        return overlap / Math.min(candidate.size, words.size) >= 0.3;
      });
      if (!duplicate) selected.push(row);
      if (selected.length >= limit) break;
    }
    return selected;
  };
  const selectedProjects = distinct(projects, 2);
  const selectedPreferences = distinct(preferences, 4);
  const selected = [...selectedProjects, ...selectedPreferences];
  if (selected.length) bumpRecall(selected.map((row) => row.id));
  const render = (rows) => rows
    .map((row) => `- ${row.person ? `${row.person}: ` : ''}${String(row.content || '').slice(0, 320)}`)
    .join('\n');
  return [
    selectedProjects.length
      ? `Mogelijke eerdere projecten; behandel ze niet als open opdracht voordat hun actuele status is bewezen:\n${render(selectedProjects)}`
      : '',
    selectedPreferences.length
      ? `Gezinsvoorkeuren; dit zijn randvoorwaarden, geen opdrachten:\n${render(selectedPreferences)}`
      : '',
  ].filter(Boolean).join('\n\n');
}

function conversationIdFor({ channel, sender, messageContext = null }) {
  const explicit = String(messageContext?.conversationId || '').trim();
  if (explicit) return explicit;
  const chatId = String(messageContext?.chatId || '').trim();
  if (chatId) return `${channel}:${chatId}`;
  const person = String(sender || 'huisgenoot')
    .normalize('NFKC')
    .trim()
    .replace(/\s+/g, ' ')
    .toLowerCase();
  return `${channel}:${person}`;
}

function telegramConversationRows(messageContext, limit) {
  const chatId = String(messageContext?.chatId || '').trim();
  const messageId = String(messageContext?.messageId || '').trim();
  if (!chatId) return [];
  const boundedLimit = Math.max(1, Math.min(40, Number(limit) || 8));
  if (!messageId) return recentTelegramMessages({ chatId, limit: boundedLimit });
  const prior = recentTelegramMessages({
    chatId,
    beforeMessageId: messageId,
    limit: Math.max(1, boundedLimit - 1),
  });
  const current = telegramMessage(chatId, messageId);
  return (current ? [...prior, current] : prior).slice(-boundedLimit);
}

function telegramConversationText(row, sender, { includeDeliveryEvidence = false } = {}) {
  const content = String(row?.content || '').trim();
  let rendered;
  if (row?.kind === 'photo') {
    const who = row.role === 'assistant' ? 'Eva' : (row.sender || sender);
    rendered = `[${who} stuurde een foto; Telegram-bericht ${row.message_id}${row.media_ref ? `; foto-ref ${row.media_ref}` : ''}. Bijschrift: ${content || 'geen bijschrift'}]`;
  } else if (row?.kind && row.kind !== 'text') {
    rendered = `[${row.sender || sender} stuurde een Telegram-${row.kind}${content ? `: ${content}` : ''}]`;
  } else rendered = content;
  if (!includeDeliveryEvidence) return rendered;
  const evidence = verifiedDoorbellTelegramDeliveryEvidence(row);
  if (!evidence) return rendered;
  const visual = evidence.visualStatus === 'available' ? 'beschikbaar' : 'niet beschikbaar';
  const identity = evidence.identityStatus === 'recognized'
    ? `herkend als ${evidence.identityPerson}` : 'niet vaststelbaar';
  return `${rendered}\n[Geverifieerde bron bij dit bericht: Home Assistant-sensor ${evidence.sensor}; deurbeltrigger bevestigd; waargenomen ${evidence.observedAt}; plek: ${evidence.place}; beeld: ${visual}; identiteit: ${identity}.]`;
}

function conversationRowKey(row) {
  return String(row?._conversationKey
    || `${row?.conversation_id || row?.channel || 'conversation'}:${row?.id || ''}`);
}

function channelConversationRows({
  channel, conversationId, sender, messageContext = null, limit = 8,
}) {
  const boundedLimit = Math.max(1, Math.min(40, Number(limit) || 8));
  const generic = recentConversation(conversationId, boundedLimit);
  if (channel !== 'telegram') return generic;
  const telegramRows = telegramConversationRows(messageContext, boundedLimit);
  const evidenceIndex = telegramRows.length >= 2
    && telegramRows.at(-1)?.role === 'user'
    && telegramRows.at(-2)?.role === 'assistant'
    ? telegramRows.length - 2 : -1;
  const telegram = telegramRows.map((row, index) => ({
    ...row,
    channel: 'telegram',
    conversation_id: conversationId,
    content: telegramConversationText(row, sender, {
      includeDeliveryEvidence: index === evidenceIndex,
    }),
    _conversationKey: `telegram:${row.chat_id}:${row.message_id}`,
  }));
  if (!telegram.length) return generic;

  // telegram_messages is voor dit kanaal de canonieke lopende draad. Alleen
  // nog ontbrekende lokale chatregels mogen de oudere rand aanvullen; zij
  // mogen nooit een vers Telegram-bericht uit het begrensde venster drukken.
  const telegramContent = new Set(telegram.map((row) => (
    `${row.role}\u0000${String(row.content || '').trim()}`
  )));
  const room = Math.max(0, boundedLimit - telegram.length);
  const supplemental = room > 0 ? generic.filter((row) => !telegramContent.has(
    `${row.role}\u0000${String(row.content || '').trim()}`,
  )).slice(-room) : [];
  return [...supplemental, ...telegram];
}

const SYNTHETIC_CONVERSATION_ID = /(?:^|[:/_-])(?:test|synthetic|blackbox|canary|persona-eval)(?:$|[:/_-])/i;

function trustedConversationRecallAllowed({
  person, conversationId, messageContext = null, mode = 'chat',
}) {
  const name = String(person || '').trim();
  const currentConversation = String(conversationId || '').trim();
  if (mode !== 'chat' || messageContext?.identityTrusted !== true
    || messageContext?.syntheticTest === true || !name || !currentConversation
    || /^huisgenoot$/i.test(name) || SYNTHETIC_CONVERSATION_ID.test(currentConversation)) return false;
  return Boolean(db.prepare('SELECT id FROM people WHERE lower(name)=lower(?)').get(name));
}

function trustedCrossChannelConversationRows({
  person, channel, conversationId, messageContext = null, mode = 'chat', limit = 4,
}) {
  const name = String(person || '').trim();
  const currentConversation = String(conversationId || '').trim();
  if (!trustedConversationRecallAllowed({
    person: name, conversationId: currentConversation, messageContext, mode,
  })) return [];
  return recentOtherConversationsForPerson(name, currentConversation, Math.max(4, limit * 2))
    .filter((row) => row.channel !== channel)
    .filter((row) => !SYNTHETIC_CONVERSATION_ID.test(String(row.conversation_id || '')))
    .slice(-Math.max(1, Math.min(4, Number(limit) || 4)));
}

function trustedOlderConversationRows({
  person, conversationId, query, messageContext = null, mode = 'chat', limit = 3,
}) {
  const name = String(person || '').trim();
  const currentConversation = String(conversationId || '').trim();
  if (!trustedConversationRecallAllowed({
    person: name, conversationId: currentConversation, messageContext, mode,
  })) return [];
  return relevantOlderConversationForPerson(
    name, currentConversation, query, Math.max(1, Math.min(3, Number(limit) || 3)),
  ).filter((row) => !SYNTHETIC_CONVERSATION_ID.test(String(row.conversation_id || '')));
}

function claimsCompletedAction(value = '') {
  return /\b(?:gelukt|klaar|gereed|voltooid|afgerond|gemaakt|geschreven|uitgevoerd|getest|beproefd|gecontroleerd|opgeleverd|verstuurd|verzonden|geactiveerd|geïnstalleerd|werkt(?:\s+nu)?|functioneert(?:\s+nu)?|staat\s+nu\s+(?:aan|uit|open|dicht))\b/i
    .test(String(value || ''));
}

function explicitlyIncompleteAction(value = '') {
  const text = String(value || '');
  return /\b(?:nog\s+)?niet\s+(?:helemaal\s+)?(?:klaar|gereed|voltooid|afgerond|rond)\b/iu.test(text)
    || /\b(?:niet|niets|geen)\b.{0,60}\b(?:gemaakt|geschreven|uitgevoerd|getest|beproefd|gecontroleerd|opgeleverd|verstuurd|verzonden|geactiveerd|geïnstalleerd|gewijzigd|aangemaakt)\b/iu.test(text)
    || /\b(?:controle|bewijs|teruglezing|stap)\b.{0,80}\b(?:ontbreekt|ontbreken|openstaat|openstaan)\b/iu.test(text)
    || /\b(?:ontbreekt|ontbreken|openstaat|openstaan)\b.{0,80}\b(?:controle|bewijs|teruglezing|stap)\b/iu.test(text);
}

function conclusionInvestigationFeedback(reply = '', userText = '', toolResults = [], actionIntent = null) {
  const answer = String(reply || '').trim();
  const inspectConclusion = actionIntent == null
    ? /(?:^|[.!?]\s*)(?:zoek(?:\s+uit)?|onderzoek|controleer|diagnosticeer|probeer|maak|doe|regel|los)\b/i.test(userText)
    : actionIntent === 'execute';
  if (['explain', 'forbid'].includes(actionIntent) || !inspectConclusion) return '';
  const scope = `${userText}\n${answer}`;
  const claimsIncapacity = /\b(?:(?:kan|kon)\s+ik\b.{0,80}\bniet|kan\s+(?:eva|dit|dat|het)\b.{0,80}\bniet|kunnen\s+(?:we|dit|dat)\b.{0,80}\bniet|ik\s+kom\s+(?:nu\s+)?niet\s+bij|heb\s+ik\s+geen\s+toegang|geen\s+(?:schrijf|uitvoer|api|netwerk|account|browser|systeem)?toegang|niet\s+(?:ondersteund|beschikbaar|bereikbaar|mogelijk)|ontbreekt\s+(?:mij|me|hier)|(?:loop|liep)\s+ik\s+(?:nu\s+)?op\s+vast|daardoor\s+kan\s+ik\b.{0,100}\bniet|zoekroute\s+(?:hapert|werkt\s+niet)|ik\s+kan\b.{0,100}\b(?:niet\s+openen|niet\s+uitvoeren|niet\s+vaststellen|niet\s+bereiken))\b/i.test(answer);
  if (!claimsIncapacity) return '';

  const parsedResults = toolResults.map((tool) => {
    let parsed = null;
    try { parsed = JSON.parse(String(tool.output || '')); } catch { /* oudere uitvoer */ }
    return { tool, parsed };
  });
  const humanDependency = parsedResults.some(({ tool, parsed }) => (
    /^human_/i.test(String(parsed?.waiting_for || parsed?.data?.waiting_for || ''))
    && tekstOverlap(userText, `${JSON.stringify(tool.args || {})} ${tool.evidence?.summary || tool.output || ''}`) >= 0.04
  ));
  if (humanDependency) return '';

  const technicalOrExternal = /\b(?:api|endpoint|sdk|cli|website|site|online|internet|provider|dienst|account|inbox|e-?mail|integratie|koppeling|plugin|add-?on|bibliotheek|library|software|documentatie|docs?|token|sleutel|auth)\b/i.test(scope);
  const researched = toolResults.some((tool) => {
    if (tool.name !== 'zoek_internet' || !tool.ok || !tool.verified) return false;
    const query = String(tool.args?.vraag || '');
    const evidence = String(tool.evidence?.summary || tool.output || '');
    const officialIntent = /\b(?:offici[eë]le?|documentatie|docs?|api\s*reference|handleiding|developer)\b/i.test(query);
    return officialIntent && tekstOverlap(userText, `${query} ${evidence}`) >= 0.04;
  });
  const inspectedSelf = toolResults.some((tool) => (
    toolHasTag(tool.name, 'reasoning:mechanical')
    && tool.ok && tool.verified
  ));
  const attemptedAction = toolResults.some((tool) => (
    toolEffect(tool.name, tool.args) !== 'read'
    && (tool.changed === true || tool.ok !== true || tool.verified !== true)
    && tekstOverlap(userText, `${JSON.stringify(tool.args || {})} ${tool.evidence?.summary || tool.output || ''}`) >= 0.04
  ));
  const failed = toolResults.filter((tool) => tool.ok !== true || tool.verified !== true);
  const successfulAlternative = toolResults.some((tool) => (
    tool.ok && tool.verified && !failed.some((failure) => failure.name === tool.name)
    && tekstOverlap(userText, `${JSON.stringify(tool.args || {})} ${tool.evidence?.summary || tool.output || ''}`) >= 0.04
  ));

  if (!toolResults.length) {
    return 'Je conclusie is nog een aanname: je hebt niets geprobeerd of onderzocht. Onderzoek nu zelf de actuele mogelijkheid met aangeboden gereedschap. Gebruik bij een externe dienst eerst de officiële documentatie en controleer bij een eigen vermogen ook je relevante bron of actuele status. Geef nog geen blokkadeantwoord.';
  }
  if (technicalOrExternal && !researched) {
    return 'Voordat je zegt dat dit niet kan, zoek je nu zelf de actuele officiële documentatie van de betreffende dienst, API, integratie of software. Zoek naar de concrete beoogde handeling en authenticatieroute; een ontbrekende lokale knop of SDK is geen bewijs dat de mogelijkheid niet bestaat.';
  }
  if (technicalOrExternal && !inspectedSelf && !attemptedAction) {
    return 'De externe documentatie alleen is nog niet genoeg. Inspecteer nu ook je eigen relevante koppeling, capability of broncode en vergelijk wat je werkelijk hebt met de gedocumenteerde route. Houd geheimen intern.';
  }
  if (failed.length && !successfulAlternative) {
    return 'Eén mislukte route bewijst nog geen onvermogen. Controleer de actuele toestand of kies nu een materieel andere veilige aanpak. Herhaal een mutatie met onzeker effect niet blind.';
  }
  return '';
}

function incompleteRequestedOutcomeFeedback(reply = '', userText = '') {
  const request = String(userText || '');
  const answer = String(reply || '').trim();
  const asksForOutcome = /(?:^|[.!?]\s*)(?:zoek(?:\s+uit)?|onderzoek|controleer|diagnosticeer|maak|doe|regel|los)\b/i
    .test(request);
  const announcesNext = /^\s*(?:ik\s+)?(?:pak|ga|zal|moet)\b.{0,220}\b(?:zelf|nu|straks|daarna|eerst|route|uitzoek|onderzoek|control|bekijk|doe|maak)/i
    .test(answer)
    || /^\s*(?:de|mijn)\s+volgende\s+stap\b/i.test(answer);
  if (!asksForOutcome || !announcesNext || claimsCompletedAction(answer)) return '';
  return 'Dit is alleen een aankondiging van je volgende stap. Voer die stap nu met het passende gereedschap uit en geef pas daarna de gevraagde conclusie. Als de actuele bronnen nog geen volledige oorzaak bewijzen, geef dan wél de concrete tijdlijn die ze tonen en benoem precies welk oorzakelijk deel onbekend blijft.';
}

function styleCorrectionPolicyFeedback(reply = '', userText = '') {
  const correction = /\b(?:je\s+had|je\s+moet|had\s+kunnen|kijk\s+nou|hou\s+op|stop\s+met|klinkt|zakelijk|bot(?:achtig)?|spreektaal|doe\s+niet\s+zo|waarom\s+(?:deed|doe|keek|kijk))\b/i
    .test(String(userText || ''));
  const policyPromise = /\b(?:voortaan|vanaf\s+nu|in\s+het\s+vervolg|ik\s+zal|ik\s+ga\s+(?:proberen|zorgen)|neem\s+ik\b.{0,80}\bmee)\b/i
    .test(String(reply || ''));
  if (!correction || !policyPromise) return '';
  return 'Interne stijlcorrectie: reageer alleen op de concrete observatie en wat die hier betekent. Schrijf geen beleid, belofte of verbeterplan met “voortaan”, “vanaf nu” of “ik zal”; laat het begrepen gedrag in deze reactie zelf zien.';
}

function missingSourceLinkFeedback(reply = '', requiredTools = [], toolResults = []) {
  if (!requiredTools.includes('zoek_internet')
    || !toolResults.some((tool) => tool.name === 'zoek_internet' && tool.ok && tool.verified)
    || /https?:\/\/\S+/i.test(String(reply || ''))) return '';
  return 'Je gebruikt actuele internetfeiten maar laat de controleerbare herkomst weg. Noem in je gewone antwoord minimaal één directe bron-URL uit het gevonden bewijs; voeg geen verzonnen of algemene zoeklink toe.';
}

function completeHomeHistoryCoverage(tool) {
  const data = actionData(tool) || {};
  const binding = tool?.semanticBinding || {};
  const requestCount = data.request_count;
  const matchedEvents = data.matched_events;
  const returnedEvents = data.returned_events;
  const start = Date.parse(String(data.window?.start || ''));
  const end = Date.parse(String(data.window?.end || ''));
  return tool?.name === 'huis_status' && binding.role === 'recovery'
    && /^[a-f0-9]{64}$/u.test(String(binding.understandingSha256 || ''))
    && typeof binding.requirementId === 'string' && binding.requirementId.length > 0
    && binding.capabilityId === 'home.context.read' && binding.operationId === 'read'
    && verifiedCompleteReceipt(tool) && tool.changed === false && tool.effect_uncertain !== true
    && data.responses_complete === true
    && Number.isSafeInteger(requestCount) && requestCount > 0
    && data.completion_count === requestCount
    && Number.isSafeInteger(matchedEvents) && matchedEvents >= 0
    && Number.isSafeInteger(returnedEvents) && returnedEvents >= 0
    && returnedEvents <= matchedEvents
    && data.events_truncated === (matchedEvents > returnedEvents)
    && Array.isArray(data.events) && data.events.length === returnedEvents
    && Array.isArray(data.entity_ids) && data.entity_ids.length === requestCount
    && new Set(data.entity_ids).size === requestCount
    && data.entity_ids.every((id) => /^[a-z0-9_]+\.[a-z0-9_]+$/u.test(String(id || '')))
    && Number.isFinite(start) && Number.isFinite(end) && start <= end;
}

function completeEmptyHomeHistoryCoverage(tool) {
  const data = actionData(tool) || {};
  return completeHomeHistoryCoverage(tool) && data.empty === true
    && data.matched_events === 0 && data.returned_events === 0;
}

function completePositiveHomeHistoryCoverage(tool) {
  const data = actionData(tool) || {};
  return completeHomeHistoryCoverage(tool) && data.empty === false
    && data.matched_events > 0 && data.returned_events > 0;
}

function sameHomeHistoryBinding(left, right) {
  const leftBinding = left?.semanticBinding || {};
  const rightBinding = right?.semanticBinding || {};
  return leftBinding.understandingSha256 === rightBinding.understandingSha256
    && leftBinding.requirementId === rightBinding.requirementId
    && leftBinding.capabilityId === rightBinding.capabilityId
    && leftBinding.operationId === rightBinding.operationId;
}

function emptyHomeHistoryReplyFeedback(reply = '', toolResults = []) {
  const positive = toolResults.filter(completePositiveHomeHistoryCoverage);
  const unresolvedEmpty = toolResults.filter(completeEmptyHomeHistoryCoverage)
    .filter((empty) => !positive.some((receipt) => sameHomeHistoryBinding(empty, receipt)));
  if (!unresolvedEmpty.length) return '';
  const normalized = String(reply || '').normalize('NFKC').replace(/\s+/gu, ' ').trim()
    .replace(/[.!?]+$/u, '').toLocaleLowerCase('nl-NL');
  const allowed = 'geen gekoppelde regels in ontvangen responses voor de exact gebonden entiteiten en tijdsperiode';
  if (normalized === allowed) return '';
  return 'De volledig ontvangen, begrensde responses bewijzen alleen dat daarin voor de exact gebonden entiteiten en tijdsperiode geen gekoppelde regels staan. Noem geen gebeurtenis, actor of oorzaak en beweer niet dat er niets gebeurde. Gebruik voor de historie alleen: “Geen gekoppelde regels in ontvangen responses voor de exact gebonden entiteiten en tijdsperiode.”';
}

function homeEffectAttributionFeedback(reply = '', userText = '', toolResults = [], options = {}) {
  const intent = options.directAction || directHomeCommand(userText);
  return homeEffectClaimFeedback(reply, intent, toolResults, {
    readbackProven: Boolean(verifiedHomeReadback(userText, toolResults)),
  });
}

function setActivity(text, details = null) {
  runtime.activity = text;
  runtime.current = details;
  bus.emit('activity', { text });
}

function toolResultSummary(results = []) {
  return results.slice(-8).map((item) => ({
    name: item.name,
    ok: item.ok,
    changed: item.changed,
    verified: item.verified,
    execution_started: item.execution_started === true,
    evidence: item.name === 'doel_bijhouden'
      ? item.ok && item.verified ? 'Doelbewerking geverifieerd.' : ''
      : String(item.evidence?.summary || '').slice(0, 700),
    output: item.name === 'doel_bijhouden' ? '' : String(item.output || '').slice(0, 600),
  }));
}

function privacySafeTurnTools(results = []) {
  return results.map((item) => {
    if (item.name !== 'doel_bijhouden') return item;
    const binding = item.semanticBinding || {};
    const targetResolution = binding.targetResolution?.kind === 'self_attention_goal_ref'
      ? binding.targetResolution : null;
    return Object.freeze({
      name: item.name,
      args: Object.freeze({
        bewerking: String(item.args?.bewerking || ''),
        ...(Number.isSafeInteger(item.args?.verwachte_revisie)
          ? { verwachte_revisie: item.args.verwachte_revisie } : {}),
        ...(Number.isSafeInteger(item.args?.verwacht_doel_id)
          ? { verwacht_doel_id: item.args.verwacht_doel_id } : {}),
        ...(targetResolution ? { target_resolution: targetResolution } : {}),
      }),
      output: '', ok: item.ok === true, changed: item.changed === true,
      verified: item.verified === true, needs_followup: item.needs_followup === true,
      partial: item.partial === true, failure_class: item.failure_class || null,
      retryable: typeof item.retryable === 'boolean' ? item.retryable : null,
      execution_started: item.execution_started === true,
      effect_uncertain: typeof item.effect_uncertain === 'boolean'
        ? item.effect_uncertain : null,
      evidence: Object.freeze({ source: item.name,
        summary: item.ok && item.verified && item.changed
          ? 'Doelbewerking geverifieerd.' : 'Doelbewerking niet toegepast.' }),
      at: item.at,
      ...(item.semanticBinding ? { semanticBinding: Object.freeze({
        understandingSha256: binding.understandingSha256 || null,
        requirementId: binding.requirementId || null,
        addressesRequirementIds: Array.isArray(binding.addressesRequirementIds)
          ? Object.freeze([...binding.addressesRequirementIds]) : Object.freeze([]),
        basisReceiptIds: Array.isArray(binding.basisReceiptIds)
          ? Object.freeze([...binding.basisReceiptIds]) : Object.freeze([]),
        capabilityId: binding.capabilityId || null,
        operationId: binding.operationId || null,
        role: binding.role || null,
        executionType: binding.executionType || null,
        ...(targetResolution ? { targetResolution } : {}),
      }) } : {}),
    });
  });
}

function contributionRows(now = new Date(), days = 1, { includeInternal = false } = {}) {
  const start = new Date(now);
  start.setHours(0, 0, 0, 0);
  start.setDate(start.getDate() - Math.max(0, Number(days || 1) - 1));
  const seen = new Set();
  return getTimeline({ kinds: ['contribution'], limit: 200 })
    .filter((row) => Date.parse(row.ts) >= start.getTime())
    .filter((row) => includeInternal || row.meta?.internalOnly !== true)
    .filter((row) => row.meta?.syntheticTest !== true)
    .filter((row) => !row.meta?.quarantinedSyntheticContamination)
    .filter((row) => {
      const evidence = String(row.meta?.evidence || '').replace(/\s+/g, ' ').trim().toLowerCase();
      const action = String(row.meta?.lastAction || '');
      let key = `${action}:${evidence || String(row.content || '').toLowerCase()}`;
      if (action === 'bedien_huis') {
        const state = evidence.match(/\b(aan|uit|open|dicht)\b/)?.[1] || '';
        const target = evidence.split(/\b(?:is|staat)\b/)[0].replace(/[^a-z0-9]+/g, ' ').trim();
        if (target && state) key = `${action}:${target}:${state}`;
      }
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
}

function acceptedVoiceReply(tool) {
  const data = actionData(tool);
  if (tool?.name !== 'spreek_in_huis' || tool.ok !== true || tool.verified !== true
    || data?.accepted !== true) return '';
  return 'Ja, naar de gekozen Voice PE gestuurd.';
}

function failedVoiceReply(tool) {
  if (tool?.name !== 'spreek_in_huis' || tool.ok !== false || tool.verified !== false) return '';
  if (tool.effect_uncertain === true) {
    return 'Ik kreeg geen bevestiging van de Voice PE. Hij kan al begonnen zijn, dus ik stuur ’m niet blind dubbel.';
  }
  if (tool.changed === false && tool.retryable === true) {
    return 'De Voice PE pakte de omroep niet aan. Er is niets afgespeeld.';
  }
  return '';
}

function voiceAudibilityClaimFeedback(reply = '', toolResults = []) {
  const acceptedOnly = [...toolResults].reverse().find((tool) => (
    tool.name === 'spreek_in_huis' && tool.ok === true && tool.verified === true
    && actionData(tool)?.accepted === true && actionData(tool)?.audible_confirmed !== true
  ));
  if (!acceptedOnly || !/\b(?:hoort|hoorde|gehoord|meekreeg|meegekregen)\b/iu.test(reply)) return '';
  return 'Home Assistant bevestigde alleen dat de Voice PE de omroepopdracht aannam; niet dat iemand haar werkelijk hoorde. Zeg natuurlijk dat de omroep naar de bedoelde Voice PE is gestuurd, zonder gehoor bij een persoon te claimen.';
}

function durableWorkState(item) {
  try {
    const value = JSON.parse(String(item?.state_json || '{}'));
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  } catch {
    return {};
  }
}

function explorationEconomicResumeBoundary(resumeContext) {
  const work = resumeContext?.workItem;
  const episode = resumeContext?.episode;
  const state = durableWorkState(work);
  if (!Object.hasOwn(state, 'exploration_economic_handoff')) return null;
  const handoff = state.exploration_economic_handoff;
  const commitment = handoff?.commitment;
  const valid = Boolean(work && episode && handoff
    && handoff.contract === 'ExplorationEconomicHandoff/v1'
    && /^[a-f0-9]{64}$/u.test(String(handoff.sourceOutcomeKey || ''))
    && /^[a-f0-9]{64}$/u.test(String(handoff.evidenceSha256 || ''))
    && handoff.goalKey === 'economic_value'
    && Number.isSafeInteger(Number(handoff.explorationId))
    && Number(handoff.explorationId) >= 1
    && Number.isSafeInteger(Number(handoff.fromRevision))
    && Number(handoff.fromRevision) >= 1
    && Number(handoff.toRevision) === Number(handoff.fromRevision) + 1
    && canonicalJson(handoff.authority) === canonicalJson({
      internalWork: true, familyDelivery: true,
      externalContact: false, publication: false, spend: false,
    })
    && commitment && typeof commitment === 'object' && !Array.isArray(commitment)
    && canonicalJson(Object.keys(commitment).sort()) === canonicalJson([
      'definitionOfDone', 'nextAction', 'outcome', 'title',
    ])
    && Object.values(commitment).every((part) => typeof part === 'string'
      && part.trim().length >= 4)
    && Number(episode.work_item_id) === Number(work.id)
    && episode.goal_key === 'economic_value'
    && episode.origin_kind === 'self' && episode.origin_channel === 'autonomy'
    && episode.requester == null && episode.delivery_policy === 'milestone'
    && episode.delivery_target == null
    && work.source === 'autonomy' && work.owner == null);
  return { present: true, valid };
}

function stripRawInterpretationReplyScope(state) {
  const binding = state?.interpretation_reply_binding;
  if (binding && typeof binding === 'object' && !Array.isArray(binding)) {
    state.interpretation_reply_binding = { ...binding };
    delete state.interpretation_reply_binding.supersessionScope;
  }
  return state;
}

function interpretationReplyFanoutReconciliationState(delivery) {
  const deliveryKey = delivery?.dedupeKey
    ? `prepared:${delivery.dedupeKey}` : '';
  if (!deliveryKey) return 'uncertain';
  const operationKey = `telegram-fanout:${createHash('sha256').update(canonicalJson({
    contract: 'TelegramFanoutOperation/v1', deliveryKey,
  })).digest('hex')}`;
  const row = /** @type {any} */ (db.prepare(`SELECT status,tool,args FROM main.turn_tool_executions
    WHERE execution_key=?`).get(operationKey));
  if (row) {
    if (row.tool !== '@transport/telegram.fanout'
      || !['started', 'done'].includes(String(row.status || ''))) return 'uncertain';
    try {
      const intent = JSON.parse(row.args);
      return intent?.contract === 'TelegramFanoutIntent/v1'
        && intent.kind === 'text' && intent.deliveryKey === deliveryKey
        && intent.payloadSha256 === createHash('sha256')
          .update(canonicalTelegramText(delivery.text)).digest('hex')
        ? 'reconcile' : 'uncertain';
    } catch { return 'uncertain'; }
  }

  // Een ontbrekend high-level manifest is alleen schoon wanneer er evenmin
  // enig childintent of lokaal transportarchief voor deze delivery bestaat.
  // Zodra één daarvan bestaat kan een bericht zichtbaar zijn; zonder het
  // fanoutmanifest is retractie niet exact te autoriseren en blokkeren we.
  const transportArtifact = /** @type {any[]} */ (db.prepare(`SELECT tool,args FROM main.turn_tool_executions
    WHERE tool IN ('@transport/telegram.fanout','@transport/telegram.sendMessage',
      '@transport/telegram.sendPhoto','@transport/telegram.sendAudio')`).all())
    .some((candidate) => {
      try { return JSON.parse(candidate.args || '{}')?.deliveryKey === deliveryKey; }
      catch { return false; }
    });
  if (transportArtifact) return 'uncertain';
  const archiveArtifact = /** @type {any[]} */ (db.prepare(`SELECT meta FROM telegram_messages
    WHERE role='assistant'`).all()).some((candidate) => {
    try { return JSON.parse(candidate.meta || '{}')?.deliveryKey === deliveryKey; }
    catch { return false; }
  });
  return archiveArtifact ? 'uncertain' : 'clean';
}

function projectedPendingDelivery(delivery, stateBinding, superseded = false) {
  return Object.freeze({
    kind: 'interpretation_reply', text: delivery.text,
    recipient: String(delivery.recipient || '').trim() || null,
    preparedAt: delivery.preparedAt || null,
    attempts: Math.max(0, Number(delivery.attempts) || 0),
    dedupeKey: String(delivery.dedupeKey || '').trim() || null,
    provenance: Object.freeze({ ...delivery.provenance }),
    provenanceSha256: String(delivery.provenanceSha256 || ''),
    supersessionScope: String(stateBinding?.supersessionScope || ''),
    supersessionTokenSha256: String(stateBinding?.supersessionTokenSha256 || ''),
    ...(superseded ? { superseded: true } : {}),
  });
}

function interpretationPendingBoundary(item, episode, delivery, stateBinding) {
  if (!verifyInterpretationRetryPendingDelivery(item, episode, delivery)) {
    return Object.freeze({ kind: 'interpretation_reply', invalid: true });
  }
  const gate = interpretationReplyDeliveryGate(episode?.id, {
    kind: delivery.kind, dedupeKey: delivery.dedupeKey,
    text: delivery.text, provenanceSha256: delivery.provenanceSha256,
  });
  if (!gate.valid) {
    return gate.current || !('bound' in gate) || !gate.bound
      ? Object.freeze({ kind: 'interpretation_reply', invalid: true })
      : projectedPendingDelivery(delivery, stateBinding, true);
  }
  if (stateBinding?.supersessionScope) {
    const latest = interactiveIngressByScope.get(stateBinding.supersessionScope);
    const latestSha256 = latest ? createHash('sha256').update(latest).digest('hex') : '';
    if (latestSha256 && latestSha256 !== stateBinding.supersessionTokenSha256) {
      return Object.freeze({ kind: 'interpretation_reply', superseded: true });
    }
  }
  const chatId = String(episode?.origin_conversation_id || '')
    .match(/^telegram:(.+)$/u)?.[1] || '';
  return chatId && newerTelegramUserMessage(chatId, episode?.origin_message_id)
    ? Object.freeze({ kind: 'interpretation_reply', superseded: true }) : null;
}

/** @returns {any} */
function pendingDeliveryForWork(item, episode = null) {
  const state = durableWorkState(item);
  const delivery = state.pending_delivery;
  if (Object.hasOwn(state, 'monitoring')) {
    const monitoring = normalizeMonitoringContract(state.monitoring);
    if (!monitoring) {
      return Object.freeze({ kind: 'monitoring_reconciliation', invalid: true });
    }
    const acceptedDelivery = acceptedMonitoringDeliveryReceiptCandidate({
      workId: Number(item?.id), episodeId: Number(episode?.id),
    });
    if (acceptedDelivery) return acceptedDelivery;
    const authority = verifyMonitoringLifecycleAuthority({
      workId: Number(item?.id), episodeId: Number(episode?.id),
      ...(delivery?.contract === 'MonitoringPendingDelivery/v2'
        ? { expectedPendingSha256: String(delivery.pendingSha256 || '') } : {}),
    });
    if (!authority) {
      return Object.freeze({ kind: 'monitoring_reconciliation', invalid: true });
    }
    if (!delivery) return null;
    if (delivery.contract !== 'MonitoringPendingDelivery/v2' || !authority.pending) {
      return Object.freeze({ kind: 'monitoring_reconciliation', invalid: true });
    }
    return Object.freeze({
      ...authority.pending, kind: 'monitoring_v2',
      monitoringPendingSha256: authority.pending.pendingSha256,
      facts: authority.facts,
      outcome: authority.outcome,
      outcomeReceipt: authority.outcomeReceipt,
    });
  }
  const stateBinding = state.interpretation_reply_binding;
  const preparation = interpretationReplyPreparationBinding(episode?.id);
  if (!delivery || typeof delivery !== 'object') {
    return preparation || stateBinding
      ? Object.freeze({ kind: 'interpretation_reply', invalid: true }) : null;
  }
  if ((preparation || stateBinding) && delivery.kind !== 'interpretation_reply') {
    return Object.freeze({ kind: 'interpretation_reply', invalid: true });
  }
  if (delivery.kind === 'interpretation_reply') {
    const boundary = interpretationPendingBoundary(item, episode, delivery, stateBinding);
    if (boundary) return boundary;
  }
  if (typeof delivery.text !== 'string' || !delivery.text.trim()) return null;
  if (delivery.kind === 'interpretation_reply') {
    return projectedPendingDelivery(delivery, stateBinding);
  }
  return {
    kind: String(delivery.kind || 'legacy'),
    text: delivery.text.trim().slice(0, 4_000),
    ...(String(delivery.facts || '').trim()
      ? { facts: String(delivery.facts).trim().slice(0, 4_000) }
      : {}),
    recipient: String(delivery.recipient || '').trim() || null,
    preparedAt: delivery.preparedAt || null,
    attempts: Math.max(0, Number(delivery.attempts) || 0),
    dedupeKey: String(delivery.dedupeKey || '').trim() || null,
  };
}

function objectFromJson(value) {
  try { const parsed = typeof value === 'string' ? JSON.parse(value) : value;
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
  } catch { return {}; }
}

function monitoringContractForWork(item) { return normalizeMonitoringContract(durableWorkState(item).monitoring); }

function verifiedMonitoringLedgerProjection(receipt, {
  ledgerTurnId, requirementId, understandingSha256,
} = {}) {
  const expectedTurn = String(ledgerTurnId || '');
  if (!expectedTurn || receipt?.name !== 'ha_lees'
    || receipt?.semanticBinding?.requirementId !== requirementId
    || receipt?.semanticBinding?.understandingSha256 !== understandingSha256
    || receipt?.semanticBinding?.capabilityId !== 'home.entity.read'
    || receipt?.semanticBinding?.operationId !== 'read') return null;
  const rows = /** @type {any[]} */ (db.prepare(`SELECT * FROM main.turn_tool_executions
    WHERE turn_id=? AND tool='ha_lees' AND status='done'
    ORDER BY execution_seq ASC`).all(expectedTurn)).flatMap((candidate) => {
    let row;
    let args;
    let semanticBinding;
    try {
      row = validateExecutionRecord(candidate);
      args = JSON.parse(String(row.args || 'null'));
      semanticBinding = JSON.parse(String(row.semantic_binding || 'null'));
    } catch { return []; }
    return canonicalJson(args) === canonicalJson(receipt.args)
      && row.output === String(receipt.output || '')
      && semanticBinding?.requirementId === requirementId
      && semanticBinding?.understandingSha256 === understandingSha256
      && semanticBinding?.capabilityId === 'home.entity.read'
      && semanticBinding?.operationId === 'read'
      ? [row] : [];
  });
  if (rows.length !== 1) return null;
  const [row] = rows;
  return Object.freeze({
    executionKey: String(row.execution_key),
    intentRecordSha256: String(row.intent_record_sha256),
    outputSha256: String(row.output_sha256),
    completionRecordSha256: String(row.completion_record_sha256),
    finishedTs: String(row.finished_ts),
  });
}

function monitoringOutcomeFromResponse(response, facts) {
  const calls = Array.isArray(response?.toolCalls) ? response.toolCalls
    : Array.isArray(response?.output)
      ? response.output.filter((item) => item?.type === 'function_call') : [];
  if (calls.length !== 1
    || (calls[0].name ?? calls[0].function?.name) !== 'commit_monitoring_outcome') return null;
  let raw;
  try {
    const value = calls[0].arguments ?? calls[0].function?.arguments;
    raw = typeof value === 'string' ? JSON.parse(value) : value;
  } catch { return null; }
  return normalizeMonitoringOutcomeDecision(raw, facts);
}

async function completeMonitoringOutcomeWithLedger({
  request, turnId, contract, conditionContext, attempt,
}) {
  let payload;
  try { payload = JSON.parse(String(request?.input?.[0]?.content?.[0]?.text || 'null')); }
  catch { payload = null; }
  const facts = Array.isArray(payload?.facts) ? payload.facts : [];
  const factIds = facts.map((fact) => String(fact?.factId || ''));
  if (!facts.length || factIds.some((id) => !/^monitoring-fact:[a-f0-9]{64}$/u.test(id))) {
    throw new Error('monitoringoutcome mist haar exacte factset');
  }
  const outcomeRequest = monitoringOutcomeRequestRecord({
    monitoring: contract, conditionContext, facts, attempt,
  });
  if (!outcomeRequest) throw new Error('monitoringoutcome mist haar hostgebonden request');
  const args = {
    monitoringId: contract.monitoringId,
    monitoringContractSha256: contract.contractSha256,
    factIds,
    requestSha256: outcomeRequest.requestSha256,
    attempt,
  };
  const executionKey = `monitoring-outcome:${createHash('sha256').update(canonicalJson({
    turnId, monitoringId: contract.monitoringId, factIds, attempt,
  })).digest('hex')}`;
  const readBindings = monitoringContractReadBindings(contract) || [];
  if (!readBindings.length) throw new Error('monitoringcontract mist leesbindings');
  const claimed = claimTurnToolExecution({
    executionKey, turnId, tool: '@monitoring/outcome', args,
    semanticBinding: {
      understandingSha256: contract.sourceUnderstandingSha256,
      requirementId: readBindings[0].requirementId,
      addressesRequirementIds: readBindings.map((item) => item.requirementId),
      basisReceiptIds: [],
      capabilityId: 'monitoring.outcome', operationId: 'decide',
      role: 'monitoring_outcome', executionType: 'host_bound',
    },
  });
  if (!claimed.claimed) {
    let stored;
    let storedArgs;
    try { stored = JSON.parse(String(claimed.row?.output || 'null')); } catch { stored = null; }
    try { storedArgs = JSON.parse(String(claimed.row?.args || 'null')); } catch { storedArgs = null; }
    const storedOutcome = normalizeMonitoringOutcomeDecision(stored?.outcome, facts);
    const storedResponse = monitoringOutcomeResponseRecord({
      requestSha256: outcomeRequest.requestSha256,
      accepted: true, outcome: storedOutcome, facts, failure: null,
    });
    if (claimed.row?.status !== 'done' || stored?.accepted !== true
      || canonicalJson(storedArgs) !== canonicalJson(args)
      || canonicalJson(Object.keys(stored || {}).sort()) !== canonicalJson([
        'accepted', 'contract', 'failure', 'outcome', 'responseSha256',
      ])
      || stored?.contract !== 'MonitoringOutcomeModelRecord/v1'
      || stored?.failure !== null || !storedOutcome || !storedResponse
      || stored.responseSha256 !== storedResponse.responseSha256) {
      throw new Error('monitoringoutcome-ledger bevat geen herbruikbaar terminaal besluit');
    }
    return {
      output: [{
        type: 'function_call', name: 'commit_monitoring_outcome',
        arguments: JSON.stringify(stored.outcome), call_id: `monitoring-replay-${attempt}`,
      }],
    };
  }
  let response;
  let decision = null;
  let failure = null;
  try {
    response = await chatComplete({
      ...request, model: config.modelVoice, effort: config.reasoningAutonomy,
      cacheKey: 'eva-monitoring-outcome', rateLimitRetries: 0,
      transientRetries: 1, authenticationRetries: 1,
    });
    decision = monitoringOutcomeFromResponse(response, facts);
  } catch (error) {
    failure = errMsg(error).slice(0, 300);
  }
  const projectedResponse = monitoringOutcomeResponseRecord({
    requestSha256: outcomeRequest.requestSha256,
    accepted: Boolean(decision), outcome: decision, facts, failure,
  });
  if (!projectedResponse) throw new Error('monitoringoutcome-uitvoer is niet projecteerbaar');
  const output = canonicalJson({
    contract: 'MonitoringOutcomeModelRecord/v1',
    accepted: Boolean(decision),
    outcome: decision,
    responseSha256: projectedResponse.responseSha256,
    failure,
  });
  completeTurnToolExecution(executionKey, output);
  if (!response) throw new Error(failure || 'monitoringoutcome-providerfout');
  return response;
}

function verifiedMonitoringOutcomeReceipt({
  turnId, contract, conditionContext, decision, facts,
}) {
  const rows = /** @type {any[]} */ (db.prepare(`SELECT * FROM main.turn_tool_executions
    WHERE turn_id=? AND tool='@monitoring/outcome' AND status='done'
    ORDER BY execution_seq ASC`).all(String(turnId))).flatMap((candidate) => {
    try {
      const row = validateExecutionRecord(candidate);
      const args = JSON.parse(String(row.args || 'null'));
      const output = JSON.parse(String(row.output || 'null'));
      const exact = normalizeMonitoringOutcomeDecision(output?.outcome, facts);
      const expectedRequest = monitoringOutcomeRequestRecord({
        monitoring: contract, conditionContext, facts, attempt: args?.attempt,
      });
      const expectedResponse = monitoringOutcomeResponseRecord({
        requestSha256: expectedRequest?.requestSha256,
        accepted: true, outcome: exact, facts, failure: null,
      });
      return args?.monitoringId === contract.monitoringId
        && args?.monitoringContractSha256 === contract.contractSha256
        && canonicalJson(Object.keys(args || {}).sort()) === canonicalJson([
          'attempt', 'factIds', 'monitoringContractSha256', 'monitoringId', 'requestSha256',
        ])
        && Number.isSafeInteger(args.attempt) && args.attempt >= 1 && args.attempt <= 2
        && expectedRequest && args.requestSha256 === expectedRequest.requestSha256
        && canonicalJson(args.factIds) === canonicalJson(decision.factIds)
        && canonicalJson(Object.keys(output || {}).sort()) === canonicalJson([
          'accepted', 'contract', 'failure', 'outcome', 'responseSha256',
        ])
        && output?.contract === 'MonitoringOutcomeModelRecord/v1'
        && output.accepted === true && output.failure === null && exact && expectedResponse
        && output.responseSha256 === expectedResponse.responseSha256
        && canonicalJson(exact) === canonicalJson(decision) ? [{ row }] : [];
    } catch { return []; }
  });
  if (rows.length !== 1) return null;
  const row = rows[0].row;
  return issueMonitoringOutcomeReceipt({
    outcome: decision, executionKey: row.execution_key,
    intentRecordSha256: row.intent_record_sha256,
    outputSha256: row.output_sha256,
    completionRecordSha256: row.completion_record_sha256,
    finishedAt: row.finished_ts,
  });
}

function monitoringFactBindingSpecs(understanding, sourceText) {
  const spansOverlap = (left, right) => Number.isInteger(left?.start)
    && Number.isInteger(left?.end) && Number.isInteger(right?.start)
    && Number.isInteger(right?.end) && left.start < right.end && right.start < left.end;
  const monitoring = understanding?.monitoring;
  const requirementIds = Array.isArray(monitoring?.sourceEntityReadRequirementIds)
    ? monitoring.sourceEntityReadRequirementIds.map(String) : [];
  if (!requirementIds.length || requirementIds.length > 8
    || new Set(requirementIds).size !== requirementIds.length) return null;
  const entityActions = new Map(requirementIds.map((requirementId) => [
    requirementId,
    understanding.actions.find((candidate) => (
      candidate.requirementId === requirementId
      && candidate.capabilityId === 'home.entity.read'
      && candidate.operationId === 'read'
    )),
  ]));
  if ([...entityActions.values()].some((action) => !action)) return null;
  const conditions = (Array.isArray(understanding?.conditions)
    ? understanding.conditions : []).filter((item) => item?.role === 'guard');
  if (!conditions.length) return null;
  const requested = new Set(requirementIds);
  const conditionRequirements = new Map();
  for (const condition of conditions) {
    const declared = Array.isArray(condition?.requirementIds)
      ? condition.requirementIds.map(String) : [];
    const ids = declared.length ? declared : requirementIds.filter((requirementId) => {
      const action = entityActions.get(requirementId);
      return Array.isArray(condition?.sourceSpans) && Array.isArray(action?.sourceSpans)
        && condition.sourceSpans.some((conditionSpan) => (
          action.sourceSpans.some((actionSpan) => spansOverlap(conditionSpan, actionSpan))
        ));
    });
    if (!ids.length || new Set(ids).size !== ids.length
      || ids.some((id) => !requested.has(id))) return null;
    conditionRequirements.set(condition, ids);
  }
  const conditionByRequirement = new Map();
  for (const requirementId of requirementIds) {
    const matches = conditions.filter((condition) => (
      conditionRequirements.get(condition).includes(requirementId)
    ));
    if (matches.length !== 1) return null;
    conditionByRequirement.set(requirementId, matches[0]);
  }
  if (new Set(conditionByRequirement.values()).size !== conditions.length) return null;
  const references = Array.isArray(monitoring.sourceReferenceTexts)
    && monitoring.sourceReferenceTexts.length === requirementIds.length
    ? monitoring.sourceReferenceTexts.map((value) => String(value || '').trim()) : null;
  const specs = requirementIds.map((requirementId, index) => {
    const action = entityActions.get(requirementId);
    const spanText = (Array.isArray(action?.sourceSpans) ? action.sourceSpans : [])
      .flatMap((span) => Number.isInteger(span?.start) && Number.isInteger(span?.end)
        && span.start >= 0 && span.end > span.start && span.end <= sourceText.length
        ? [sourceText.slice(span.start, span.end)] : [])
      .join(' ').trim();
    const exactSource = references?.[index] || spanText;
    const condition = conditionByRequirement.get(requirementId);
    const peers = conditionRequirements.get(condition);
    const peerIndex = peers.indexOf(requirementId);
    const operand = peers.length === 1 ? 'subject'
      : peers.length === 2 ? peerIndex === 0 ? 'left' : 'right' : null;
    const statement = String(condition?.statement || '').trim();
    if (!action || !exactSource || !statement || !operand) return null;
    return Object.freeze({
      requirementId,
      condition: statement,
      conditionRole: 'guard',
      operand,
      sourceText: exactSource,
    });
  });
  return specs.some((item) => !item) ? null : Object.freeze(specs);
}

function verifiedMonitoringStartAuthority({
  executionKey, channel, conversationId, messageId, requester, originalText, messageContext,
}) {
  const source = exactTurnUnderstandingSource({
    channel, conversationId, messageId, requester, originalText, messageContext,
  });
  const row = validateExecutionRecord(db.prepare(`SELECT *
    FROM main.turn_tool_executions WHERE execution_key=?`).get(executionKey));
  const authority = turnUnderstandingAuthorityRecord(db, row);
  let args;
  try { args = JSON.parse(String(row?.args || 'null')); } catch { args = null; }
  if (!authority || row.status !== 'started' || row.tool !== '@turn/understanding'
    || row.turn_id !== `ingress:${channel}:${conversationId}:${String(messageId)}`
    || args?.channel !== channel || args.conversationId !== conversationId
    || String(args.messageId || '') !== String(messageId)
    || args.requester !== requester || args.originalText !== originalText
    || args.syntheticTest === true
    || authority.sourceKind !== source.kind || authority.sourceRef !== source.ref
    || authority.sourceSha256 !== source.sha256
    || Number(authority.sourceGeneration) !== Number(source.generation)) return null;
  return Object.freeze({
    executionKey: authority.executionKey,
    executionSeq: Number(authority.executionSeq),
    intentRecordSha256: authority.intentRecordSha256,
    authoritySha256: authority.authoritySha256,
    channel, conversationId, messageId: String(messageId),
    sourceKind: authority.sourceKind,
    sourceRef: authority.sourceRef,
    sourceSha256: authority.sourceSha256,
    sourceGeneration: Number(authority.sourceGeneration),
  });
}

function monitoringStateWithContract(item, contract, runtimeState = null) {
  const state = durableWorkState(item);
  return {
    ...state,
    monitoring: contract,
    ...(runtimeState ? { monitoring_runtime: runtimeState } : {}),
  };
}

async function runBoundMonitoringResume({
  contract, command, episode, turnId, syntheticTest, person, channel, mode, executePlan,
}) {
  const resumeAuthority = verifyMonitoringLifecycleAuthority({
    workId: Number(command?.id), episodeId: Number(episode?.id),
  });
  if (!resumeAuthority
    || resumeAuthority.monitoring.contractSha256 !== contract.contractSha256) {
    const blocked = transitionGoalEpisode(episode.id, {
      state: 'blocked', wakeTs: '',
      checkpoint: 'Monitoring gestopt: de verzegelde oorsprongs- of lifecyclebinding is niet intact.',
      nextAction: 'Beoordeel de monitoringbinding handmatig; lees of verstuur niets automatisch.',
      sessionRef: turnId, eventKind: 'monitoring_integrity_blocked',
      idempotencyKey: `monitoring-integrity:${episode.id}:${contract.monitoringId}`,
      work: {
        clearWait: true, nextWakeTs: '', clearLease: true,
        lastAction: 'monitoring_integriteit', lastError: 'monitoring_lifecycle_invalid',
      },
    });
    return { ...blocked, decision: 'retry', reply: '', delivery: null };
  }
  const endsAtMs = Date.parse(contract.endsAt || '');
  if (Number.isFinite(endsAtMs) && Date.now() >= endsAtMs) {
    const proof = 'De afgesproken monitoringhorizon is verstreken zonder openstaande bijstelwaarschuwing.';
    const runtimeState = {
      lastObservedAt: null,
      lastVerdict: 'complete',
      basisReceiptIds: [],
      reason: proof,
    };
    const transition = transitionGoalEpisode(episode.id, {
      state: 'closed',
      deliveryPolicy: 'none',
      checkpoint: proof,
      proofSummary: proof,
      sessionRef: turnId,
      eventKind: 'monitoring_horizon_completed',
      idempotencyKey: `monitoring-horizon:${episode.id}:${contract.endsAt}`,
      work: {
        state: monitoringStateWithContract(command, contract, runtimeState),
        result: proof,
        evidence: proof,
        nextWakeTs: '',
        clearWait: true,
        clearLease: true,
        lastAction: command.last_action || 'monitoring',
      },
    });
    return { ...transition, decision: 'complete', reply: '', delivery: null };
  }
  const effectPolicy = { mode: 'allow', deniedEffects: [] };
  let outcomeAttempt = 0;
  const cycle = await runMonitoringCycle({ contract }, {
    observe: (binding) => {
      let plan = null;
      try {
        plan = planCapabilityAction({
          requirementId: binding.requirementId,
          capabilityId: 'home.entity.read', operationId: 'read',
          semanticInput: { entityId: binding.entityId }, effect: 'read',
          polarity: 'request', authorization: 'allowed',
        }, {
          mode: 'autonomy', interactiveAuthority: 'ambient',
          fullAutonomy: config.autonomyFull, synthetic: syntheticTest,
          syntheticNamespace: contract.sourceUnderstandingSha256,
          understandingSha256: contract.sourceUnderstandingSha256, effectPolicy,
        });
      } catch { plan = null; }
      return plan ? executePlan(plan, {
        semanticRole: 'monitoring_observation',
        monitoringExecutionTurnId: turnId,
        understandingSha256: contract.sourceUnderstandingSha256, effectPolicy,
      }) : null;
    },
    identityInstructions: buildRedactedInstructions({
      sender: episode.requester || person, channel: episode.origin_channel || channel, mode,
      familyContext: '', extra: '',
    }),
    timeoutMs: 30_000,
    conditionContext: resumeAuthority.conditionContext,
    verifyLedgerReceipt: (receipt, binding) => verifiedMonitoringLedgerProjection(receipt, {
      ledgerTurnId: turnId,
      requirementId: binding.requirementId,
      understandingSha256: contract.sourceUnderstandingSha256,
    }),
    complete: (request) => completeMonitoringOutcomeWithLedger({
      request, turnId, contract, conditionContext: resumeAuthority.conditionContext,
      attempt: ++outcomeAttempt,
    }),
  });
  const runtimeState = {
    lastObservedAt: new Date().toISOString(),
    lastVerdict: cycle.kind,
    factIds: cycle.decision?.factIds || [],
    failure: String(cycle.failure || '').slice(0, 800),
  };
  let reply = '';
  if (cycle.kind === 'notify' && episode.delivery_policy === 'required') {
    reply = renderMonitoringTerminalText(cycle.facts);
    if (!reply) {
      runtimeState.lastVerdict = 'retry';
      runtimeState.failure = 'monitoring_terminal_render_invalid';
    }
  }
  if (cycle.kind !== 'notify' || episode.delivery_policy !== 'required' || !reply) {
    const plannedWakeMs = Date.now() + contract.cadenceMs;
    const nextWake = new Date(Number.isFinite(endsAtMs)
      ? Math.min(plannedWakeMs, endsAtMs) : plannedWakeMs).toISOString();
    const transition = transitionGoalEpisode(episode.id, {
      state: 'waiting', wakeTs: nextWake,
      checkpoint: cycle.proof || 'De monitoringmeting leverde nog geen volledige verse bewijsset op.',
      proofSummary: cycle.proof || undefined,
      nextAction: episode.next_action || command.next_step || '', sessionRef: turnId,
      work: {
        state: monitoringStateWithContract(command, contract, runtimeState),
        nextWakeTs: nextWake, wait: { reason: 'monitoring_resume', resumeAfter: nextWake },
        evidence: cycle.proof || undefined,
        lastAction: cycle.observations.at(-1)?.name || command.last_action || 'monitoring',
      },
    });
    return { ...transition, decision: cycle.kind === 'notify'
      ? episode.delivery_policy === 'required' && !reply ? 'retry' : 'silent' : cycle.kind,
      reply: '', delivery: null };
  }
  const dedupeKey = `episode:${episode.id}:monitoring-final`;
  const deliveryState = monitoringStateWithContract(command, contract, runtimeState);
  const persistenceAuthority = verifyMonitoringLifecycleAuthority({
    workId: Number(command.id), episodeId: Number(episode.id),
  });
  const outcomeReceipt = cycle.decision ? verifiedMonitoringOutcomeReceipt({
    turnId, contract, conditionContext: resumeAuthority.conditionContext,
    decision: cycle.decision, facts: cycle.facts,
  }) : null;
  const pendingPreparedAt = new Date().toISOString();
  const pending = persistenceAuthority && outcomeReceipt ? issueMonitoringPendingDelivery({
    monitoring: contract, lifecycle: persistenceAuthority.lifecycle,
    outcome: cycle.decision, outcomeReceipt, facts: cycle.facts, text: reply,
    episodeId: Number(episode.id), workId: Number(command.id), dedupeKey,
    preparedAt: pendingPreparedAt, attempts: 0,
  }) : null;
  if (!pending) {
    const nextWake = new Date(Date.now() + contract.cadenceMs).toISOString();
    const transition = transitionGoalEpisode(episode.id, {
      state: 'waiting', wakeTs: nextWake,
      checkpoint: 'De terminale monitoringuitkomst kon niet volledig worden verzegeld.',
      nextAction: episode.next_action || command.next_step || '', sessionRef: turnId,
      work: {
        state: monitoringStateWithContract(command, contract, {
          ...runtimeState, lastVerdict: 'retry', failure: 'monitoring_pending_invalid',
        }),
        nextWakeTs: nextWake,
        wait: { reason: 'monitoring_resume', resumeAfter: nextWake },
        lastError: 'monitoring_pending_invalid',
      },
    });
    return { ...transition, decision: 'retry', reply: '', delivery: null };
  }
  const retrySchedule = monitoringDeliveryRetrySchedule(pending, contract, {
    now: pendingPreparedAt, requestedBackoffMs: 15 * 60_000,
  });
  if (!retrySchedule) {
    const staleState = { ...deliveryState };
    delete staleState.pending_delivery;
    const reobserveAt = new Date(Date.parse(pendingPreparedAt) + 15_000).toISOString();
    const waiting = transitionGoalEpisode(episode.id, {
      state: 'waiting', wakeTs: reobserveAt,
      checkpoint: 'De afleverhorizon was te krap; een verse monitoringmeting is vereist.',
      nextAction: episode.next_action || command.next_step || '',
      sessionRef: turnId,
      work: {
        state: staleState, nextWakeTs: reobserveAt, clearLease: true,
        wait: { reason: 'monitoring_resume', resumeAfter: reobserveAt },
        lastError: 'monitoring_delivery_fresh_observation_required',
      },
    });
    return { ...waiting, decision: 'retry', reply: '', delivery: null };
  }
  const retryAt = retrySchedule.wakeAt;
  deliveryState.pending_delivery = pending;
  if (!monitoringStateFitsDurableBudget(deliveryState)) {
    const blocked = transitionGoalEpisode(episode.id, {
      state: 'blocked', wakeTs: '',
      checkpoint: 'Monitoring gestopt: de volledige verzegelde afleverstate past niet veilig.',
      nextAction: 'Beoordeel de monitoring handmatig; verstuur niets automatisch.',
      sessionRef: turnId,
      work: { clearWait: true, nextWakeTs: '', clearLease: true,
        lastError: 'monitoring_state_budget_exceeded' },
    });
    return { ...blocked, decision: 'retry', reply: '', delivery: null };
  }
  const transition = transitionGoalEpisode(episode.id, {
    state: 'delivery_pending', wakeTs: retryAt,
    checkpoint: cycle.proof,
    proofSummary: cycle.proof,
    nextAction: 'Bezorg de gegronde waarschuwing aan de oorspronkelijke verzoeker.',
    sessionRef: turnId,
    work: {
      state: deliveryState, nextWakeTs: retryAt,
      wait: { reason: 'delivery_retry', resumeAfter: retryAt, attempts: 0 },
      evidence: cycle.proof,
      lastAction: cycle.observations.at(-1)?.name || 'ha_lees',
    },
  });
  return { ...transition, decision: 'notify', reply,
    delivery: {
      args: { tekst: reply }, dedupeKey, effectPolicy,
      deliveryKind: 'monitoring_v2', pendingSha256: pending.pendingSha256,
    } };
}

async function executePinnedDelivery({
  args, dedupeKey, effectPolicy, deliveryKind = 'legacy',
  provenanceSha256 = '', monitoringPendingSha256 = '', isCurrent = null,
  reconcileSuperseded = false, runtime,
}) {
  runtime.toolNameAttempts.set('stuur_bericht', 1);
  runtime.toolAttempts.set(`stuur_bericht:${canonicalJson(args)}`, 1);
  let executionStarted = false;
  const output = await runtime.executeForTurn('stuur_bericht', args, {
    mode: runtime.mode,
    sender: runtime.person,
    turnId: runtime.turnId,
    messageContext: runtime.messageContext,
    userText: runtime.userText,
    ...(effectPolicy ? { effectPolicy } : {}),
    priorToolResults: runtime.toolResults,
    activeWorkId: runtime.activeWorkId,
    activeGoalEpisodeId: runtime.goalEpisodeId,
    activeExplorationId: runtime.activeExplorationId,
    cognitiveEpisodeId: runtime.cognitiveEpisodeId,
    preparedDeliveryDedupeKey: dedupeKey,
    preparedDeliveryKind: deliveryKind,
    preparedDeliveryProvenanceSha256: provenanceSha256,
    preparedDeliveryMonitoringPendingSha256: monitoringPendingSha256,
    preparedDeliveryIsCurrent: isCurrent,
    preparedDeliveryReconcileSuperseded: reconcileSuperseded === true,
    markExecutionStarted: () => { executionStarted = true; },
  });
  return runtime.recordToolOutput(
    'stuur_bericht', args, output, null, null, null, executionStarted,
  );
}

async function executePendingDeliveryResume(preparedDelivery, runtime) {
  if (preparedDelivery.kind === 'monitoring_receipt_only') {
    const receipt = reconcileAcceptedMonitoringDelivery({
      workId: Number(runtime.activeWorkId), episodeId: Number(runtime.goalEpisode?.id),
    });
    const ok = Boolean(receipt);
    return {
      action: {
        contract: 'ActionResult/v1', tool: 'stuur_bericht', ok, changed: ok,
        verified: ok, needs_followup: !ok, partial: false,
        failure_class: ok ? null : 'integrity', retryable: false,
        effect_uncertain: false,
      },
      reply: ok ? preparedDelivery.resultText : '',
      superseded: false,
    };
  }
  const args = {
    tekst: preparedDelivery.text,
    ...(preparedDelivery.recipient ? { aan: preparedDelivery.recipient } : {}),
  };
  runtime.setActivity('bezorgt een eerder voorbereid resultaat', {
    ...runtime.currentActivity,
    tool: 'stuur_bericht',
  });
  const originStillCurrent = preparedDelivery.kind === 'interpretation_reply'
    ? () => {
      if (preparedDelivery.supersessionScope) {
        const latest = interactiveIngressByScope.get(preparedDelivery.supersessionScope);
        if (latest && createHash('sha256').update(latest).digest('hex')
          !== preparedDelivery.supersessionTokenSha256) return false;
      }
      const gate = interpretationReplyDeliveryGate(runtime.goalEpisode?.id, {
        kind: preparedDelivery.kind,
        dedupeKey: preparedDelivery.dedupeKey,
        text: preparedDelivery.text,
        provenanceSha256: preparedDelivery.provenanceSha256,
      });
      if (!gate.valid || !gate.current) return false;
      const chatId = String(runtime.goalEpisode?.origin_conversation_id || '')
        .match(/^telegram:(.+)$/u)?.[1] || '';
      return !chatId || !newerTelegramUserMessage(
        chatId, runtime.goalEpisode?.origin_message_id,
      );
    }
    : preparedDelivery.kind === 'monitoring_v2'
      ? () => Boolean(verifyMonitoringLifecycleAuthority({
        workId: Number(runtime.activeWorkId), episodeId: Number(runtime.goalEpisode?.id),
        expectedPendingSha256: preparedDelivery.monitoringPendingSha256,
      })) : null;
  const alreadySuperseded = preparedDelivery.kind === 'interpretation_reply'
    && originStillCurrent && !originStillCurrent();
  if (alreadySuperseded && preparedDelivery.superseded !== true) {
    return { action: null, reply: '', superseded: true };
  }
  const action = await executePinnedDelivery({
    args, dedupeKey: preparedDelivery.dedupeKey,
    effectPolicy: { mode: 'allow', deniedEffects: [] },
    deliveryKind: preparedDelivery.kind,
    provenanceSha256: preparedDelivery.provenanceSha256,
    monitoringPendingSha256: preparedDelivery.monitoringPendingSha256,
    isCurrent: originStillCurrent,
    reconcileSuperseded: alreadySuperseded && preparedDelivery.superseded === true,
    runtime,
  });
  return {
    action,
    reply: action.ok && action.verified ? preparedDelivery.text : '',
    superseded: actionData(action)?.superseded === true,
  };
}

async function executeMonitoringResume({
  contract, command, episode, monitoringRuntime, deliveryRuntime,
}) {
  const resumed = await runBoundMonitoringResume({
    contract,
    command,
    episode,
    ...monitoringRuntime,
  });
  if (resumed.delivery) {
    const pendingSha256 = String(resumed.delivery.pendingSha256 || '');
    const monitoringStillCurrent = () => Boolean(verifyMonitoringLifecycleAuthority({
      workId: Number(resumed.workItem?.id), episodeId: Number(resumed.episode?.id),
      expectedPendingSha256: pendingSha256,
    }));
    const delivered = await executePinnedDelivery({
      ...resumed.delivery,
      monitoringPendingSha256: pendingSha256,
      isCurrent: monitoringStillCurrent,
      runtime: { ...deliveryRuntime, goalEpisodeId: resumed.episode.id },
    });
    resumed.reply = delivered.ok && delivered.verified ? resumed.reply : '';
  }
  return resumed;
}

function requiredToolSatisfied(name, toolResults = []) {
  return toolResults.some((tool) => {
    if (tool.name !== name || !tool.ok || !tool.verified) return false;
    if (name === 'bedien_huis' && boundedVerifiedHomePartialEffect(tool, tool.args)) return true;
    let parsed = null;
    try { parsed = JSON.parse(String(tool.output || '')); } catch { /* oudere uitvoer */ }
    // Dit is geen inhoudelijk succes maar wel een geldige pauzegrens voor deze
    // beurt: Eva heeft alles gedaan wat zonder de mens kan en vraagt exact één
    // ontbrekend gegeven. De tooluitvoer houdt die afhankelijkheid bewijsbaar.
    if (parsed?.ok === true && /^human_/i.test(String(parsed?.waiting_for || parsed?.data?.waiting_for || ''))) return true;
    if (parsed?.needs_followup === true || parsed?.partial === true) return false;
    if (name === 'maak_vaardigheid' || name === 'beheer_addon') {
      return actionData(tool)?.capability?.status === 'active';
    }
    if (name === 'gebruik_capability') return actionData(tool)?.demonstrated === true;
    return true;
  });
}

function verifiedHomeReadback(userText, toolResults = []) {
  const command = directHomeCommand(userText);
  if (!command || !homeReadbackProvesIntent(command, toolResults)) return null;
  return [...toolResults].reverse().find((tool) => tool.name === 'ha_lees') || null;
}

function alternateRequirementEvidence(name, userText, toolResults) {
  return name === 'bedien_huis' && Boolean(verifiedHomeReadback(userText, toolResults));
}

function relevantConversationEvidence(tool, text = '') {
  return tool?.name === 'zoek_gesprek' && tool.ok && tool.verified
    && Number(actionData(tool)?.count || 0) > 0
    && tekstOverlap(text, tool.output || '') >= 0.04;
}

function specializedRequirementStatus(name, text, toolResults, options = {}) {
  if (name === 'werk_bijhouden' && options.turnKind === 'monitoring') {
    return Boolean(scheduledMonitoringWork(toolResults, options));
  }
  if (name === 'zoek_gesprek' && ['self_improvement', 'self_repair'].includes(options.turnKind)) {
    return toolResults.some((tool) => relevantConversationEvidence(tool, text));
  }
  if (name === 'beheer_runtime' && options.turnKind === 'self_repair') {
    const lifecycle = toolResults.filter((tool) => tool.name === name && tool.ok)
      .map((tool) => ({ tool, data: actionData(tool) })).filter(({ data }) => data);
    return lifecycle.some(({ data }) => [
      'test_queued', 'root_tested', 'approval_required', 'apply_queued',
      'committed', 'rollback_queued', 'rolled_back', 'boot_failed_rolled_back_live',
    ].includes(String(data.status || '')));
  }
  if (name === 'voer_uit'
    && /\bwelke\s+invoer\b.{0,80}\bw[eé]l\s+werkt\b|\binvoer\b.{0,80}\bdie\s+w[eé]l\s+werkt\b/i.test(text)) {
    return toolResults.some((tool) => tool.name === name && successfulZeroExit(tool));
  }
  if (name !== 'lees_bestand') return null;
  if (options.turnKind === 'self_repair') {
    const readIndex = toolResults.findLastIndex((tool) => (
      tool.name === name && tool.ok && tool.verified
      && /^(?:src\/|package\.json$|docs\/|config\/)/i.test(
        String(tool.args?.pad || '').replace(/^\.\//, ''),
      )
      && tekstOverlap(text, `${tool.args?.zoek || ''} ${tool.output || ''}`) >= 0.04
    ));
    return readIndex > 0 && toolResults.findIndex((tool, index) => (
      index < readIndex && tool.name === 'lijst_bestanden' && tool.ok && tool.verified
    )) >= 0;
  }
  const asksForSourceAndTest = /\bbron(?:code)?\b.{0,100}\b(?:plus|en)\b.{0,100}\btests?\b/i.test(text)
    || /\btests?\b.{0,100}\b(?:plus|en)\b.{0,100}\bbron(?:code)?\b/i.test(text);
  if (!asksForSourceAndTest) return null;
  const verifiedReads = toolResults.filter((tool) => (
    tool.name === name && tool.ok && tool.verified
  ));
  return verifiedReads.some((tool) => (
    /\.(?:cjs|js|mjs|py|sh|ts)$/i.test(readArtifactPath(tool))
    && !isTestArtifactPath(readArtifactPath(tool))
  )) && verifiedReads.some((tool) => isTestArtifactPath(readArtifactPath(tool)));
}

function baseTurnRequirementSatisfied(name, toolResults = [], userText = '') {
  const text = cleanUserText(userText).toLowerCase();
  if (name === 'ha_lees'
    && /\b(?:logboek|geschiedenis|eerder|later|vanochtend|gisteren|gebeurd|waardoor|oorzaak|weer\s+aan|weer\s+uit)\b/i.test(text)) {
    return toolResults.some((tool) => (
      tool.name === 'ha_lees' && tool.ok && tool.verified
      && /^\/api\/logbook\//i.test(String(tool.args?.pad || ''))
      && /[?&]entity=/.test(String(tool.args?.pad || ''))
    ));
  }
  if (name === 'zoek_internet'
    && /\b(?:controleer|bekijk|onderzoek)\b.{0,180}\b(?:eerst|of)\b.{0,220}\b(?:anders|alternatief)\b/i.test(text)) {
    const successfulSearches = toolResults.filter((tool) => (
      tool.name === name && tool.ok && tool.verified
    ));
    const distinctQuestions = new Set(successfulSearches.map((tool) => (
      cleanUserText(tool.args?.vraag).toLowerCase().replace(/\s+/g, ' ').trim()
    )).filter(Boolean));
    if (distinctQuestions.size < 2) return false;
  }
  if (name === 'zoek_gesprek') {
    const requestedCount = /\b(?:drie|3)\b/i.test(text) ? 3
      : /\b(?:twee|2)\b/i.test(text) ? 2 : 1;
    const enough = toolResults.some((tool) => {
      if (tool.name !== name || !tool.ok || !tool.verified) return false;
      const data = actionData(tool);
      return Number(data?.count || data?.messages?.length || 0) >= requestedCount;
    });
    if (!enough) return false;
  }
  if (name === 'lees_bestand') {
    const requestedPaths = [...text.matchAll(/\b(?:src|data\/workspace)\/[a-z0-9_./-]+\.[a-z0-9]+\b/gi)]
      .map((match) => match[0].toLowerCase());
    const failedRequested = toolResults.some((tool) => (
      tool.name === name && tool.ok === false
      && requestedPaths.includes(String(tool.args?.pad || '').toLowerCase())
    ));
    const listedFallback = toolResults.some((tool) => tool.name === 'lijst_bestanden' && tool.ok && tool.verified);
    const requestedTerms = requestedPaths.flatMap((file) => file.split('/').at(-1).split(/[^a-z0-9]+/))
      .filter((term) => term.length >= 4 && !['bestand', 'status', 'vandaag', 'nieuwste', 'onderzoek'].includes(term));
    const alternateRead = toolResults.some((tool) => (
      tool.name === name && tool.ok && tool.verified
      && !requestedPaths.includes(String(tool.args?.pad || '').toLowerCase())
      && (!requestedTerms.length || requestedTerms.some((term) => (
        `${tool.args?.pad || ''} ${tool.args?.zoek || ''} ${tool.output || ''}`
          .toLowerCase().includes(term)
      )))
    ));
    if (failedRequested && listedFallback && alternateRead
      && /\b(?:anders|als\s+dat\s+niet|bij\s+ontbreken|nieuwste|alternatief|zoek\s+dan)\b/i.test(text)) return true;
  }
  if (!requiredToolSatisfied(name, toolResults)) return false;
  if (['beheer_addon', 'gebruik_capability'].includes(name)) {
    const targetMatch = text.match(/\b(?:genaamd|add-?on|capability)\s+([a-z0-9][a-z0-9_-]{2,80})\b/i);
    const target = String(targetMatch?.[1] || '').toLowerCase();
    if (target && !['met', 'die', 'deze', 'bestaande', 'actieve'].includes(target)) {
      const matchedTarget = toolResults.some((tool) => {
        if (tool.name !== name || !tool.ok || !tool.verified) return false;
        const capability = actionData(tool)?.capability;
        return [tool.args?.naam, capability?.name, capability?.slug]
          .map((value) => String(value || '').toLowerCase())
          .includes(target);
      });
      if (!matchedTarget) return false;
    }
  }
  if (name === 'beheer_agentmail') {
    const succeeded = toolResults.filter((tool) => tool.name === name && tool.ok && tool.verified);
    const actionIs = (tool, values) => values.includes(
      cleanUserText(tool.args?.actie).toLowerCase(),
    );
    if (/^\s*\d{6}\s*$/.test(text)
      || /\b(?:otp|verificatiecode|verification\s+code|verifi(?:eer|ëren|cation))\b/i.test(text)) {
      return succeeded.some((tool) => actionIs(tool, ['verifieren', 'verifieer', 'verify']));
    }
    if (/\b(?:stuur|verstuur|mail)\w*\b.{0,100}\b(?:e-?mail|mailtje)\b/i.test(text)
      || /\b(?:e-?mail|mailtje)\b.{0,100}\b(?:stuur|verstuur|mail)\w*\b/i.test(text)) {
      return succeeded.some((tool) => {
        const data = actionData(tool);
        return actionIs(tool, ['versturen', 'sturen', 'send', 'mailen'])
          && !!data?.message_id && !!data?.thread_id && data?.delivery_state === 'accepted';
      });
    }
    if (/\b(?:lees|bekijk|controleer|zoek)\w*\b.{0,100}\b(?:e-?mail|mailtje|inbox|berichten?)\b/i.test(text)) {
      return succeeded.some((tool) => actionIs(tool, ['berichten', 'inbox', 'lezen', 'list', 'zoeken', 'zoek', 'search']));
    }
  }
  if (name === 'gebruik_robot') {
    const robotResults = toolResults.filter((tool) => tool.name === name);
    const actions = new Set(robotResults.filter((tool) => (
      tool.name === name && tool.ok && tool.verified
    )).map((tool) => String(tool.args?.actie || '').toLowerCase()));
    if (/\b(?:kaart|map|positie)\b/i.test(text) && !actions.has('kaart')) return false;
    if (/\b(?:status|dock|waar\s+(?:staat|is)|readiness|gereed)\b/i.test(text) && !actions.has('status')) return false;
  }
  if (name === 'lees_bestand') {
    const requestedPaths = [...text.matchAll(/\b(?:src|data\/workspace)\/[a-z0-9_./-]+\.[a-z0-9]+\b/gi)]
      .map((match) => match[0].toLowerCase());
    if (requestedPaths.length) {
      const read = new Set(toolResults.filter((tool) => (
        tool.name === name && tool.ok && tool.verified
      )).map((tool) => String(tool.args?.pad || '').toLowerCase()));
      if (requestedPaths.some((file) => !read.has(file))) return false;
    }
  }
  return true;
}

function turnRequirementSatisfied(name, toolResults = [], userText = '', options = {}) {
  const specialized = specializedRequirementStatus(
    name, cleanUserText(userText).toLowerCase(), toolResults, options,
  );
  if (specialized !== null) return specialized;
  if (alternateRequirementEvidence(name, userText, toolResults)) return true;
  return baseTurnRequirementSatisfied(name, toolResults, userText);
}

function humanResultText(value, {
  limit = 2_400,
  compact = false,
  preferDetailed = false,
} = {}, depth = 0, seen = new Set()) {
  if (value == null || depth > 7) return '';
  if (typeof value === 'string') {
    const raw = value.trim();
    if (!raw) return '';
    const looksLikeJson = /^\{|^\[\s*(?:\{|"|-?\d|true|false|null|\])/i.test(raw);
    if (looksLikeJson) {
      try {
        return humanResultText(JSON.parse(raw), {
          limit,
          compact,
          preferDetailed,
        }, depth + 1, seen);
      } catch {
        for (const key of ['message', 'text', 'human_message', 'summary', 'stdout']) {
          const match = raw.match(new RegExp(`"${key}"\\s*:\\s*("(?:\\\\.|[^"\\\\])*")`, 's'));
          if (!match) continue;
          try {
            return humanResultText(JSON.parse(match[1]), {
              limit,
              compact,
              preferDetailed,
            }, depth + 1, seen);
          } catch { /* probeer het volgende leesbare veld */ }
        }
        return '';
      }
    }
    const normalized = compact ? raw.replace(/\s+/g, ' ') : raw;
    return normalized.slice(0, limit).trim();
  }
  if (typeof value !== 'object') return '';
  if (seen.has(value)) return '';
  seen.add(value);

  if (Array.isArray(value)) {
    for (const entry of value) {
      const result = humanResultText(entry, { limit, compact, preferDetailed }, depth + 1, seen);
      if (result) return result;
    }
    return '';
  }

  const dataFirst = preferDetailed
    ? [value.data, value.text, value.message]
    : [value.human_message, value.message, value.text];
  const candidates = [
    ...dataFirst,
    value.stdout,
    value.info,
    value.reply,
    value.result,
    value.item?.result,
    value.item?.evidence,
    value.item?.next_step,
    value.item?.title,
    value.evidence?.summary,
    ...(preferDetailed ? [value.human_message] : [value.data]),
  ];
  for (const candidate of candidates) {
    if (candidate === value) continue;
    const result = humanResultText(candidate, { limit, compact, preferDetailed }, depth + 1, seen);
    if (result) return result;
  }
  if (value.path) return `Bestand ${String(value.path).slice(0, 1_000)} is opgeslagen.`;
  if (value.count === 0) return 'Er zijn geen bruikbare resultaten gevonden.';
  return '';
}

function readableEvidence(value) {
  return humanResultText(value, { limit: 800, compact: true, preferDetailed: true });
}

function checkpointEvidence(tool) {
  if (!tool) return '';
  if (tool.name === 'voer_uit') {
    const data = actionData(tool) || {};
    const output = readableEvidence(data.stdout || data.stderr || '');
    return output ? `Uitvoering met exitcode ${data.exit_code ?? 0}: ${output}` : '';
  }
  if (tool.name === 'lees_bestand') {
    const file = String(tool.args?.pad || '').trim();
    return file ? `Bestand ${file} is teruggelezen.` : '';
  }
  return readableEvidence(tool.output || tool.evidence?.summary || '');
}

function executionGroundedDependencies(toolResults = []) {
  return toolResults.filter((tool) => (
    verifiedCompleteReceipt(tool)
    && ['read', 'discovery'].includes(toolEffect(tool.name, tool.args))
  )).slice(-8).map((tool, index) => Object.freeze({
    sequence: index + 1,
    capabilityId: tool.semanticBinding?.capabilityId || null,
    operationId: tool.semanticBinding?.operationId || null,
    input: redactSensitiveText(JSON.stringify(tool.args || {})).slice(0, 1_200),
    evidence: humanResultText(tool.output || tool.evidence?.summary, {
      limit: 6_000,
      preferDetailed: true,
    }),
  })).filter((dependency) => dependency.evidence);
}

function autonomyContinuationStep(path, readyButUnreported, toolResults = []) {
  if (readyButUnreported) {
    return `Controleer ${path} opnieuw, meld alleen het concrete eindresultaat en werk dit punt daarna bij.`;
  }
  const canonicalPath = canonicalArtifactDisplayPath(path);
  const writeIndex = toolResults.findLastIndex((tool) => (
    tool.name === 'schrijf_bestand' && writtenArtifactPath(tool) === canonicalPath
  ));
  const failedExecution = toolResults.findLast((tool, index) => (
    index > writeIndex
    && tool.name === 'voer_uit'
    && executedArtifactPath(tool) === canonicalPath
    && (!tool.ok || !tool.verified)
  ));
  const failure = checkpointEvidence(failedExecution);
  return failure
    ? `Herstel voor ${path} deze concrete fout: ${failure}. Voer het werkstuk daarna opnieuw uit en lees het terug.`
    : `Voer het opgeslagen werkstuk uit, herstel eventuele fouten en lees ${path} daarna terug.`;
}

/** @param {any} resolution */
function iterativeRecoveryResolved({ recovery, receipt } = {}) {
  if (!recovery || !verifiedCompleteReceipt(receipt?.result)
    || receipt.recoveryReceipt !== Number(recovery.receipt)) return false;
  const type = (alias) => String(alias || '')
    .match(/_(write|run|readback|inspect|repair|retest)$/u)?.[1] || '';
  const failureType = type(recovery.alias);
  // Inspecting only establishes the current effect of a write/read failure. A
  // failed execution still needs a successful retest, even when its effect was
  // initially uncertain; otherwise a harmless source read could hide the
  // original failed program run.
  const expectedType = ['run', 'retest'].includes(failureType) ? 'retest'
    : recovery.effect_uncertain === true ? 'inspect' : ({
    write: 'repair', repair: 'repair', run: 'retest', retest: 'retest',
    readback: 'inspect', inspect: 'repair',
  })[failureType];
  return Boolean(expectedType) && type(receipt.alias) === expectedType;
}

function globalToolNameAttempt(previous, issuedBoundRoute, limit) {
  const attempts = Number(previous || 0) + (issuedBoundRoute ? 0 : 1);
  return { attempts, exceeded: !issuedBoundRoute && attempts > limit };
}

function autonomyLaneForTool(name = '') {
  // Een brede statuslezing is context, geen keuze om de hele beurt aan het huis te wijden.
  if (['signalen_afhandelen', 'huis_status'].includes(name)) return null;
  return ['house', 'research', 'workspace', 'family']
    .find((lane) => toolHasTag(name, `primary:${lane}`)) || null;
}

function autonomyLaneForWork(item = null) {
  const subject = `${item?.title || ''} ${item?.goal || ''} ${item?.next_step || ''}`.toLowerCase();
  if (!subject.trim()) return null;
  if (/\b(?:bestand|code|script|programma|software|website|app|tool|config|yaml|json|python|node|npm|test|audit)\b/i.test(subject)) return 'workspace';
  if (/\b(?:onderzoek|bronnen?|markt|prijs|vergelijk|regeling|subsidie|internet)\b/i.test(subject)) return 'research';
  if (/\b(?:home assistant|huis|lamp|licht|deur|raam|camera|sensor|temperatuur|aanwezig)\b/i.test(subject)) return 'house';
  if (/\b(?:gezin|jaap|sanne|lola|persoon|familie|voorkeur|gesprek)\b/i.test(subject)) return 'family';
  return null;
}

function autonomyProofRelevantToWork(item, tool) {
  if (!item || !tool?.ok || !tool?.verified) return false;
  const workLane = autonomyLaneForWork(item);
  const proofLane = toolHasTag(tool.name, 'proof:house') ? 'house' : autonomyLaneForTool(tool.name);
  if (!proofLane) return false;
  if (['research', 'workspace'].includes(workLane)) {
    return ['research', 'workspace'].includes(proofLane);
  }
  return !workLane || workLane === proofLane;
}

function currentWorkItem(id, toolResults = []) {
  const fromTurn = [...toolResults].reverse().find((tool) => (
    tool.name === 'werk_bijhouden' && tool.ok && tool.verified
    && Number(actionData(tool)?.item?.id) === Number(id)
  ));
  if (actionData(fromTurn)?.item) return actionData(fromTurn).item;
  const status = durableWorkStatus();
  return [...status.open, ...status.blocked, ...status.doneToday]
    .find((item) => Number(item.id) === Number(id)) || null;
}

function completedAutonomousArtifact(toolResults = []) {
  const writes = [...toolResults].reverse().filter((tool) => (
    tool.name === 'schrijf_bestand' && tool.ok && tool.verified
  ));
  const codeWrites = writes.filter((write) => (
    /\.(?:cjs|js|mjs|py|sh|ts)$/i.test(writtenArtifactPath(write))
  ));
  for (const write of codeWrites.length ? codeWrites : writes) {
    const artifactPath = writtenArtifactPath(write);
    if (!artifactPath) continue;
    const isCode = /\.(?:cjs|js|mjs|py|sh|ts)$/i.test(artifactPath);
    const completed = completedArtifact(toolResults, {
      requireExecution: isCode,
      path: artifactPath,
    });
    if (completed) return completed.write;
  }
  return null;
}

function incompleteAutonomousWrite(toolResults = []) {
  const writes = [...toolResults].reverse().filter((tool) => (
    tool.name === 'schrijf_bestand' && tool.ok === true && tool.verified === true
  ));
  const incomplete = (write) => {
    const artifactPath = writtenArtifactPath(write);
    if (!artifactPath) return false;
    const isCode = /\.(?:cjs|js|mjs|py|sh|ts)$/i.test(artifactPath);
    return !completedArtifact(toolResults, {
      requireExecution: isCode,
      path: artifactPath,
    });
  };
  return writes.find((write) => /\.(?:cjs|js|mjs|py|sh|ts)$/i.test(writtenArtifactPath(write)) && incomplete(write))
    || writes.find(incomplete)
    || null;
}

function autonomyDiscoveryAllowed(name, toolResults = [], full = config.autonomyFull) {
  if (!toolHasTag(name, 'discovery')) return true;
  if (name === 'lees_bestand' && incompleteAutonomousWrite(toolResults)) return true;
  const sameToolFailures = toolResults.filter((tool) => (
    tool.name === name && tool.ok === false
  )).length;
  if (sameToolFailures >= 2) return false;
  const used = toolResults.filter((tool) => (
    toolHasTag(tool.name, 'discovery')
  )).length;
  return used < (full ? AUTONOMY_DISCOVERY_LIMIT + 2 : AUTONOMY_DISCOVERY_LIMIT);
}

function autonomyLaneAllows(name, lane, toolResults = [], full = config.autonomyFull) {
  if (full) return true;
  if (!lane || name === 'signalen_afhandelen') return true;
  if (lane === 'research' && name === 'lees_bestand' && incompleteAutonomousWrite(toolResults)) {
    return true;
  }
  return toolHasTag(name, `lane:${lane}`);
}

function autonomousArtifactReadIndex(toolResults = []) {
  const write = completedAutonomousArtifact(toolResults);
  if (!write) return -1;
  const artifactPath = writtenArtifactPath(write);
  return toolResults.findLastIndex((tool) => (
    tool.name === 'lees_bestand' && tool.ok && tool.verified
    && readArtifactPath(tool) === artifactPath
  ));
}

function autonomyArtifactReportPending(toolResults = []) {
  if (config.autonomyFull) return false;
  const artifactIndex = autonomousArtifactReadIndex(toolResults);
  if (artifactIndex < 0) return false;
  const sentIndex = toolResults.findLastIndex((tool) => (
    tool.name === 'stuur_bericht' && tool.ok && tool.verified
  ));
  return sentIndex < artifactIndex;
}

function autonomyRequiredWorkIds(toolResults = [], initialOpenWorkIds = []) {
  const requiredIds = new Set((Array.isArray(initialOpenWorkIds) ? initialOpenWorkIds : [])
    .map(Number).filter(Number.isFinite));
  for (const tool of toolResults) {
    const item = tool.name === 'werk_bijhouden' ? actionData(tool)?.item : null;
    if (['open', 'blocked'].includes(item?.status) && Number.isFinite(Number(item?.id))) {
      requiredIds.add(Number(item.id));
    }
  }
  return [...requiredIds];
}

function autonomyPendingCheckpointIds(toolResults = [], initialOpenWorkIds = []) {
  const requiredIds = autonomyRequiredWorkIds(toolResults, initialOpenWorkIds);
  if (!requiredIds.length) return [];
  const outcomeIndex = toolResults.findLastIndex((tool) => (
    tool.name === 'stuur_bericht' && tool.ok && tool.verified
  ));
  if (outcomeIndex < 0) return [];
  return requiredIds.filter((requiredId) => {
    const item = currentWorkItem(requiredId, toolResults);
    const hasRelevantProof = toolResults.some((tool, index) => (
      index < outcomeIndex && autonomyProofRelevantToWork(item, tool)
    ));
    if (!hasRelevantProof) return false;
    const updates = toolResults.map((tool, index) => ({ tool, index }))
      .filter(({ tool }) => (
        tool.name === 'werk_bijhouden' && tool.ok && tool.verified
        && Number(actionData(tool)?.item?.id) === requiredId
      ));
    return !updates.some(({ index }) => index > outcomeIndex);
  });
}

function autonomyWorkCheckpointPending(toolResults = [], initialOpenWorkIds = []) {
  return autonomyPendingCheckpointIds(toolResults, initialOpenWorkIds).length > 0;
}

function workItemMateriallyChanged(before, after) {
  if (!before || !after) return !!after;
  return ['status', 'next_step', 'evidence', 'last_action', 'result']
    .some((field) => String(before[field] ?? '') !== String(after[field] ?? ''));
}

function autonomyProgressFromTools(toolResults = [], continuation = null) {
  const progressTool = [...toolResults].reverse().find((tool) => (
    tool.ok && tool.verified && tool.changed
    && (tool.name === 'schrijf_bestand'
      || (tool.name === 'werk_bijhouden' && actionData(tool)?.proofAccepted === true))
  ));
  if (!progressTool && !continuation) return null;
  const item = actionData(progressTool)?.item || continuation;
  const progressEvidence = checkpointEvidence(progressTool);
  const summary = progressTool?.name === 'schrijf_bestand'
    ? [
        progressEvidence,
        item?.next_step ? `Volgende stap: ${item.next_step}` : '',
      ].filter(Boolean).join(' | ')
    : item
    ? [
        item.title ? `Werk #${item.id}: ${item.title}` : '',
        item.evidence || progressEvidence,
        item.next_step ? `Volgende stap: ${item.next_step}` : '',
      ].filter(Boolean).join(' | ')
    : progressEvidence;
  return {
    made_progress: true,
    tool: progressTool?.name || item?.last_action || 'werk_bijhouden',
    summary: String(summary || 'Een duurzame tussenstap is gecontroleerd en opgeslagen.').slice(0, 800),
  };
}

function autonomyNoActionReason(rawAnswer, toolResults = []) {
  const answer = String(rawAnswer || '').replace(/\s+/g, ' ').trim();
  if (answer && answer.length <= 600
    && !/(?:to=functions\.|__eva_|function_call|tool_call)/i.test(answer)) {
    if (claimsCompletedAction(answer)) {
      return 'De controles in deze beurt leverden geen nieuw resultaat op dat een gezinsbeslissing of handeling veranderde.';
    }
    return answer;
  }
  if (toolResults.length) {
    return 'De controles in deze beurt leverden geen nieuw resultaat op dat een gezinsbeslissing of handeling veranderde.';
  }
  return 'Er was nu geen concrete, nieuwe bijdrage die betrouwbaar uitgevoerd kon worden.';
}

function contributionFromTools(toolResults, mode, userText = '', {
  durable = false,
  plannedRequiredTools = [],
  plannedTurnKind = null,
  plannedActionRequested = false,
  autoBoundOneShot = null,
} = {}) {
  const verified = toolResults.filter((tool) => tool?.ok === true && tool?.verified === true);
  if (mode !== 'autonomy') {
    const required = Array.isArray(plannedRequiredTools) ? plannedRequiredTools : [];
    if (!required.length && !durable && !plannedActionRequested) return null;
    if (durable) {
      const latestWrite = [...toolResults].reverse().find((tool) => (
        tool.name === 'schrijf_bestand' && tool.ok && tool.verified
      ));
      const artifactPath = writtenArtifactPath(latestWrite);
      const artifact = completedArtifact(toolResults, {
        requireExecution: /\.(?:cjs|js|mjs|py|sh|ts)$/i.test(artifactPath),
      });
      if (artifact) return artifact.execute || artifact.write;
      if (latestWrite) return null;
      const memory = oneShotAutoBoundMemoryContribution(toolResults, {
        ...autoBoundOneShot,
        actionRequested: plannedActionRequested,
        requiredTools: required,
        turnKind: plannedTurnKind,
      });
      if (memory) return memory;
      return [...verified].reverse().find((tool) => (
        (tool.changed === true
          && !['zoek_internet', 'schrijf_bestand', 'onthoud', 'stuur_bericht', 'werk_bijhouden'].includes(tool.name))
        || (tool.name === 'voer_uit' && plannedActionRequested && successfulZeroExit(tool))
      )) || null;
    }
    if (required.includes('schrijf_bestand')) {
      const artifact = completedArtifact(toolResults, {
        requireExecution: required.includes('voer_uit'),
      });
      return artifact ? (artifact.execute || artifact.write) : null;
    }
    if (required.length && required.some((name) => !turnRequirementSatisfied(
      name, toolResults, userText, { turnKind: plannedTurnKind },
    ))) {
      return null;
    }
    return [...verified].reverse().find((tool) => (
      (tool.changed === true
        && tool.name !== 'werk_bijhouden'
        && tool.name !== 'leer_gezicht_van_foto'
        && (tool.name !== 'onthoud' || /^\s*(?:onthoud|noteer)\b/i.test(cleanUserText(userText))))
      || (tool.name === 'zoek_internet' && /^\s*(?:zoek|onderzoek|vergelijk)\b/i.test(cleanUserText(userText)))
      || (tool.name === 'voer_uit' && plannedActionRequested && successfulZeroExit(tool))
    )) || null;
  }

  const sentIndex = toolResults.findLastIndex((tool) => (
    tool.name === 'stuur_bericht' && tool.ok === true && tool.verified === true
      && tool.changed === true
  ));
  // Een bronrapport of tussenbericht mag een daarna geschreven demo niet tot
  // een bijdrage promoveren. Het werk blijft open zodat Eva het in haar eigen
  // volgende werksessie uitvoert en terugleest.
  if (incompleteAutonomousWrite(toolResults)) return null;
  const completedAutonomyArtifact = completedAutonomousArtifact(toolResults);
  const completedArtifactIndex = completedAutonomyArtifact
    ? autonomousArtifactReadIndex(toolResults)
    : -1;
  if (config.autonomyFull
    && completedAutonomyArtifact && completedArtifactIndex > sentIndex) return completedAutonomyArtifact;
  if (config.autonomyFull && sentIndex >= 0) return toolResults[sentIndex];
  if (sentIndex >= 0 && toolResults.slice(0, sentIndex).some((tool) => (
    tool.ok === true && tool.verified === true
      && !['onthoud', 'toon_werk', 'werk_bijhouden', 'stuur_bericht'].includes(tool.name)
  ))) {
    return toolResults[sentIndex];
  }
  // Een aantoonbare stille verandering is ook een bijdrage. Communicatie is een
  // keuze, geen vereiste om uitgevoerd werk als productief te boeken.
  const silentChange = [...verified].reverse().find((tool) => (
    tool.changed === true
    && ['bedien_huis', 'zet_herinnering', 'annuleer_herinnering', 'spreek_in_huis'].includes(tool.name)
    && (tool.name !== 'zet_herinnering' || autonomyEffectRequiresReport(tool))
  ));
  if (silentChange) return silentChange;
  if (config.autonomyFull && completedAutonomyArtifact) return completedAutonomyArtifact;
  return null;
}

function workcellEvidenceFallback(toolResults = []) {
  const proof = [...toolResults].reverse().find((tool) => tool.name === 'beheer_werkcel'
    && tool.ok && tool.verified && actionData(tool)?.cell?.status === 'completed');
  const cell = actionData(proof)?.cell;
  if (!cell) return '';
  const streams = Array.isArray(cell.streams) ? cell.streams : [];
  return `Ik heb ${streams.filter((stream) => stream.status === 'completed').length}/${streams.length} onafhankelijke onderzoekssporen afgerond en het bewijs met de bronlinks bewaard.`;
}

function packageEvidenceFallback(toolResults = []) {
  const proof = [...toolResults].reverse().find((tool) => tool.name === 'beheer_toolpakket'
    && tool.ok && tool.verified);
  const data = actionData(proof);
  const item = data?.installation || (Array.isArray(data?.installations) ? data.installations[0] : null);
  if (!item) return '';
  return [
    `${item.package_name}@${item.package_version} staat op ${item.status}.`,
    item.integrity_ok === true ? 'De inhoud klopt nog met de gecontroleerde versie.' : '',
    item.status === 'staged' ? 'Hij staat klaar, maar draait nog niet.' : '',
  ].filter(Boolean).join(' ');
}

function capabilityEvidenceFallback(toolResults = []) {
  const used = [...toolResults].reverse().find((tool) => tool.name === 'gebruik_capability'
    && tool.ok && tool.verified && actionData(tool)?.demonstrated === true);
  const data = actionData(used);
  const capability = data?.capability;
  if (!capability) return '';
  const boundary = capability.kind === 'skill'
    ? 'De procedure is echt geladen en gebruikt; ze krijgt daar geen extra rechten door.'
    : 'De add-on is echt uitgevoerd in haar afgeschermde werkruimte.';
  const demoOutput = String(data?.stdout || data?.text || '').trim().split('')
    .filter((character) => character.charCodeAt(0) > 31 || [9, 10, 13].includes(character.charCodeAt(0)))
    .join('').slice(0, 800);
  return [
    `${capability.name} v${capability.version} is echt gebruikt.`,
    demoOutput ? `Letterlijke demo-uitvoer: ${demoOutput}` : '', boundary,
  ].filter(Boolean).join(' ');
}

function genericEvidenceFallback(toolResults = [], required = new Set(), userText = '') {
  const completedDocument = completedArtifact(toolResults, { requireExecution: false });
  if (completedDocument && !/\.(?:cjs|js|mjs|py|sh|ts)$/i.test(completedDocument.path)) {
    const content = humanResultText(
      completedDocument.read.output || completedDocument.read.evidence?.summary,
      { limit: 12_000, preferDetailed: true },
    );
    if (content) return content;
  }
  const useful = new Set(['bedien_huis', 'bekijk_camera', 'spreek_in_huis']);
  if (/^\s*(?:zoek|onderzoek|vergelijk)\b/i.test(cleanUserText(userText))) useful.add('zoek_internet');
  for (const name of required) {
    if (!['huis_status', 'ha_lees', 'lijst_bestanden', 'lees_bestand', 'wie_is_thuis', 'zoek_geheugen', 'toon_werk'].includes(name)) useful.add(name);
  }
  const proven = [...toolResults].reverse().find((tool) => tool?.ok && tool?.verified
    && useful.has(tool.name) && (required.size === 0 || required.has(tool.name)));
  return proven ? humanResultText(proven.output || proven.evidence?.summary, {
    limit: 2_400, preferDetailed: true,
  }) : '';
}

function evidenceFallback(toolResults, { requiredTools = [], userText = '' } = {}) {
  const required = new Set((Array.isArray(requiredTools) ? requiredTools : []).map(String));
  const special = [
    required.has('beheer_werkcel') ? workcellEvidenceFallback(toolResults) : '',
    required.has('beheer_toolpakket') ? packageEvidenceFallback(toolResults) : '',
    required.has('gebruik_capability') ? capabilityEvidenceFallback(toolResults) : '',
  ].find(Boolean);
  if (special) return special;
  if (required.has('schrijf_bestand')) {
    if (!required.has('voer_uit') && pendingExecutableArtifactPath(toolResults)) return '';
    const artifact = completedArtifact(toolResults, { requireExecution: required.has('voer_uit') });
    if (!artifact) return '';
    if (required.has('maak_vaardigheid') || required.has('beheer_addon')) {
      const capabilityTool = required.has('maak_vaardigheid') ? 'maak_vaardigheid' : 'beheer_addon';
      const active = [...toolResults].reverse().find((tool) => (
        tool.name === capabilityTool && tool.ok && tool.verified
        && actionData(tool)?.capability?.status === 'active'
      ));
      const capability = actionData(active)?.capability;
      if (!capability) return '';
      return `De ${capability.kind === 'addon' ? 'add-on' : 'vaardigheid'} ${capability.name} v${capability.version} is na de gebonden proef actief geworden.`;
    }
    if (required.has('voer_uit')) {
      const acceptance = programmingAcceptanceStatus(toolResults, userText);
      if ((acceptance.needsTests || acceptance.needsCli) && acceptance.complete) {
        const requestedFile = cleanUserText(userText).match(/\b([a-z0-9_-]+\.(?:cjs|js|mjs|py|sh|ts))\b/i)?.[1] || '';
        const writtenSources = [...toolResults].reverse().filter((tool) => {
          const file = writtenArtifactPath(tool);
          return tool.name === 'schrijf_bestand' && /\.(?:cjs|js|mjs|py|sh|ts)$/i.test(file)
            && !/(?:^|\/)test[_-]|[._-]test\./i.test(file);
        });
        const source = writtenSources.find((tool) => writtenArtifactPath(tool).endsWith(`/${requestedFile}`))
          || writtenSources[0];
        const testData = actionData(acceptance.testExecution) || {};
        const cliData = actionData(acceptance.cliExecution) || {};
        const testText = readableEvidence(`${testData.stdout || ''} ${testData.stderr || ''}`);
        const cliText = readableEvidence(cliData.stdout || cliData.stderr || '');
        return [
          `${writtenArtifactPath(source) || artifact.path} is geschreven en gecontroleerd.`,
          acceptance.testExecution
            ? `De gerichte tests slaagden met exitcode 0${testText ? `: ${testText}` : '.'}` : '',
          acceptance.cliExecution
            ? `De echte CLI-run slaagde met exitcode 0${cliText ? ` en gaf: ${cliText}` : '.'}` : '',
        ].filter(Boolean).join(' ');
      }
      return checkpointEvidence(artifact.execute)
        || 'Het programma is met exitcode 0 uitgevoerd en daarna teruggelezen.';
    }
  }
  return genericEvidenceFallback(toolResults, required, userText);
}

function looksLikeInternalOutput(value = '', { allowProjectPaths = false } = {}) {
  const text = String(value || '');
  return /\bdata[\\/]workspace\b|(?:"(?:stdout|stderr|exit_code)"|\b(?:stdout|stderr))\s*:|_ngcontent-|<\/?(?:html|div|p|script)\b|\{\s*"(?:contract|tool|ok)"/i.test(text)
    || (!allowProjectPaths && /`[^`\n]{1,160}\.(?:md|txt|json|ya?ml|csv|m?js|py)`/i.test(text))
    || /\[[\s\d,]{120,}\]/.test(text);
}

function recentStoredTurnUnderstandings(conversationId) {
  return db.prepare(`SELECT meta FROM main.timeline
    WHERE kind='turn_understanding'
      AND json_extract(meta, '$.conversationId')=?
    ORDER BY id DESC LIMIT 4`).all(conversationId)
    .map((row) => {
      try { return JSON.parse(row.meta)?.understanding || null; } catch { return null; }
    })
    .filter(verifyTurnUnderstandingRecord)
    .reverse();
}

function recentResourceReceipts(conversationId, { requester, userText } = {}) {
  const executions = recentVerifiedTurnToolReceipts(conversationId, 40);
  const priorHomeReceipt = priorVerifiedHomeContinuationResourceReceipt({
    conversationId, requester, userText, executions,
  });
  if (priorHomeReceipt) return [priorHomeReceipt];
  if (priorActionResultFollowup(userText)) return [];
  const candidates = executions.flatMap((row) => { try {
    const binding = JSON.parse(row.semantic_binding), args = JSON.parse(row.args);
    const result = JSON.parse(row.output), data = result.data;
    if (row.tool !== 'ha_lees' || binding.capabilityId !== 'home.entity.read' || binding.operationId !== 'read'
      || result.contract !== 'ActionResult/v1' || result.tool !== 'ha_lees' || !verifiedCompleteReceipt(result)
      || result.changed !== false || result.effect_uncertain === true || args.entity_id !== data?.entity_id
      || !/^[a-z0-9_]+\.[a-z0-9_]+$/u.test(String(data?.entity_id || ''))) return [];
    return [{ capabilityId: binding.capabilityId, operationId: binding.operationId, field: 'entityId',
      value: data.entity_id, sourceTurnId: row.turn_id, understandingSha256: binding.understandingSha256,
      requirementId: binding.requirementId,
      receiptSha256: row.completion_record_sha256, observedAt: row.finished_ts }];
  } catch { return []; } });
  return [...new Map(candidates.filter((item) => item.sourceTurnId === candidates[0]?.sourceTurnId)
    .map((item) => [item.value, item])).values()].slice(0, 8);
}

function boundAutonomyResumeContext(messageContext = null) {
  if (messageContext?.attention) return null;
  const workId = Number(messageContext?.workId);
  const episodeId = Number(messageContext?.goalEpisodeId);
  if (!Number.isFinite(workId) || !Number.isFinite(episodeId)) return null;
  const status = durableWorkStatus();
  const workItem = [...status.open, ...status.blocked]
    .find((item) => Number(item.id) === workId) || null;
  const episode = goalEpisodeById(episodeId);
  if (!workItem || !episode || Number(episode.work_item_id) !== workId
    || ['closed', 'abandoned'].includes(String(episode.state || ''))) return null;
  const storedState = durableWorkState(workItem);
  const retryCandidate = interpretationRetryResume(workItem, episode);
  const invalidInterpretationRetry = Object.hasOwn(storedState, 'interpretation_retry')
    && retryCandidate?.valid !== true;
  const interpretationRetry = retryCandidate?.valid === true ? retryCandidate : null;
  return Object.freeze({
    workItem,
    episode,
    monitoringContract: monitoringContractForWork(workItem),
    interpretationRetry,
    invalidInterpretationRetry,
  });
}

function retryInterpretationTimeout(channel, retry, mode = 'chat') {
  const foreground = channel === 'voice' ? VOICE_FOREGROUND_TIMEOUT_MS
    : mode === 'autonomy' ? AUTONOMY_INTERPRETATION_TIMEOUT_MS : 60_000;
  const remaining = retry?.valid ? Date.parse(retry.deadlineAt || '') - Date.now() : Number.POSITIVE_INFINITY;
  return Number.isFinite(remaining) ? Math.max(250, Math.min(foreground, remaining)) : foreground;
}

function issuedIterativeChildPlan(plan) {
  return ['proof', 'recovery'].includes(plan?.execution?.lane);
}

function runtimeInterpretationLimits(channel, selfAttention, retryValid = false) {
  const bounded = retryValid || channel === 'voice' || selfAttention;
  return {
    provider: {
      transientRetries: bounded ? 0 : 2,
      authenticationRetries: bounded ? 0 : 1,
      ...(selfAttention || retryValid ? { maxOutputTokens: 8_000 } : {}),
    },
    maxAttempts: bounded ? 1 : 3,
  };
}

function boundedSelfAttentionTransportRetry(receipt, messageContext, assertCurrent) {
  if (!receipt || typeof assertCurrent !== 'function') return null;
  const goalClaimUntil = messageContext?.attention?.kind === 'goal'
    ? Date.parse(messageContext.attention.claimed_until || '') : null;
  return Object.freeze({
    maxRetries: 1,
    assertCurrent: () => {
      assertCurrent();
      if (goalClaimUntil != null
        && (!Number.isFinite(goalClaimUntil) || Date.now() >= goalClaimUntil)) {
        throw Object.assign(new Error('De geattesteerde doelaandacht is verlopen.'), {
          code: 'EVA_SELF_ATTENTION_EXPIRED',
        });
      }
    },
  });
}

function invalidSelfAttentionInterpretation(artifactReceipts, workReceipt) {
  return {
    ok: false, source: 'host_self_attention_boundary', attempts: 0,
    understanding: null, firstDecision: null,
    failure: {
      contract: 'TurnUnderstandingFailure/v1',
      code: 'invalid_self_attention_receipt', attempts: 0,
    },
    artifactReceipts, conversationWorkReceipt: workReceipt,
  };
}

function retryInterpretationRoute(mode, channel, retry) {
  if (!retry?.valid) return { mode, channel };
  return { mode: 'chat', channel: retry.origin.channel || channel };
}

function freeAutonomyIdentityConstraints({
  mode, messageContext, resumeContext, authenticFreeAutonomy,
}) {
  const exactFreeAutonomy = authenticFreeAutonomy === true
    && exactAuthenticFreeAutonomyContext({
      mode, messageContext, resumeContext,
      schedulerEpoch: verifiedAutonomyInteractionEpoch(messageContext),
    });
  if (!exactFreeAutonomy) return { sleepingConstraint: '', freeNoActionContext: '' };
  return {
    sleepingConstraint: sleepingExplorationConstraintText(new Date()),
    freeNoActionContext: recentFreeNoActionContextText(),
  };
}

async function runtimeVoiceHomeGrounding({
  interpretationChannel, userText, messageContext, turnId, conversationId,
}) {
  if (interpretationChannel !== 'voice') return null;
  const homeCommand = directHomeCommand(userText);
  if (!homeCommand) return null;
  try {
    return await groundContextualHomeCommand(homeCommand, {
      isCurrent: typeof messageContext?.isCurrent === 'function'
        ? messageContext.isCurrent : () => true,
      ...(messageContext?.syntheticTest === true && Array.isArray(messageContext?.voiceHomeStates)
        ? { states: messageContext.voiceHomeStates } : {}),
    });
  } catch (error) {
    logTimeline('voice_grounding_unavailable', `Voice-huiscontext kon niet worden gelezen: ${errMsg(error)}`, {
      internalOnly: true, turnId, conversationId,
    });
    return null;
  }
}

async function understandRuntimeTurn({
  turnId, channel, person, userText, conversationId, messageContext, mode, signalBatch,
  resumeContext = null, trustedActiveWorkId, assertCurrent, authenticFreeAutonomy = false,
}) {
  const retry = resumeContext?.interpretationRetry;
  if (retry?.stopReason) {
    return {
      ok: false,
      source: 'host_retry_boundary',
      attempts: 0,
      understanding: null,
      firstDecision: null,
      failure: {
        contract: 'TurnUnderstandingFailure/v1',
        code: `interpreter_retry_${retry.stopReason}`,
        attempts: 0,
      },
    };
  }
  const interpretationRoute = retryInterpretationRoute(mode, channel, retry);
  const interpretationMode = interpretationRoute.mode;
  const interpretationChannel = interpretationRoute.channel;
  const recent = interpretationMode === 'chat' ? channelConversationRows({
    channel: interpretationChannel,
    conversationId,
    sender: person,
    messageContext,
    limit: 8,
  }) : [];
  const episodic = trustedOlderConversationRows({
    person, conversationId, query: userText, messageContext,
    mode: interpretationMode, limit: 3,
  });
  const crossChannel = trustedCrossChannelConversationRows({
    person, channel: interpretationChannel, conversationId, messageContext,
    mode: interpretationMode, limit: episodic.length ? 1 : 3,
  });
  const artifactReceipts = recentEpisodeArtifactReceipts(conversationId);
  const resourceReceipts = recentResourceReceipts(conversationId, {
    requester: person, userText,
  });
  const workReceipt = interpretationMode === 'chat' ? conversationWorkReceipt(conversationId, person) : null;
  const episodicIds = new Set(episodic.map(conversationRowKey));
  const dialogueRows = [...new Map([
    ...episodic,
    ...crossChannel,
    ...recent.slice(-(episodic.length ? 4 : 5)),
  ].map((row) => [conversationRowKey(row), row])).values()];
  if (interpretationChannel !== 'telegram') {
    dialogueRows.sort((a, b) => Number(a.id || 0) - Number(b.id || 0));
  }
  const priorUnderstandings = recentStoredTurnUnderstandings(conversationId);
  const voiceRetryIntent = interpretationMode === 'chat'
    ? deriveVoiceRetryIntent({ conversationId }) : null;
  const activeEpisode = interpretationMode === 'chat'
    ? goalEpisodeById(workReceipt?.episodeId) || openEpisodeForConversation(conversationId) : null;
  const continuingEpisode = resumeContext?.episode || activeEpisode;
  const activeGoal = continuingEpisode ? {
    statement: continuingEpisode.project_title || continuingEpisode.goal_title
      || continuingEpisode.requested_outcome || null,
    outcome: continuingEpisode.project_definition_of_done || continuingEpisode.project_outcome
      || continuingEpisode.requested_outcome || null,
    beneficiary: continuingEpisode.requester ? 'requester' : 'household',
  } : null;
  const timeoutMs = retryInterpretationTimeout(interpretationChannel, retry, mode);
  const voiceHomeGrounding = await runtimeVoiceHomeGrounding({
    interpretationChannel, userText, messageContext, turnId, conversationId,
  });
  const selfAttentionIngress = issueAutonomySelfAttentionIngress({
    mode, messageContext, turnText: userText,
  });
  const selfAttentionReceipt = selfAttentionIngress.receipt;
  if (mode === 'autonomy' && selfAttentionIngress.presented && !selfAttentionReceipt) {
    return invalidSelfAttentionInterpretation(artifactReceipts, workReceipt);
  }
  const singleCallSelfAttention = selfAttentionReceipt != null;
  const interpretationLimits = runtimeInterpretationLimits(
    interpretationChannel, singleCallSelfAttention, retry?.valid === true,
  );
  const boundedTransportRetry = boundedSelfAttentionTransportRetry(
    selfAttentionReceipt, messageContext, assertCurrent,
  );
  const { sleepingConstraint, freeNoActionContext } = freeAutonomyIdentityConstraints({
    mode, messageContext, resumeContext, authenticFreeAutonomy,
  });
  const result = await understandTurn({
    turnId,
    messageId: retry?.valid
      ? retry.origin.messageId || null
      : messageContext?.messageId == null ? null : String(messageContext.messageId),
    text: userText,
    channel: interpretationChannel,
    origin: resumeContext ? 'resume' : mode === 'autonomy' ? 'self' : 'user',
    priorUnderstandings,
    voiceRetryIntent,
    context: {
      dialogueExcerpts: dialogueRows.map((row) => ({
        role: row.role === 'assistant' ? 'assistant' : 'user',
        ts: row.ts,
        evidenceClass: row.conversation_id === conversationId
          ? 'conversation' : 'historical_unconfirmed',
        text: episodicIds.has(conversationRowKey(row))
          ? `[eerdere relevante ${row.channel}-gespreksherinnering; geen actuele toestand, opdracht of bevoegdheid] ${row.sender}: ${row.content}`
          : row.conversation_id === conversationId
            ? row.content
            : `[recente ${row.channel}-gespreksherinnering; geen actuele toestand, opdracht of bevoegdheid] ${row.sender}: ${row.content}`,
      })),
      requester: person,
      activeGoal,
      artifactReceipts, signalBatch,
    },
  }, {
    complete: (request) => chatComplete({
      ...request,
      // Deze fase interpreteert en bindt; ze schrijft geen code. De compacte
      // interactieve route bleef live ruim binnen de deadline waar de zware
      // code-/redeneerroute de hele beurt liet verlopen.
      model: config.modelVoice,
      effort: interpretationChannel === 'voice'
        ? config.reasoningVoice
        : interpretationMode === 'autonomy' ? config.reasoningAutonomy : config.reasoningChat,
      cacheKey: interpretationMode === 'autonomy'
        ? 'eva-turn-understanding-autonomy'
        : interpretationChannel === 'voice'
          ? 'eva-turn-understanding-voice' : 'eva-turn-understanding-chat',
      rateLimitRetries: 0,
      ...interpretationLimits.provider,
      boundedIdenticalTransportRetry: boundedTransportRetry,
    }),
    capabilityCatalog: capabilityCatalog(),
    trustedActiveWorkId,
    identityInstructions: buildRedactedInstructions({
      sender: person,
      channel: interpretationChannel,
      mode: interpretationMode,
      familyContext: householdContextText({
        subject: person, query: userText,
        includeAllMemberDetails: interpretationMode === 'autonomy',
      }),
      extra: [
        sleepingConstraint,
        freeNoActionContext,
        selfContinuityContextText({ mode: interpretationMode, query: userText }),
        interpretationMode === 'autonomy' ? autonomousMemoryContextText() : '',
        moodActionGuidance(),
      ].filter(Boolean).join('\n'),
    }),
    timeoutMs,
    maxAttempts: interpretationLimits.maxAttempts,
    trustedResourceReceipts: resourceReceipts,
    trustedConversationWorkReceipts: workReceipt ? [workReceipt] : [], trustedConversationId: conversationId,
    trustedSelfAttentionReceipt: selfAttentionReceipt,
    voiceTargets: config.voiceAssistSatelliteTargets,
    voiceHomeGrounding,
  });
  const runtimeResult = {
    ...result, artifactReceipts, conversationWorkReceipt: workReceipt,
  };
  if (singleCallSelfAttention) BOUNDED_SELF_ATTENTION_RESULTS.add(runtimeResult);
  return runtimeResult;
}

function failedSemanticTurnState({
  result, error, failureStage, person, userText, conversationId, messageContext, mode,
}) {
  const interpretationSucceeded = result?.ok === true
    && verifyTurnUnderstanding(result.understanding);
  const stage = ['understanding', 'binding', 'authority'].includes(failureStage)
    ? failureStage
    : interpretationSucceeded ? 'binding' : 'understanding';
  const requiredTools = [];
  const allowedToolNames = new Set();
  const effectPolicy = { mode: 'deny_all', deniedEffects: [] };
  const turnContract = {
    kind: mode === 'autonomy' ? 'autonomy' : 'conversation',
    outcome: stage === 'understanding'
      ? 'Geen handeling zonder een geldige beurtinterpretatie.'
      : stage === 'binding'
        ? 'De geldige beurtinterpretatie kon niet veilig aan een uitvoerhandeling worden gebonden.'
        : 'De geldige beurtinterpretatie kreeg geen bevoegdheid voor uitvoering.',
    proof: 'fail-closed',
    requiredTools,
    allowedTools: allowedToolNames,
    effort: null,
  };
  return {
    ok: false,
    result,
    error,
    failureStage: stage,
    interpretationSucceeded,
    understanding: result?.understanding || null,
    plans: [],
    person,
    userText,
    conversationId,
    syntheticTest: messageContext?.syntheticTest === true,
    continuationRequest: false,
    continuationAnchor: '',
    readOnlyRequest: true,
    voiceDirective: null,
    turnPlan: {
      contract: turnContract,
      requiredTools,
      allowedToolNames,
      actionIntent: 'forbid',
      effectPolicy,
      directAction: null,
    },
    turnContract,
    requiredTools,
    projectWork: false,
    longWork: mode === 'autonomy',
    explicitTool: false,
    recapOnly: false,
    reflectiveQuestion: false,
    buildWork: false,
    workspaceEditWork: false,
    newBuildWork: false,
    buildNeedsExecution: false,
    durableInteraction: false,
    liveInspection: false,
    liveHouseInspection: false,
    includeHomeSnapshot: mode === 'autonomy',
    continuationNeedsCode: false,
    continuationNeedsResearch: false,
    conversationOnly: true,
    identityLearningName: null,
    currentReplyAct: 'conversation',
    workContextRequested: mode === 'autonomy',
    monitoring: { requested: false, deliveryRequired: false, topicTokens: [] },
  };
}

function recordSemanticTurnTimeline({ semanticTurn, semanticResult, turnId, conversationId }) {
  const sourceUnderstanding = semanticResult?.understanding;
  const understandingRecord = verifyTurnUnderstanding(sourceUnderstanding)
    ? createTurnUnderstandingRecord(sourceUnderstanding, { redactText: redactSensitiveText })
    : null;
  const interpretationSucceeded = semanticTurn?.interpretationSucceeded === true;
  const understanding = logTimeline('turn_understanding', interpretationSucceeded
    ? `${sourceUnderstanding.dialogueAct}: ${sourceUnderstanding.goal.outcome || sourceUnderstanding.goal.statement || 'geen expliciet doel'}`
    : `fail-closed: ${semanticTurn?.error || 'interpreter_invalid'}`, {
    internalOnly: true,
    turnId,
    conversationId,
    source: semanticResult?.source,
    attempts: semanticResult?.attempts,
    attemptFailures: semanticResult?.failure?.attemptFailures || [],
    understanding: understandingRecord,
    interpretationSucceeded,
    error: semanticTurn?.failureStage === 'understanding' ? semanticTurn.error : null,
  });
  let planning = null;
  if (!semanticTurn?.ok && ['binding', 'authority'].includes(semanticTurn?.failureStage)) {
    planning = logTimeline(
      'turn_planning',
      `fail-closed ${semanticTurn.failureStage}: ${semanticTurn.error}`,
      {
        internalOnly: true,
        turnId,
        conversationId,
        understandingSha256: sourceUnderstanding.sha256,
        failureStage: semanticTurn.failureStage,
        error: semanticTurn.error,
      },
    );
  }
  const turnContract = semanticTurn?.turnContract || {};
  const requiredTools = Array.isArray(semanticTurn?.requiredTools)
    ? semanticTurn.requiredTools : [];
  const allowedToolNames = semanticTurn?.turnPlan?.allowedToolNames;
  const offeredTools = allowedToolNames instanceof Set
    ? [...allowedToolNames] : requiredTools;
  const contract = logTimeline(
    'turn_contract',
    `${turnContract.kind || 'conversation'}: ${turnContract.outcome || 'Beantwoord de huidige beurt.'}`,
    {
      internalOnly: true,
      turnId,
      conversationId,
      kind: turnContract.kind || 'conversation',
      requiredTools,
      offeredTools,
      actionIntent: semanticTurn?.turnPlan?.actionIntent || 'explain',
      effectPolicy: semanticTurn?.turnPlan?.effectPolicy || { mode: 'deny_all', deniedEffects: [] },
      effort: turnContract.effort || null,
    },
  );
  return { understanding, planning, contract };
}

function buildSemanticPlans({
  selectedActions, understanding, result, person, userText, conversationId,
  messageContext, mode, replyEvidence,
}) {
  let plans = selectedActions.map((action) => {
    const identityClaim = action.capabilityId === 'identity.face.learn'
      && action.operationId === 'learn'
      ? verifiedTelegramIdentityContext(messageContext, {
        channel: 'telegram', conversationId,
        messageId: String(messageContext?.messageId || ''),
        requester: person, originalText: userText,
        name: action.semanticInput?.naam,
        replyEvidence,
      }) : null;
    const plan = planCapabilityAction(action, {
      mode,
      interactiveAuthority: identityClaim
        ? 'trusted_context' : mode === 'chat' ? 'explicit' : 'ambient',
      fullAutonomy: config.autonomyFull,
      synthetic: messageContext?.syntheticTest === true,
      syntheticNamespace: result.understanding?.source?.turnId || null,
      understanding,
      understandingSha256: understanding.sha256,
      effectPolicy: { mode: 'allow', deniedEffects: understanding.deniedEffects },
    });
    if (plan && identityClaim) ISSUED_IDENTITY_CONTEXT_CLAIMS.set(plan, identityClaim);
    return plan;
  });
  const priorHomeExecutions = mode === 'chat'
    && messageContext?.identityTrusted === true
    && ['continue', 'correct'].includes(understanding.dialogueAct)
    ? recentVerifiedTurnToolReceipts(conversationId, 20) : [];
  plans = plans.map((plan) => {
    if (plan?.capabilityId === 'signal.assess') {
      return bindSignalAssessmentPlan(plan, understanding);
    }
    if (!['home.context.read', 'home.entity.read'].includes(plan?.capabilityId)) return plan;
    const continuityPlan = bindContinuityContextPlan(plan, understanding);
    return bindPriorVerifiedHomeContextPlan(continuityPlan, {
      conversationId,
      requester: person,
      userText,
      understanding,
      executions: priorHomeExecutions,
    });
  });
  return plans;
}

function semanticTurnKind({ mode, capabilities, monitoringRequested, continuityRequested, plans }) {
  if (mode === 'autonomy') return 'autonomy';
  if (capabilities.has('home.voice.announce')) return 'voice_action';
  if (monitoringRequested) return 'monitoring';
  if (continuityRequested) return 'continuity';
  if ([...capabilities].some((id) => id.startsWith('runtime.'))) return 'self_repair';
  if (capabilities.has('identity.face.learn')) return 'identity';
  if (capabilities.has('research.workcell.manage')) return 'parallel_work';
  if (capabilities.has('package.workspace.manage')) return 'tool_package';
  if ([...capabilities].some((id) => (
    id.startsWith('capability.addon.') || id.startsWith('capability.skill.')
  ))) return 'capability_expansion';
  if (capabilities.has('workspace.file.write')) return 'programming_build';
  if (plans.length && plans.every((plan) => plan.effect === 'read')) return 'live_inspection';
  return plans.length ? 'action' : 'conversation';
}

function semanticTurnState({
  result, person, userText, conversationId, messageContext, mode, channel,
  replyEvidence = null,
  resumeContext = null,
}) {
  if (!result?.ok || !verifyTurnUnderstanding(result.understanding)) {
    return failedSemanticTurnState({
      result, error: result?.failure?.code || 'interpreter_invalid',
      failureStage: 'understanding',
      person, userText, conversationId, messageContext, mode,
    });
  }
  const understanding = result.understanding;
  const selected = new Set(understanding.firstDecision.requirementIds);
  const selectedActions = understanding.actions.filter((action) => selected.has(action.requirementId));
  if (selectedActions.some((action) => action.authorization !== 'allowed')) {
    return failedSemanticTurnState({
      result, error: 'capability_authority_denied', failureStage: 'authority',
      person, userText, conversationId, messageContext, mode,
    });
  }
  let plans;
  try {
    plans = buildSemanticPlans({
      selectedActions, understanding, result, person, userText, conversationId,
      messageContext, mode, replyEvidence,
    });
  } catch (error) {
    return failedSemanticTurnState({
      result, error: `capability_binding:${errMsg(error)}`,
      failureStage: 'binding',
      person, userText, conversationId, messageContext, mode,
    });
  }
  if (plans.some((plan) => !plan)) {
    // Alleen op het zeldzame fail-closed pad onderscheiden we een verdwenen
    // registrybinding van een bekende handeling zonder uitvoerbevoegdheid.
    // De normale beurt bouwt de grote capabilitycatalogus dus niet dubbel op.
    const knownOperations = new Set(capabilityCatalog().flatMap((capability) => (
      capability.operations.map((operation) => `${capability.id}\u0000${operation.id}`)
    )));
    const unknownAction = selectedActions.find((action, index) => !plans[index]
      && !knownOperations.has(`${action.capabilityId}\u0000${action.operationId}`));
    const failureStage = unknownAction ? 'binding' : 'authority';
    return failedSemanticTurnState({
      result,
      error: unknownAction
        ? `capability_binding:unknown_operation:${unknownAction.capabilityId}:${unknownAction.operationId}`
        : 'capability_authority_denied',
      failureStage,
      person, userText, conversationId, messageContext, mode,
    });
  }
  const economicBoundary = mode === 'autonomy'
    ? explorationEconomicResumeBoundary(resumeContext) : null;
  const forbiddenEconomicExternal = plans.some((plan) => (
    plan.effect === 'external'
      && !(plan.capabilityId === 'message.deliver' && plan.operationId === 'send'
        && understanding.delivery?.target === 'family')
  ));
  if (economicBoundary?.present
    && (!economicBoundary.valid || forbiddenEconomicExternal)) {
    return failedSemanticTurnState({
      result,
      error: economicBoundary.valid
        ? 'exploration_economic_handoff_external_authority_denied'
        : 'exploration_economic_handoff_integrity',
      failureStage: 'authority', person, userText, conversationId, messageContext, mode,
    });
  }
  let aspirationExplorationAnchor = null;
  let explorationTerminalContribution = null;
  try {
    aspirationExplorationAnchor = bindIssuedAspirationExplorationPlans(
      understanding, plans,
    );
    explorationTerminalContribution = bindIssuedExplorationTerminalPlan(
      understanding, plans,
    );
  } catch (error) {
    return failedSemanticTurnState({
      result, error: `capability_binding:${errMsg(error)}`,
      failureStage: 'binding', person, userText, conversationId, messageContext, mode,
    });
  }
  const capabilities = new Set(selectedActions.map((action) => action.capabilityId));
  const effects = new Set(plans.map((plan) => plan.effect));
  const requiredTools = [...new Set(plans.map((plan) => plan.toolName))];
  const allowedToolNames = new Set(requiredTools);
  const monitoring = understanding.monitoring ? Object.freeze({
    ...understanding.monitoring,
    sourceUnderstandingSha256: understanding.sha256,
    conditions: Object.freeze(understanding.conditions.map((condition) => condition.statement)),
    temporalCondition: understanding.conditions
      .filter((condition) => condition.role === 'guard')
      .map((condition) => condition.statement).join(' ').slice(0, 1_000) || null,
    // Bewaar de letterlijke bewonersvoorwaarde naast de modelsemantiek. Zo
    // beoordeelt iedere latere meetcyclus nog steeds exact dezelfde opdracht,
    // ook wanneer de work-titel of definitie haar vrij parafraseert.
    definitionOfDone: String(userText || '').trim().slice(0, 1_200) || null,
  }) : null;
  const monitoringRequested = monitoring?.requested === true;
  const continuity = continuityWatchForUnderstanding(understanding), continuityRequested = continuity?.requested === true;
  const kind = semanticTurnKind({
    mode, capabilities, monitoringRequested, continuityRequested, plans,
  });
  const proof = [...new Set([
    ...understanding.proofNeeds,
    ...plans.flatMap((plan) => plan.proofFloor),
  ])];
  const outcome = understanding.goal.outcome || understanding.goal.statement
    || (plans.length ? 'De gekozen handeling is gecontroleerd uitgevoerd.' : 'De vraag is direct beantwoord.');
  const effectPolicy = { mode: 'allow', deniedEffects: understanding.deniedEffects };
  const actionIntent = plans.length
    ? plans.every((plan) => plan.effect === 'read') ? 'inspect' : 'execute'
    : ['cancel', 'reject'].includes(understanding.dialogueAct) ? 'forbid' : 'explain';
  const turnContract = {
    kind,
    outcome,
    proof: proof.length ? proof.join(', ') : 'geen onbewezen uitvoeringsclaim',
    requiredTools,
    allowedTools: allowedToolNames,
    effort: effects.has('runtime') || effects.has('workspace') ? 'high' : plans.length ? 'medium' : null,
    ...(monitoringRequested ? { monitoring } : {}),
    ...(continuityRequested ? { continuity } : {}),
  };
  const latestAssistant = [...(mode === 'chat' ? channelConversationRows({
    channel,
    conversationId,
    sender: person,
    messageContext,
    limit: 8,
  }) : [])]
    .reverse().find((row) => row.role === 'assistant')?.content || '';
  const voicePlan = plans.find((plan) => plan.capabilityId === 'home.voice.announce');
  const identityPlan = plans.find((plan) => plan.capabilityId === 'identity.face.learn');
  const hasHomeCapability = [...capabilities].some((id) => (
    id.startsWith('home.') || id.startsWith('household.presence.')
    || id.startsWith('robot.') || id.startsWith('camera.')
  ));
  const decisionExecutes = ['act', 'inspect', 'schedule'].includes(understanding.firstDecision.kind);
  const turnPlan = {
    contract: turnContract,
    requiredTools,
    allowedToolNames,
    actionIntent,
    effectPolicy,
    directAction: plans.length === 1 && plans[0].toolName === 'bedien_huis'
      ? { ...plans[0].args, requester: person }
      : null,
  };
  const continuationRequest = CONTINUATION_DIALOGUE_ACTS.includes(understanding.dialogueAct);
  const receiptWorkId = Number(result.conversationWorkReceipt?.value);
  const conversationWorkId = plans.some((plan) => plan.capabilityId === 'work.episode.manage'
    && plan.operationId === 'annuleren' && Number(plan.args?.id) === receiptWorkId) ? receiptWorkId : Number.NaN;
  return {
    ok: true,
    result,
    failureStage: null,
    interpretationSucceeded: true,
    understanding,
    plans,
    aspirationExplorationAnchor,
    explorationTerminalContribution,
    person,
    userText,
    conversationId,
    syntheticTest: messageContext?.syntheticTest === true,
    conversationWorkId,
    continuationRequest,
    continuationAnchor: continuationRequest ? latestAssistant : '',
    readOnlyRequest: plans.length > 0 && plans.every((plan) => plan.effect === 'read'),
    voiceDirective: voicePlan ? {
      requested: true, target: voicePlan.args.doel, text: voicePlan.args.tekst, inherited: false,
    } : null,
    turnPlan,
    turnContract,
    requiredTools,
    projectWork: understanding.durability === 'episode',
    longWork: understanding.durability === 'episode' || plans.length > 2
      || effects.has('runtime') || effects.has('workspace'),
    explicitTool: decisionExecutes && plans.length > 0,
    recapOnly: false,
    reflectiveQuestion: understanding.dialogueAct === 'question' && plans.length === 0,
    buildWork: requiredTools.includes('schrijf_bestand'),
    workspaceEditWork: false,
    newBuildWork: requiredTools.includes('schrijf_bestand'),
    buildNeedsExecution: requiredTools.includes('voer_uit'),
    // Een episode-action uit dezelfde interpretatie is zelf de canonieke
    // persistentie; maak daarnaast geen tweede generiek werkpunt aan.
    durableInteraction: understanding.durability === 'episode'
      && !plans.some((plan) => plan.effect === 'persistent'),
    liveInspection: plans.some((plan) => plan.effect === 'read'),
    liveHouseInspection: plans.some((plan) => plan.effect === 'read') && hasHomeCapability,
    includeHomeSnapshot: mode === 'autonomy' || hasHomeCapability,
    continuationNeedsCode: [...capabilities].some((id) => id.startsWith('workspace.')),
    continuationNeedsResearch: capabilities.has('internet.research'),
    conversationOnly: plans.length === 0 && understanding.firstDecision.kind === 'reply',
    identityLearningName: identityPlan?.args?.naam || null,
    currentReplyAct: voicePlan ? 'voice' : identityPlan ? 'identity'
      : hasHomeCapability && plans.some((plan) => plan.effect === 'read') ? 'live_inspection' : 'conversation',
    workContextRequested: understanding.durability === 'episode'
      || [...capabilities].some((id) => id.startsWith('work.') || id.startsWith('goal.')),
    monitoring,
  };
}

function selfAttentionProviderExplorationMatches(profile) {
  const current = explorationById(profile?.exploration?.id);
  const issued = profile?.exploration;
  return Boolean(current && issued
    && Number(current.id) === issued.id
    && Number(current.revision) === issued.revision
    && String(current.status || '') === issued.status
    && Number(current.action_count) === issued.actionCount
    && Number(current.max_actions) === issued.maxActions
    && issued.actionCount < issued.maxActions
    && String(current.question || '') === issued.question);
}

function selfAttentionProviderTimeout(profile) {
  const remaining = Math.floor(Number(profile?.deadlineTs) - Date.now());
  if (!Number.isSafeInteger(remaining) || remaining < 250) {
    throw Object.assign(new Error('Het begrensde researchvenster van deze aandachtbeurt is verlopen.'), {
      code: 'EVA_SELF_ATTENTION_PROVIDER_TIMEOUT',
    });
  }
  return Math.min(AUTONOMY_INTERPRETATION_TIMEOUT_MS, remaining);
}

function assertSelfAttentionProviderCurrent(profile, assertCurrent) {
  assertCurrent();
  if (selfAttentionProviderExplorationMatches(profile)) return;
  throw Object.assign(new Error('De uitgegeven exploration-revisie is intussen gewijzigd.'), {
    code: 'EVA_SELF_ATTENTION_EXPLORATION_STALE',
  });
}

function selfAttentionProviderAuditOutput(name, {
  providerRequestCompleted = false,
  failureClass = 'conflict',
  error = 'De begrensde provideruitkomst kan niet aan de actuele exploration worden gekoppeld.',
} = {}) {
  return JSON.stringify({
    contract: 'ActionResult/v1', tool: name, ok: false, changed: false,
    verified: false, evidence: null, needs_followup: true, partial: false,
    failure_class: failureClass, retryable: false, effect_uncertain: false,
    risk: 'autonomous', human_message: '', error,
    data: { provider_request_completed: providerRequestCompleted },
  });
}

function selfAttentionProviderClaimCollision(executionKey, name, profile) {
  let existing = null;
  try {
    existing = validateExecutionRecord(db.prepare(
      'SELECT * FROM main.turn_tool_executions WHERE execution_key=?',
    ).get(executionKey));
  } catch { return false; }
  let binding = null;
  try { binding = JSON.parse(String(existing?.semantic_binding || 'null')); } catch { return false; }
  return Boolean(existing && existing.tool === name
    && ['started', 'done'].includes(existing.status)
    && binding?.capabilityId === profile.plan.capabilityId
    && binding?.operationId === profile.plan.operationId);
}

/** @param {any} options */
async function executeSelfAttentionProviderPlan({
  profile, name, args, governedContext, ledgerBinding, turnId,
  assertCurrent, cognitiveEpisodeId,
}) {
  if (args !== profile.plan.args) {
    throw Object.assign(new Error('De uitgegeven researchargumenten zijn na planning gewijzigd.'), {
      code: 'EVA_SELF_ATTENTION_PROVIDER_ARGS_DRIFT',
    });
  }
  assertSelfAttentionProviderCurrent(profile, assertCurrent);
  const executionKey = profile.executionKey;
  let claimed;
  try {
    claimed = claimTurnToolExecution({
      executionKey, turnId, tool: name, args, semanticBinding: ledgerBinding,
    });
  } catch (error) {
    if (!selfAttentionProviderClaimCollision(executionKey, name, profile)) throw error;
    governedContext?.markProviderReplaySettlement?.(profile);
    return rejectedToolOutput(name,
      `De providerpoging voor exploration #${profile.exploration.id} revisie ${profile.exploration.revision} is al duurzaam geclaimd; ik open geen tweede request.`,
      'conflict');
  }
  if (!claimed.claimed) {
    if (claimed.row?.status === 'done' && claimed.row.output) return claimed.row.output;
    if (claimed.row?.status === 'started') {
      governedContext?.markProviderReplaySettlement?.(profile);
    }
    return rejectedToolOutput(name,
      'De vorige procesbeurt stopte tijdens deze handeling. Ik voer haar niet blind dubbel uit; de actuele toestand moet eerst worden gecontroleerd.',
      'conflict');
  }
  assertSelfAttentionProviderCurrent(profile, assertCurrent);
  try {
    // De ledgerclaim kan op een SQLite-writer hebben gewacht. Bereken het
    // resterende wirebudget daarom pas hier, direct vóór de provider-I/O.
    governedContext.timeoutMs = selfAttentionProviderTimeout(profile);
  } catch (error) {
    completeTurnToolExecution(executionKey, selfAttentionProviderAuditOutput(name, {
      failureClass: 'timeout',
      error: 'Het begrensde researchvenster verliep vóór de providerrequest begon.',
    }));
    throw error;
  }
  governedContext?.markExecutionStarted?.();
  const output = redactSensitiveText(await executeTool(name, args, governedContext));
  try {
    assertSelfAttentionProviderCurrent(profile, assertCurrent);
  } catch (error) {
    // Bewaar alleen dát het ene request terugkeerde. De stale inhoud zelf
    // wordt geen verified receipt en bereikt timeline/exploration nooit.
    completeTurnToolExecution(executionKey, selfAttentionProviderAuditOutput(name, {
      providerRequestCompleted: true,
      error: 'De provideruitkomst kwam terug nadat deze aandachtbeurt of exploration-revisie was ingehaald.',
    }));
    throw error;
  }
  const observed = inspectToolOutcome(name, args, output);
  try {
    db.transaction(() => {
      const note = noteIssuedExplorationAction(profile.exploration.id, profile.exploration, {
        tool: name,
        ok: observed.recorded.ok,
        verified: observed.recorded.verified,
        evidence: observed.parsed?.evidence?.summary || observed.parsed?.human_message || null,
        episodeId: cognitiveEpisodeId,
      });
      if (!note.accepted) {
        throw Object.assign(new Error(
          'De exploration-revisie wijzigde vóór de providerreceipt atomair kon worden gekoppeld.',
        ), { code: 'EVA_SELF_ATTENTION_EXPLORATION_STALE' });
      }
      completeTurnToolExecution(executionKey, output);
    }).immediate();
  } catch (error) {
    completeTurnToolExecution(executionKey, selfAttentionProviderAuditOutput(name, {
      providerRequestCompleted: true,
      error: 'De provideruitkomst kon niet atomair aan de uitgegeven exploration-revisie worden gekoppeld.',
    }));
    throw error;
  }
  SETTLED_SELF_ATTENTION_PROVIDER_PLANS.add(profile.plan);
  return output;
}

function selfAttentionProviderProfile(providerPlan, exploration, deadlineTs) {
  const key = `${providerPlan?.capabilityId}\u0000${providerPlan?.operationId}`;
  const exact = key === 'internet.research\u0000search'
    && providerPlan?.effect === 'read'
    && providerPlan.execution?.mode === 'direct'
    && providerPlan.supportsDeferred === false
    && Object.isFrozen(providerPlan) && Object.isFrozen(providerPlan.args)
    && Object.keys(providerPlan.args).length === 1
    && typeof providerPlan.args.vraag === 'string'
    && Boolean(providerPlan.args.vraag.trim())
    && !hasUnresolvedReference(providerPlan.args)
    && Number.isSafeInteger(exploration?.id) && exploration.id >= 1
    && Number.isSafeInteger(exploration?.revision) && exploration.revision >= 1
    && Number.isSafeInteger(exploration?.actionCount) && exploration.actionCount >= 0
    && Number.isSafeInteger(exploration?.maxActions)
    && exploration.actionCount < exploration.maxActions
    && typeof exploration?.status === 'string' && Boolean(exploration.status)
    && typeof exploration?.question === 'string' && Boolean(exploration.question.trim())
    && Number.isSafeInteger(deadlineTs) && deadlineTs > Date.now();
  if (!exact) return null;
  const profile = Object.freeze({
    contract: SELF_ATTENTION_PROVIDER_EXECUTION_CONTRACT,
    ok: true, kind: 'provider_direct', plan: providerPlan, deadlineTs,
    executionKey: createHash('sha256').update(canonicalJson({
      contract: SELF_ATTENTION_PROVIDER_EXECUTION_CONTRACT,
      explorationId: exploration.id,
      explorationRevision: exploration.revision,
      capabilityId: providerPlan.capabilityId,
      operationId: providerPlan.operationId,
    })).digest('hex'),
    exploration: Object.freeze({
      id: exploration.id, revision: exploration.revision,
      status: exploration.status, actionCount: exploration.actionCount,
      maxActions: exploration.maxActions, question: exploration.question,
    }),
  });
  return selfAttentionProviderExplorationMatches(profile) ? profile : null;
}

function selfAttentionExecutionProfile(semanticTurn, messageContext, deadlineTs) {
  const plans = semanticTurn.plans || [];
  const key = (plan) => `${plan.capabilityId}\u0000${plan.operationId}`;
  const unsupportedDirect = plans.filter((plan) => plan.execution?.mode === 'direct'
    && SELF_ATTENTION_PROVIDER_BACKED_DIRECT.has(key(plan)));
  const iterative = plans.filter((plan) => plan.execution?.mode === 'iterative');
  const providerPlan = unsupportedDirect.length === 1 ? unsupportedDirect[0] : null;
  const exploration = issuedExplorationContinuationContext(messageContext?.attention);
  const exactExplorationResearch = providerPlan
    && plans.length === 1 && iterative.length === 0
    && messageContext?.attention?.kind === 'exploration'
    && exploration?.id === Number(messageContext.attention.id)
    && selfAttentionProviderProfile(providerPlan, exploration, deadlineTs);
  if (exactExplorationResearch) {
    return selfAttentionProviderProfile(providerPlan, exploration, deadlineTs);
  }
  const anchor = semanticTurn.aspirationExplorationAnchor;
  const exactAspirationStartResearch = providerPlan
    && plans.length === 2 && iterative.length === 0
    && anchor?.startPlan === plans[0] && anchor.nextPlan === providerPlan
    && anchor.aspirationId === Number(messageContext?.attention?.id)
    && messageContext?.attention?.kind === 'aspiration'
    && key(providerPlan) === 'internet.research\u0000search'
    && providerPlan.effect === 'read'
    && providerPlan.execution?.mode === 'direct'
    && providerPlan.supportsDeferred === false
    && Object.isFrozen(providerPlan) && Object.isFrozen(providerPlan.args)
    && Object.keys(providerPlan.args).length === 1
    && typeof providerPlan.args.vraag === 'string'
    && Boolean(providerPlan.args.vraag.trim())
    && !hasUnresolvedReference(providerPlan.args)
    && Number.isSafeInteger(deadlineTs) && deadlineTs > Date.now();
  if (exactAspirationStartResearch) {
    return Object.freeze({
      contract: SELF_ATTENTION_PROVIDER_EXECUTION_CONTRACT,
      ok: true, kind: 'provider_after_aspiration_start',
      plan: providerPlan, startPlan: anchor.startPlan,
      aspirationId: anchor.aspirationId, deadlineTs,
    });
  }
  if (unsupportedDirect.length || iterative.length > 1) return Object.freeze({
    ok: false,
    error: `self_attention_provider_budget:${[
      ...unsupportedDirect.map(key), ...iterative.slice(1).map(key),
    ].sort().join(',') || 'unsupported_plan'}`,
  });
  if (!iterative.length) return Object.freeze({ ok: true, kind: 'direct' });
  const write = iterative[0];
  if (key(write) !== 'workspace.file.write\u0000write'
    || executableWorkspaceArtifact(write.args?.pad)) return Object.freeze({
    ok: false,
    error: `self_attention_provider_budget:${key(write)}:unsupported_iterative_target`,
  });
  return Object.freeze({ ok: true, kind: 'artifact', write });
}

function inspectToolOutcome(name, args, output) {
  let parsed = null;
  try { parsed = JSON.parse(String(output)); } catch { /* gewone tekst is ook geldig */ }
  return {
    parsed,
    recorded: {
      name,
      args,
      output,
      ok: parsed?.ok !== false && !/^Fout bij /i.test(String(output)),
      verified: parsed?.verified === true,
      changed: parsed?.changed === true,
      needs_followup: parsed?.needs_followup === true,
      partial: parsed?.partial === true,
      failure_class: parsed?.failure_class || null,
      retryable: typeof parsed?.retryable === 'boolean' ? parsed.retryable : null,
      effect_uncertain: typeof parsed?.effect_uncertain === 'boolean'
        ? parsed.effect_uncertain : null,
      evidence: parsed?.evidence || null,
      at: new Date().toISOString(),
    },
  };
}

function safeReadRouteFailure(plan, recorded) {
  return ['read', 'discovery'].includes(plan?.effect)
    && !String(plan?.capabilityId || '').startsWith('runtime.')
    && !verifiedCompleteReceipt(recorded)
    && recorded?.changed !== true
    && recorded?.effect_uncertain !== true
    && !['permission', 'policy'].includes(String(recorded?.failure_class || ''));
}
const plannedResultComplete = ({
  plan, recorded, completed, adaptiveRecorded, investigationRequired, investigationComplete,
}) => (((completed && (verifiedCompleteReceipt(recorded)
    || boundedVerifiedHomePartialEffect(recorded, plan?.args)))
  || verifiedCompleteReceipt(adaptiveRecorded)
  || boundedVerifiedHomePartialEffect(adaptiveRecorded, plan?.args))
  && (investigationRequired !== true || investigationComplete === true));
function safeReadFailuresCanYieldToIssuedWorkspace(understanding, pending, nextPlan) {
  const failedRequirements = pending.map(({ plan }) => plan?.requirementId);
  return pending.length > 0 && !pending.some(({ deferredBinding }) => deferredBinding)
    && nextPlan?.effect === 'workspace' && nextPlan.execution?.mode === 'iterative'
    && nextPlan.execution?.target?.boundary === 'workspace' && !hasUnresolvedReference(nextPlan.args)
    && !(understanding?.conditions || []).some((condition) => (
    ['required', 'unmet'].includes(condition.state) && condition.role === 'action'
    && condition.requirementIds.includes(nextPlan.requirementId)
    && condition.afterRequirementIds.some((id) => failedRequirements.includes(id))
  ));
}
const ASPIRATION_PARTIAL_READ_PERSISTENCE = new Set([
  'exploration.manage\u0000start',
  'work.episode.manage\u0000start',
]);
function actionRequirementPath(understanding, fromRequirementId, toRequirementId) {
  const graph = new Map();
  for (const condition of understanding?.conditions || []) {
    if (condition?.role !== 'action') continue;
    const requirements = Array.isArray(condition.requirementIds)
      ? condition.requirementIds : [];
    const predecessors = Array.isArray(condition.afterRequirementIds)
      ? condition.afterRequirementIds : [];
    for (const predecessor of predecessors) {
      const successors = graph.get(predecessor) || new Set();
      for (const requirement of requirements) successors.add(requirement);
      graph.set(predecessor, successors);
    }
  }
  const visited = new Set([fromRequirementId]);
  const queue = [...(graph.get(fromRequirementId) || [])];
  while (queue.length) {
    const requirementId = queue.shift();
    if (requirementId === toRequirementId) return true;
    if (visited.has(requirementId)) continue;
    visited.add(requirementId);
    queue.push(...(graph.get(requirementId) || []));
  }
  return false;
}
function issuedAspirationPersistencePlanIndex(nextPlan, issuedPlans, selected) {
  if (!Array.isArray(issuedPlans) || !issuedPlans.includes(nextPlan)) return -1;
  const key = `${nextPlan?.capabilityId || ''}\u0000${nextPlan?.operationId || ''}`;
  if (!ASPIRATION_PARTIAL_READ_PERSISTENCE.has(key)
    || nextPlan?.effect !== 'persistent' || nextPlan?.execution?.mode !== 'direct'
    || nextPlan?.idempotency !== 'cas' || hasUnresolvedReference(nextPlan?.args)
    || !selected.has(nextPlan?.requirementId)) return -1;
  return issuedPlans.indexOf(nextPlan);
}
function pendingReadsPrecedeIndependentPersistence({
  understanding, pending, nextPlan, issuedPlans, selected, nextIndex,
}) {
  let previousIndex = -1;
  for (const entry of pending) {
    const issuedIndex = issuedPlans.indexOf(entry?.plan);
    if (issuedIndex < 0 || issuedIndex >= nextIndex || issuedIndex <= previousIndex
      || !selected.has(entry?.plan?.requirementId)
      || entry?.deferredBinding || hasUnresolvedReference(entry?.plan?.args)
      || !safeReadRouteFailure(entry?.plan, entry?.recorded)
      || actionRequirementPath(
        understanding, entry?.plan?.requirementId, nextPlan?.requirementId,
      )) return false;
    previousIndex = issuedIndex;
  }
  return true;
}
function safeReadFailuresCanPreserveIssuedAspirationPersistence({
  understanding, pending, nextPlan, issuedPlans,
  boundedSelfAttention = false, attentionKind = null,
} = {}) {
  if (!boundedSelfAttention || attentionKind !== 'aspiration'
    || !Array.isArray(pending) || pending.length === 0) return false;
  const selected = new Set(understanding?.firstDecision?.requirementIds || []);
  const nextIndex = issuedAspirationPersistencePlanIndex(nextPlan, issuedPlans, selected);
  return nextIndex >= 0 && pendingReadsPrecedeIndependentPersistence({
    understanding, pending, nextPlan, issuedPlans, selected, nextIndex,
  });
}
function turnStepCandidates(value, output = [], depth = 0) {
  if (depth > 5 || output.length >= 40 || value == null) return output;
  if (Array.isArray(value)) {
    for (const item of value) turnStepCandidates(item, output, depth + 1);
    return output;
  }
  if (typeof value !== 'object') return output;
  const add = (kind, candidate) => {
    const text = String(candidate || '').trim();
    if (!text || output.some((item) => item.kind === kind && item.value === text)) return;
    output.push({ kind, value: text });
  };
  if (typeof value.path === 'string') add('path', value.path);
  if (typeof value.pad === 'string') add('path', value.pad);
  if (typeof value.entity_id === 'string') add('entity', value.entity_id);
  if (typeof value.entityId === 'string') add('entity', value.entityId);
  if (typeof value.target === 'string') add('target', value.target);
  if (typeof value.name === 'string' && ['file', 'directory'].includes(String(value.type || ''))) {
    add('path', value.name);
  }
  for (const child of Object.values(value)) turnStepCandidates(child, output, depth + 1);
  return output;
}

function turnStepEvidence(toolResults, understandingSha256) {
  return toolResults.filter((item) => (
    item.semanticBinding?.understandingSha256 === understandingSha256
  )).slice(-10).map((item) => {
    let typed = null;
    try { typed = JSON.parse(String(item.output || '')); } catch { /* geen typed candidates */ }
    return {
    id: `s${toolResults.indexOf(item) + 1}`,
    requirementId: item.semanticBinding?.requirementId || '',
    capabilityId: item.semanticBinding?.capabilityId || '',
    operationId: item.semanticBinding?.operationId || '',
    ok: item.ok === true,
    verified: item.verified === true,
    failureClass: item.failure_class || '', partial: item.partial === true,
    needsFollowup: item.needs_followup === true, effectUncertain: item.effect_uncertain === true,
    // Een geverifieerde read verifieert niet alleen haar payload, maar ook het
    // werkelijk uitgevoerde, door de uitgegeven planbinding begrensde doel.
    // Ook een geverifieerde partiële read kan echte entity-/padkandidaten
    // opleveren; partial betekent alleen dat de vraag nog niet af is. Een
    // mislukt, onzeker of niet aan deze understanding gebonden argument kan
    // via deze route nooit eenzelfde-operation rebind dragen.
    candidates: item.ok === true && item.verified === true
      && item.effect_uncertain !== true
      ? turnStepCandidates({ result: typed?.data, executed: item.args }) : [],
    result: String(item.output || '').slice(0, 4_000),
    };
  });
}

function partialTurnProgress(toolResults) {
  const facts = toolResults.filter(verifiedCompleteReceipt).slice(-6).flatMap((tool) => {
    // Een onderzoeksnotitie is bronmateriaal voor Eva, geen tussenantwoord
    // voor het gezin. Als de beurt daarna niet afgemaakt kan worden, mag die
    // nooit rauw of dubbel uit een foutpad ontsnappen.
    if (tool.name === 'zoek_internet') return [];
    let typed = null;
    try { typed = JSON.parse(String(tool.output || '')); } catch { /* gewone uitvoer */ }
    const references = turnStepCandidates(typed?.data).slice(0, 2).map((item) => item.value);
    if (references.length) return [`Gevonden of bevestigd: ${references.join(', ')}.`];
    const fact = checkpointEvidence(tool)
      || humanResultText(tool.evidence?.summary || tool.output || '', {
        limit: 500, compact: true, preferDetailed: true,
      });
    return fact ? [fact] : [];
  });
  return [...new Set(facts)].slice(-3).join(' ');
}

function naturalReadInvestigationBlocker(entry, detail = '') {
  const reason = String(entry?.investigationFailure || 'blocked');
  const blocker = ({
    deadline: 'De benodigde informatiebron leverde de vervolggegevens niet op tijd; daardoor kan ik dit nu niet betrouwbaar vaststellen.',
    no_progress: 'De volgende beschikbare leesstap zou dezelfde bron opnieuw proberen en geen nieuwe informatie opleveren; daardoor kan ik dit nu niet betrouwbaar vaststellen.',
    round_limit: 'De beschikbare informatiebronnen gaven na meerdere leesstappen nog niet genoeg om dit betrouwbaar vast te stellen.',
    user_fact_required: 'Ik mis nog één concreet gegeven van jou om de juiste informatiebron te kunnen kiezen.',
    external_block: 'De benodigde informatiebron is nu niet bereikbaar, waardoor ik dit nog niet betrouwbaar kan vaststellen.',
    effect_uncertain: 'De laatste uitlezing had geen betrouwbare eindstatus, waardoor ik dit nu niet zeker kan vaststellen.',
    invalid_step: 'Ik vond geen andere alleen-lezen bron die het ontbrekende gegeven kan leveren.',
    blocked: 'De beschikbare informatiebronnen leveren het ontbrekende gegeven nu niet.',
  })[reason] || 'De beschikbare informatiebronnen leveren het ontbrekende gegeven nu niet.';
  const naturalDetail = String(detail || '').replace(/\s+/gu, ' ').trim().slice(0, 320);
  const researchNarrative = /\b(?:onderzoeksnotitie|gevonden feiten|koersdrivers|directe bronnen)\b/iu
    .test(naturalDetail);
  const usableDetail = naturalDetail
    && !researchNarrative
    && !looksLikeInternalOutput(naturalDetail)
    && !/[{}]|\b(?:actionresult|capability|deadline|no_progress|receipt|requirement|round_limit|tool)\b/iu
      .test(naturalDetail)
    && !/^Ik kon de relevante gegevens niet volledig uitlezen/iu.test(naturalDetail)
    ? naturalDetail : '';
  return [blocker, usableDetail ? `De laatste bron gaf daarbij terug: ${usableDetail}` : '']
    .filter(Boolean).join(' ');
}

async function composeEvaReply({
  person, channel, conversationId, userText, understanding, toolResults, fallback,
  turnContract, turnPlan, requiredTools, buildWork, buildNeedsExecution,
  monitoringProofOptions, syntheticTest, messageContext = null, executionComplete = null,
  semanticFailure = null, behaviorLensContext = '', onAcceptedReply = null,
  assertCurrent = null,
}) {
  const grounded = normalizeUserFacingText(fallback).trim();
  const deadline = Date.now()
    + (channel === 'voice' ? VOICE_REPLY_COMPOSER_TIMEOUT_MS : 30_000);
  try {
    const episodic = trustedOlderConversationRows({
      person, conversationId, query: userText, messageContext, mode: 'chat', limit: 2,
    });
    const crossChannel = trustedCrossChannelConversationRows({
      person, channel, conversationId, messageContext, mode: 'chat',
      limit: episodic.length ? 1 : 2,
    });
    const episodicIds = new Set(episodic.map(conversationRowKey));
    const dialogueRows = [...new Map([
      ...episodic,
      ...crossChannel,
      ...channelConversationRows({
        channel, conversationId, sender: person, messageContext, limit: 4,
      }),
    ].map((row) => [conversationRowKey(row), row])).values()];
    if (channel !== 'telegram') {
      dialogueRows.sort((a, b) => Number(a.id || 0) - Number(b.id || 0));
    }
    const dialogue = dialogueRows.map((item) => ({
      role: item.role === 'assistant' ? 'Eva' : item.sender || person,
      evidenceClass: item.conversation_id === conversationId
        ? 'conversation' : 'historical_unconfirmed',
      text: `${item.role === 'assistant'
        ? '[eerder antwoord van Eva; gesprekscontext, geen zelfstandig bewijs] '
        : ''}${episodicIds.has(conversationRowKey(item))
        ? `[eerdere relevante ${item.channel}-gespreksherinnering; niet actueel] `
        : item.conversation_id === conversationId
          ? '' : `[recente ${item.channel}-gespreksherinnering; niet actueel] `}${String(item.content || '').slice(0, 1_200)}`,
    }));
    const selectedActions = (understanding?.actions || []).filter((action) => (
      understanding?.firstDecision?.requirementIds?.includes(action.requirementId)
    ));
    const evidence = toolResults.slice(-12).map((item, index) => ({
      step: index + 1,
      capability: item.semanticBinding?.capabilityId
        || selectedActions.find((action) => (
          action.requirementId === item.semanticBinding?.requirementId
        ))?.capabilityId
        || null,
      operation: item.semanticBinding?.operationId || null,
      role: item.semanticBinding?.role || null,
      ok: item.ok === true,
      verified: item.verified === true,
      changed: item.changed === true,
      partial: item.partial === true,
      needsFollowup: item.needs_followup === true,
      evidence: String(item.evidence?.summary || '').slice(0, 1_200) || null,
      result: humanResultText(item.output || '').slice(0, 2_400) || null,
    }));
    const selfContext = selfContinuityContextText({ mode: 'chat', query: userText });
    const instructions = buildRedactedInstructions({
      sender: person,
      channel,
      mode: 'chat',
      familyContext: householdContextText({
        subject: person,
        query: userText,
        feedbackLimit: 6,
      }),
      extra: [
        selfContext,
        moodActionGuidance(),
        behaviorLensContext,
        semanticFailure
          ? 'Dit is alleen een korte antwoordfase nadat Eva de opdracht deze keer niet betrouwbaar genoeg kon verwerken. Roep niets aan, voer niets uit en verzin geen begrip. Het oorspronkelijke bericht is bewaard en er is niets uitgevoerd of gewijzigd. Vraag niet om herhalen, herformuleren of inkorten en stel nu geen andere vraag. Beloof geen latere uitvoering of terugmelding. Laat interne componentnamen en systeemtaal weg.'
          : 'Dit is alleen de antwoordfase na het begrijpen en eventuele handelen. Roep niets aan en bedenk geen nieuwe uitvoering. Antwoord vanuit het bewijs; een mislukte of onzekere stap blijft mislukt of onzeker. Eerdere antwoorden van Eva zijn alleen context, nooit zelfstandig bewijs. Als zo’n antwoord afwijkt van het huidige bewijs, los je dat verschil rechtstreeks op in plaats van een nieuw los oordeel ernaast te zetten. Laat interne componentnamen, contracten en werkverslagtaal weg.',
      ].filter(Boolean).join('\n'),
    });
    const baseInput = [
      `Wat ${person} vroeg: ${userText.slice(0, 2_000)}`,
      dialogue.length ? `Lopende gespreksdraad: ${JSON.stringify(dialogue)}` : '',
      semanticFailure
        ? `Vaststaande feiten: ${semanticFailure}. Het oorspronkelijke bericht staat nog volledig in de gespreksdraad. Er is niets uitgevoerd of gewijzigd.`
        : `Begrepen uitkomst: ${String(understanding?.goal?.outcome || understanding?.goal?.statement || '').slice(0, 1_200)}`,
      `Gecontroleerde uitkomsten: ${JSON.stringify(evidence)}`,
      grounded ? `Veilige korte terugvaltekst: ${grounded.slice(0, 1_200)}` : '',
      channel === 'voice'
        ? 'Geef nu alleen Eva haar uiteindelijke gesproken antwoord. Eén of twee korte zinnen.'
        : 'Geef nu alleen Eva haar uiteindelijke appantwoord. Meestal één tot drie zinnen.',
    ].filter(Boolean).join('\n\n');
    let correction = '';
    for (let attempt = 0; attempt < 2; attempt += 1) {
      const remainingMs = deadline - Date.now();
      if (remainingMs <= 0) return grounded;
      if (typeof assertCurrent === 'function') assertCurrent();
      const result = await chatComplete({
        instructions,
        input: [msg('user', [baseInput, correction].filter(Boolean).join('\n\n'))],
        tools: [],
        model: config.modelVoice,
        effort: 'low',
        cacheKey: attempt ? 'eva-grounded-reply-repair' : 'eva-grounded-reply',
        timeoutMs: remainingMs,
        rateLimitRetries: 0,
        transientRetries: 0,
        authenticationRetries: 0,
      });
      if (typeof assertCurrent === 'function') assertCurrent();
      const proposed = normalizeUserFacingText(result.text).trim();
      if (!proposed) return grounded;
      const feedback = semanticFailure
        ? semanticFailureReplyFeedback(proposed, { internal: looksLikeInternalOutput(proposed) })
        : finalReplyFeedback({
          reply: proposed,
          mode: 'chat',
          syntheticTest,
          toolResults,
          requiredTools,
          userText,
          turnContract,
          turnPlan,
          buildWork,
          buildNeedsExecution,
          executionComplete,
          monitoringProofOptions,
        });
      if (!feedback) {
        if (typeof onAcceptedReply === 'function') onAcceptedReply(proposed);
        return proposed;
      }
      correction = `Afgekeurde antwoordpoging: ${JSON.stringify(proposed.slice(0, 1_200))}\nAfkeurreden: ${feedback.slice(0, 1_000)}\nHerschrijf hem nu vanuit dezelfde Eva, met exact hetzelfde bewijs en zonder nieuwe handeling of claim.`;
    }
    return grounded;
  } catch (error) {
    if (error?.code === 'EVA_TURN_SUPERSEDED') throw error;
    return grounded;
  }
}

function completionClaimFeedback({
  reply = '', mode = 'chat', toolResults = [], requiredTools = [], userText = '',
  buildWork = false, buildNeedsExecution = true, executionComplete = null,
  monitoringProofOptions = {},
} = {}) {
  if (mode !== 'chat' || !claimsCompletedAction(reply)) return '';
  const buildIncomplete = buildWork && (executionComplete === false
    || (executionComplete == null && (!buildMissionComplete(toolResults, buildNeedsExecution)
      || !phasedProgrammingBuildStatus(toolResults, userText).complete)));
  if (buildIncomplete) {
    if (explicitlyIncompleteAction(reply)) return '';
    return 'Het gevraagde bouwwerk is nog niet volledig geschreven, uitgevoerd en teruggelezen. Claim geen voltooiing; beschrijf in je eigen stem alleen de aantoonbare tussenstand.';
  }
  if (buildWork && executionComplete !== true
    && !programmingAcceptanceStatus(toolResults, userText).complete) {
    return 'De gevraagde acceptatieproef ontbreekt nog. Claim niet dat het werk klaar of voldoende getest is; beschrijf in je eigen stem alleen wat wel gecontroleerd is.';
  }
  if (!requiredTools.length) {
    const verifiedMutation = toolResults.some((tool) => verifiedCompleteReceipt(tool)
      && toolEffect(tool.name, tool.args) !== 'read');
    return !verifiedMutation && !explicitlyIncompleteAction(reply)
      ? 'Je antwoord noemt een gevraagde handeling voltooid, maar deze beurt bevat geen geverifieerde mutatie. Claim geen uitvoering; zeg alleen eerlijk dat de handeling nog niet aantoonbaar is uitgevoerd.'
      : '';
  }
  const missing = requiredTools.filter(
    (required) => !toolResults.some((tool) => tool.name === required),
  );
  if (missing.length) {
    return 'Je conclusie schrijft een handeling aan jezelf toe die niet is uitgevoerd. Claim geen voltooiing en zeg in je eigen gewone stem wat er werkelijk is gebeurd.';
  }
  const unverified = requiredTools.filter((required) => !turnRequirementSatisfied(
    required, toolResults, userText, monitoringProofOptions,
  ));
  return unverified.length
    ? 'Je conclusie noemt de opdracht voltooid, maar het vereiste resultaat is niet bevestigd. Claim geen succes en beschrijf in je eigen gewone stem alleen het gecontroleerde bewijs.'
    : '';
}

/** @param {any} [options] */
function finalReplyFeedback({
  reply = '', mode = 'chat', syntheticTest = false, toolResults = [], requiredTools = [],
  userText = '', turnContract = {}, turnPlan = {}, buildWork = false,
  buildNeedsExecution = true, executionComplete = null, monitoringProofOptions = {},
} = {}) {
  if (syntheticTest && syntheticReplyNeedsPersistentState(reply)) {
    return 'Interne conclusiecontrole, geen uitspraak van de bewoner: deze geïsoleerde proef heeft geen duurzaam hervat- of meldingsrecht. Claim geen toekomstige handeling of vastgelegd hervatmoment; geef direct alleen wat in deze beurt werkelijk is aangetoond, zonder erkenning, excuus of verbeterplan.';
  }
  if (/^\s*\{|^\s*\[\s*(?:\{|"|-?\d|true|false|null|\])/i.test(String(reply || ''))
    || looksLikeInternalOutput(reply, {
      allowProjectPaths: localProjectReadRequest(userText)
        || ['programming_build', 'programming_verification', 'repair'].includes(turnContract.kind),
    })) {
    return 'Dit is ruwe uitvoer of interne werktaal. Schrijf vanuit hetzelfde bewijs opnieuw één gewoon antwoord in Eva haar eigen stem, zonder interne paden, velden of gereedschapstaal.';
  }
  return emptyHomeHistoryReplyFeedback(reply, toolResults)
    || styleCorrectionPolicyFeedback(reply, userText)
    || conclusionInvestigationFeedback(reply, userText, toolResults, turnPlan.actionIntent)
    || missingSourceLinkFeedback(reply, requiredTools, toolResults)
    || monitoringFeedbackForRequest(
      reply, ['monitoring', 'continuity'].includes(turnContract.kind), toolResults, monitoringProofOptions,
    )
    || homeEffectAttributionFeedback(reply, userText, toolResults, {
      directAction: turnPlan.directAction,
    })
    || selfRepairClaimFeedback(reply, turnContract.kind, toolResults)
    || voiceAudibilityClaimFeedback(reply, toolResults)
    || completionClaimFeedback({
      reply, mode, toolResults, requiredTools, userText, buildWork,
      buildNeedsExecution, executionComplete, monitoringProofOptions,
    })
    || incompleteRequestedOutcomeFeedback(reply, userText);
}

function persistUserHandoff({
  understanding, reinterpret, failureClass, continuationRequest, continuationWork,
  channel, conversationId, inboundMessageId, person, userText, rawUserText = userText, turnId,
  messageContext, isCurrent, assertCurrent,
} = /** @type {any} */ ({})) {
  assertCurrent();
  if (reinterpret) {
    let persisted = createInterpretationRetryHandoff({
      channel, conversationId, messageId: inboundMessageId, requester: person,
      originalText: rawUserText, failureClass, sessionRef: turnId,
      supersessionScope: ['telegram', 'voice', 'web'].includes(channel)
        ? messageContext?.ingressScope : '',
      supersessionToken: ['telegram', 'voice', 'web'].includes(channel)
        ? messageContext?.ingressToken : '',
    });
    if (!isCurrent()) {
      persisted = abandonInterpretationRetry(
        persisted.workItem, persisted.episode,
        'de opdracht is door een nieuwer bericht ingehaald', turnId,
      );
      assertCurrent();
    }
    return persisted;
  }
  if (!verifyTurnUnderstanding(understanding)) {
    throw new Error('duurzaam vervolg vereist een geldige beurtinterpretatie');
  }
  if (continuationRequest && continuationWork) {
    const episode = episodeForWork(continuationWork.id)
      || ensureEpisodeForWorkItem(continuationWork, {
        originKind: 'user', originChannel: channel,
        originConversationId: conversationId, originMessageId: inboundMessageId,
        requester: person, deliveryPolicy: 'required', deliveryTarget: person,
      });
    assertCurrent();
    attachEpisodeOrigin(episode.id, {
      originKind: 'user', originChannel: channel,
      originConversationId: conversationId, originMessageId: inboundMessageId, requester: person,
    });
    return { workItem: continuationWork, episode };
  }
  const statement = String(understanding?.goal?.statement || '').trim();
  const outcome = String(understanding?.goal?.outcome || '').trim();
  const goal = !outcome || outcome === userText
    ? userText : `${outcome}\n\nOorspronkelijke opdracht: ${userText}`;
  assertCurrent();
  return createDurableWork({
    source: channel, owner: person,
    title: `Opdracht: ${(statement || outcome || userText).slice(0, 210)}`,
    goal,
    nextStep: 'Hervat vanuit de oorspronkelijke opdracht, voer de eerste nog onbewezen stap uit en controleer het resultaat voordat je het terugkoppelt.',
    visibleToFamily: true, definitionOfDone: outcome || userText, priority: 75,
  }, {
    originKind: 'user', originChannel: channel,
    originConversationId: conversationId, originMessageId: inboundMessageId,
    requester: person, deliveryPolicy: 'required', deliveryTarget: person,
  });
}

async function finishSemanticFailureTurn(options) {
  const {
    semanticTurn, channel, mode, syntheticTest, persistDurableHandoff, finishTurn,
    person, conversationId, userText, toolResults, turnContract, turnPlan,
    requiredTools, messageContext, assertTurnCurrent,
    resumedInterpretationRetry, handleInterpretationRetryFailure,
  } = options;
  const failureMetadata = semanticFailureResultMetadata(semanticTurn);
  if (mode === 'autonomy' && resumedInterpretationRetry) {
    const outcome = handleInterpretationRetryFailure(semanticTurn);
    return finishTurn('', {
      error: semanticTurn.error,
      durableHandoff: outcome?.kind === 'retry',
      executionPending: ['retry', 'delivery'].includes(outcome?.kind),
      superseded: outcome?.kind === 'superseded',
      yielded: outcome?.kind === 'superseded',
      ...failureMetadata,
    });
  }
  const validation = String(semanticTurn.result?.failure?.validation || '');
  const temporaryTransportFailure = ['voice', 'telegram'].includes(channel)
    && ['interpreter_timeout', 'interpreter_unavailable'].includes(semanticTurn.error);
  const decisionRetrySummary = semanticTurn.result?.failure?.decisionRetrySummary;
  const repairableDecisionFailure = ['voice', 'telegram', 'web'].includes(channel)
    && semanticTurn.error === 'interpreter_malformed'
    && validation === 'selected_denied_action'
    && decisionRetrySummary?.contract === 'TurnDecisionRetrySummary/v1'
    && Number(decisionRetrySummary.selectCount) >= 1
    && Number(decisionRetrySummary.excludeCount) >= 1;
  const durableHandoff = mode === 'chat' && !syntheticTest
    && messageContext?.identityTrusted === true
    && (temporaryTransportFailure || repairableDecisionFailure);
  if (durableHandoff) {
    persistDurableHandoff({
      reinterpret: true,
      failureClass: semanticTurn.result?.failure?.failureClass,
    });
  }
  let reply = '';
  if (mode !== 'autonomy') {
    const presentation = semanticFailurePresentation(semanticTurn.error);
    assertTurnCurrent();
    reply = await composeEvaReply({
      person, channel, conversationId, userText, understanding: semanticTurn.understanding,
      toolResults,
      fallback: durableHandoff
        ? 'Ik heb je hele opdracht bewaard. Er is nog niets uitgevoerd of gewijzigd.'
        : presentation.reply,
      buildWork: false, buildNeedsExecution: false, monitoringProofOptions: {}, syntheticTest,
      messageContext, executionComplete: false,
      semanticFailure: durableHandoff
        ? `${presentation.grounding}; de volledige opdracht is voor een nieuwe poging bewaard`
        : presentation.grounding,
      turnContract, turnPlan, requiredTools,
      assertCurrent: assertTurnCurrent,
    });
    assertTurnCurrent();
  }
  return finishTurn(reply, {
    error: semanticTurn.error,
    durableHandoff: durableHandoff || undefined,
    ...failureMetadata,
  });
}

function resumedTurnPreview({
  mode, sender, text, messageContext, providedConversationId, channel,
} = /** @type {any} */ ({})) {
  const autonomyResume = mode === 'autonomy'
    ? boundAutonomyResumeContext(messageContext) : null;
  const interpretationRetry = autonomyResume?.interpretationRetry || null;
  const fallbackPerson = String(sender || 'huisgenoot').trim() || 'huisgenoot';
  const person = interpretationRetry?.valid
    ? interpretationRetry.origin.requester || fallbackPerson : fallbackPerson;
  const fallbackConversation = providedConversationId
    || conversationIdFor({ channel, sender: person, messageContext });
  const rawText = interpretationRetry?.valid
    ? interpretationRetry.originalText
    : String(text ?? '');
  return {
    autonomyResume,
    interpretationRetry,
    person,
    rawText,
    text: interpretationRetry?.valid
      ? interpretationRetry.originalText
      : rawText.trim(),
    conversationId: interpretationRetry?.valid
      ? interpretationRetry.origin.conversationId || fallbackConversation
      : fallbackConversation,
  };
}

// Alleen deze module bezit de writer voor de gereserveerde understanding-ledger.
// De capability zelf is leeg en onkopieerbaar; alle identiteit blijft in deze
// WeakMap en wordt bij iedere mutatie opnieuw tegen ledger + sidecar gelezen.
const TURN_UNDERSTANDING_MUTATION_AUTHORITIES = new WeakMap();
const CONSUMED_TURN_UNDERSTANDING_MUTATION_AUTHORITIES = new WeakSet();
const VERIFIED_TELEGRAM_DURABLE_HANDOFF_PROJECTIONS = new WeakMap();

function turnUnderstandingIntentSha256({
  executionSeq, turnId, tool, args, semanticHash,
}) {
  return createHash('sha256').update(canonicalJson({
    executionContract: EXECUTION_RECORD_CONTRACT_V2,
    executionSeq: Number(executionSeq),
    turnId, tool, args, binding: null, semanticHash,
  })).digest('hex');
}

function turnUnderstandingCompletionSha256(row) {
  return createHash('sha256').update(canonicalJson({
    executionContract: row.execution_contract,
    executionSeq: Number(row.execution_seq),
    executionKey: row.execution_key,
    turnId: row.turn_id,
    tool: row.tool,
    args: row.args,
    semanticHash: row.semantic_hash,
    semanticBinding: row.semantic_binding,
    intentRecordSha256: row.intent_record_sha256,
    status: row.status,
    startedTs: row.started_ts,
    finishedTs: row.finished_ts,
    outputSha256: row.output_sha256,
    materialSha256: row.material_sha256,
  })).digest('hex');
}

/** @param {any} input */
function exactTurnUnderstandingSource({
  channel, conversationId, messageId, requester, originalText, messageContext,
} = {}) {
  const expected = Object.freeze({
    channel, conversationId, messageId: String(messageId), requester, originalText,
  });
  const current = () => {
    let verified = null;
    if (channel === 'web') {
      const capability = messageContext?.httpIngressAuthority;
      const http = capability == null ? null
        : verifiedHttpIngressExecutionAuthority(capability, Object.freeze({
          channel, conversationId, requestId: messageId,
          intent: messageContext?.httpIngressIntent,
        }));
      if (http) verified = Object.freeze({
        kind: 'http_ingress', ref: http.requestRef,
        sha256: http.intentSha256, generation: Number(http.generation),
      });
    } else if (channel === 'telegram') {
      verified = verifiedTelegramUnderstandingSource(messageContext, expected);
    } else if (channel === 'voice') {
      verified = verifiedVoiceUnderstandingSource(messageContext, expected);
    }
    const generation = Number(verified?.generation);
    return verified && Number.isSafeInteger(generation) && generation >= 1
      && generation <= 64 ? Object.freeze({
        kind: verified.kind, ref: verified.ref,
        sha256: verified.sha256,
        ...(typeof verified.bodyJson === 'string' ? { bodyJson: verified.bodyJson } : {}),
        generation,
      }) : null;
  };
  const source = current();
  if (source) return Object.freeze({ ...source, current });
  throw Object.assign(
    new Error(`${channel}-understanding vereist een actuele private ingress-authority`),
    { code: 'EVA_TURN_AUTHORITY_INGRESS_UNAVAILABLE' },
  );
}

/** @param {any} execution @param {any} source */
function insertTurnUnderstandingAuthority(execution, source) {
  const issuedTs = new Date().toISOString();
  const draft = {
    contract: TURN_UNDERSTANDING_AUTHORITY_CONTRACT,
    execution_key: execution.execution_key,
    execution_seq: Number(execution.execution_seq),
    intent_record_sha256: execution.intent_record_sha256,
    channel: JSON.parse(execution.args).channel,
    conversation_id: JSON.parse(execution.args).conversationId,
    message_id: String(JSON.parse(execution.args).messageId),
    source_kind: source.kind,
    source_ref: source.ref,
    source_sha256: source.sha256,
    source_body_json: source.bodyJson ?? null,
    source_generation: Number(source.generation),
    authority_sha256: '',
    issued_ts: issuedTs,
  };
  draft.authority_sha256 = createHash('sha256').update(canonicalJson(
    turnUnderstandingAuthorityBody(draft, execution),
  )).digest('hex');
  db.prepare(`INSERT INTO main.${TURN_UNDERSTANDING_AUTHORITY_TABLE} (
    contract,execution_key,execution_seq,intent_record_sha256,channel,
    conversation_id,message_id,source_kind,source_ref,source_sha256,source_body_json,
    source_generation,authority_sha256,issued_ts
  ) VALUES (@contract,@execution_key,@execution_seq,@intent_record_sha256,@channel,
    @conversation_id,@message_id,@source_kind,@source_ref,@source_sha256,@source_body_json,
    @source_generation,@authority_sha256,@issued_ts)`).run(draft);
}

/** @param {any} row @param {any} source */
function issueTurnUnderstandingMutationAuthority(row, source) {
  const durable = turnUnderstandingAuthorityRecord(db, row);
  if (!durable) throw new Error('understandingclaim mist exacte store-owned authority');
  const capability = Object.freeze({});
  TURN_UNDERSTANDING_MUTATION_AUTHORITIES.set(capability, Object.freeze({
    executionKey: row.execution_key,
    intentRecordSha256: row.intent_record_sha256,
    authoritySha256: durable.authoritySha256,
    sourceGeneration: durable.sourceGeneration,
    currentSource: source.current,
  }));
  return capability;
}

/** @param {{executionKey:string,turnId:string,tool:string,args:any,output:any}} spec */
function persistTurnEffectBoundaryRecord(spec) {
  const claimed = claimTurnToolExecution({
    executionKey: spec.executionKey,
    turnId: spec.turnId,
    tool: spec.tool,
    args: spec.args,
  });
  const expectedOutput = canonicalJson(spec.output);
  if (claimed.claimed) {
    return completeTurnToolExecution(spec.executionKey, expectedOutput).row;
  }
  if (claimed.row?.status !== 'done' || claimed.row.output !== expectedOutput) {
    throw new Error('duurzame effectgrens heeft een conflicterende toestand');
  }
  return validateExecutionRecord(claimed.row);
}

/** @param {any} parent */
function persistVerifiedEmptyTurnEffectManifest(parent) {
  return persistTurnEffectBoundaryRecord(turnEffectManifestSpec(parent));
}

/** @param {any} parent @param {string} planSha256 */
function persistTurnEffectPlan(parent, planSha256) {
  return persistTurnEffectBoundaryRecord(turnEffectPlanSpec(parent, planSha256));
}

/** @param {any} parent */
function classifiedTurnEffects(parent) {
  let args = null;
  try { args = JSON.parse(String(parent?.args || 'null')); } catch { /* classified below */ }
  const parentTurnId = String(parent?.turn_id || '');
  const executionTurnId = String(args?.sessionRef || '');
  const rawRows = parentTurnId && executionTurnId ? /** @type {any[]} */ (db.prepare(`
    SELECT * FROM main.turn_tool_executions
    WHERE execution_key<>? AND (turn_id=? OR turn_id=?)
    ORDER BY execution_seq
  `).all(parent.execution_key, parentTurnId, executionTurnId)) : [];
  const records = [];
  const invalidRecords = [];
  for (const raw of rawRows) {
    try { records.push(validateExecutionRecord(raw)); } catch { invalidRecords.push(raw); }
  }
  return classifyTurnEffectManifest({ parent, records, invalidRecords });
}

function recoveryDispositionEvidence(disposition, evidence) {
  return Object.freeze({
    disposition,
    evidenceSha256: createHash('sha256').update(canonicalJson({
      contract: 'TurnRecoveryDispositionEvidence/v1', disposition, evidence,
    })).digest('hex'),
  });
}

/** @param {any} row @param {any} source @param {any} messageContext */
function interruptedTelegramExistingDisposition(row, source, messageContext) {
  const chatId = String(messageContext?.chatId || '');
  const messageId = String(messageContext?.messageId || '');
  const archived = telegramMessage(chatId, messageId);
  let body = null;
  try { body = JSON.parse(String(source?.bodyJson || 'null')); } catch { /* no evidence */ }
  if (!archived || !body || String(archived.chat_id) !== chatId
    || String(archived.message_id) !== messageId) return null;
  const replies = db.prepare(`SELECT id,message_id,reply_to_message_id,deleted
    FROM main.telegram_messages
    WHERE chat_id=? AND role='assistant' AND reply_to_message_id=?
    ORDER BY id`).all(chatId, messageId);
  if (replies.length) {
    let proof = null;
    try {
      const meta = archived.meta && typeof archived.meta === 'object' ? archived.meta : {};
      proof = validateTelegramAcceptedReplyRecoveryEvidence({
        chatId,
        replyToMessageId: messageId,
        directTurnSignalRef: `turn:telegram:${chatId}:${messageId}`,
        originPrincipal: `telegram:${String(
          meta.fromTelegramId || body?.meta?.fromTelegramId || '',
        )}`,
      });
    } catch { /* ieder onvolledig replybewijs wordt hieronder fail-closed */ }
    if (proof) return recoveryDispositionEvidence('already_replied', {
      parentExecutionKey: row.execution_key,
      parentIntentRecordSha256: row.intent_record_sha256,
      archivedUserRowId: Number(archived.id),
      acceptedReplyEvidenceSha256: proof.evidenceSha256,
    });
    return recoveryDispositionEvidence('effect_reconciliation_required', {
      parentExecutionKey: row.execution_key,
      parentIntentRecordSha256: row.intent_record_sha256,
      archivedUserRowId: Number(archived.id),
      unverifiedReplyRows: replies.map((reply) => ({
        id: Number(reply.id),
        messageId: String(reply.message_id),
        deleted: Number(reply.deleted || 0) === 1,
      })),
    });
  }
  const origin = db.prepare(`SELECT id,episode_id,origin_key
    FROM main.eva_episode_origins
    WHERE origin_channel='telegram' AND origin_conversation_id=?
      AND origin_message_id=? ORDER BY id LIMIT 1`).get(
    `telegram:${chatId}`, messageId,
  );
  if (origin) return recoveryDispositionEvidence('existing_origin', {
    parentExecutionKey: row.execution_key,
    parentIntentRecordSha256: row.intent_record_sha256,
    originId: Number(origin.id), episodeId: Number(origin.episode_id),
    originKey: String(origin.origin_key),
  });
  const meta = archived.meta && typeof archived.meta === 'object' ? archived.meta : {};
  const newer = newerTelegramUserMessage(chatId, messageId, {
    fromTelegramId: String(meta.fromTelegramId || body?.meta?.fromTelegramId || ''),
    messageThreadId: meta.messageThreadId ?? body?.meta?.messageThreadId ?? null,
  });
  if (newer) return recoveryDispositionEvidence('superseded', {
    parentExecutionKey: row.execution_key,
    parentIntentRecordSha256: row.intent_record_sha256,
    newerRowId: Number(newer.id), newerMessageId: String(newer.message_id),
  });
  if (meta.preparedReply) return recoveryDispositionEvidence(
    'effect_reconciliation_required', {
      parentExecutionKey: row.execution_key,
      parentIntentRecordSha256: row.intent_record_sha256,
      archivedUserRowId: Number(archived.id), preparedReplyPresent: true,
    },
  );
  return null;
}

function assertSameTurnUnderstandingClaim(current, { turnId, tool, storedArgs, semanticHash }) {
  if (current.execution_contract !== EXECUTION_RECORD_CONTRACT_V2
    || current.execution_verification !== EXECUTION_VERIFICATION_VERIFIED
    || current.turn_id !== turnId || current.tool !== tool
    || current.args !== storedArgs || current.semantic_hash !== semanticHash
    || current.semantic_binding !== null) {
    throw new Error('duurzame understanding-sleutel is al aan een andere handeling gebonden');
  }
}

/** @param {any} input */
function claimTurnUnderstandingExecution({
  executionKey, turnId, args, source,
} = {}) {
  const tool = '@turn/understanding';
  const storedArgsValue = redactSecrets(args);
  const storedArgs = canonicalJson(storedArgsValue);
  const semanticHash = createHash('sha256').update(canonicalJson({
    turnId, tool, args, binding: null,
  })).digest('hex');
  let claimed = false;
  const row = db.transaction(() => {
    requireExecutionLedger(db);
    requireTurnUnderstandingAuthorityStore(db);
    let current = /** @type {any} */ (db.prepare(
      `SELECT * FROM main.turn_tool_executions WHERE execution_key=?`,
    ).get(executionKey));
    if (!current) {
      const nextSequence = /** @type {{value:number}} */ (db.prepare(
        `SELECT coalesce(max(execution_seq),0)+1 AS value
          FROM main.turn_tool_executions`,
      ).get());
      const sequence = Number(nextSequence.value);
      if (!Number.isSafeInteger(sequence) || sequence < 1) {
        throw new Error('understandingclaim kan geen veilige volgorde reserveren');
      }
      const intentRecordSha256 = turnUnderstandingIntentSha256({
        executionSeq: sequence, turnId, tool, args: storedArgsValue, semanticHash,
      });
      db.prepare(`INSERT INTO main.turn_tool_executions (
        execution_key,turn_id,tool,args,status,started_ts,semantic_hash,
        semantic_binding,intent_record_sha256,execution_seq,execution_contract,
        execution_verification
      ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`).run(
        executionKey, turnId, tool, storedArgs, 'started', new Date().toISOString(),
        semanticHash, null, intentRecordSha256, sequence,
        EXECUTION_RECORD_CONTRACT_V2, EXECUTION_VERIFICATION_VERIFIED,
      );
      current = /** @type {any} */ (db.prepare(
        `SELECT * FROM main.turn_tool_executions WHERE execution_key=?`,
      ).get(executionKey));
      insertTurnUnderstandingAuthority(current, source);
      claimed = true;
    } else {
      assertSameTurnUnderstandingClaim(current, { turnId, tool, storedArgs, semanticHash });
      const expectedIntent = turnUnderstandingIntentSha256({
        executionSeq: current.execution_seq, turnId, tool,
        args: storedArgsValue, semanticHash,
      });
      if (current.intent_record_sha256 !== expectedIntent) {
        throw new Error('duurzame understanding-intentiehash is gewijzigd');
      }
      let durable = turnUnderstandingAuthorityRecord(db, current);
      const eventCountRow = /** @type {{value:number}} */ (db.prepare(
        `SELECT count(*) AS value
        FROM main.${TURN_UNDERSTANDING_AUTHORITY_TABLE}
        WHERE execution_key=?`,
      ).get(current.execution_key));
      const eventCount = Number(eventCountRow.value);
      if (!durable && eventCount > 0) {
        throw new Error('understandingclaim heeft een ongeldige authority-eventhistorie');
      }
      if (current.status === 'done') {
        if (!durable) {
          throw new Error('historische understanding zonder sidecar heeft geen authority');
        }
        if (durable.sourceKind !== source.kind
          || durable.sourceRef !== source.ref
          || durable.sourceSha256 !== source.sha256
          || (durable.sourceBodyJson ?? null) !== (source.bodyJson ?? null)
          || durable.sourceGeneration !== Number(source.generation)) {
          throw new Error('historische understanding hoort bij een andere authentieke bron');
        }
      } else {
        if (current.status !== 'started' || current.finished_ts != null
          || current.output != null || current.output_sha256 != null
          || current.material_sha256 != null || current.completion_record_sha256 != null) {
          throw new Error('historische understanding zonder sidecar heeft geen authority');
        }
        const sourceGeneration = Number(source.generation);
        const sameSource = durable && durable.sourceKind === source.kind
          && durable.sourceRef === source.ref && durable.sourceSha256 === source.sha256
          && (durable.sourceBodyJson ?? null) === (source.bodyJson ?? null);
        if (!durable) {
          insertTurnUnderstandingAuthority(current, source);
        } else if (!sameSource || sourceGeneration < durable.sourceGeneration) {
          throw new Error('understandingclaim hoort bij een andere authentieke bron');
        } else if (sourceGeneration > durable.sourceGeneration) {
          insertTurnUnderstandingAuthority(current, source);
        }
        durable = turnUnderstandingAuthorityRecord(db, current);
        if (!durable || durable.sourceGeneration !== sourceGeneration
          || durable.sourceKind !== source.kind || durable.sourceRef !== source.ref
          || durable.sourceSha256 !== source.sha256
          || (durable.sourceBodyJson ?? null) !== (source.bodyJson ?? null)) {
          throw new Error('understandingclaim hoort niet bij de actuele bron-generatie');
        }
      }
    }
    return validateExecutionRecord(current);
  }).immediate();
  if (claimed) persistVerifiedEmptyTurnEffectManifest(row);
  return Object.freeze({
    claimed, row, authority: row.status === 'started'
      ? issueTurnUnderstandingMutationAuthority(row, source) : null,
  });
}

/** @param {any} result */
function preparedTurnUnderstandingValue(result) {
  const fields = [
    'channel', 'conversationId', 'originMessageId', 'turnId', 'reply',
    'workItemId', 'goalEpisodeId', 'finalDeliveryBindingSha256', 'syntheticTest',
    'error', 'understood', 'interpretationFailed', 'noPlan', 'recovered',
    'durableHandoff', 'executionPending', 'actionFailed', 'durableProofMissing',
    'superseded', 'yielded', 'failureStage', 'planningError', 'noPlanDecision',
  ];
  return Object.fromEntries(fields.filter((field) => Object.hasOwn(result, field))
    .map((field) => [field, result[field]]));
}

/** @param {any} result */
function supersededTurnUnderstandingValue(result) {
  return {
    turnId: String(result.turnId || ''),
    ...(result.goalEpisodeId == null ? {} : { goalEpisodeId: result.goalEpisodeId }),
    ...(result.workItemId == null ? {} : { workItemId: result.workItemId }),
    ...(result.durationMs == null ? {} : { durationMs: result.durationMs }),
  };
}

/** @param {any} authority @param {any} options */
function completeTurnUnderstandingExecution(authority, options = {}) {
  const { kind, value = null } = options;
  const issued = authority && typeof authority === 'object'
    ? TURN_UNDERSTANDING_MUTATION_AUTHORITIES.get(authority) : null;
  if (!issued || CONSUMED_TURN_UNDERSTANDING_MUTATION_AUTHORITIES.has(authority)) {
    throw new Error('ongeldige of verbruikte understanding mutation-authority');
  }
  const completed = db.transaction(() => {
    requireExecutionLedger(db);
    requireTurnUnderstandingAuthorityStore(db);
    const current = /** @type {any} */ (db.prepare(
      `SELECT * FROM main.turn_tool_executions WHERE execution_key=?`,
    ).get(issued.executionKey));
    const durable = turnUnderstandingAuthorityRecord(db, current);
    const source = typeof issued.currentSource === 'function'
      ? issued.currentSource() : null;
    if (!current || !durable || !source
      || current.intent_record_sha256 !== issued.intentRecordSha256
      || durable.authoritySha256 !== issued.authoritySha256
      || durable.sourceGeneration !== issued.sourceGeneration
      || durable.sourceKind !== source.kind
      || durable.sourceRef !== source.ref
      || durable.sourceSha256 !== source.sha256
      || (durable.sourceBodyJson ?? null) !== (source.bodyJson ?? null)
      || durable.sourceGeneration !== Number(source.generation)
      || current.status !== 'started') {
      throw new Error('understanding mutation-authority is niet meer actueel');
    }
    const args = JSON.parse(current.args);
    let typed;
    try {
      typed = projectTurnUnderstandingTerminalOutcome({
        args, kind, value: redactSecrets(value),
      });
    } catch (cause) {
      throw Object.assign(
        new Error('getypeerde understanding-uitvoer is ongeldig of te groot'),
        { code: 'EVA_TURN_UNDERSTANDING_OUTPUT_INVALID', cause },
      );
    }
    const output = canonicalJson(typed);
    if (Buffer.byteLength(output, 'utf8') > 40_000) {
      throw Object.assign(
        new Error('getypeerde understanding-uitvoer overschrijdt de duurzame grens'),
        { code: 'EVA_TURN_UNDERSTANDING_OUTPUT_INVALID' },
      );
    }
    const finishedTs = new Date().toISOString();
    const outputSha256 = createHash('sha256').update(output).digest('hex');
    const terminal = {
      ...current, status: 'done', finished_ts: finishedTs, output,
      output_sha256: outputSha256, material_sha256: null,
    };
    const completionRecordSha256 = turnUnderstandingCompletionSha256(terminal);
    const changed = db.prepare(`UPDATE main.turn_tool_executions
      SET status='done',finished_ts=?,output=?,output_sha256=?,material_sha256=NULL,
        completion_record_sha256=?
      WHERE execution_key=? AND status='started' AND intent_record_sha256=?`).run(
      finishedTs, output, outputSha256, completionRecordSha256,
      issued.executionKey, issued.intentRecordSha256,
    );
    if (Number(changed.changes) !== 1) {
      throw new Error('understandingafronding verloor haar compare-and-swap');
    }
    return validateExecutionRecord(db.prepare(`SELECT * FROM main.turn_tool_executions
      WHERE execution_key=?`).get(issued.executionKey));
  }).immediate();
  CONSUMED_TURN_UNDERSTANDING_MUTATION_AUTHORITIES.add(authority);
  TURN_UNDERSTANDING_MUTATION_AUTHORITIES.delete(authority);
  return Object.freeze({ completed: true, row: completed });
}

/** @param {any} row @param {string} scope */
function newerAuthorizedUnderstanding(row, scope) {
  const candidates = /** @type {any[]} */ (db.prepare(`SELECT * FROM main.turn_tool_executions
    WHERE tool='@turn/understanding' AND execution_seq>?
      AND execution_key<>? AND json_valid(args)
      AND json_extract(args,'$.supersessionScope')=?
    ORDER BY execution_seq DESC`).all(
    Number(row.execution_seq), row.execution_key, scope,
  ));
  return candidates.find((candidate) => {
    try {
      return Boolean(candidate.status === 'done' && validateExecutionRecord(candidate)
        && turnUnderstandingAuthorityRecord(db, candidate));
    } catch { return false; }
  }) || null;
}

/** @param {any} ingress @param {any} options */
function recoverClaimedTurnUnderstanding(ingress, {
  createHandoff = true, visibleToFamily = true, recoveryDisposition = null,
} = {}) {
  const { row, authority } = ingress;
  if (!row || row.status !== 'started' || !authority) return false;
  const args = JSON.parse(row.args);
  const effects = classifiedTurnEffects(row);
  if (effects.disposition !== 'verified_empty') {
    completeTurnUnderstandingExecution(authority, {
      kind: 'recovery_disposition', value: {
        disposition: 'effect_reconciliation_required',
        evidenceSha256: effects.evidenceSha256,
      },
    });
    return true;
  }
  if (recoveryDisposition) {
    completeTurnUnderstandingExecution(authority, {
      kind: 'recovery_disposition', value: recoveryDisposition,
    });
    return true;
  }
  const newer = args.supersessionScope
    ? newerAuthorizedUnderstanding(row, args.supersessionScope) : null;
  if (newer) {
    completeTurnUnderstandingExecution(authority, {
      kind: 'recovery_disposition', value: recoveryDispositionEvidence('superseded', {
        parentExecutionKey: row.execution_key,
        parentIntentRecordSha256: row.intent_record_sha256,
        newerExecutionKey: newer.execution_key,
        newerIntentRecordSha256: newer.intent_record_sha256,
      }),
    });
    return true;
  }
  if (args.syntheticTest === true) {
    completeTurnUnderstandingExecution(authority, { kind: 'synthetic_interrupted' });
    return true;
  }
  if (!createHandoff) return false;
  const handoff = /** @type {any} */ (createInterpretationRetryHandoff({
    ...args, failureClass: 'runtime_interrupted', visibleToFamily,
  }));
  completeTurnUnderstandingExecution(authority, {
    kind: 'durable_handoff', value: {
      workItemId: handoff.workItem.id, goalEpisodeId: handoff.episode.id,
      turnId: `recovered:${createHash('sha256').update(canonicalJson({
        channel: args.channel, conversationId: args.conversationId,
        messageId: String(args.messageId),
      })).digest('hex')}`,
    },
  });
  return true;
}

/**
 * Adopteert uitsluitend een al duurzaam geclaimde Telegram-understanding via
 * de actuele opaque ingress-capability. Deze seam kan geen nieuwe execution
 * minten en geeft vrije callerdata nooit herstelbevoegdheid.
 */
export function recoverInterruptedTelegramUnderstandingIngress({
  conversationId, messageId, requester, originalText, messageContext,
} = /** @type {any} */ ({})) {
  const identity = preparedIngressIdentity({
    channel: 'telegram', conversationId, messageId,
  });
  if (!identity || identity.conversationId !== `telegram:${String(messageContext?.chatId || '')}`
    || String(messageContext?.messageId || '') !== identity.messageId
    || typeof requester !== 'string' || !requester
    || typeof originalText !== 'string' || !originalText.trim()) return null;
  const executionKey = `turn-understanding:${createHash('sha256')
    .update(canonicalJson(identity)).digest('hex')}`;
  const raw = db.prepare(`SELECT * FROM main.turn_tool_executions
    WHERE execution_key=?`).get(executionKey);
  let row;
  let args;
  try {
    row = validateExecutionRecord(raw);
    args = JSON.parse(String(row?.args || 'null'));
  } catch { return null; }
  if (!row || row.status !== 'started' || row.tool !== '@turn/understanding'
    || row.turn_id !== `ingress:telegram:${identity.conversationId}:${identity.messageId}`
    || args?.channel !== 'telegram'
    || args.conversationId !== identity.conversationId
    || String(args.messageId || '') !== identity.messageId
    || args.requester !== requester || args.originalText !== originalText
    || args.syntheticTest === true || !turnUnderstandingAuthorityRecord(db, row)) return null;
  let source;
  try {
    source = exactTurnUnderstandingSource({
      channel: 'telegram', conversationId: identity.conversationId,
      messageId: identity.messageId, requester, originalText, messageContext,
    });
  } catch { return null; }
  const recoveryDisposition = interruptedTelegramExistingDisposition(
    row, source, messageContext,
  );
  return db.transaction(() => {
    const ingress = claimTurnUnderstandingExecution({
      executionKey, turnId: row.turn_id, args, source,
    });
    if (ingress.claimed || ingress.row?.status !== 'started'
      || !recoverClaimedTurnUnderstanding(ingress, {
        createHandoff: true, visibleToFamily: false, recoveryDisposition,
      })) return null;
    const replay = preparedUnderstandingIngressReplay({
      channel: 'telegram', conversationId: identity.conversationId,
      messageId: identity.messageId, chatId: messageContext.chatId, sender: requester,
    });
    const typedRecovery = typeof replay?.noPlanDecision === 'string'
      && replay.noPlanDecision.startsWith('recovery:');
    return replay?.reply === '' && (replay.durableHandoff === true || typedRecovery)
      ? replay : null;
  }).immediate();
}

/** @param {any} input */
function prepareUnderstandingIngress({
  mode, channel, messageContext, preview, sessionRef, startedAt, assertCurrent,
}) {
  if (mode !== 'chat' || !['telegram', 'voice', 'web'].includes(channel)
    || messageContext?.messageId == null) {
    return { executionKey: null, authority: null, replay: null };
  }
  assertCurrent();
  const { person: requester, text: originalText, conversationId } = preview;
  const messageId = String(messageContext.messageId);
  const args = {
    channel, conversationId, messageId,
    requester, originalText, sessionRef: String(sessionRef || ''),
    syntheticTest: messageContext?.syntheticTest === true,
    supersessionScope: messageContext?.ingressScope == null
      ? null : String(messageContext.ingressScope),
    supersessionToken: messageContext?.ingressToken == null
      ? null : String(messageContext.ingressToken),
  };
  const executionKey = `turn-understanding:${createHash('sha256').update(canonicalJson({
    channel, conversationId, messageId,
  })).digest('hex')}`;
  const source = exactTurnUnderstandingSource({
    channel, conversationId, messageId, requester, originalText, messageContext,
  });
  let ingress = claimTurnUnderstandingExecution({
    executionKey, turnId: `ingress:${channel}:${conversationId}:${messageId}`,
    args, source,
  });
  if (ingress.claimed) return { executionKey, authority: ingress.authority, replay: null };
  if (ingress.row.status === 'started') {
    recoverClaimedTurnUnderstanding(ingress, { createHandoff: true });
    ingress = { ...ingress, row: db.prepare(`SELECT * FROM main.turn_tool_executions
      WHERE execution_key=?`).get(executionKey) };
  }
  const replay = preparedUnderstandingIngressReplay({
    channel, conversationId, messageId: messageContext.messageId,
    chatId: messageContext?.chatId, sender: requester, startedAt,
  });
  if (replay) {
    if (String(replay.reply || '').trim()
      && !issuedPreparedUnderstandingResult(replay, {
        channel, conversationId, messageId, sender: requester, rawText: originalText,
      })) {
      throw new Error('duurzame understanding-replay mist exacte issued-result provenance');
    }
    return { executionKey: null, authority: null, replay };
  }
  throw Object.assign(new Error('deze ingress is al duurzaam geclaimd'), {
    code: 'EVA_INGRESS_ALREADY_CLAIMED',
  });
}

function preparedIngressIdentity(value = {}) {
  const channel = String(value.channel || '');
  const conversationId = String(value.conversationId || '');
  const messageId = String(value.messageId || '');
  if (!['telegram', 'voice', 'web'].includes(channel)
    || !conversationId || !messageId) return null;
  return { channel, conversationId, messageId };
}

function deliverableResultBody(result) {
  if (!result || typeof result !== 'object'
    || typeof result.channel !== 'string' || !result.channel
    || typeof result.conversationId !== 'string' || !result.conversationId
    || typeof result.turnId !== 'string' || !result.turnId
    || typeof result.reply !== 'string'
    || typeof result.sender !== 'string') return null;
  const originMessageId = result.originMessageId == null ? null : result.originMessageId;
  const goalEpisodeId = result.goalEpisodeId == null ? null : result.goalEpisodeId;
  const workItemId = result.workItemId == null ? null : result.workItemId;
  const finalDeliveryBindingSha256 = result.finalDeliveryBindingSha256 == null
    || result.finalDeliveryBindingSha256 === ''
    ? null : result.finalDeliveryBindingSha256;
  if ((originMessageId != null && typeof originMessageId !== 'string')
    || (['telegram', 'voice'].includes(result.channel) && !originMessageId)
    || (goalEpisodeId != null
      && (!Number.isSafeInteger(goalEpisodeId) || goalEpisodeId < 1))
    || (workItemId != null && (!Number.isSafeInteger(workItemId) || workItemId < 1))
    || (finalDeliveryBindingSha256 != null
      && typeof finalDeliveryBindingSha256 !== 'string')) return null;
  const replySha256 = createHash('sha256').update(result.reply).digest('hex');
  return Object.freeze({
    contract: 'DeliverableTurnResult/v1',
    originChannel: result.channel,
    originConversationId: result.conversationId,
    originMessageId,
    turnId: result.turnId,
    reply: result.reply,
    replySha256,
    sender: result.sender,
    goalEpisodeId,
    workItemId,
    finalDeliveryBindingSha256,
    syntheticTest: result.syntheticTest === true,
    replayed: result.replayed === true,
    durableHandoff: result.durableHandoff === true,
  });
}

/**
 * @param {any} result
 * @param {{ kind?: string, receipt?: any }} [options]
 */
function issueDeliverableResult(result, { kind, receipt = null } = {}) {
  if (result && typeof result === 'object' && CONSUMED_DELIVERABLE_RESULTS.has(result)) {
    return false;
  }
  const body = deliverableResultBody(result);
  if (!body || !body.reply.trim()) return false;
  let durableSource = null;
  if (receipt) {
    const authority = receipt.authority;
    if (!authority
      || authority.channel !== body.originChannel
      || authority.conversationId !== body.originConversationId
      || authority.messageId !== body.originMessageId
      || !/^[a-f0-9]{64}$/u.test(String(authority.authoritySha256 || ''))
      || !/^[a-f0-9]{64}$/u.test(String(authority.sourceSha256 || ''))
      || !Number.isSafeInteger(Number(authority.sourceGeneration))
      || Number(authority.sourceGeneration) < 1
      || Number(authority.sourceGeneration) > 64) return false;
    durableSource = Object.freeze({
      executionKey: receipt.row.execution_key,
      intentRecordSha256: receipt.row.intent_record_sha256,
      completionRecordSha256: receipt.row.completion_record_sha256,
      preparedSha256: receipt.prepared.sha256,
      authoritySha256: authority.authoritySha256,
      sourceKind: authority.sourceKind,
      sourceRef: authority.sourceRef,
      sourceSha256: authority.sourceSha256,
      sourceBodyJson: authority.sourceBodyJson ?? null,
      sourceGeneration: Number(authority.sourceGeneration),
    });
  } else if (['telegram', 'voice'].includes(body.originChannel)) {
    return false;
  }
  const bodyJson = canonicalJson(body);
  const resultSha256 = createHash('sha256').update(bodyJson).digest('hex');
  const provenance = {
    contract: DELIVERABLE_RESULT_PROVENANCE_CONTRACT,
    kind: String(kind || ''),
    resultSha256,
    durableSource,
  };
  const provenanceSha256 = createHash('sha256')
    .update(canonicalJson(provenance)).digest('hex');
  ISSUED_DELIVERABLE_RESULTS.set(result, Object.freeze({
    ...provenance,
    provenanceSha256,
    body,
    bodyJson,
  }));
  return true;
}

function issuedDeliverableResult(result) {
  const issued = result && typeof result === 'object'
    ? ISSUED_DELIVERABLE_RESULTS.get(result) : null;
  if (!issued || issued.contract !== DELIVERABLE_RESULT_PROVENANCE_CONTRACT) return null;
  const body = deliverableResultBody(result);
  if (!body) return null;
  const bodyJson = canonicalJson(body);
  const resultSha256 = createHash('sha256').update(bodyJson).digest('hex');
  const provenanceSha256 = createHash('sha256').update(canonicalJson({
    contract: issued.contract,
    kind: issued.kind,
    resultSha256: issued.resultSha256,
    durableSource: issued.durableSource,
  })).digest('hex');
  return bodyJson === issued.bodyJson
    && resultSha256 === issued.resultSha256
    && provenanceSha256 === issued.provenanceSha256
    ? issued : null;
}

function preparedUnderstandingIngressRecord(identity, expected = {}) {
  const executionKey = `turn-understanding:${createHash('sha256')
    .update(canonicalJson(identity)).digest('hex')}`;
  const raw = db.prepare(`SELECT * FROM main.turn_tool_executions
    WHERE execution_key=? AND turn_id=? AND tool='@turn/understanding'`)
    .get(
      executionKey,
      `ingress:${identity.channel}:${identity.conversationId}:${identity.messageId}`,
    );
  let row;
  let args;
  let prepared;
  let authority;
  try {
    row = validateExecutionRecord(raw);
    args = JSON.parse(String(row.args || 'null'));
    authority = turnUnderstandingAuthorityRecord(db, row);
    prepared = verifyPreparedUnderstandingTurn(
      JSON.parse(String(row.output || 'null')), identity,
    );
  } catch { return null; }
  if (row.status !== 'done' || !authority || !prepared
    || authority.channel !== identity.channel
    || authority.conversationId !== identity.conversationId
    || authority.messageId !== identity.messageId
    || args?.channel !== identity.channel
    || args?.conversationId !== identity.conversationId
    || String(args?.messageId || '') !== identity.messageId
    || (expected.rawText != null
      && String(args?.originalText || '') !== String(expected.rawText).trim())
    || (expected.sender != null
      && String(args?.requester || '') !== String(expected.sender))) return null;
  return { row, args, authority, prepared };
}

function exactPendingFinalTelegramBinding(prepared) {
  if (!prepared.finalDeliveryBindingSha256) {
    return !prepared.goalEpisodeId
      || !episodeHasFinalDelivery(Number(prepared.goalEpisodeId));
  }
  if (!Number.isSafeInteger(prepared.goalEpisodeId) || prepared.goalEpisodeId < 1
    || !Number.isSafeInteger(prepared.workItemId) || prepared.workItemId < 1) return false;
  const episode = goalEpisodeById(prepared.goalEpisodeId);
  const work = db.prepare('SELECT * FROM main.eva_work_items WHERE id=?')
    .get(prepared.workItemId);
  let state;
  try { state = JSON.parse(String(work?.state_json || 'null')); } catch { return false; }
  const binding = state?.final_delivery_binding;
  return Boolean(episode && work && binding
    && episode.state === 'delivery_pending'
    && Number(episode.work_item_id) === prepared.workItemId
    && Number(binding.episodeId) === prepared.goalEpisodeId
    && Number(binding.workItemId) === prepared.workItemId
    && binding.stage === 'sealed'
    && binding.bindingSha256 === prepared.finalDeliveryBindingSha256
    && binding.turnId === prepared.turnId
    && binding.channel === 'telegram'
    && binding.conversationId === prepared.conversationId
    && binding.turnOriginMessageId === prepared.originMessageId
    && verifiedTelegramFinalDeliveryOrigin({
      episodeId: prepared.goalEpisodeId,
      workItemId: prepared.workItemId,
      bindingSha256: prepared.finalDeliveryBindingSha256,
      turnId: prepared.turnId,
      conversationId: prepared.conversationId,
      originMessageId: prepared.originMessageId,
    })
    && !episodeHasFinalDelivery(prepared.goalEpisodeId));
}

/**
 * Read-only preclaimclassificatie voor de smalle crashruimte waarin de
 * understanding-uitvoer al done is maar de Telegram-sidecar nog ontbreekt.
 */
export function inspectPreparedTelegramUnderstandingRecovery(value = {}) {
  const identity = preparedIngressIdentity({
    channel: 'telegram', conversationId: value.conversationId,
    messageId: value.messageId,
  });
  if (!identity || identity.conversationId !== `telegram:${String(value.chatId || '')}`) {
    return 'collision';
  }
  const executionKey = `turn-understanding:${createHash('sha256')
    .update(canonicalJson(identity)).digest('hex')}`;
  const raw = db.prepare(`SELECT * FROM main.turn_tool_executions
    WHERE execution_key=?`).get(executionKey);
  if (!raw) return 'absent';
  const receipt = preparedUnderstandingIngressRecord(identity, {
    sender: value.sender, rawText: value.rawText,
  });
  const source = value.source;
  const prepared = receipt?.prepared;
  const authority = receipt?.authority;
  if (!receipt || !prepared || !authority || !source
    || receipt.row.execution_key !== executionKey
    || prepared.reply.trim() === ''
    || prepared.turnId !== `telegram:${String(value.chatId)}:${identity.messageId}`
    || authority.sourceKind !== 'telegram_ingress'
    || authority.sourceRef !== source.ref
    || authority.sourceSha256 !== source.sha256
    || (authority.sourceBodyJson ?? null) !== (source.bodyJson ?? null)
    || authority.sourceGeneration !== Number(source.generation)
    || !Number.isSafeInteger(Number(source.generation))
    || Number(source.generation) < 1 || Number(source.generation) > 64
    || !verifiedTelegramRecoveryOriginalText({
      sourceBodyJson: source.bodyJson,
      originalText: receipt.args.originalText,
      sender: value.sender,
    })
    || hasDeliveredTurn(prepared.turnId)
    || !exactPendingFinalTelegramBinding(prepared)) return 'collision';
  return 'exact';
}

export function preparedUnderstandingIngressReplay(value = {}) {
  const identity = preparedIngressIdentity(value);
  if (!identity) return null;
  const receipt = preparedUnderstandingIngressRecord(identity);
  if (!receipt) return null;
  const prepared = receipt.prepared;
  const replay = {
    reply: prepared.reply, turnId: prepared.turnId, channel: identity.channel,
    sender: String(value.sender || 'huisgenoot'),
    conversationId: identity.conversationId, tools: [], contribution: null,
    continuation: null, goalEpisodeId: prepared.goalEpisodeId,
    workItemId: prepared.workItemId, originMessageId: prepared.originMessageId,
    durationMs: Number.isFinite(Number(value.startedAt))
      ? Math.round(performance.now() - Number(value.startedAt)) : 0,
    syntheticTest: prepared.syntheticTest, replayed: true, ...prepared.metadata,
    ...(prepared.finalDeliveryBindingSha256
      ? { finalDeliveryBindingSha256: prepared.finalDeliveryBindingSha256 } : {}),
    ...(prepared.error ? { error: prepared.error } : {}),
  };
  if (identity.channel !== 'telegram' || prepared.metadata.durableHandoff !== true
    || prepared.reply !== '') {
    return replay;
  }
  const chatId = String(value.chatId || '');
  const canonicalConversationId = `telegram:${chatId}`;
  if (!chatId || identity.conversationId !== canonicalConversationId
    || prepared.originMessageId !== identity.messageId
    || !Number.isSafeInteger(prepared.workItemId) || prepared.workItemId < 1
    || !Number.isSafeInteger(prepared.goalEpisodeId) || prepared.goalEpisodeId < 1) return null;
  const projection = Object.freeze({
    ...replay,
    turnId: `telegram:${chatId}:${identity.messageId}`,
  });
  VERIFIED_TELEGRAM_DURABLE_HANDOFF_PROJECTIONS.set(projection, Object.freeze({
    identity: Object.freeze({ ...identity }),
    chatId,
    requester: String(value.sender || 'huisgenoot'),
    executionKey: receipt.row.execution_key,
    authoritySha256: receipt.authority.authoritySha256,
    preparedSha256: prepared.sha256,
    executionTurnId: prepared.turnId,
  }));
  return projection;
}

export function verifiedTelegramDurableHandoffProjection(value, expected = {}) {
  const issued = value && typeof value === 'object'
    ? VERIFIED_TELEGRAM_DURABLE_HANDOFF_PROJECTIONS.get(value) : null;
  if (!issued
    || String(expected?.chatId || '') !== issued.chatId
    || String(expected?.conversationId || '') !== issued.identity.conversationId
    || String(expected?.messageId || '') !== issued.identity.messageId
    || String(expected?.requester || '') !== issued.requester
    || value.channel !== 'telegram'
    || value.conversationId !== issued.identity.conversationId
    || String(value.originMessageId || '') !== issued.identity.messageId
    || value.turnId !== `telegram:${issued.chatId}:${issued.identity.messageId}`
    || value.reply !== '' || value.replayed !== true || value.durableHandoff !== true) return null;
  const receipt = preparedUnderstandingIngressRecord(issued.identity, {
    sender: issued.requester,
  });
  if (!receipt
    || receipt.row.execution_key !== issued.executionKey
    || receipt.authority.authoritySha256 !== issued.authoritySha256
    || receipt.prepared.sha256 !== issued.preparedSha256
    || receipt.prepared.turnId !== issued.executionTurnId
    || receipt.prepared.metadata.durableHandoff !== true) return null;
  return value;
}

function preparedUnderstandingResultMatches(
  result, identity, prepared, expectedMetadata, telegramPreparedProjection,
) {
  return result?.channel === identity.channel
    && result?.conversationId === identity.conversationId
    && result?.originMessageId === identity.messageId
    && prepared.turnId === String(result?.turnId || '')
    && prepared.reply === String(result?.reply || '')
    && prepared.originMessageId === String(result?.originMessageId || '')
    && Number(prepared.workItemId || 0) === Number(result?.workItemId || 0)
    && Number(prepared.goalEpisodeId || 0) === Number(result?.goalEpisodeId || 0)
    && prepared.syntheticTest === (result?.syntheticTest === true)
    && String(prepared.finalDeliveryBindingSha256 || '')
      === String(result?.finalDeliveryBindingSha256 || '')
    && String(prepared.error || '') === String(result?.error || '')
    && (telegramPreparedProjection
      || canonicalJson(prepared.metadata) === canonicalJson(expectedMetadata));
}

function exactPreparedUnderstandingResult(result, expected = {}, {
  telegramPreparedProjection = false,
} = {}) {
  const identity = preparedIngressIdentity({
    channel: expected.channel ?? result?.channel,
    conversationId: expected.conversationId ?? result?.conversationId,
    messageId: expected.messageId ?? result?.originMessageId,
  });
  if (!identity) return null;
  const receipt = preparedUnderstandingIngressRecord(identity, expected);
  if (!receipt) return null;
  const prepared = receipt.prepared;
  const expectedMetadata = retainedTurnResultMetadata(result || {});
  if (!preparedUnderstandingResultMatches(
    result, identity, prepared, expectedMetadata, telegramPreparedProjection,
  )) return null;
  return { identity, receipt, prepared };
}

function issuedPreparedUnderstandingResult(result, expected = {}, options = {}) {
  const exact = exactPreparedUnderstandingResult(result, expected, options);
  if (!exact || !issueDeliverableResult(result, {
    kind: options.telegramPreparedProjection
      ? 'verified_telegram_prepared_projection' : 'verified_prepared_understanding',
    receipt: exact.receipt,
  })) return null;
  return exact;
}

export function verifiedPreparedUnderstandingTurnForResult(result, expected = {}) {
  const telegramPreparedProjection = expected.projection
    === 'telegram-prepared-reply/v2';
  const exact = exactPreparedUnderstandingResult(result, expected, {
    telegramPreparedProjection,
  });
  if (!exact || (telegramPreparedProjection && exact.identity.channel !== 'telegram')) return null;
  if (['telegram', 'voice'].includes(exact.identity.channel)) {
    const authority = exact.receipt.authority;
    if (!Object.hasOwn(expected, 'sourceRef')
      || !Object.hasOwn(expected, 'sourceSha256')
      || !Object.hasOwn(expected, 'sourceBodyJson')
      || !Object.hasOwn(expected, 'sourceGeneration')
      || String(expected.sourceRef || '') !== authority.sourceRef
      || String(expected.sourceSha256 || '') !== authority.sourceSha256
      || String(expected.sourceBodyJson || '') !== String(authority.sourceBodyJson || '')
      || Number(expected.sourceGeneration) !== authority.sourceGeneration) return null;
  }
  if (!issueDeliverableResult(result, {
    kind: telegramPreparedProjection
      ? 'verified_telegram_prepared_projection' : 'verified_prepared_understanding',
    receipt: exact.receipt,
  })) return null;
  return exact.prepared;
}

/**
 * Herstel een verloren finale Voice-capability uitsluitend voor hetzelfde
 * private issued result nadat sidecar én de actuele gen3-keten zijn bewezen.
 */
export function verifiedVoiceFinalDeliveryRecoveryRequest(request) {
  return request && typeof request === 'object'
    ? ISSUED_VOICE_FINAL_RECOVERY_REQUESTS.get(request) || null : null;
}

export async function verifiedVoiceFinalDeliveryRecoveryForResult(
  result, authority, delivery,
) {
  const issued = issuedDeliverableResult(result);
  const source = issued?.durableSource;
  if (!issued || issued.body.originChannel !== 'voice'
    || issued.body.replayed !== true || issued.body.syntheticTest === true
    || !Number.isSafeInteger(Number(issued.body.goalEpisodeId))
    || Number(issued.body.goalEpisodeId) < 1
    || !Number.isSafeInteger(Number(issued.body.workItemId))
    || Number(issued.body.workItemId) < 1
    || !/^[a-f0-9]{64}$/u.test(String(issued.body.finalDeliveryBindingSha256 || ''))
    || authority?.stage !== 'acknowledged'
    || authority?.requestId !== issued.body.originMessageId
    || authority?.conversationId !== issued.body.originConversationId
    || authority?.requester !== issued.body.sender
    || authority?.offer?.turnId !== issued.body.turnId
    || authority?.query?.sourceRef !== source?.sourceRef
    || authority?.query?.sourceSha256 !== source?.sourceSha256
    || authority?.query?.sourceBodyJson !== source?.sourceBodyJson
    || Number(source?.sourceGeneration) !== 1
    || delivery?.accepted !== true) return null;
  const deliveryJson = canonicalJson(delivery);
  const deliverySha256 = createHash('sha256').update(deliveryJson).digest('hex');
  if (authority.acknowledgement?.deliveryJson !== deliveryJson
    || authority.acknowledgement?.deliverySha256 !== deliverySha256) return null;
  const privateRequest = Object.freeze({});
  ISSUED_VOICE_FINAL_RECOVERY_REQUESTS.set(privateRequest, Object.freeze({
    resultBodyJson: issued.bodyJson,
    resultSha256: issued.resultSha256,
    deliveryJson,
    deliverySha256,
  }));
  let proof;
  try {
    proof = await verifiedAcknowledgedVoiceFinalDeliveryRecovery(
      db, authority, privateRequest,
    );
  } finally {
    ISSUED_VOICE_FINAL_RECOVERY_REQUESTS.delete(privateRequest);
  }
  if (!proof) return null;
  ISSUED_FINAL_DELIVERY_CAPABILITIES.set(result, Object.freeze({
    bindingSha256: issued.body.finalDeliveryBindingSha256,
    episodeId: issued.body.goalEpisodeId,
    workItemId: issued.body.workItemId,
    turnId: issued.body.turnId,
    channel: issued.body.originChannel,
    conversationId: issued.body.originConversationId,
    originMessageId: issued.body.originMessageId,
    replySha256: issued.body.replySha256,
    resultObjectReplySha256: createHash('sha256')
      .update(issued.body.reply.trim()).digest('hex'),
    resultSha256: issued.resultSha256,
    deliverySha256,
    recovery: true,
    voiceRecoveryProof: proof,
  }));
  return true;
}

async function executeTurn({
  channel,
  sender,
  text,
  replyTo = null,
  replyEvidence = null,
  messageContext = null,
  mode = 'chat',
  persistInbound = true,
  conversationId: providedConversationId = null,
}) {
  const sealedAutonomyInteractionEpoch = mode === 'autonomy'
    ? verifiedAutonomyInteractionEpoch(messageContext) : null;
  const autonomyInteractionEpoch = sealedAutonomyInteractionEpoch ?? runtime.interactionEpoch;
  const selfAttentionProviderDeadlineTs = mode === 'autonomy'
    ? Date.now() + SELF_ATTENTION_PROVIDER_WINDOW_MS : null;
  const started = performance.now();
  const preview = resumedTurnPreview({
    mode, sender, text, messageContext, providedConversationId, channel,
  });
  const { autonomyResume, interpretationRetry: resumedInterpretationRetry,
    person: previewPerson, text: previewText, rawText: previewRawText,
    conversationId: previewConversationId } = preview;
  const trustedVoiceIngress = channel === 'voice'
    ? verifiedVoiceUnderstandingSource(messageContext, {
      channel, conversationId: previewConversationId,
      messageId: String(messageContext?.messageId || ''),
      requester: previewPerson, originalText: previewText,
    }) : null;
  const materialRequestAuthorized = messageContext?.identityTrusted === true
    || trustedVoiceIngress != null
    || resumedInterpretationRetry?.valid === true
    || (mode === 'autonomy' && (
      Number.isSafeInteger(sealedAutonomyInteractionEpoch) || autonomyResume != null
    ));
  const materialSentinel = materialRequestSentinel({
    sourceText: previewRawText,
    sourceIdentitySha256: resumedInterpretationRetry?.rawOriginalTextSha256 || '',
    synthetic: messageContext?.syntheticTest === true,
  });
  const authenticFreeAutonomy = exactAuthenticFreeAutonomyContext({
    mode,
    messageContext,
    resumeContext: autonomyResume,
    schedulerEpoch: sealedAutonomyInteractionEpoch,
  });
  const durableTelegramTurn = channel === 'telegram'
    && messageContext?.chatId != null && messageContext?.messageId != null
    ? `telegram:${messageContext.chatId}:${messageContext.messageId}`
    : null;
  const durableWebTurn = channel === 'web' && messageContext?.messageId != null
    ? `web:${createHash('sha256').update(canonicalJson({
      conversationId: previewConversationId,
      messageId: String(messageContext.messageId),
    })).digest('hex')}` : null;
  const durableExecutionTurn = durableTelegramTurn
    || durableWebTurn
    || (channel === 'voice' && messageContext?.messageId != null
      ? `${previewConversationId}:${messageContext.messageId}` : null)
    || (resumedInterpretationRetry?.valid
      ? `${resumedInterpretationRetry.origin.conversationId}:${resumedInterpretationRetry.origin.messageId}` : null);
  const turnId = durableTelegramTurn || durableWebTurn
    || `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  const supersedingTelegramMessage = () => {
    if (channel !== 'telegram') return null;
    return newerTelegramUserMessage(
      messageContext?.chatId,
      messageContext?.messageId,
    );
  };
  const turnIsCurrent = () => {
    try {
      if (mode === 'autonomy' && (runtime.interactivePending !== 0
        || runtime.interactionEpoch !== autonomyInteractionEpoch)) return false;
      if (channel === 'voice' && typeof messageContext?.isCurrent === 'function'
        && !messageContext.isCurrent()) return false;
      if (messageContext?.httpIngressAuthority
        && !httpIngressAuthorityCurrent(messageContext.httpIngressAuthority)) return false;
      if (messageContext?.ingressScope && messageContext?.ingressToken) {
        const latestIngress = interactiveIngressByScope.get(messageContext.ingressScope);
        if (latestIngress && latestIngress !== messageContext.ingressToken) return false;
      }
      if (resumedInterpretationRetry?.supersessionScope) {
        const latestIngress = interactiveIngressByScope.get(
          resumedInterpretationRetry.supersessionScope,
        );
        if (latestIngress
          && latestIngress !== resumedInterpretationRetry.supersessionToken) return false;
      }
      if (resumedInterpretationRetry
        && interpretationRetryResume(
          autonomyResume?.workItem, autonomyResume?.episode,
        )?.stopReason === 'superseded') return false;
      return !supersedingTelegramMessage();
    } catch {
      return false;
    }
  };
  const assertTurnCurrent = () => {
    if (turnIsCurrent()) return;
    throw Object.assign(new Error('deze beurt is ingehaald'), {
      code: 'EVA_TURN_SUPERSEDED',
    });
  };
  const refreshedRetrySuperseded = () => {
    if (!resumedInterpretationRetry) return false;
    try {
      const itemId = Number(durableCommand?.id || autonomyResume?.workItem?.id);
      const episodeId = Number(durableGoalEpisode?.id || autonomyResume?.episode?.id);
      const item = Number.isFinite(itemId)
        ? db.prepare('SELECT * FROM eva_work_items WHERE id=?').get(itemId) : null;
      const episode = Number.isFinite(episodeId) ? goalEpisodeById(episodeId) : null;
      return interpretationRetryResume(item, episode)?.stopReason === 'superseded';
    } catch {
      return true;
    }
  };
  const resumedMonitoringContract = autonomyResume?.monitoringContract || null;
  const resumedPreparedDelivery = mode === 'autonomy'
    ? pendingDeliveryForWork(autonomyResume?.workItem, autonomyResume?.episode) : null;
  const ingress = prepareUnderstandingIngress({ mode, channel, messageContext, preview,
    sessionRef: durableExecutionTurn || turnId, turnId, startedAt: started, assertCurrent: assertTurnCurrent });
  if (ingress.replay) return ingress.replay;
  let ingressExecutionKey = ingress.executionKey;
  let ingressMutationAuthority = ingress.authority;
  const settleBoundRetrySupersession = (item, episode) => {
    if (resumedInterpretationRetry && item && episode) {
      const currentRetry = interpretationRetryResume(item, episode);
      if (currentRetry?.stopReason === 'superseded') {
        return resolveInterpretationRetryFailure({
          item,
          episode,
          retry: currentRetry,
          sessionRef: turnId,
        });
      }
    }
    return null;
  };
  const yieldSuperseded = () => {
    settleBoundRetrySupersession(autonomyResume?.workItem, autonomyResume?.episode);
    const durationMs = Math.round(performance.now() - started);
    const result = {
      reply: '', turnId, channel, sender: previewPerson,
      conversationId: previewConversationId, tools: [], contribution: null,
      continuation: null, goalEpisodeId: Number(messageContext?.goalEpisodeId) || null,
      workItemId: Number(messageContext?.workId) || null,
      originMessageId: messageContext?.messageId == null
        ? null : String(messageContext.messageId),
      progress: null, noActionReason: null, yielded: true, exhausted: false,
      superseded: true, durationMs, syntheticTest: messageContext?.syntheticTest === true,
    };
    if (ingressExecutionKey && ingressMutationAuthority) {
      completeTurnUnderstandingExecution(ingressMutationAuthority, {
        kind: 'superseded', value: supersededTurnUnderstandingValue(result),
      });
      ingressExecutionKey = null;
      ingressMutationAuthority = null;
    }
    runtime.lastTurn = {
      turnId, channel, sender: previewPerson, mode, text: previewText.slice(0, 300),
      reply: '', noActionReason: null, progress: null, tools: [], durationMs,
      finishedAt: new Date().toISOString(), yielded: true, superseded: true,
    };
    if (mode === 'autonomy') runtime.lastAutonomyTurn = runtime.lastTurn;
    else runtime.lastInteractiveTurn = runtime.lastTurn;
    return result;
  };
  if (mode === 'autonomy' && !turnIsCurrent()) return yieldSuperseded();
  // Een gepind antwoord is al semantisch afgerond. De afleverbeurt mag geen
  // tweede providerinterpretatie doen: die kan de inhoud niet meer wijzigen
  // en maakte bij storingen eerder juist een hostfallback tot schijnresultaat.
  const semanticResult = resumedPreparedDelivery ? {
    ok: false,
    source: 'host_prepared_delivery',
    attempts: 0,
    understanding: null,
    firstDecision: null,
    failure: {
      contract: 'TurnUnderstandingFailure/v1',
      code: resumedPreparedDelivery.invalid
        ? 'prepared_delivery_integrity'
        : resumedPreparedDelivery.superseded
          ? 'prepared_delivery_superseded'
          : 'prepared_delivery_resume',
      attempts: 0,
    },
  } : await understandRuntimeTurn({
    turnId,
    channel,
    person: previewPerson,
    userText: previewText,
    conversationId: previewConversationId,
    messageContext, signalBatch: messageContext?.signalBatch,
    mode, resumeContext: autonomyResume, trustedActiveWorkId: autonomyResume?.workItem?.id,
    assertCurrent: assertTurnCurrent, authenticFreeAutonomy,
  });
  if (mode === 'autonomy' && !turnIsCurrent()) return yieldSuperseded();
  let semanticTurn = semanticTurnState({
    result: semanticResult,
    person: previewPerson,
    userText: previewText,
    conversationId: previewConversationId,
    messageContext,
    replyEvidence,
    channel,
    mode,
    resumeContext: autonomyResume,
  });
  if (BOUNDED_SELF_ATTENTION_RESULTS.has(semanticResult)) {
    if (semanticTurn.ok) {
      const profile = selfAttentionExecutionProfile(
        semanticTurn, messageContext, selfAttentionProviderDeadlineTs,
      );
      if (profile.ok) SELF_ATTENTION_EXECUTION_PROFILES.set(semanticTurn, profile);
      else semanticTurn = failedSemanticTurnState({
        result: semanticResult, error: profile.error, failureStage: 'authority',
        person: previewPerson, userText: previewText,
        conversationId: previewConversationId, messageContext, mode,
      });
    }
    BOUNDED_SELF_ATTENTION_TURNS.add(semanticTurn);
  }
  const {
    person, userText, conversationId, syntheticTest,
    continuationRequest, turnPlan, turnContract, requiredTools,
    explicitTool, recapOnly, durableInteraction, liveInspection,
  } = semanticTurn;
  if (ingressExecutionKey && semanticTurn.ok && semanticTurn.plans.length) {
    const parent = validateExecutionRecord(db.prepare(`SELECT *
      FROM main.turn_tool_executions WHERE execution_key=?`).get(ingressExecutionKey));
    persistTurnEffectPlan(parent, interpretationRetryPlanSha256(semanticTurn));
  }
  const materialObligation = semanticTurn.ok ? materialCompletionObligation({
    understanding: semanticTurn.understanding,
    plans: semanticTurn.plans,
    sourceText: userText,
    requestSourceText: previewRawText,
    requestSourceTextSha256: resumedInterpretationRetry?.rawOriginalTextSha256 || '',
    requestSentinel: materialSentinel,
    trusted: materialRequestAuthorized,
    synthetic: syntheticTest,
  }) : null;
  const unauthorizedMutatingPlan = semanticTurn.ok && !materialRequestAuthorized
    && semanticTurn.plans.some((plan) => !['read', 'discovery'].includes(plan.effect));
  // Alle bewijsdragende tools van een materiaalverplichting komen onder de
  // huidige, duurzaam opgeslagen TurnUnderstanding te staan. Dit is bewust de
  // runtime-turn-id: artifact-authority verifieert exact dezelfde timeline-rij.
  const materialExecutionTurn = materialObligation?.required === true ? turnId : null;
  const turnMonitoring = semanticTurn.understanding?.monitoring || null;
  let monitoringAdmissionFailure = '';
  let monitoringIssuanceContext = null;
  if (turnMonitoring?.requested === true && mode === 'chat') {
    const factBindingSpecs = monitoringFactBindingSpecs(
      semanticTurn.understanding, userText,
    );
    const sourceTextSha256 = createHash('sha256').update(userText).digest('hex');
    let deliveryOrigin = null;
    let startAuthority = null;
    try {
      startAuthority = verifiedMonitoringStartAuthority({
        executionKey: ingressExecutionKey, channel, conversationId,
        messageId: String(messageContext?.messageId || ''), requester: person,
        originalText: userText, messageContext,
      });
      if (!startAuthority) throw new Error('monitoring-startauthority ontbreekt');
    } catch {
      monitoringAdmissionFailure = 'monitoring_start_authority_unverified';
    }
    if (turnMonitoring.deliveryRequired === true) {
      const sameRequester = String(turnMonitoring.deliveryTarget || '') === 'requester'
        && (!String(turnMonitoring.recipient || '').trim()
          || String(turnMonitoring.recipient).trim().toLowerCase()
            === String(person).trim().toLowerCase());
      if (monitoringAdmissionFailure || channel !== 'telegram'
        || messageContext?.identityTrusted !== true
        || !sameRequester) {
        monitoringAdmissionFailure ||= 'monitoring_required_delivery_not_direct_telegram_requester';
      } else {
        try {
          const ingressSource = verifiedTelegramUnderstandingSource(messageContext, {
            channel: 'telegram', conversationId,
            messageId: String(messageContext?.messageId || ''),
            requester: person, originalText: userText,
          });
          if (!ingressSource) throw new Error('private Telegram-ingressbron ontbreekt');
          if (startAuthority.sourceKind !== ingressSource.kind
            || startAuthority.sourceRef !== ingressSource.ref
            || startAuthority.sourceSha256 !== ingressSource.sha256
            || Number(startAuthority.sourceGeneration) !== Number(ingressSource.generation)) {
            throw new Error('duurzame Telegram-startauthority is niet exact');
          }
          const proof = requiredTelegramOriginBinding(
            conversationId, String(messageContext?.messageId || ''),
          );
          const originProofSha256 = createHash('sha256').update(canonicalJson({
            contract: 'MonitoringTelegramOriginAuthority/v1',
            executionKey: startAuthority.executionKey,
            executionSeq: startAuthority.executionSeq,
            intentRecordSha256: startAuthority.intentRecordSha256,
            authoritySha256: startAuthority.authoritySha256,
            sourceRef: ingressSource.ref,
            sourceSha256: ingressSource.sha256,
            sourceGeneration: ingressSource.generation,
            archiveRowId: proof.archiveRowId,
            archivedOriginProofSha256: proof.originProofSha256,
            conversationId,
            messageId: String(messageContext.messageId),
            principal: proof.originPrincipal,
            messageThreadId: proof.messageThreadId,
          })).digest('hex');
          deliveryOrigin = Object.freeze({
            channel: 'telegram', conversationId,
            messageId: String(messageContext.messageId),
            principal: proof.originPrincipal,
            messageThreadId: proof.messageThreadId,
            executionKey: startAuthority.executionKey,
            executionSeq: startAuthority.executionSeq,
            intentRecordSha256: startAuthority.intentRecordSha256,
            authoritySha256: startAuthority.authoritySha256,
            sourceRef: ingressSource.ref,
            sourceSha256: ingressSource.sha256,
            sourceGeneration: ingressSource.generation,
            archiveRowId: proof.archiveRowId,
            originProofSha256,
          });
        } catch (error) {
          monitoringAdmissionFailure = 'monitoring_required_delivery_origin_unverified';
        }
      }
    }
    if (!factBindingSpecs) {
      monitoringAdmissionFailure ||= 'monitoring_fact_binding_specs_incomplete';
    }
    if (!monitoringAdmissionFailure) {
      const ledgerTurnId = durableExecutionTurn || turnId;
      monitoringIssuanceContext = Object.freeze({
        factBindingSpecs,
        requesterLabel: person,
        sourceTextSha256,
        deliveryOrigin,
        startAuthority,
        verifyInitialReceipt: (receipt) => verifiedMonitoringLedgerProjection(receipt, {
          ledgerTurnId,
          requirementId: receipt?.semanticBinding?.requirementId,
          understandingSha256: semanticTurn.understanding.sha256,
        }),
      });
    }
  }
  const allOpenWorkItems = mode === 'autonomy' ? durableWorkStatus().open : [];
  const selectedAutonomyWorkId = Number(messageContext?.workId || semanticTurn.conversationWorkId);
  const initialOpenWorkItems = mode === 'autonomy' && !messageContext?.attention
    ? autonomyResume
      ? [autonomyResume.workItem]
      : Number.isFinite(selectedAutonomyWorkId)
      ? allOpenWorkItems.filter((item) => Number(item.id) === selectedAutonomyWorkId).slice(0, 1)
      : allOpenWorkItems.slice(0, 1)
    : [];
  const initialOpenWorkIds = initialOpenWorkItems
    .map((item) => Number(item.id)).filter(Number.isFinite);
  // Vervolgtaal bindt aan de episode van dit gesprek, nooit aan het oudste
  // globale werkpunt. Een expliciet work-id blijft de sterkste aanwijzing.
  const continuationWorkId = Number.isFinite(selectedAutonomyWorkId) ? selectedAutonomyWorkId
    : continuationRequest ? Number(openEpisodeForConversation(conversationId)?.work_item_id) : Number.NaN;
  const continuationWork = continuationRequest && Number.isFinite(continuationWorkId)
    ? db.prepare("SELECT * FROM eva_work_items WHERE id = ? AND status IN ('open','blocked')")
      .get(continuationWorkId) || null
    : null;
  let autonomyLane = mode === 'autonomy' && Array.isArray(messageContext?.pendingSignals)
    && messageContext.pendingSignals.some((signal) => (
      /^(?:arrival|departure|door|doorbell)$/i.test(String(signal?.type || ''))
    )) ? 'house' : autonomyLaneForWork(initialOpenWorkItems[0]);
  let inboundMessageId = messageContext?.messageId == null
    ? null
    : String(messageContext.messageId);
  const cognitiveEpisode = beginCognitiveEpisode({
    correlationId: /** @type {any} */ (turnId),
    kind: mode === 'autonomy' ? 'autonomous_attention' : 'interaction',
    modality: mode === 'autonomy' ? 'internal' : channel === 'voice' ? 'auditory_language' : 'linguistic',
    source: mode === 'autonomy' ? 'autonomy_clock' : channel,
    sourceRef: conversationId,
    subjectKey: mode === 'autonomy' ? 'eva' : person,
    focusKind: mode === 'autonomy' ? 'self_chosen_or_continuing_work' : 'conversation',
    focusRef: mode === 'autonomy'
      ? (initialOpenWorkIds[0] ? `work:${initialOpenWorkIds[0]}` : null)
      : conversationId,
    goalKey: Number(messageContext?.goalEpisodeId)
      ? `episode:${Number(messageContext.goalEpisodeId)}`
      : initialOpenWorkIds[0]
        ? `work:${initialOpenWorkIds[0]}`
        : mode === 'autonomy'
          ? Array.isArray(messageContext?.pendingSignals) && messageContext.pendingSignals.length
            ? 'signal_assessment'
            : messageContext?.attention?.kind
              ? `attention:${messageContext.attention.kind}:${messageContext.attention.id || '-'}`
              : 'free_attention'
          : `${mode}:${turnContract.kind}`,
    whyNow: mode === 'autonomy'
      ? userText.slice(0, 300)
      : `${person} richt zich nu tot Eva`,
    expectedChange: turnContract.outcome,
    salience: mode === 'autonomy' ? 0.55 : 0.9,
    payload: {
      channel,
      mode,
      sender: person,
      contractKind: turnContract.kind,
      ...(authenticFreeAutonomy ? { authenticFree: true, syntheticTest: false } : {}),
    },
  });
  const toolResults = [];
  // Alleen recoverReadEntry kan hierin een hostkind registreren. Een exported
  // binder of een structureel gelijk plan/receipt krijgt daardoor nooit zelf
  // authority om de vrije-aandacht-precommitgrens te passeren.
  const issuedHomeContextRecoveryPrecommits = new WeakMap();
  const consumedHomeContextRecoveryPrecommits = new WeakSet();
  let iterativeExecutionComplete = null;
  let behaviorCanaryChecked = false;
  let behaviorCanaryClaim = null;
  let behaviorCanaryValue = null;
  let behaviorCanaryAcceptedReply = null;
  const behaviorReplyContext = (replyPerson, replyChannel, replyConversationId) => {
    const exactMainReply = mode === 'chat' && syntheticTest !== true
      && messageContext?.identityTrusted === true
      && replyPerson === person && replyChannel === channel
      && replyConversationId === conversationId;
    if (!exactMainReply) return Object.freeze({ text: '', accepts: null });
    if (!behaviorCanaryChecked) {
      behaviorCanaryChecked = true;
      // Telegram reconstrueert het resultaatobject na persist. Tot een durable
      // canary-recoverybinding bestaat, laat deze eerste veilige tranche daar
      // bewust geen onbewijsbare proef lopen. Web en voice behouden identiteit.
      if (['web', 'voice'].includes(channel)) {
        try {
          behaviorCanaryClaim = claimBehaviorCanaryForPerson(person, {
            turnId, conversationId,
            dialogueAct: semanticTurn?.understanding?.dialogueAct,
          });
          behaviorCanaryValue = behaviorCanaryClaim
            ? behaviorCanaryLens(behaviorCanaryClaim) : null;
          if (behaviorCanaryClaim && !behaviorCanaryValue) {
            releaseBehaviorCanaryClaim(behaviorCanaryClaim);
            behaviorCanaryClaim = null;
          }
        } catch {
          if (behaviorCanaryClaim) releaseBehaviorCanaryClaim(behaviorCanaryClaim);
          behaviorCanaryClaim = null;
          behaviorCanaryValue = null;
        }
      }
    }
    const text = behaviorCanaryValue?.promptText || '';
    return Object.freeze({
      text,
      accepts: behaviorCanaryValue
        ? (reply) => { behaviorCanaryAcceptedReply = String(reply || '').trim(); }
        : null,
    });
  };
  const replyFeedback = (reply, monitoringProofOptions = {}, sourceText = userText) => finalReplyFeedback({
    reply,
    mode: 'chat',
    syntheticTest,
    toolResults,
    requiredTools,
    userText: sourceText,
    turnContract: monitoringProofOptions.turnKind === 'monitoring'
      ? { ...turnContract, kind: 'monitoring' } : turnContract,
    turnPlan,
    buildWork: semanticTurn.buildWork,
    buildNeedsExecution: semanticTurn.buildNeedsExecution,
    executionComplete: iterativeExecutionComplete,
    monitoringProofOptions,
  });
  const replyInEvaVoice = async (
    fallback, monitoringProofOptions = {}, {
      guardedFallback = '', force = false, sourceText = userText,
      replyPerson = person, replyChannel = channel, replyConversationId = conversationId,
    } = {},
  ) => {
    const grounded = normalizeUserFacingText(fallback).trim();
    if (mode !== 'chat' && !force) return grounded;
    const groundedFeedback = grounded
      ? replyFeedback(grounded, monitoringProofOptions, sourceText) : '';
    const behavior = behaviorReplyContext(
      replyPerson, replyChannel, replyConversationId,
    );
    return composeEvaReply({
      person: replyPerson,
      channel: replyChannel,
      conversationId: replyConversationId,
      userText: sourceText,
      understanding: semanticTurn.understanding,
      toolResults,
      // Een door de bewijsgrens afgekeurde draft mag bij provideruitval niet
      // alsnog als terugvaltekst ontsnappen.
      fallback: groundedFeedback ? String(guardedFallback || '').trim() : grounded,
      turnContract: monitoringProofOptions.turnKind === 'monitoring'
        ? { ...turnContract, kind: 'monitoring' } : turnContract,
      turnPlan,
      requiredTools,
      buildWork: semanticTurn.buildWork,
      buildNeedsExecution: semanticTurn.buildNeedsExecution,
      monitoringProofOptions,
      syntheticTest,
      messageContext,
      executionComplete: iterativeExecutionComplete,
      behaviorLensContext: behavior.text,
      onAcceptedReply: behavior.accepts,
      assertCurrent: assertTurnCurrent,
    });
  };
  let durableCommand = mode === 'autonomy' ? (initialOpenWorkItems[0] || null) : null,
    durableGoalEpisode = autonomyResume?.episode || null, durableExecution = durableInteraction;
  let autoBoundDurableWorkId = null;
  const persistDurableHandoff = ({
    understanding = null, reinterpret = false, failureClass = '',
  } = {}) => {
    const persisted = persistUserHandoff({
      understanding, reinterpret, failureClass, continuationRequest, continuationWork,
      channel, conversationId, inboundMessageId, person, userText,
      rawUserText: previewRawText, turnId,
      messageContext, isCurrent: turnIsCurrent, assertCurrent: assertTurnCurrent,
    });
    durableCommand = persisted.workItem; durableGoalEpisode = persisted.episode;
    durableExecution = true;
    return persisted;
  };
  const handleInterpretationRetryFailure = (failedTurn) => {
    const retry = interpretationRetryResume(durableCommand, durableGoalEpisode)
      || resumedInterpretationRetry;
    if (!retry || !durableCommand || !durableGoalEpisode) return { kind: 'none' };
    const resolved = resolveInterpretationRetryFailure({
      item: durableCommand, episode: durableGoalEpisode, retry,
      error: failedTurn?.error,
      failureClass: failedTurn?.result?.failure?.failureClass,
      validation: failedTurn?.result?.failure?.validation,
      sessionRef: turnId,
    });
    durableCommand = resolved.workItem; durableGoalEpisode = resolved.episode;
    return resolved;
  };
  let boundAutonomyWorkId = initialOpenWorkIds[0] || (Number.isFinite(selectedAutonomyWorkId) ? selectedAutonomyWorkId : null);
  let boundAutonomyExplorationId = Number(messageContext?.attention?.kind === 'exploration'
    ? messageContext.attention.id : null) || null;
  let aspirationExplorationSettlement = null;
  let explorationTerminalContributionSettlement = null;
  const explorationTerminalContributionPrecommit = exactAuthenticBoundExplorationTerminalContext({
    semanticTurn, mode, messageContext, resumeContext: autonomyResume,
    schedulerEpoch: sealedAutonomyInteractionEpoch, initialOpenWorkIds,
    boundExplorationId: boundAutonomyExplorationId, goalEpisode: durableGoalEpisode,
  });
  const explorationTerminalContributionExecution = explorationTerminalContributionPrecommit
    ? bindExplorationTerminalContributionExecution(
      explorationTerminalContributionPrecommit.claim,
      explorationTerminalContributionPrecommit.plan.args,
      turnIsCurrent,
    ) : null;
  const observationContributionPrecommit = issueObservationContributionPrecommit({
    semanticTurn, mode, messageContext, resumeContext: autonomyResume,
    schedulerEpoch: sealedAutonomyInteractionEpoch, initialOpenWorkIds,
    boundExplorationId: boundAutonomyExplorationId,
    goalEpisode: durableGoalEpisode, turnId, turnCurrent: turnIsCurrent(),
  });
  const activeAutonomyWorkIds = () => [...new Set([
    ...initialOpenWorkIds,
    Number(durableCommand?.id),
  ].filter(Number.isFinite))];
  const toolAttempts = new Map();
  const toolNameAttempts = new Map();
  const attemptedDirectPlans = new WeakSet();
  const durableExecutionCounts = new Map();
  const attemptLimitFor = (name) => {
    // Een brede zoekopdracht die time-out of weinig oplevert mag niet de hele
    // onderzoeksmissie beëindigen. Een andere vraagstelling is een echte pivot.
    if (name === 'zoek_internet') return 3;
    if (mode !== 'autonomy' && liveInspection && name === 'ha_lees') return 3;
    if (mode !== 'autonomy' && name === 'gebruik_robot') return 5;
    if (mode !== 'autonomy' && localProjectReadRequest(userText)) {
      return ({ lijst_bestanden: 1, lees_bestand: 3 })[name] || 1;
    }
    if (mode !== 'autonomy'
      && ['programming_verification', 'repair'].includes(turnContract.kind)
      && name === 'voer_uit') return 4;
    if (mode !== 'autonomy' && name === 'lees_bestand' && requiredTools.includes(name)) return 8;
    if (turnContract.kind === 'self_repair' && name === 'beheer_runtime') return 6;
    if (mode !== 'autonomy') {
      if (!requiredTools.includes(name)) {
        return ({ lijst_bestanden: 1, lees_bestand: 3, schrijf_bestand: 2 })[name] || 2;
      }
      if (requiredTools.includes('schrijf_bestand')) {
        return ({
          schrijf_bestand: 8,
          voer_uit: 4,
          lees_bestand: 7,
          lijst_bestanden: 2,
          maak_vaardigheid: 8,
          beheer_addon: 8,
        })[name] || 3;
      }
      return 2;
    }
    if (config.autonomyFull) return name === 'stuur_bericht' ? 2 : 8;
    return ({
      schrijf_bestand: 3,
      voer_uit: 3,
      werk_bijhouden: 2,
      stuur_bericht: 1,
    })[name] || 2;
  };
  const executeForTurn = async (name, args, context) => {
    assertTurnCurrent();
    const selfAttentionProvider = SELF_ATTENTION_EXECUTION_PROFILES.get(semanticTurn);
    const boundedSelfAttentionProvider = selfAttentionProvider?.kind === 'provider_direct'
      && selfAttentionProvider.plan === context?.issuedPlan
      && selfAttentionProvider.contract === SELF_ATTENTION_PROVIDER_EXECUTION_CONTRACT;
    const unboundAspirationProvider = selfAttentionProvider?.kind
      === 'provider_after_aspiration_start'
      && selfAttentionProvider.plan === context?.issuedPlan
      && selfAttentionProvider.contract === SELF_ATTENTION_PROVIDER_EXECUTION_CONTRACT;
    if (unboundAspirationProvider) {
      throw Object.assign(new Error(
        'De providerread is nog niet aan een geverifieerde verse exploration-start gebonden.',
      ), { code: 'EVA_SELF_ATTENTION_PROVIDER_UNBOUND_START' });
    }
    const governedContext = {
      ...context,
      effectPolicy: context?.effectPolicy || turnPlan.effectPolicy,
    };
    const exactMaterialLedger = materialExecutionTurn != null;
    const monitoringExecutionTurn = context?.semanticRole === 'monitoring_observation'
      && String(context?.monitoringExecutionTurnId || '') === turnId
      ? turnId : null;
    if (monitoringExecutionTurn) governedContext.monitoringExactRead = true;
    const executionLedgerTurn = materialExecutionTurn || durableExecutionTurn
      || monitoringExecutionTurn;
    const ledgerBinding = exactMaterialLedger
      ? context?.semanticBinding || null
      : interpretationRetryLedgerBinding(
        context?.semanticBinding || null, resumedInterpretationRetry,
      );
    if (boundedSelfAttentionProvider) {
      return executeSelfAttentionProviderPlan({
        profile: selfAttentionProvider, name, args,
        governedContext: { ...governedContext, selfAttentionProviderRequestBudget: 1 },
        ledgerBinding, turnId, assertCurrent: assertTurnCurrent,
        cognitiveEpisodeId: cognitiveEpisode.id,
      });
    }
    const durableSignalAssessment = mode === 'autonomy' && name === 'signalen_afhandelen'
      && `${ledgerBinding?.capabilityId}:${ledgerBinding?.operationId}` === 'signal.assess:assess';
    if (!exactMaterialLedger && !monitoringExecutionTurn
      && ((durableExecutionTurn && !toolIsDurable(name))
      || (!durableExecutionTurn && toolHasTag(name, 'receipt:conversation')))) {
      context?.markExecutionStarted?.();
      const output = redactSensitiveText(await executeTool(name, args, governedContext));
      recordTurnToolObservation({
        turnId: resumedInterpretationRetry?.valid ? turnId : durableExecutionTurn || turnId,
        tool: name, args, semanticBinding: ledgerBinding, output,
      });
      return output;
    }
    if (!executionLedgerTurn && name !== 'spreek_in_huis' && !durableSignalAssessment) {
      context?.markExecutionStarted?.();
      return executeTool(name, args, governedContext);
    }
    const signature = `${name}:${canonicalJson(args || {})}`;
    const occurrence = Number(durableExecutionCounts.get(signature) || 0) + 1;
    durableExecutionCounts.set(signature, occurrence);
    const executionKey = createHash('sha256')
      .update(`${executionLedgerTurn || turnId}\n${signature}\n${occurrence}`)
      .digest('hex');
    const claimed = claimTurnToolExecution({
      executionKey,
      turnId: executionLedgerTurn || turnId,
      tool: name,
      args,
      semanticBinding: ledgerBinding,
    });
    if (!claimed.claimed) {
      if (claimed.row?.status === 'done' && claimed.row.output) return claimed.row.output;
      return JSON.stringify({
        contract: 'ActionResult/v1',
        tool: name,
        ok: false,
        changed: false,
        verified: false,
        needs_followup: true,
        error: 'De vorige procesbeurt stopte tijdens deze handeling. Ik voer haar niet blind dubbel uit; de actuele toestand moet eerst worden gecontroleerd.',
      });
    }
    context?.markExecutionStarted?.();
    const output = redactSensitiveText(await executeTool(name, args, governedContext));
    completeTurnToolExecution(executionKey, output);
    return output;
  };
  const recordToolOutput = (
    name, args, output, semanticBinding = null, freeAutonomySelectedReadBinding = null,
    freeAutonomyContinuationAudit = null, executionStarted = false,
    providerReplaySettlement = null,
  ) => {
    let { recorded, parsed } = inspectToolOutcome(name, args, output);
    if (semanticBinding) recorded.semanticBinding = Object.freeze({ ...semanticBinding });
    if (freeAutonomyContinuationAudit) {
      recorded.continuation = Object.freeze({ ...freeAutonomyContinuationAudit });
    }
    const resolvedGoalFailure = name === 'doel_bijhouden'
      && semanticBinding?.targetResolution?.kind === 'self_attention_goal_ref'
      && !(recorded.ok && recorded.verified && recorded.changed);
    if (resolvedGoalFailure) {
      const provenCasNoChange = parsed?.data?.ok === false
        && parsed?.data?.changed === false;
      const safeArgs = Object.freeze({
        bewerking: String(args?.bewerking || ''),
        verwachte_revisie: Number.isSafeInteger(args?.verwachte_revisie)
          ? args.verwachte_revisie : null,
        verwacht_doel_id: Number.isSafeInteger(args?.verwacht_doel_id)
          ? args.verwacht_doel_id : null,
        target_resolution: semanticBinding.targetResolution,
      });
      const safeOutput = JSON.stringify({
        contract: 'ActionResult/v1', tool: name, ok: false, changed: false,
        verified: false, evidence: null,
        needs_followup: provenCasNoChange ? false : recorded.needs_followup,
        partial: recorded.partial,
        failure_class: provenCasNoChange ? 'conflict' : recorded.failure_class,
        retryable: provenCasNoChange ? false : recorded.retryable,
        effect_uncertain: provenCasNoChange ? false : recorded.effect_uncertain,
        risk: 'autonomous',
        human_message: 'De doelbewerking is niet toegepast.',
        error: 'De doelbewerking is niet toegepast.',
      });
      ({ recorded, parsed } = inspectToolOutcome(name, safeArgs, safeOutput));
      recorded.semanticBinding = Object.freeze({ ...semanticBinding });
      args = safeArgs;
      output = safeOutput;
    }
    const { ok, verified, changed } = recorded;
    recorded.execution_started = executionStarted === true;
    toolResults.push(recorded);
    // Een effect kan tijdens zijn eigen I/O zijn ingehaald. Bewaar het interne
    // receipt, maar maak op basis van een stale beurt geen nieuw werk, episode
    // of ander vervolgproject aan.
    if ((mode === 'chat' || mode === 'autonomy') && !turnIsCurrent()) return recorded;
    if (!executionStarted) {
      const providerProfile = SELF_ATTENTION_EXECUTION_PROFILES.get(semanticTurn);
      const hostVerifiedProviderReplay = providerReplaySettlement === providerProfile
        && mode === 'autonomy'
        && Number(boundAutonomyExplorationId) === Number(providerProfile?.exploration?.id)
        && providerProfile?.kind === 'provider_direct'
        && providerProfile.plan?.toolName === name
        && providerProfile.plan?.requirementId === semanticBinding?.requirementId
        && providerProfile.plan?.capabilityId === semanticBinding?.capabilityId
        && providerProfile.plan?.operationId === semanticBinding?.operationId
        && canonicalJson(providerProfile.plan?.args) === canonicalJson(args)
        && selfAttentionProviderExplorationMatches(providerProfile)
        && selfAttentionProviderClaimCollision(
          providerProfile.executionKey, name, providerProfile,
        );
      if (hostVerifiedProviderReplay
        && !SETTLED_SELF_ATTENTION_PROVIDER_PLANS.has(providerProfile.plan)) {
        const settlement = noteIssuedExplorationAction(
          boundAutonomyExplorationId, providerProfile.exploration, {
            tool: name, ok, verified,
            evidence: parsed?.evidence?.summary || parsed?.human_message || null,
            episodeId: cognitiveEpisode.id,
          },
        );
        if (settlement.accepted) SETTLED_SELF_ATTENTION_PROVIDER_PLANS.add(providerProfile.plan);
      }
      logTimeline('action', `${name}: niet gestart`, {
        internalOnly: true, turnId, mode, syntheticTest, tool: name, args,
        executionStarted: false, ok, verified, changed,
        output: auditToolOutput(output, parsed),
        semanticBinding: recorded.semanticBinding || null,
      });
      return recorded;
    }
    if (freeAutonomySelectedReadBinding) {
      settleIssuedFreeAutonomySelectedRead(
        freeAutonomySelectedReadBinding, recorded, semanticBinding,
        freeAutonomyContinuationAudit, cognitiveEpisode.id,
      );
    }
    if (mode === 'autonomy' && !syntheticTest && name === 'stuur_bericht'
      && recorded.ok === false && actionData(recorded)?.superseded !== true
      && durableCommand) {
      const data = actionData(recorded) || {};
      const freshWork = db.prepare('SELECT * FROM eva_work_items WHERE id=?')
        .get(Number(durableCommand.id)) || durableCommand;
      const freshEpisode = goalEpisodeById(Number(durableGoalEpisode?.id));
      durableCommand = freshWork;
      durableGoalEpisode = freshEpisode;
      const previous = pendingDeliveryForWork(freshWork, freshEpisode);
      const monitoringScoped = Object.hasOwn(durableWorkState(freshWork), 'monitoring');
      if (monitoringScoped && previous?.kind !== 'monitoring_v2') {
        const blocked = transitionGoalEpisode(freshEpisode.id, {
          state: 'blocked', wakeTs: '',
          checkpoint: 'Monitoringaflevering geblokkeerd: de opgeslagen pending is ongeldig.',
          nextAction: 'Beoordeel de monitoringaflevering handmatig; verstuur haar niet opnieuw.',
          sessionRef: turnId,
          work: { state: durableWorkState(freshWork), clearWait: true,
            nextWakeTs: '', clearLease: true,
            lastError: 'monitoring_pending_retry_invalid' },
        });
        durableGoalEpisode = blocked.episode;
        durableCommand = blocked.workItem;
        return recorded;
      }
      const retryNow = new Date().toISOString();
      const retryNowMs = Date.parse(retryNow);
      const attempts = Number(previous?.attempts || 0) + 1;
      const quietHours = data.waiting_for === 'quiet_hours';
      const retryMs = Math.min(6 * 60 * 60_000, 15 * 60_000 * (2 ** Math.min(5, attempts - 1)));
      const quietResumeAfter = String(data.resume_after || '');
      const quietResumeMs = Date.parse(quietResumeAfter);
      const quietResumeValid = !quietHours || (Number.isFinite(quietResumeMs)
        && new Date(quietResumeMs).toISOString() === quietResumeAfter
        && quietResumeMs > retryNowMs);
      const workState = durableWorkState(durableCommand);
      const interpretationReply = previous?.kind === 'interpretation_reply';
      const monitoringDelivery = previous?.kind === 'monitoring_v2';
      const monitoringAuthority = monitoringDelivery ? verifyMonitoringLifecycleAuthority({
        workId: Number(durableCommand.id), episodeId: Number(durableGoalEpisode?.id),
        expectedPendingSha256: previous.monitoringPendingSha256,
      }) : null;
      const payload = monitoringDelivery
        ? monitoringAuthority ? issueMonitoringPendingDelivery({
          monitoring: monitoringAuthority.monitoring,
          lifecycle: monitoringAuthority.lifecycle,
          outcome: monitoringAuthority.outcome,
          outcomeReceipt: monitoringAuthority.outcomeReceipt,
          facts: monitoringAuthority.facts, text: monitoringAuthority.pending.text,
          episodeId: Number(durableGoalEpisode.id), workId: Number(durableCommand.id),
          dedupeKey: monitoringAuthority.pending.dedupeKey,
          preparedAt: monitoringAuthority.pending.preparedAt, attempts,
        }) : null
        : {
          kind: previous?.kind || 'legacy',
          text: interpretationReply
            ? previous.text
            : String(args?.tekst || previous?.text || '').trim().slice(0, 4_000),
          ...(!interpretationReply ? { facts: previous?.facts
            || String(durableGoalEpisode?.proof_summary || durableCommand?.evidence || '').trim().slice(0, 4_000)
            || String(args?.tekst || previous?.text || '').trim().slice(0, 4_000) } : {}),
          recipient: interpretationReply
            ? null : String(args?.aan || previous?.recipient || '').trim() || null,
          preparedAt: previous?.preparedAt || retryNow,
          attempts,
          dedupeKey: previous?.dedupeKey || `episode:${durableGoalEpisode?.id || '-'}:prepared-delivery`,
          ...(previous?.provenance ? {
            provenance: previous.provenance,
            provenanceSha256: durableWorkState(durableCommand).pending_delivery?.provenanceSha256,
          } : {}),
        };
      if (monitoringDelivery && !payload) {
        const blocked = transitionGoalEpisode(durableGoalEpisode.id, {
          state: 'blocked', wakeTs: '',
          checkpoint: 'Monitoringaflevering geblokkeerd: de verzegelde retry verloor authority.',
          nextAction: 'Beoordeel de monitoringaflevering handmatig; verstuur haar niet opnieuw.',
          sessionRef: turnId,
          work: { state: workState, clearWait: true, nextWakeTs: '', clearLease: true,
            lastError: 'monitoring_delivery_retry_integrity' },
        });
        durableGoalEpisode = blocked.episode;
        durableCommand = blocked.workItem;
        return recorded;
      }
      if (monitoringDelivery && !quietResumeValid) {
        const blocked = transitionGoalEpisode(durableGoalEpisode.id, {
          state: 'blocked', wakeTs: '',
          checkpoint: 'Monitoringaflevering geblokkeerd: quiet-hours gaf geen exacte hervattijd.',
          nextAction: 'Beoordeel de aflevering handmatig; verstuur haar niet automatisch.',
          sessionRef: turnId,
          work: { state: workState, clearWait: true, nextWakeTs: '', clearLease: true,
            lastError: 'monitoring_delivery_quiet_hours_invalid' },
        });
        durableGoalEpisode = blocked.episode;
        durableCommand = blocked.workItem;
        return recorded;
      }
      const monitoringSchedule = monitoringDelivery ? monitoringDeliveryRetrySchedule(
        payload, monitoringAuthority.monitoring, {
          now: retryNow,
          requestedBackoffMs: retryMs,
          ...(quietHours ? { notBefore: quietResumeAfter } : {}),
        },
      ) : null;
      if (monitoringDelivery && !monitoringSchedule) {
        if (recorded.effect_uncertain === true) {
          const blocked = transitionGoalEpisode(durableGoalEpisode.id, {
            state: 'blocked', wakeTs: '',
            checkpoint: 'Monitoringaflevering onzeker: transport kan al zichtbaar zijn.',
            nextAction: 'Verifieer de transportstatus handmatig; verstuur niet opnieuw.',
            sessionRef: turnId,
            work: { state: workState, clearWait: true, nextWakeTs: '', clearLease: true,
              lastError: 'monitoring_delivery_effect_uncertain' },
          });
          durableGoalEpisode = blocked.episode;
          durableCommand = blocked.workItem;
          return recorded;
        }
        const freshObservationState = { ...workState };
        delete freshObservationState.pending_delivery;
        const reobserveAt = new Date(retryNowMs + 15_000).toISOString();
        const waiting = transitionGoalEpisode(durableGoalEpisode.id, {
          state: 'waiting', wakeTs: reobserveAt,
          checkpoint: 'De verzegelde meting werd te oud voor een veilige retry; meet opnieuw.',
          nextAction: 'Voer een verse gebonden monitoringmeting uit.',
          sessionRef: turnId,
          work: {
            state: freshObservationState, nextWakeTs: reobserveAt, clearLease: true,
            wait: { reason: 'monitoring_resume', resumeAfter: reobserveAt },
            lastError: 'monitoring_delivery_fresh_observation_required',
          },
        });
        durableGoalEpisode = waiting.episode;
        durableCommand = waiting.workItem;
        return recorded;
      }
      const resumeAfter = monitoringDelivery
        ? monitoringSchedule.wakeAt
        : quietHours && quietResumeValid
          ? quietResumeAfter
          : new Date(retryNowMs + retryMs).toISOString();
      const deliveryWork = {
        state: { ...workState, pending_delivery: payload },
        nextWakeTs: resumeAfter,
        wait: {
          reason: quietHours ? 'quiet_hours_delivery' : 'delivery_retry',
          resumeAfter,
          attempts,
        },
        nextStep: 'Beoordeel het geverifieerde resultaat opnieuw en bezorg het alleen passend.',
      };
      if (durableGoalEpisode) {
        const transition = transitionGoalEpisode(durableGoalEpisode.id, {
          state: 'delivery_pending', wakeTs: resumeAfter,
          nextAction: deliveryWork.nextStep, sessionRef: turnId, work: deliveryWork,
        });
        durableGoalEpisode = transition.episode;
        durableCommand = transition.workItem;
      } else durableCommand = updateDurableWork(durableCommand.id, deliveryWork);
    }
    const workItem = name === 'werk_bijhouden' ? actionData(recorded)?.item : null;
    if (!syntheticTest && Number.isFinite(Number(workItem?.id))) {
      if (!durableCommand) durableCommand = workItem;
      const episode = ensureEpisodeForWorkItem(workItem, {
        originKind: mode === 'autonomy' ? 'self' : 'user',
        originChannel: channel,
        originConversationId: mode === 'chat' ? conversationId : null,
        originMessageId: inboundMessageId,
        requester: mode === 'chat' ? person : null,
      });
      if (!durableGoalEpisode) durableGoalEpisode = episode;
    }
    if (shouldAutoBindDurableTool({ mode, syntheticTest, turnKind: turnContract.kind, hasEpisode: Boolean(durableGoalEpisode), name, recorded })) {
      ({ command: durableCommand, episode: durableGoalEpisode } = createAutoBoundTurnWork({
        channel, person, userText, outcome: turnContract.outcome,
        conversationId, originMessageId: inboundMessageId,
      }));
      autoBoundDurableWorkId = Number(durableCommand.id);
      durableExecution = true;
    }
    if (mode === 'autonomy' && !syntheticTest && name === 'zet_herinnering'
      && !autonomyEffectRequiresReport(name, args)
      && recorded.ok && recorded.verified && recorded.changed && durableCommand) {
      const due = actionData(recorded)?.due;
      if (Number.isFinite(Date.parse(due || ''))) {
        const reminderWork = {
          nextWakeTs: due,
          wait: { reason: 'self_reminder', reminderId: actionData(recorded)?.id || null },
          currentStep: durableCommand.current_step || durableCommand.next_step || '',
        };
        if (durableGoalEpisode) {
          const transition = transitionGoalEpisode(durableGoalEpisode.id, {
            state: 'waiting', wakeTs: due, nextAction: reminderWork.currentStep,
            sessionRef: turnId, work: reminderWork,
          });
          durableGoalEpisode = transition.episode;
          durableCommand = transition.workItem;
        } else durableCommand = updateDurableWork(durableCommand.id, reminderWork);
      }
    }
    if (mode === 'autonomy' && !syntheticTest && durableGoalEpisode
      && autonomyEffectRequiresReport(name, args)
      && recorded.ok && recorded.verified && recorded.changed) {
      const proof = readableEvidence(
        recorded.evidence?.summary || recorded.output || `${name} is uitgevoerd`,
      ).slice(0, 1_200);
      const transition = transitionGoalEpisode(durableGoalEpisode.id, {
        state: 'delivery_pending', checkpoint: proof, proofSummary: proof,
        nextAction: 'Bezorg het concrete effect kort aan het gezin.',
        deliveryPolicy: 'required',
        deliveryTarget: durableGoalEpisode.delivery_target || 'gezin',
        sessionRef: turnId,
        work: { evidence: proof, lastAction: name },
      });
      durableGoalEpisode = transition.episode;
      if (transition.workItem) durableCommand = transition.workItem;
    }
    if (mode === 'autonomy' && !syntheticTest && !durableGoalEpisode
      && autonomyEffectRequiresReport(name, args)
      && recorded.ok && recorded.verified && recorded.changed) {
      const proof = readableEvidence(
        recorded.evidence?.summary || recorded.output || `${name} is uitgevoerd`,
      ).slice(0, 1_200);
      ({ command: durableCommand, episode: durableGoalEpisode } = db.transaction(() => {
        const command = createWorkItem({
          source: 'autonomy',
          owner: null,
          title: `Zelfgekozen verandering: ${name}`,
          goal: `Maak de uitgevoerde verandering begrijpelijk zichtbaar voor het gezin: ${proof}`,
          nextStep: 'Bezorg het concrete effect kort aan het gezin.',
          definitionOfDone: 'Het effect is geverifieerd en precies eenmaal passend gemeld.',
          priority: 80,
          visibleToFamily: true,
        });
        const existing = episodeForWork(command.id);
        const episode = existing || bindEpisodeToWork(createGoalEpisode({
          goalKey: 'household_value', title: command.title, outcome: command.goal,
          definitionOfDone: command.definition_of_done, originKind: 'self',
          originChannel: 'autonomy', deliveryPolicy: 'required',
          deliveryTarget: 'gezin',
          nextAction: command.next_step,
        }).id, command.id);
        const transition = transitionGoalEpisode(episode.id, {
          state: 'delivery_pending', checkpoint: proof, proofSummary: proof,
          nextAction: command.next_step, sessionRef: turnId,
          work: { evidence: proof, lastAction: name },
        });
        return {
          command: transition.workItem,
          episode: transition.episode,
        };
      })());
      boundAutonomyWorkId = durableCommand.id;
    }
    if (mode === 'autonomy' && !boundAutonomyWorkId && Number.isFinite(Number(workItem?.id))) {
      boundAutonomyWorkId = Number(workItem.id);
    }
    const exploration = name === 'verkenning_bijhouden'
      ? actionData(recorded)?.exploration
      : null;
    const freshAspirationAnchorStart = mode === 'autonomy'
      && semanticTurn.aspirationExplorationAnchor?.startPlan?.toolName === name
      && semanticBinding?.understandingSha256
        === semanticTurn.aspirationExplorationAnchor.understandingSha256
      && semanticBinding?.requirementId === 'r1'
      && semanticBinding?.capabilityId === 'exploration.manage'
      && semanticBinding?.operationId === 'start';
    const anchorNextPlan = semanticTurn.aspirationExplorationAnchor?.nextPlan;
    const freshAspirationAnchorNext = mode === 'autonomy'
      && anchorNextPlan?.toolName === name
      && semanticBinding?.understandingSha256
        === semanticTurn.aspirationExplorationAnchor.understandingSha256
      && semanticBinding?.requirementId === anchorNextPlan.requirementId
      && semanticBinding?.capabilityId === anchorNextPlan.capabilityId
      && semanticBinding?.operationId === anchorNextPlan.operationId
      && canonicalJson(args) === canonicalJson(anchorNextPlan.args);
    if (!freshAspirationAnchorStart && mode === 'autonomy'
      && Number.isFinite(Number(exploration?.id))
      && (recorded.changed || Number(boundAutonomyExplorationId) === Number(exploration.id))) {
      boundAutonomyExplorationId = Number(exploration.id);
    } else if (mode === 'autonomy' && boundAutonomyExplorationId
      && !freeAutonomySelectedReadBinding) {
      const providerProfile = SELF_ATTENTION_EXECUTION_PROFILES.get(semanticTurn);
      const exactProviderPlan = providerProfile?.kind === 'provider_direct'
        && providerProfile.plan?.toolName === name
        && providerProfile.plan?.requirementId === semanticBinding?.requirementId
        && canonicalJson(providerProfile.plan?.args) === canonicalJson(args);
      const noteOptions = {
        tool: name,
        ok,
        verified,
        evidence: parsed?.evidence?.summary || parsed?.human_message || null,
        episodeId: cognitiveEpisode.id,
      };
      if (exactProviderPlan) {
        if (!SETTLED_SELF_ATTENTION_PROVIDER_PLANS.has(providerProfile.plan)) {
          noteIssuedExplorationAction(
            boundAutonomyExplorationId, providerProfile.exploration, noteOptions,
          );
        }
      } else if (freshAspirationAnchorNext
        && aspirationExplorationSettlement?.expected
        && Number(boundAutonomyExplorationId)
          === Number(aspirationExplorationSettlement.explorationId)) {
        noteIssuedExplorationAction(
          boundAutonomyExplorationId,
          aspirationExplorationSettlement.expected,
          noteOptions,
        );
      } else if (!freshAspirationAnchorNext) {
        noteExplorationAction(boundAutonomyExplorationId, noteOptions);
      }
    }
    logTimeline('action', `${name}: ${ok ? 'gelukt' : 'niet gelukt'}`, {
      internalOnly: true,
      turnId,
      mode,
      syntheticTest,
      tool: name,
      args,
      ok,
      verified,
      changed,
      executionStarted: true,
      output: auditToolOutput(output, parsed),
      semanticBinding: recorded.semanticBinding || null,
      continuation: recorded.continuation || null,
    });
    try {
      const subject = args?.doel || args?.entiteit || args?.entity_id
        || args?.pad || args?.id || args?.naam || args?.vraag || null;
      const sourceUpdatedAt = parsed?.data?.entity?.updated
        || parsed?.data?.entity?.last_updated
        || parsed?.data?.updated
        || null;
      recordCognitiveAction(cognitiveEpisode.id, {
        correlationId: turnId,
        tool: name,
        capabilityId: recorded.semanticBinding?.capabilityId || null,
        operationId: recorded.semanticBinding?.operationId || null,
        subject,
        expected: turnContract.outcome,
        ok,
        verified,
        changed,
        executionStarted: true,
        effect: toolEffect(name, args),
        partial: recorded.partial,
        needsFollowup: recorded.needs_followup,
        effectUncertain: recorded.effect_uncertain,
        evidence: parsed?.evidence?.summary || null,
      });
      recordCognitiveOutcome(cognitiveEpisode.id, {
        correlationId: turnId,
        resultClass: name,
        ok,
        verified,
        changed,
        executionStarted: true,
        effectType: toolEffect(name, args),
        partial: recorded.partial,
        needsFollowup: recorded.needs_followup,
        effectUncertain: recorded.effect_uncertain,
        evidence: parsed?.evidence?.summary || null,
        effect: parsed?.human_message || parsed?.error || null,
        sourceUpdatedAt,
      });
    } catch (error) {
      logTimeline('system', `Cognitieve actiekoppeling mislukte: ${errMsg(error)}`, {
        internalOnly: true,
        turnId,
        tool: name,
      });
    }
    return recorded;
  };
  const ensureAutonomyCheckpointAfterReport = () => {
    if (mode !== 'autonomy') return true;
    const pendingIds = autonomyPendingCheckpointIds(toolResults, activeAutonomyWorkIds());
    if (!pendingIds.length) return true;
    const sent = [...toolResults].reverse().find((tool) => (
      tool.name === 'stuur_bericht' && tool.ok && tool.verified
    ));
    if (!sent) return false;
    for (const workId of pendingIds) {
      const status = durableWorkStatus();
      const candidates = [...status.open, ...status.blocked, ...status.doneToday];
      const current = candidates.find((item) => Number(item.id) === Number(workId));
      if (!current) return false;
      const proof = [...toolResults].reverse().find((tool) => (
        autonomyProofRelevantToWork(current, tool)
      ));
      if (!proof) continue;
      try {
        const item = updateDurableWork(current.id, {
          status: current.status,
          evidence: checkpointEvidence(proof) || undefined,
          lastAction: proof.name,
          proofAccepted: current.status === 'done',
        });
        recordToolOutput('werk_bijhouden', {
          bewerking: 'herstelcheckpoint',
          id: current.id,
        }, JSON.stringify({
          contract: 'ActionResult/v1',
          tool: 'werk_bijhouden',
          ok: true,
          changed: true,
          verified: true,
          evidence: {
            source: 'werk_bijhouden',
            summary: `Werkpunt #${current.id} is na het bezorgde resultaat als ${current.status} vastgelegd.`,
          },
          needs_followup: current.status !== 'done',
          risk: 'autonomous',
          human_message: '',
          data: { ok: true, changed: true, item },
        }), null, null, null, true);
      } catch (error) {
        logTimeline('system', `Herstelcheckpoint voor zelfstandig werk mislukte: ${errMsg(error)}`, {
          internalOnly: true,
          autonomous: true,
          turnId,
          workId: current.id,
        });
        return false;
      }
    }
    return !autonomyWorkCheckpointPending(toolResults, activeAutonomyWorkIds());
  };
  const checkpointAutonomyProgress = () => {
    if (mode !== 'autonomy' || !initialOpenWorkIds.length) return null;
    const status = durableWorkStatus();
    const current = [...status.open, ...status.blocked]
      .find((item) => Number(item.id) === initialOpenWorkIds[0]);
    if (!current) return null;
    const proof = [...toolResults].reverse().find((tool) => (
      autonomyProofRelevantToWork(current, tool)
      && !['toon_werk', 'lijst_bestanden', 'werk_bijhouden', 'signalen_afhandelen', 'stuur_bericht'].includes(tool.name)
    ));
    if (!proof) return null;
    const updated = updateDurableWork(current.id, {
      status: current.status,
      evidence: checkpointEvidence(proof) || undefined,
      lastAction: proof.name,
    });
    return workItemMateriallyChanged(current, updated) ? updated : null;
  };
  const finishTurn = (reply, result = {}) => {
    const staleAtFinish = (mode === 'chat' || mode === 'autonomy') && !turnIsCurrent();
    if (staleAtFinish && mode === 'autonomy') {
      const settled = settleBoundRetrySupersession(durableCommand, durableGoalEpisode);
      if (settled) {
        durableCommand = settled.workItem;
        durableGoalEpisode = settled.episode;
      }
    }
    if (staleAtFinish) {
      reply = '';
      result = { ...result, superseded: true, yielded: true };
    }
    const resultMetadata = retainedTurnResultMetadata(result);
    let answer = normalizeUserFacingText(reply).trim();
    let contribution = contributionFromTools(toolResults, mode, userText, {
      durable: durableExecution,
      plannedRequiredTools: requiredTools,
      plannedTurnKind: turnContract.kind,
      plannedActionRequested: explicitTool,
      autoBoundOneShot: {
        autoBoundThisTurn: Number.isFinite(autoBoundDurableWorkId)
          && Number(durableCommand?.id) === autoBoundDurableWorkId,
        turnSucceeded: result.toolFinal === true && result.understood === true
          && !result.actionFailed && !result.durableProofMissing && !result.executionPending
          && !result.error && !result.superseded && !result.yielded,
        understanding: semanticTurn.understanding,
        continuation: continuationRequest,
        resumed: Boolean(resumedInterpretationRetry || resumedMonitoringContract
          || resumedPreparedDelivery),
        plans: semanticTurn.plans,
      },
    });
    const terminalContributionSettlement = explorationTerminalContributionSettlement
      && mode === 'autonomy' && turnIsCurrent()
      && result?.toolFinal === true && result?.understood === true
      && result?.actionFailed !== true && result?.durableProofMissing !== true
      && result?.executionPending !== true && result?.preparedRetryDelivery !== true
      && result?.deliveryOnly !== true && result?.superseded !== true
      && result?.yielded !== true && !result?.error
      ? explorationTerminalContributionSettlement : null;
    if (terminalContributionSettlement) {
      contribution = terminalContributionSettlement.replayed === true
        ? null : terminalContributionSettlement.contribution;
    }
    const preparedRetryDelivery = result.preparedRetryDelivery === true;
    if (preparedRetryDelivery || result.deliveryOnly === true) contribution = null;
    if (result.actionFailed === true || result.durableProofMissing === true
      || result.superseded === true || result.yielded === true) contribution = null;
    if (contribution && mode === 'autonomy'
      && !ensureAutonomyCheckpointAfterReport()) contribution = null;
    const observationSettlement = !contribution ? settleObservationContribution({
      precommit: observationContributionPrecommit,
      toolResults,
      result,
      mode,
      turnCurrent: turnIsCurrent(),
    }) : null;
    if (observationSettlement) contribution = observationSettlement.contribution;
    let continuation = null;
    if (mode === 'autonomy' && !contribution && !preparedRetryDelivery
      && result.deliveryOnly !== true && result.superseded !== true && result.yielded !== true) {
      const incomplete = incompleteAutonomousWrite(toolResults)
        || (autonomyArtifactReportPending(toolResults)
          ? completedAutonomousArtifact(toolResults)
          : null);
      const path = String(actionData(incomplete)?.path || incomplete?.args?.pad || '').trim();
      if (path) {
        const readyButUnreported = completedAutonomousArtifact(toolResults) === incomplete;
        const nextStep = autonomyContinuationStep(path, readyButUnreported, toolResults);
        const latestProof = [...toolResults].reverse().find((tool) => (
          tool.ok && tool.verified && tool.name === 'voer_uit'
        )) || [...toolResults].reverse().find((tool) => (
          tool.ok && tool.verified && !['werk_bijhouden', 'stuur_bericht'].includes(tool.name)
        ));
        if (initialOpenWorkIds.length) {
          const before = durableWorkStatus().open.find((item) => (
            Number(item.id) === Number(initialOpenWorkIds[0])
          ));
          const updated = updateDurableWork(initialOpenWorkIds[0], {
            status: 'open',
            nextStep,
            evidence: checkpointEvidence(latestProof) || undefined,
            lastAction: latestProof?.name || 'werk_bijhouden',
          });
          continuation = workItemMateriallyChanged(before, updated) ? updated : null;
        } else {
          continuation = createWorkItem({
            source: 'autonomy',
            title: readyButUnreported
              ? `Meld gecontroleerd werkstuk: ${path.split('/').at(-1)}`
              : `Controleer autonoom werkstuk: ${path.split('/').at(-1)}`,
            goal: readyButUnreported
              ? `Maak het bewezen resultaat van ${path} kort en begrijpelijk zichtbaar voor het gezin.`
              : `Maak ${path} aantoonbaar werkend en laat geen onbewezen tussenversie als afgerond resultaat staan.`,
            nextStep,
            visibleToFamily: true,
          });
          const continuationEpisode = ensureEpisodeForWorkItem(continuation, {
            originKind: 'self',
            originChannel: 'autonomy',
          });
          if (!durableGoalEpisode) durableGoalEpisode = continuationEpisode;
        }
      }
      if (!continuation) continuation = checkpointAutonomyProgress();
    }
    if (contribution) {
      const evidence = observationSettlement
        ? observationSettlement.contribution.evidence.summary
        : readableEvidence(
          contribution.output || contribution.evidence?.summary || '',
        ).slice(0, 1_200);
      const topic = mode === 'autonomy'
        ? contribution.name === 'stuur_bericht'
          ? `Uit zichzelf gemeld: ${evidence}`
          : `Zelfstandig afgerond: ${evidence}`
        : `Opdracht van ${person}: ${userText.slice(0, 240)}`;
      if (!observationSettlement && !terminalContributionSettlement) {
        logTimeline('contribution', topic.slice(0, 500), {
          internalOnly: true,
          turnId,
          source: mode === 'autonomy' ? 'bijdrage' : 'opdracht',
          owner: mode === 'autonomy' ? null : person,
          evidence,
          lastAction: contribution.name,
          status: durableGoalEpisode?.delivery_policy === 'required'
            && !episodeHasFinalDelivery(durableGoalEpisode.id)
            ? 'delivery_pending'
            : 'done',
          syntheticTest,
        });
      }
      if (durableCommand) {
        const deliveryRequired = durableGoalEpisode?.delivery_policy === 'required';
        const deliverySent = durableGoalEpisode
          ? episodeHasFinalDelivery(durableGoalEpisode.id)
          : false;
        const waitingForDelivery = deliveryRequired && !deliverySent;
        const nextAction = waitingForDelivery
          ? mode === 'autonomy'
            ? 'Bezorg het gecontroleerde resultaat kort aan het gezin.'
            : 'Bezorg het gecontroleerde resultaat kort aan de oorspronkelijke verzoeker.'
          : '';
        const work = {
          nextStep: nextAction,
          evidence,
          lastAction: contribution.name,
          result: answer || evidence,
        };
        if (durableGoalEpisode) {
          const transition = transitionGoalEpisode(durableGoalEpisode.id, {
            state: waitingForDelivery
            ? 'delivery_pending'
            : deliverySent || (mode === 'autonomy'
              && durableGoalEpisode.delivery_policy !== 'required')
              ? 'closed'
              : 'verifying',
            checkpoint: evidence, proofSummary: evidence, nextAction,
            sessionRef: turnId, work,
          });
          durableGoalEpisode = transition.episode;
          durableCommand = transition.workItem;
        } else {
          durableCommand = updateDurableWork(durableCommand.id, {
            ...work, status: waitingForDelivery ? 'open' : 'done',
            proofAccepted: !waitingForDelivery,
          });
        }
      }
    }
    const currentResumedRetry = resumedInterpretationRetry?.valid === true
      ? interpretationRetryResume(durableCommand, durableGoalEpisode) : null;
    const mayFinalizeStaleRetry = (result.superseded === true || result.yielded === true)
      && currentResumedRetry?.valid === true && !currentResumedRetry.stopReason;
    const currentDurableGoalEpisode = durableGoalEpisode
      ? goalEpisodeById(durableGoalEpisode.id) || durableGoalEpisode : null;
    if (!contribution && durableCommand && !preparedRetryDelivery
      && currentDurableGoalEpisode?.state !== 'delivery_pending'
      && result.deliveryOnly !== true
      && (mayFinalizeStaleRetry
        || (result.superseded !== true && result.yielded !== true))) {
      const currentCommand = db.prepare('SELECT * FROM eva_work_items WHERE id=?')
        .get(durableCommand.id) || durableCommand;
      if (['done', 'cancelled'].includes(currentCommand.status)) {
        durableCommand = currentCommand;
        if (durableGoalEpisode && durableGoalEpisode.delivery_policy !== 'required'
          && !['closed', 'abandoned'].includes(durableGoalEpisode.state)) {
          durableGoalEpisode = updateGoalEpisode(durableGoalEpisode.id, {
            state: currentCommand.status === 'done' ? 'closed' : 'abandoned',
            checkpoint: currentCommand.evidence || currentCommand.result || answer || '',
            proofSummary: currentCommand.evidence || '',
            nextAction: '', wakeTs: '', sessionRef: turnId,
          });
        }
      } else {
        const checkpoint = [...toolResults].reverse().find((tool) => (
          tool.ok && tool.verified && tool.name !== 'werk_bijhouden'
        ));
        if (checkpoint) {
          const evidence = checkpointEvidence(checkpoint);
          const work = {
            evidence: evidence || undefined,
            lastAction: checkpoint.name,
          };
          if (durableGoalEpisode) {
            const transition = transitionGoalEpisode(durableGoalEpisode.id, {
              state: 'waiting', checkpoint: evidence || answer || '',
              nextAction: durableCommand.next_step || '', sessionRef: turnId, work,
            });
            durableGoalEpisode = transition.episode;
            durableCommand = transition.workItem;
            continuation = transition.workItem;
          } else continuation = updateDurableWork(durableCommand.id, { ...work, status: 'open' });
        }
      }
    }
    const progress = mode === 'autonomy' && result.superseded !== true && result.yielded !== true
      ? autonomyProgressFromTools(toolResults, continuation)
      : null;
    const noActionReason = mode === 'autonomy' && !contribution && !progress
      && result.superseded !== true && result.yielded !== true
      && result.preserveAutonomyReply !== true
      ? result.executionPending
        ? 'Lopend bewonerswerk kreeg nog geen materiële uitvoerstap; alleen administratie telt niet als voortgang.'
        : autonomyNoActionReason(answer, toolResults)
      : null;
    if (progress) {
      answer = '';
      logTimeline('decision', `Zelfstandig werk voortgezet: ${progress.summary}`, {
        internalOnly: true,
        autonomous: true,
        turnId,
        outcome: 'progress',
        tool: progress.tool,
        tools: toolResultSummary(toolResults),
      });
    } else if (noActionReason) {
      answer = '';
      logTimeline('decision', `Geen zelfstandige actie: ${noActionReason}`, {
        internalOnly: true,
        autonomous: true,
        turnId,
        outcome: 'no_action',
        tools: toolResultSummary(toolResults),
      });
    }
    const hideSupersededObservationTools = mode === 'autonomy'
      && observationContributionPrecommit != null
      && (result.superseded === true || result.yielded === true);
    runtime.lastTurn = {
      turnId,
      channel,
      sender: person,
      mode,
      text: userText.slice(0, 300),
      reply: answer.slice(0, 600),
      noActionReason,
      progress,
      tools: hideSupersededObservationTools ? [] : toolResultSummary(toolResults),
      durationMs: Math.round(performance.now() - started),
      finishedAt: new Date().toISOString(),
      ...resultMetadata,
      ...(result.yielded === true ? { yielded: true } : {}),
      ...(result.superseded === true ? { superseded: true } : {}),
      ...(result.error ? { error: String(result.error).slice(0, 500) } : {}),
    };
    if (mode === 'autonomy') runtime.lastAutonomyTurn = runtime.lastTurn;
    else runtime.lastInteractiveTurn = runtime.lastTurn;
    runtime.lastTurn.reply = answer.slice(0, 600);
    try {
      closeCognitiveEpisode(cognitiveEpisode.id, {
        // Een cognitieve episode beschrijft deze ene aandacht-/gespreksbeurt.
        // Vervolgwerk leeft canoniek in work + portfolio, niet als een derde
        // open/wachtende status in het cognitieve spoor.
        status: result.error || result.superseded || result.yielded
          ? 'interrupted'
          : 'completed',
        resultClass: result.error ? 'turn_error' : result.actionFailed
          ? 'partial_execution'
          : contribution?.name
          || progress?.tool
          || (noActionReason ? 'no_action' : answer ? 'reply' : 'silent'),
        evidence: {
          tools: toolResultSummary(toolResults),
          contribution: contribution?.evidence?.summary || null,
        },
        effect: {
          replied: Boolean(answer),
          replySummary: answer.slice(0, 600),
          madeProgress: Boolean(contribution || progress?.made_progress === true
            || toolResults.some((tool) => verifiedCompleteReceipt(tool) && tool.changed === true)),
          continuationId: continuation?.id || null,
          hadChanged: toolResults.some((tool) => tool.execution_started === true
            && tool.changed === true),
          hadEffectUncertainty: toolResults.some((tool) => tool.execution_started === true
            && tool.effect_uncertain === true),
          hadDurableEffect: toolResults.some((tool) => tool.execution_started === true
            && tool.changed === true
            && !['read', 'discovery'].includes(toolEffect(tool.name, tool.args))),
          readOnly: toolResults.some((tool) => tool.execution_started === true)
            && toolResults.filter((tool) => tool.execution_started === true)
              .every((tool) => ['read', 'discovery'].includes(toolEffect(tool.name, tool.args))),
        },
        ...(result.error ? { reflection: {
          error: String(result.error).slice(0, 500),
          ...(resultMetadata.failureStage
            ? { failureStage: resultMetadata.failureStage }
            : {}),
        } } : {}),
      });
    } catch (error) {
      logTimeline('system', `Cognitieve episode kon niet worden gesloten: ${errMsg(error)}`, {
        internalOnly: true,
        turnId,
      });
    }
    let finalDeliveryCapability = null;
    if (answer && mode === 'chat' && durableGoalEpisode) {
      try {
        finalDeliveryCapability = sealFinalDeliveryReply(durableGoalEpisode.id, {
          workItemId: durableCommand?.id,
          turnId,
          channel,
          conversationId,
          originMessageId: inboundMessageId,
          resultText: answer,
        });
        if (finalDeliveryCapability) {
          durableGoalEpisode = goalEpisodeById(durableGoalEpisode.id) || durableGoalEpisode;
          durableCommand = db.prepare('SELECT * FROM eva_work_items WHERE id=?')
            .get(durableCommand?.id) || durableCommand;
        }
      } catch (error) {
        logTimeline('system', `Finale antwoordbinding kon niet worden verzegeld: ${errMsg(error)}`, {
          internalOnly: true, turnId, goalEpisodeId: durableGoalEpisode.id,
        });
      }
    }
    let behaviorDeliveryCapability = null;
    const exactAcceptedBehaviorReply = behaviorCanaryClaim && behaviorCanaryValue
      && answer && answer === behaviorCanaryAcceptedReply
      && mode === 'chat' && syntheticTest !== true
      && !result.error && result.superseded !== true && result.yielded !== true;
    if (exactAcceptedBehaviorReply) {
      try {
        behaviorDeliveryCapability = prepareBehaviorCanaryDelivery(behaviorCanaryClaim, {
          turnId, conversationId, reply: answer,
          operationKey: `behavior-${behaviorCanaryValue.exposureKind}:${turnId}`,
        });
      } catch (error) {
        logTimeline('system', `Gedragsproef kon niet exact aan dit antwoord worden gebonden: ${errMsg(error)}`, {
          internalOnly: true, turnId, behaviorLearning: true,
        });
      }
    }
    if (!behaviorDeliveryCapability && behaviorCanaryClaim) {
      releaseBehaviorCanaryClaim(behaviorCanaryClaim);
    }
    const completedResult = {
      reply: answer,
      turnId,
      channel,
      sender: person,
      conversationId,
      tools: hideSupersededObservationTools ? [] : privacySafeTurnTools(toolResults),
      contribution: contribution ? {
        tool: contribution.name,
        evidence: contribution.evidence || null,
      } : null,
      continuation: continuation ? { id: continuation.id, title: continuation.title } : null,
      goalEpisodeId: durableGoalEpisode?.id || Number(messageContext?.goalEpisodeId) || null,
      workItemId: durableCommand?.id || null,
      originMessageId: inboundMessageId,
      progress,
      noActionReason,
      yielded: result.yielded === true,
      exhausted: result.exhausted === true,
      superseded: result.superseded === true,
      durationMs: runtime.lastTurn.durationMs,
      syntheticTest,
      ...(finalDeliveryCapability ? {
        finalDeliveryBindingSha256: finalDeliveryCapability.bindingSha256,
      } : {}),
      ...resultMetadata,
      ...(result.error ? { error: result.error } : {}),
    };
    if (finalDeliveryCapability && channel !== 'telegram') {
      ISSUED_FINAL_DELIVERY_CAPABILITIES.set(completedResult, finalDeliveryCapability);
    }
    if (behaviorDeliveryCapability) {
      ISSUED_BEHAVIOR_DELIVERY_CAPABILITIES.set(
        completedResult, behaviorDeliveryCapability,
      );
    }
    let issuedForDelivery = false;
    if (ingressExecutionKey && ingressMutationAuthority) {
      completeTurnUnderstandingExecution(ingressMutationAuthority, {
        kind: 'prepared', value: preparedTurnUnderstandingValue(completedResult),
      });
      issuedForDelivery = !answer || Boolean(issuedPreparedUnderstandingResult(
        completedResult,
        {
          channel, conversationId, messageId: inboundMessageId,
          sender: person, rawText: userText,
        },
      ));
      ingressExecutionKey = null;
      ingressMutationAuthority = null;
    } else if (ingressExecutionKey) {
      throw new Error('understandingafronding mist actuele ingress mutation-authority');
    } else {
      issuedForDelivery = !answer || issueDeliverableResult(completedResult, {
        kind: 'completed_execute_turn',
      });
    }
    if (!issuedForDelivery) {
      throw new Error('afgeronde beurt mist exacte private issued-result provenance');
    }
    return completedResult;
  };

  runtime.active += 1;
  if (mode === 'chat') runtime.activeInteractive = Number(runtime.activeInteractive || 0) + 1;
  setActivity(mode === 'autonomy' ? 'zoekt zelfstandig iets nuttigs uit' : `praat met ${person}`, {
    turnId,
    channel,
    sender: person,
    text: userText.slice(0, 240),
    startedAt: new Date().toISOString(),
  });
  try {
    if (persistInbound) {
      if (messageContext?.identityTrusted === true && !syntheticTest) upsertPerson(person);
      const previousDeliveredReply = !syntheticTest
        ? recentConversation(conversationId, 1).at(-1) || null : null;
      let inboundMessage = null;
      if (!syntheticTest) {
        inboundMessage = addMessage(channel, person, 'user', text, conversationId);
        inboundMessageId ||= String(inboundMessage.id);
      } else {
        inboundMessageId ||= `synthetic:${turnId}`;
      }
      logTimeline('chat_in', `${person}: ${userText}`, {
        channel,
        conversationId,
        sender: person,
        syntheticTest,
        messageContext,
        replyTo,
      });
      const interactionLesson = syntheticTest ? null : applyInteractionFeedback(userText);
      const causalFeedbackKind = syntheticTest
        ? null : relationshipOutcomeFeedbackKind(userText);
      const socialFeedback = syntheticTest ? null
        : causalFeedbackKind === 'direct_style_appreciation'
          ? {
            kind: 'appreciation', topic: 'direct antwoordresultaat',
            summary: 'Het direct voorafgaande antwoord kreeg expliciete positieve stijlfeedback; behoud alleen de abstracte voorkeur en maak er geen vaste formulering van.',
          }
          : causalFeedbackKind === 'direct_style_correction'
            ? {
              kind: 'correction', topic: 'direct antwoordresultaat',
              summary: 'Het direct voorafgaande antwoord kreeg expliciete negatieve stijlfeedback; stop de tijdelijke lens zonder een vast tegengesteld script te maken.',
            }
            : relationshipFeedbackFromText(userText);
      const storedFeedback = socialFeedback || (interactionLesson ? {
        kind: interactionLesson === 'warmte' ? 'appreciation' : 'correction',
        topic: 'samenwerking',
        summary: interactionLesson === 'warmte'
          ? 'Een korte, concrete reactie landde goed; behoud die beknoptheid en relevantie.'
          : 'De vorige reactie hielp niet; herhaal geen leeg of mislukt antwoord en geef alleen een echte uitkomst of één concreet ontbrekend feit.',
      } : null);
      const exactPriorDelivery = previousDeliveredReply?.role === 'assistant'
        && previousDeliveredReply?.sender === 'Eva'
        && String(previousDeliveredReply?.content || '').trim().length > 0;
      let behaviorOutcomeSettled = false;
      if (storedFeedback && messageContext?.identityTrusted === true) {
        try {
          const recordedFeedback = recordRelationshipFeedback({ person, ...storedFeedback });
          const causalStoredKind = causalFeedbackKind === 'direct_style_appreciation'
            ? 'appreciation' : causalFeedbackKind === 'direct_style_correction'
              ? 'correction' : null;
          // relationship_feedback dedupliceert de abstracte les. Het huidige,
          // vertrouwde inbound-message-id is de unieke causale evidence; een
          // herhaalde les mag daarom opnieuw een latere proef beoordelen.
          const exactFreshFeedback = recordedFeedback?.kind === causalStoredKind;
          if (exactFreshFeedback && exactPriorDelivery && inboundMessage?.id) {
            const feedbackContext = Object.freeze({});
            ISSUED_BEHAVIOR_FEEDBACK_CONTEXTS.set(feedbackContext, Object.freeze({
              person, feedbackId: recordedFeedback.id,
              feedbackKind: recordedFeedback.kind, outcomeType: causalFeedbackKind,
              conversationId,
              inboundContentSha256: createHash('sha256').update(userText).digest('hex'),
              classifierContract: RELATIONSHIP_STYLE_OUTCOME_CONTRACT,
              priorLocalMessageId: String(previousDeliveredReply.id),
              replyToExternalRef: null,
              inboundMessageId: String(inboundMessage.id),
            }));
            try {
              behaviorOutcomeSettled = Boolean(settleBehaviorCanaryFromFeedback({
                feedbackContext,
                operationKey: `behavior-feedback:${inboundMessage.id}`,
              }));
            } finally {
              ISSUED_BEHAVIOR_FEEDBACK_CONTEXTS.delete(feedbackContext);
            }
          }
        } catch {
          // De relatie wordt alleen bijgewerkt voor een reeds bekende identiteit.
        }
      }
      if (!behaviorOutcomeSettled && !causalFeedbackKind
        && messageContext?.identityTrusted === true && exactPriorDelivery && inboundMessage?.id) {
        const feedbackContext = Object.freeze({});
        ISSUED_BEHAVIOR_FEEDBACK_CONTEXTS.set(feedbackContext, Object.freeze({
          person, feedbackId: null, feedbackKind: null, conversationId,
          inboundContentSha256: createHash('sha256').update(userText).digest('hex'),
          classifierContract: RELATIONSHIP_STYLE_OUTCOME_CONTRACT,
          priorLocalMessageId: String(previousDeliveredReply.id),
          replyToExternalRef: null,
          inboundMessageId: String(inboundMessage.id),
        }));
        try {
          settleBehaviorCanaryInconclusive({
            feedbackContext,
            operationKey: `behavior-inconclusive:${inboundMessage.id}`,
          });
        } catch (error) {
          logTimeline('system', `Gedragsproef kon niet fail-closed als onbeslist worden gesloten: ${errMsg(error)}`, {
            internalOnly: true, turnId, behaviorLearning: true,
          });
        } finally {
          ISSUED_BEHAVIOR_FEEDBACK_CONTEXTS.delete(feedbackContext);
        }
      }
    }

    if (!turnIsCurrent()) {
      const settled = settleBoundRetrySupersession(durableCommand, durableGoalEpisode);
      if (settled) {
        durableCommand = settled.workItem;
        durableGoalEpisode = settled.episode;
      }
      return finishTurn('', { superseded: true, yielded: true });
    }

    if (monitoringAdmissionFailure && !resumedMonitoringContract
      && !resumedPreparedDelivery) {
      return finishTurn(
        'Ik kan deze monitoring hier niet veilig met een latere terugmelding starten. Ik heb niets ingepland.',
        { error: monitoringAdmissionFailure, actionFailed: true },
      );
    }

    recordSemanticTurnTimeline({
      semanticTurn, semanticResult, turnId, conversationId,
    });
    const supersededFanoutState = resumedPreparedDelivery?.superseded === true
      ? interpretationReplyFanoutReconciliationState(resumedPreparedDelivery) : null;
    const reconcileSupersededFanout = supersededFanoutState === 'reconcile';
    const uncertainSupersededFanout = supersededFanoutState === 'uncertain';
    if (resumedPreparedDelivery?.invalid
      || (resumedPreparedDelivery?.superseded && !reconcileSupersededFanout)) {
      const currentState = stripRawInterpretationReplyScope(
        durableWorkState(durableCommand),
      );
      const cleanSupersession = resumedPreparedDelivery.superseded
        && !uncertainSupersededFanout;
      if (cleanSupersession) delete currentState.pending_delivery;
      const quarantined = transitionGoalEpisode(durableGoalEpisode.id, {
        state: cleanSupersession ? 'abandoned' : 'blocked',
        ...(cleanSupersession
          ? { deliveryPolicy: 'none', deliveryTarget: '' }
          : {}),
        wakeTs: '',
        checkpoint: uncertainSupersededFanout
          ? 'Aflevering onzeker: transportartefacten bestaan zonder exact fanoutmanifest.'
          : cleanSupersession
            ? 'Niet bezorgd: een nieuwer bericht heeft dit antwoord ingehaald.'
            : 'Niet bezorgd: de verzegelde antwoordbinding is beschadigd.',
        nextAction: cleanSupersession
          ? '' : uncertainSupersededFanout
            ? 'Beoordeel de mogelijk zichtbare aflevering handmatig; verstuur haar niet opnieuw.'
            : 'Herstel of beoordeel de beschadigde antwoordbinding handmatig.',
        sessionRef: turnId,
        work: {
          state: currentState, clearWait: true, nextWakeTs: '', clearLease: true,
          nextStep: cleanSupersession
            ? '' : uncertainSupersededFanout
              ? 'Beoordeel de mogelijk zichtbare aflevering handmatig; verstuur haar niet opnieuw.'
              : 'Herstel of beoordeel de beschadigde antwoordbinding handmatig.',
          lastAction: cleanSupersession
            ? 'antwoord_ingehaald' : uncertainSupersededFanout
              ? 'antwoord_transport_onzeker' : 'antwoord_quarantaine',
          lastError: cleanSupersession
            ? 'prepared_delivery_superseded' : uncertainSupersededFanout
              ? 'prepared_delivery_transport_integrity_uncertain'
              : 'prepared_delivery_integrity',
        },
      });
      durableCommand = quarantined.workItem;
      durableGoalEpisode = quarantined.episode;
      return finishTurn('', {
        actionFailed: true,
        superseded: resumedPreparedDelivery.superseded === true,
        yielded: cleanSupersession,
        error: resumedPreparedDelivery.invalid
          ? 'prepared_delivery_integrity'
          : uncertainSupersededFanout
            ? 'prepared_delivery_transport_integrity_uncertain' : undefined,
      });
    }
    if (semanticTurn.ok && resumedInterpretationRetry
      && Date.now() >= Date.parse(resumedInterpretationRetry.deadlineAt || '')) {
      const expired = handleInterpretationRetryFailure({
        error: 'interpreter_retry_deadline',
      });
      return finishTurn('', {
        understood: true, exhausted: true,
        executionPending: expired?.kind === 'delivery',
      });
    }
    if (((materialSentinel?.required === true && !materialRequestAuthorized)
      || unauthorizedMutatingPlan)
      && !resumedMonitoringContract && !resumedPreparedDelivery) {
      return finishTurn(fixedMaterialAuthorizationReply(), {
        error: 'material_request_identity_required',
        actionFailed: true,
        durableProofMissing: true,
      });
    }
    if (!semanticTurn.ok && materialSentinel?.required === true
      && !resumedMonitoringContract && !resumedPreparedDelivery) {
      let handoffVerified = false;
      if (mode === 'chat' && !syntheticTest
        && (messageContext?.identityTrusted === true
          || trustedVoiceIngress != null)) {
        try {
          persistDurableHandoff({
            reinterpret: true,
            failureClass: 'material_request_uninterpreted',
          });
          handoffVerified = true;
        } catch (error) {
          logTimeline('system', `Materiële opdracht kon na interpretatiefout niet duurzaam worden vastgezet: ${errMsg(error)}`, {
            internalOnly: true, turnId, materialCompletion: true,
          });
        }
      }
      return finishTurn(fixedMaterialIncompleteReply({
        hadVerifiedRead: false, handoffVerified,
      }), {
        error: 'material_request_uninterpreted',
        actionFailed: true,
        durableProofMissing: true,
        durableHandoff: handoffVerified || undefined,
        executionPending: handoffVerified,
      });
    }
    if (!semanticTurn.ok && !resumedMonitoringContract && !resumedPreparedDelivery) {
      return await finishSemanticFailureTurn({
        semanticTurn, channel, mode, syntheticTest, persistDurableHandoff, finishTurn,
        person, conversationId, userText, toolResults, turnContract, turnPlan,
        requiredTools, messageContext, assertTurnCurrent,
        resumedInterpretationRetry, handleInterpretationRetryFailure,
      });
    }
    const unprovenNoPlanCompletion = materialSentinel?.required === true
      && semanticTurn.ok
      && semanticTurn.plans.length === 0
      && semanticTurn.understanding?.firstDecision?.kind === 'reply'
      && claimsCompletedAction(semanticTurn.understanding?.firstDecision?.replyDraft)
      && !explicitlyIncompleteAction(semanticTurn.understanding?.firstDecision?.replyDraft);
    const unresolvedExplicitWorkspaceTarget = semanticTurn.ok
      && semanticTurn.plans.length === 0
      && semanticTurn.understanding?.firstDecision?.kind === 'reply'
      && materialSentinel?.explicitTargetPaths?.length > 0;
    if ((unprovenNoPlanCompletion || unresolvedExplicitWorkspaceTarget)
      && !resumedMonitoringContract && !resumedPreparedDelivery) {
      if (!materialRequestAuthorized) {
        return finishTurn(fixedMaterialAuthorizationReply(), {
          error: 'material_request_identity_required',
          actionFailed: true,
          durableProofMissing: true,
        });
      }
      let handoffVerified = false;
      if (resumedInterpretationRetry) {
        const deferred = handleInterpretationRetryFailure({
          error: 'interpreter_malformed',
          result: { failure: { validation: 'invalid_reply_draft' } },
        });
        handoffVerified = ['retry', 'delivery'].includes(deferred?.kind);
      } else if (mode === 'chat' && !syntheticTest) {
        try {
          persistDurableHandoff({
            reinterpret: true,
            failureClass: 'unproven_no_plan_completion',
          });
          handoffVerified = true;
        } catch (error) {
          logTimeline('system', `Onbewezen uitvoeringsclaim kon niet duurzaam worden vastgezet: ${errMsg(error)}`, {
            internalOnly: true, turnId, materialCompletion: true,
          });
        }
      }
      return finishTurn(fixedMaterialIncompleteReply({
        hadVerifiedRead: false, handoffVerified,
      }), {
        error: 'unproven_no_plan_completion',
        actionFailed: true,
        durableProofMissing: true,
        durableHandoff: handoffVerified || undefined,
        executionPending: handoffVerified,
      });
    }
    if (semanticTurn.ok && resumedInterpretationRetry) {
      const planSha256 = interpretationRetryPlanSha256(semanticTurn);
      if (resumedInterpretationRetry.planSha256
        && resumedInterpretationRetry.planSha256 !== planSha256) {
        const drifted = handleInterpretationRetryFailure({
          error: 'interpreter_retry_plan_drift',
        });
        return finishTurn('', {
          understood: true, executionPending: drifted?.kind === 'delivery',
          actionFailed: true,
        });
      }
      const completeMaterialAuthority = materialObligation?.required !== true
        || (materialObligation.selectedWrites.length > 0
          && materialObligation.deferredRequirementIds.length === 0
          && materialObligation.scopeUnknown !== true);
      if (!completeMaterialAuthority) {
        const deferred = handleInterpretationRetryFailure({
          error: 'material_completion_unproven',
          result: { failure: { validation: 'material_completion_unproven' } },
        });
        return finishTurn(mode === 'chat' ? fixedMaterialIncompleteReply({
          hadVerifiedRead: false,
          handoffVerified: ['retry', 'delivery'].includes(deferred?.kind),
        }) : '', {
          understood: true,
          actionFailed: true,
          durableProofMissing: true,
          durableHandoff: deferred?.kind === 'retry' || undefined,
          executionPending: ['retry', 'delivery'].includes(deferred?.kind),
        });
      }
      const resumed = completeInterpretationRetry(
        durableCommand, durableGoalEpisode, turnId, planSha256,
        semanticTurn.understanding.sha256,
        materialObligation?.required === true ? materialObligation.sha256 : '',
        turnUnderstandingSemanticAuthoritySha256(semanticTurn.understanding),
      );
      durableCommand = resumed.workItem;
      durableGoalEpisode = resumed.episode;
    }
    let initialMaterialHandoff = false;
    if (materialObligation?.required === true && !resumedInterpretationRetry) {
      try {
        persistDurableHandoff({
          reinterpret: true,
          failureClass: 'material_completion_pending',
        });
        const completeMaterialAuthority = materialObligation.selectedWrites.length > 0
          && materialObligation.deferredRequirementIds.length === 0
          && materialObligation.scopeUnknown !== true;
        if (completeMaterialAuthority) {
          const understood = completeInterpretationRetry(
            durableCommand, durableGoalEpisode, turnId,
            interpretationRetryPlanSha256(semanticTurn),
            semanticTurn.understanding.sha256,
            materialObligation.sha256,
            turnUnderstandingSemanticAuthoritySha256(semanticTurn.understanding),
          );
          durableCommand = understood.workItem;
          durableGoalEpisode = understood.episode;
        }
        initialMaterialHandoff = true;
      } catch (error) {
        logTimeline('system', `Materiële opdracht kon niet duurzaam worden vastgezet: ${errMsg(error)}`, {
          internalOnly: true, turnId, materialCompletion: true,
        });
        return finishTurn(fixedMaterialIncompleteReply({
          hadVerifiedRead: false, handoffVerified: false,
        }), {
          error: 'material_completion_handoff_failed',
          actionFailed: true, durableProofMissing: true,
        });
      }
    }
    const activeResidentWork = initialOpenWorkItems[0]
      && (initialOpenWorkItems[0].owner
        || ['user', 'voice', 'telegram', 'web', 'chat']
          .includes(String(initialOpenWorkItems[0].source || '').toLowerCase()));
    const priorMaterialEvidence = activeResidentWork
      && String(initialOpenWorkItems[0].evidence || initialOpenWorkItems[0].result || '').trim()
      && !['', 'werk_bijhouden'].includes(String(initialOpenWorkItems[0].last_action || ''));
    const onlyAdministrativePlans = semanticTurn.plans.every((plan) => (
      ['werk_bijhouden', 'toon_werk', 'signalen_afhandelen'].includes(plan.toolName)
    ));
    const hasPendingSignals = Array.isArray(messageContext?.pendingSignals)
      && messageContext.pendingSignals.length > 0;
    if (!resumedMonitoringContract && !resumedInterpretationRetry && !resumedPreparedDelivery
      && materialObligation?.required !== true
      && activeResidentWork && !priorMaterialEvidence && !hasPendingSignals
      && onlyAdministrativePlans) {
      return finishTurn('', {
        understood: true,
        executionPending: true,
        noPlan: semanticTurn.plans.length === 0,
        noPlanDecision: semanticTurn.understanding.firstDecision.kind,
      });
    }
    const foregroundVoiceHandoff = channel === 'voice' && mode === 'chat'
      && !syntheticTest
      && (semanticTurn.longWork
        || semanticTurn.plans.some((plan) => plan.execution?.mode === 'iterative'));
    if (foregroundVoiceHandoff) {
      if (materialObligation?.required === true) {
        return finishTurn(fixedMaterialIncompleteReply({
          hadVerifiedRead: false,
          handoffVerified: Boolean(initialMaterialHandoff || resumedInterpretationRetry),
        }), {
          understood: true, durableHandoff: true, executionPending: true,
          actionFailed: true, durableProofMissing: true,
        });
      }
      persistDurableHandoff({ understanding: semanticTurn.understanding });
      assertTurnCurrent();
      const reply = await replyInEvaVoice(
        semanticTurn.understanding.firstDecision.replyDraft,
      );
      return finishTurn(reply, { understood: true, durableHandoff: true });
    }
    if (materialObligation?.required === true && semanticTurn.plans.length === 0
      && !resumedMonitoringContract && !resumedPreparedDelivery) {
      const retryOutcome = resumedInterpretationRetry
        ? handleInterpretationRetryFailure({
          error: 'interpreter_malformed',
          result: { failure: { validation: 'material_completion_unproven' } },
        }) : null;
      return finishTurn(fixedMaterialIncompleteReply({
        hadVerifiedRead: false,
        handoffVerified: Boolean(initialMaterialHandoff
          || ['retry', 'delivery'].includes(retryOutcome?.kind)),
      }), {
        understood: true, durableHandoff: true, executionPending: true,
        actionFailed: true, durableProofMissing: true,
      });
    }
    if (semanticTurn.plans.length === 0 && !resumedMonitoringContract
      && !resumedPreparedDelivery) {
      const decision = semanticTurn.understanding.firstDecision;
      let reply = '';
      if (resumedInterpretationRetry && decision.kind === 'cancel') {
        const stopped = abandonInterpretationRetry(
          durableCommand, durableGoalEpisode, 'de verzoeker heeft de opdracht geannuleerd', turnId,
        );
        durableCommand = stopped.workItem;
        durableGoalEpisode = stopped.episode;
        return finishTurn('', {
          understood: true, noPlan: true, noPlanDecision: decision.kind,
          superseded: true, yielded: true,
        });
      }
      if (resumedInterpretationRetry && decision.kind === 'clarify') {
        // Een verduidelijkingsvraag is niet het resultaat van de oorspronkelijke
        // opdracht. Zolang er geen aparte non-terminale afleverreceipt bestaat,
        // blijft deze route bewust fail-closed en kan transport het werk niet
        // als beantwoord/afgerond sluiten.
        const deferred = handleInterpretationRetryFailure({
          error: 'interpreter_malformed',
          result: { failure: { validation: 'clarification_not_terminal' } },
        });
        return finishTurn('', {
          understood: true, noPlan: true, noPlanDecision: decision.kind,
          executionPending: deferred?.kind === 'retry', actionFailed: true,
        });
      }
      if (resumedInterpretationRetry && decision.kind === 'reply') {
        const feedback = replyFeedback(decision.replyDraft || '');
        if (feedback) {
          const deferred = handleInterpretationRetryFailure({
            error: 'interpreter_malformed',
            result: { failure: { validation: 'invalid_reply_draft' } },
          });
          return finishTurn('', {
            understood: true, noPlan: true, noPlanDecision: decision.kind,
            executionPending: deferred?.kind === 'retry', actionFailed: true,
          });
        }
        try {
          const candidate = issueInterpretationReplyCandidate({
            understanding: semanticTurn.understanding,
            item: durableCommand,
            episode: durableGoalEpisode,
            source: 'model_reply_draft',
            executionTurnId: durableExecutionTurn || turnId,
          });
          const prepared = prepareIssuedInterpretationRetryDelivery(
            durableCommand, durableGoalEpisode, candidate, durableExecutionTurn || turnId,
          );
          durableCommand = prepared.workItem;
          durableGoalEpisode = prepared.episode;
          return finishTurn('', {
            understood: true, executionPending: true, preparedRetryDelivery: true,
            noPlan: true, noPlanDecision: decision.kind,
          });
        } catch {
          const deferred = handleInterpretationRetryFailure({
            error: 'interpreter_malformed',
            result: { failure: { validation: 'invalid_reply_draft' } },
          });
          return finishTurn('', {
            understood: true, noPlan: true, noPlanDecision: decision.kind,
            executionPending: deferred?.kind === 'retry', actionFailed: true,
          });
        }
      }
      if (mode === 'chat' && decision.kind === 'cancel') {
        cancelInterpretationRetryForConversation(
          conversationId, 'de verzoeker heeft de opdracht geannuleerd', turnId,
        );
        reply = decision.replyDraft || 'Is goed, laat ik liggen.';
      } else if (mode === 'chat' && decision.kind === 'clarify') {
        const ambiguity = semanticTurn.understanding.ambiguities[0];
        reply = decision.replyDraft
          || (ambiguity ? `Eén ding nog: ${ambiguity}` : 'Wat bedoel je precies?');
      } else if (decision.kind === 'reply') {
        reply = decision.replyDraft || (mode === 'chat' ? 'Vertel.' : '');
      } else if (mode === 'chat' && decision.kind === 'fail_closed') {
        reply = decision.replyDraft
          || 'Wacht ff, hier kies ik nu bewust geen handeling zonder een betrouwbaar plan.';
      }
      if (reply) {
        assertTurnCurrent();
        reply = await replyInEvaVoice(reply);
      }
      const shouldPreserveReply = decision.kind === 'reply'
        && !(mode === 'autonomy' && durableCommand
          && ['open', 'blocked'].includes(String(durableCommand.status || '')));
      return finishTurn(reply, {
        understood: true,
        noPlan: true,
        noPlanDecision: decision.kind,
        preserveAutonomyReply: shouldPreserveReply,
      });
    }

    if (durableInteraction && !recapOnly && !syntheticTest && !durableCommand) {
      assertTurnCurrent();
      durableCommand = continuationWork;
      if (!continuationWork) {
        const silentMonitoring = turnContract.kind === 'monitoring'
          && turnMonitoring?.deliveryRequired === false;
        const created = createDurableWork({
          source: channel,
          owner: person,
          title: `Opdracht: ${userText.slice(0, 210)}`,
          goal: userText,
          nextStep: 'Voer de opdracht uit, controleer het resultaat en geef het bewezen eindresultaat terug.',
          visibleToFamily: true,
          definitionOfDone: turnContract.outcome || userText,
          priority: 75,
        }, {
          originKind: 'user', originChannel: channel,
          originConversationId: conversationId,
          originMessageId: inboundMessageId,
          requester: person,
          deliveryPolicy: silentMonitoring ? 'milestone' : 'required',
          deliveryTarget: silentMonitoring ? null : turnMonitoring?.recipient || person,
        });
        durableCommand = created.workItem;
        durableGoalEpisode = created.episode;
      } else {
        assertTurnCurrent();
        durableGoalEpisode = episodeForWork(durableCommand.id);
        if (durableGoalEpisode) {
          assertTurnCurrent();
          attachEpisodeOrigin(durableGoalEpisode.id, {
            originKind: 'user', originChannel: channel,
            originConversationId: conversationId,
            originMessageId: inboundMessageId,
            requester: person,
          });
        }
      }
    }

    const monitoringProofOptions = turnMonitoringProofOptions(
      turnContract, semanticTurn.understanding, userText, () => durableCommand?.id);
    /**
     * Eén uitvoeringsgrens voor een semantisch gebonden plan. Iedere uitkomst
     * wordt exact eenmaal met de werkelijk gebruikte argumenten geregistreerd.
     * @param {any} plan
     */
    const executeGovernedPlanStep = async (plan, executionContext = null) => {
      assertTurnCurrent();
      const name = plan.toolName;
      let args = plan.args;
      const semanticBinding = governedPlanSemanticBinding(
        plan, executionContext, semanticTurn.understanding,
      );
      let receiptSemanticBinding = materialExecutionTurn
        ? semanticBinding
        : interpretationRetryLedgerBinding(
          semanticBinding, resumedInterpretationRetry,
        );
      const freeAutonomySelectedReadBinding =
        executionContext?.freeAutonomySelectedReadBinding || null;
      let executionStarted = false;
      let providerReplaySettlement = null;
      /** @param {string} output */
      const finishStep = (output, executionStarted = false) => recordToolOutput(
        name, args, output, receiptSemanticBinding, freeAutonomySelectedReadBinding,
        executionContext?.continuationAudit || null, executionStarted,
        providerReplaySettlement,
      );
      const prepared = prepareTurnToolCall({
        name,
        args,
        turnKind: turnContract.kind,
        workId: Number(/** @type {any} */ (durableCommand)?.id),
        toolResults,
        userText,
        person,
        monitoring: monitoringProofOptions,
        monitoringIssuanceContext,
        supersedingMessage: supersedingTelegramMessage,
        semanticBinding,
      });
      args = prepared.args;
      if (prepared.output) return finishStep(prepared.output);
      const freeAutonomyStartBinding = executionContext?.freeAutonomyStartBinding || null;
      const freeAutonomyPrecommit = consumeFreeAutonomyExecutionPrecommit({
        startBinding: freeAutonomyStartBinding,
        selectedReadBinding: freeAutonomySelectedReadBinding,
        plan, args, messageContext, turnId,
        schedulerEpoch: sealedAutonomyInteractionEpoch,
        turnCurrent: turnIsCurrent(),
        cognitiveEpisodeId: cognitiveEpisode.id,
      });
      if (!freeAutonomyPrecommit.ok) {
        return finishStep(rejectedToolOutput(
          name, freeAutonomyPrecommit.reason, 'stale_continuation',
        ));
      }
      const freeAutonomyExplorationAtomicStart = freeAutonomyPrecommit.atomicStart;
      const proofOnly = isIssuedExecutionProofReadback(plan);
      const consumeHomeContextRecoveryPrecommit = () => {
        const binding = issuedHomeContextRecoveryPrecommits.get(plan);
        if (!binding || consumedHomeContextRecoveryPrecommits.has(plan)) return false;
        const valid = homeContextRecoveryPrecommitValid({
          binding, plan, args, name, mode, semanticBinding, toolResults,
          plannedResults: binding.plannedResults,
          issuedPlans: semanticTurn.plans, understanding: semanticTurn.understanding,
          initialOpenWorkIds, pendingSignals: messageContext?.pendingSignals,
          attention: messageContext?.attention, explorationId: boundAutonomyExplorationId,
          syntheticTest, goalEpisode: durableGoalEpisode,
          schedulerEpoch: sealedAutonomyInteractionEpoch, turnId, turnCurrent: turnIsCurrent(),
        });
        if (valid) consumedHomeContextRecoveryPrecommits.add(plan);
        return valid;
      };
      const autonomy = prepareAutonomyPlanStep({
        mode, name, args, plan, lane: autonomyLane, toolResults, initialOpenWorkIds,
        pendingSignals: messageContext?.pendingSignals,
        explorationId: boundAutonomyExplorationId, syntheticTest,
        goalEpisode: durableGoalEpisode, proofOnly,
        consumeHomeContextRecoveryPrecommit,
      });
      autonomyLane = autonomy.lane;
      if (autonomy.output) return finishStep(autonomy.output);
      if (autonomy.command) {
        durableCommand = autonomy.command; durableGoalEpisode = autonomy.episode;
        boundAutonomyWorkId = durableCommand.id;
      }
      const issuedIterativeChild = issuedIterativeChildPlan(plan);
      // Iedere direct geselecteerde requirement is al begrensd door één
      // immutable TurnUnderstanding (maximaal twaalf actions). De eerste
      // uitvoering van zo'n exact plan mag daarom niet botsen met een andere,
      // semantisch onderscheiden requirement die toevallig dezelfde toolnaam
      // gebruikt. Een replay van hetzelfde plan blijft wél meetellen; de
      // per-signaturegrens hieronder blijft daarnaast identieke calls stoppen.
      const firstIssuedDirectPlan = !issuedIterativeChild
        && semanticTurn.plans.includes(plan) && !attemptedDirectPlans.has(plan);
      if (firstIssuedDirectPlan) attemptedDirectPlans.add(plan);
      const nameAttempt = globalToolNameAttempt(
        toolNameAttempts.get(name), issuedIterativeChild || firstIssuedDirectPlan,
        attemptLimitFor(name),
      );
      toolNameAttempts.set(name, nameAttempt.attempts);
      if (nameAttempt.exceeded) {
        return finishStep(rejectedToolOutput(name,
          `${name} is in deze beurt al vaak genoeg geprobeerd. Rond af met het beschikbare bewijs of bewaar een concrete volgende stap.`,
          'attempt_limit'));
      }
      const signature = `${name}:${canonicalJson(args || {})}`;
      if (liveInspection && toolEffect(name, args) === 'read' && toolResults.some((tool) => (
        tool.ok !== true && `${tool.name}:${canonicalJson(tool.args || {})}` === signature
      ))) {
        return finishStep(rejectedToolOutput(name,
          'Deze exacte uitlezing mislukte al. Kies nu een andere actuele bron, entiteit, query of veilige read-capability.'));
      }
      const attempts = Number(toolAttempts.get(signature) || 0) + 1;
      toolAttempts.set(signature, attempts);
      if (attempts > (issuedIterativeChild ? 4 : 2)) {
        return finishStep(rejectedToolOutput(name,
          'Dezelfde gereedschapsaanroep heeft de begrensde poginglimiet bereikt. Gebruik het bestaande resultaat of kies een andere concrete stap.',
          'attempt_limit'));
      }
      const messageGateOutput = autonomousMessageGate({
        mode, syntheticTest, name, args, toolResults, durableGoalEpisode,
      });
      if (messageGateOutput) return finishStep(messageGateOutput);
      const resolvedGoalPlan = resolveSelfAttentionGoalPlanStep({
        semanticTurn, plan, args, semanticBinding, receiptSemanticBinding,
        resumedInterpretationRetry, assertCurrent: assertTurnCurrent,
      });
      args = resolvedGoalPlan.args;
      receiptSemanticBinding = resolvedGoalPlan.receiptSemanticBinding;
      setActivity(`voert ${plan.capabilityId} uit`, { ...runtime.current, tool: name });
      const output = await executeForTurn(name, args, {
        mode,
        channel,
        sender: person,
        conversationId,
        originMessageId: inboundMessageId,
        turnId,
        issuedPlan: plan,
        replyEvidence,
        replyTo,
        messageContext: resumedInterpretationRetry
          ? { ...messageContext, isCurrent: turnIsCurrent }
          : messageContext,
        userText,
        faceIdentityClaim: ISSUED_IDENTITY_CONTEXT_CLAIMS.get(plan) || null,
        turnUnderstanding: semanticTurn.understanding,
        artifactReceipts: ['continue', 'correct'].includes(semanticTurn.understanding?.dialogueAct)
          ? /** @type {any} */ (semanticResult).artifactReceipts || [] : [],
        semanticBinding,
        semanticRole: executionContext?.semanticRole,
        monitoringExecutionTurnId: executionContext?.monitoringExecutionTurnId,
        priorToolResults: toolResults,
        offeredToolNames: [...turnPlan.allowedToolNames],
        activeWorkId: boundAutonomyWorkId,
        activeGoalEpisodeId: durableGoalEpisode?.id || Number(messageContext?.goalEpisodeId) || null,
        activeExplorationId: boundAutonomyExplorationId,
        cognitiveEpisodeId: cognitiveEpisode.id,
        markExecutionStarted: () => { executionStarted = true; },
        markProviderReplaySettlement: (profile) => {
          if (profile === SELF_ATTENTION_EXECUTION_PROFILES.get(semanticTurn)) {
            providerReplaySettlement = profile;
          }
        },
        ...(freeAutonomyExplorationAtomicStart
          ? { freeAutonomyExplorationAtomicStart } : {}),
        ...terminalExecutionContext(
          explorationTerminalContributionPrecommit,
          explorationTerminalContributionExecution,
          plan,
        ),
        ...(prepared.trustedWorkState ? { trustedWorkState: prepared.trustedWorkState } : {}),
        ...(executionContext ? {
          deadlineTs: executionContext.deadlineTs,
          timeoutMs: executionContext.timeoutMs,
          effectPolicy: executionContext.effectPolicy,
        } : {}),
      });
      const recorded = finishStep(output, executionStarted);
      if (refreshedRetrySuperseded()) {
        throw Object.assign(new Error('deze hersteluitvoering is ingehaald'), {
          code: 'EVA_TURN_SUPERSEDED',
        });
      }
      assertTurnCurrent();
      return recorded;
    };

    const runIssuedExecutionPlan = async (plan, candidatePlans = []) => {
      const selfAttentionArtifact = SELF_ATTENTION_EXECUTION_PROFILES.get(semanticTurn)?.write === plan;
      const standardExecutionBudget = iterativeExecutionBudget(mode, config.autonomyTurnMinutes);
      const executionBudget = selfAttentionArtifact
        ? { maxRounds: 2, baseRounds: 2, progressExtensionRounds: 0,
          totalTimeoutMs: Math.min(240_000, standardExecutionBudget.totalTimeoutMs) }
        : standardExecutionBudget;
      const handles = executionHandlesForPlan(plan);
      const groundedDependencies = executionGroundedDependencies(toolResults);
      const offeredHandles = [...handles.proof, ...handles.recovery];
      const issuedReceipts = [];
      const handleFor = (lane, type) => offeredHandles.find((handle) => (
        handle.alias === `execution_${lane}_${type}`
      ));
      const targetIsExecutable = executableWorkspaceArtifact(plan.args?.pad);
      const implementationSpec = [
        userText,
        `Werk uitsluitend aan het uitgegeven doel ${handles.target.path}.`,
        targetIsExecutable
          ? 'Bewijsgrens van de host: materialiseer het echte programma op exact dat doel en schrijf in dezelfde map een afzonderlijke gerichte proef plus benodigde fixtures. Voer na de relevante writes zowel die proef als het echte programma met representatieve invoer uit; hun onderlinge volgorde is vrij. Lees daarna zowel het doelbestand als het proefbestand exact terug. Herstel een concrete mislukking binnen dezelfde uitgegeven bestanden en controleer opnieuw.'
          : 'Bewijsvolgorde van de host: materialiseer het echte eindartefact op exact dat doel en lees datzelfde bestand daarna terug.',
        'Toolargumenten bevatten het werk zelf, nooit een beschrijving van code of inhoud die later nog gemaakt moet worden. Gebruik bij uitvoeren de doelmap als werkmap en bestandsnamen relatief aan die map.',
      ].join('\n\n');
      const status = () => iterativeArtifactStatus(toolResults, plan);
      let expectedObligationForRound = null;
      const selectedRequirementIds = semanticTurn.understanding.firstDecision.requirementIds;
      const consumedCandidateRequirements = () => executionGroupConsumptions({
        parentPlan: plan,
        candidatePlans,
        receiptContext: issuedReceipts,
        understandingSha256: semanticTurn.understanding.sha256,
        requirementIds: selectedRequirementIds,
      });
      const nextCandidateObligation = () => {
        const consumed = new Set(consumedCandidateRequirements().map((item) => item.requirementId));
        for (const candidate of candidatePlans) {
          if (consumed.has(candidate.requirementId)) continue;
          const obligation = executionObligationForPlan(plan, candidate, {
            receiptContext: issuedReceipts,
          });
          return obligation ? { candidate, obligation } : null;
        }
        return null;
      };
      const result = /** @type {any} */ (await runExecutionDriver({
        implementationSpec,
        understandingSummary: {
          contract: semanticTurn.understanding.contract,
          sha256: semanticTurn.understanding.sha256,
          goal: semanticTurn.understanding.goal,
          conditions: semanticTurn.understanding.conditions,
          requirement: {
            id: plan.requirementId,
            capabilityId: plan.capabilityId,
            operationId: plan.operationId,
            effect: plan.effect,
          },
          executionTarget: handles.target,
          groundedDependencies,
        },
        offeredHandles,
        maxRounds: executionBudget.maxRounds,
        baseRounds: executionBudget.baseRounds,
        progressExtensionRounds: executionBudget.progressExtensionRounds,
        model: config.model, effort: config.reasoningCode,
        fallbackModel: selfAttentionArtifact ? null : config.modelVoice,
        fallbackEffort: config.reasoningEffort,
        cacheKey: 'eva-issued-execution', roundTimeoutMs: 120_000,
        totalTimeoutMs: executionBudget.totalTimeoutMs,
        maxOutputTokens: selfAttentionArtifact ? 2_000 : null,
        rateLimitRetries: selfAttentionArtifact ? 0 : 1,
        transientRetries: 0,
        authenticationRetries: selfAttentionArtifact ? 0 : 1,
        recoveryResolved: iterativeRecoveryResolved,
        shouldYield: () => mode === 'autonomy'
          ? runtime.interactivePending > 0
          : !turnIsCurrent(),
        obligations: (context) => {
          const current = status();
          if (current.complete) return [];
          expectedObligationForRound = null;
          if (!current.sourceWrite) {
            const obligation = executionObligationForPlan(plan, plan, {
              receiptContext: issuedReceipts,
            });
            expectedObligationForRound = obligation;
            return [obligation.descriptor];
          }
          const candidate = nextCandidateObligation();
          if (candidate && !context.recovery) {
            expectedObligationForRound = candidate.obligation;
            return [candidate.obligation.descriptor];
          }
          const obligation = iterativeExecutionObligation(context, current);
          const handle = handleFor(obligation.lane, obligation.type);
          if (!handle) return [];
          return [{
            alias: handle.alias,
            ...(obligation.type === 'readback' && current.readTarget
              ? { expectedInput: { pad: current.readTarget } }
              : {}),
          }];
        },
        // Technische artefactstatus alleen is niet genoeg: expliciet gekozen,
        // compatibele siblings moeten door exacte receipts zijn geconsumeerd.
        // Anders kan herstel de parent vroeg klaar verklaren en voert de
        // buitenlus dezelfde test of read daarna nogmaals uit.
        done: () => status().complete && !nextCandidateObligation(),
        execute: async (handle, semanticInput, executionContext) => {
          let childPlan;
          try {
            childPlan = bindExecutionHandle(handle, semanticInput, {
              receiptContext: issuedReceipts,
              expectedObligation: expectedObligationForRound,
            });
          } catch (error) {
            if (error?.code === 'EVA_EXECUTION_OBLIGATION_MISMATCH') throw error;
            return {
              contract: 'ActionResult/v1', ok: false, changed: false, verified: false,
              needs_followup: true, partial: false, failure_class: 'policy',
              retryable: false, effect_uncertain: false,
              error: errMsg(error),
            };
          }
          const recorded = await executeGovernedPlanStep(childPlan, executionContext);
          issuedReceipts.push({ plan: childPlan, result: recorded });
          return executionActionResult(recorded);
        },
      }));
      const finalStatus = status();
      iterativeExecutionComplete = result.status === 'completed' && finalStatus.complete;
      if (finalStatus.complete && durableCommand) {
        const state = durableWorkState(durableCommand);
        state.artifact_receipts = [['source', finalStatus.sourceWrite], ['test', finalStatus.testWrite]]
          .filter(([, item]) => item).map(([role, { tool }]) => ({
            path: writtenArtifactPath(tool), sha256: actionData(tool)?.material_sha256, role,
            understandingSha256: semanticTurn.understanding.sha256, requirementId: plan.requirementId,
          }));
        durableCommand = updateDurableWork(durableCommand.id, { state });
      }
      const observedConsumptions = consumedCandidateRequirements();
      const consumptions = iterativeExecutionComplete
        ? observedConsumptions
        : [];
      logTimeline('execution_trace', `Gebonden uitvoering: ${result.status}`,
        issuedExecutionTraceMetadata({
          result,
          turnId,
          understandingSha256: semanticTurn.understanding.sha256,
          requirementId: plan.requirementId,
          observedConsumptions,
          consumptions,
        }));
      return { ...result, consumptions };
    };

    const preparedDelivery = mode === 'autonomy'
      ? pendingDeliveryForWork(durableCommand, durableGoalEpisode) : null;
    if (preparedDelivery) {
      const delivered = await executePendingDeliveryResume(preparedDelivery, {
        mode, person, turnId, messageContext, userText, toolResults, executeForTurn, recordToolOutput,
        toolNameAttempts, toolAttempts, setActivity, currentActivity: runtime.current, activeWorkId: boundAutonomyWorkId,
        goalEpisodeId: durableGoalEpisode?.id || Number(messageContext?.goalEpisodeId) || null,
        goalEpisode: durableGoalEpisode,
        activeExplorationId: boundAutonomyExplorationId, cognitiveEpisodeId: cognitiveEpisode.id,
      });
      if (delivered.superseded) {
        const state = stripRawInterpretationReplyScope(
          durableWorkState(durableCommand),
        );
        delete state.pending_delivery;
        const deliveryUncertain = delivered.action?.effect_uncertain === true;
        const stopped = transitionGoalEpisode(durableGoalEpisode.id, {
          state: deliveryUncertain ? 'blocked' : 'abandoned',
          ...(deliveryUncertain ? {} : { deliveryPolicy: 'none', deliveryTarget: '' }),
          wakeTs: '',
          checkpoint: deliveryUncertain
            ? 'Aflevering onzeker: het ingehaalde antwoord kon niet aantoonbaar worden ingetrokken.'
            : 'Niet bezorgd: een nieuwer bericht heeft dit antwoord ingehaald.',
          nextAction: deliveryUncertain
            ? 'Beoordeel de mogelijk zichtbare aflevering handmatig; verstuur haar niet opnieuw.' : '',
          sessionRef: turnId,
          work: {
            state, clearWait: true, nextWakeTs: '', clearLease: true,
            nextStep: deliveryUncertain
              ? 'Beoordeel de mogelijk zichtbare aflevering handmatig; verstuur haar niet opnieuw.' : '',
            lastAction: deliveryUncertain
              ? 'antwoord_aflevering_onzeker' : 'antwoord_ingehaald',
            lastError: deliveryUncertain
              ? 'prepared_delivery_retraction_uncertain' : 'prepared_delivery_superseded',
          },
        });
        durableCommand = stopped.workItem;
        durableGoalEpisode = stopped.episode;
        return finishTurn('', {
          toolFinal: true, deliveryOnly: true,
          superseded: true, yielded: !deliveryUncertain, actionFailed: true,
          ...(deliveryUncertain ? { error: 'prepared_delivery_retraction_uncertain' } : {}),
        });
      }
      return finishTurn(delivered.reply, {
        toolFinal: true,
        deliveryOnly: true,
        preserveAutonomyReply: Boolean(delivered.reply),
      });
    }

    if (resumedMonitoringContract && durableCommand && durableGoalEpisode) {
      const resumed = await executeMonitoringResume({
        contract: resumedMonitoringContract, command: durableCommand, episode: durableGoalEpisode,
        monitoringRuntime: { turnId, syntheticTest, person, channel, mode,
          executePlan: executeGovernedPlanStep, present: replyInEvaVoice },
        deliveryRuntime: { mode, person, turnId, messageContext, userText, toolResults, executeForTurn,
          recordToolOutput, toolNameAttempts, toolAttempts, activeWorkId: boundAutonomyWorkId,
          activeExplorationId: boundAutonomyExplorationId, cognitiveEpisodeId: cognitiveEpisode.id },
      });
      durableGoalEpisode = resumed.episode; durableCommand = resumed.workItem;
      return finishTurn(resumed.reply, {
        toolFinal: Boolean(resumed.delivery), understood: semanticTurn.ok,
        deliveryOnly: Boolean(resumed.delivery),
        preserveAutonomyReply: Boolean(resumed.delivery && resumed.reply),
        monitoringDecision: resumed.decision, actionFailed: resumed.decision === 'retry',
      });
    }
    if (semanticTurn.plans.length) {
      const plannedResults = [];
      const aspirationExplorationAnchor = semanticTurn.aspirationExplorationAnchor;
      const consumedRequirements = new Map();
      const pendingReadRecoveries = [];
      // Een inspectiebeurt is geen vooraf dichtgetimmerde reeks calls. Na elke
      // waarneming beoordeelt dezelfde Eva of het oorspronkelijke doel echt is
      // beantwoord, of dat één andere read/discovery-stap nodig is. Dit is de
      // gewone model→tool→model-lus; er staat hier bewust geen domeinvraag,
      // woordlijst of Home-Assistant-uitzondering in.
      const semanticReadInvestigation = mode === 'chat'
        && semanticTurn.understanding.firstDecision.kind === 'inspect';
      const readInvestigationDeadlineTs = semanticReadInvestigation
        ? Date.now() + 45_000 : null;
      const readInvestigation = semanticReadInvestigation ? {
        executions: 0,
        maxExecutions: 4,
        signatures: new Set(),
      } : null;
      const planSignature = (plan) => `${plan?.capabilityId || ''}\u0000${plan?.operationId || ''}`
        + `\u0000${canonicalJson(plan?.args || {})}`;
      const reserveInvestigationRead = (plan) => {
        if (!readInvestigation) return { ok: true, reason: '' };
        const signature = planSignature(plan);
        if (readInvestigation.signatures.has(signature)) {
          return { ok: false, reason: 'no_progress' };
        }
        if (readInvestigation.executions >= readInvestigation.maxExecutions) {
          return { ok: false, reason: 'round_limit' };
        }
        readInvestigation.signatures.add(signature);
        readInvestigation.executions += 1;
        return { ok: true, reason: '' };
      };
      const bindRecoveryPlan = (action) => planCapabilityAction(action, { mode, interactiveAuthority: 'ambient',
        fullAutonomy: config.autonomyFull, synthetic: syntheticTest, effectPolicy: turnPlan.effectPolicy,
        syntheticNamespace: semanticTurn.understanding.source.turnId, understandingSha256: semanticTurn.understanding.sha256 });
      let monitoringContextBindingsChecked = false;
      let monitoringContextBindings = null;
      const deterministicMonitoringContextBindings = () => {
        if (monitoringContextBindingsChecked) return monitoringContextBindings;
        monitoringContextBindingsChecked = true;
        const monitoring = semanticTurn.monitoring?.requested === true
          ? semanticTurn.monitoring : null;
        const discoveryRequirementId = String(
          monitoring?.sourceDiscoveryRequirementId || '',
        ).trim();
        const entityRequirementIds = [...new Set((
          Array.isArray(monitoring?.sourceEntityReadRequirementIds)
            ? monitoring.sourceEntityReadRequirementIds : []
        ).map(String))];
        if (!discoveryRequirementId || !entityRequirementIds.length
          || entityRequirementIds.length > 8
          || monitoring?.sourceUnderstandingSha256 !== semanticTurn.understanding.sha256) return null;
        const pendingCandidates = plannedResults.filter((entry) => (
          entityRequirementIds.includes(String(entry?.plan?.requirementId || ''))
          && entry?.deferredBinding === true
          && entry?.plan?.capabilityId === 'home.entity.read'
          && entry?.plan?.operationId === 'read'
          && hasUnresolvedReference(entry?.plan?.args)
        ));
        if (pendingCandidates.length !== entityRequirementIds.length
          || new Set(pendingCandidates.map((entry) => entry.plan.requirementId)).size
            !== entityRequirementIds.length
          || entityRequirementIds.some((requirementId) => !pendingCandidates.some((entry) => (
            entry.plan.requirementId === requirementId
          )))) return null;
        const pendingByRequirement = new Map(pendingCandidates.map((entry) => (
          [entry.plan.requirementId, entry]
        )));
        const pending = entityRequirementIds.map((requirementId) => (
          pendingByRequirement.get(requirementId)
        ));
        const discoveryEntries = plannedResults.filter((entry) => (
          entry?.plan?.requirementId === discoveryRequirementId
          && entry?.plan?.capabilityId === 'home.context.read'
          && entry?.plan?.operationId === 'read'
          && entry?.plan?.effect === 'read'
          && entry?.completed === true
          && entry?.recorded?.name === 'huis_status'
          && entry.recorded.args === entry.plan.args
          && verifiedCompleteReceipt(entry.recorded)
          && entry.recorded.changed === false
          && entry.recorded.effect_uncertain !== true
          && entry.recorded.semanticBinding?.understandingSha256
            === semanticTurn.understanding.sha256
          && entry.recorded.semanticBinding?.requirementId === discoveryRequirementId
          && entry.recorded.semanticBinding?.capabilityId === 'home.context.read'
          && entry.recorded.semanticBinding?.operationId === 'read'
        ));
        if (discoveryEntries.length !== 1
          || pending.some((entry) => plannedResults.indexOf(entry)
            <= plannedResults.indexOf(discoveryEntries[0]))) return null;
        const discovery = discoveryEntries[0].recorded;
        if (toolResults.filter((receipt) => receipt === discovery).length !== 1
          || Object.hasOwn(discovery.args || {}, 'historie')) return null;
        let result = null;
        try { result = JSON.parse(String(discovery.output || '')); } catch { return null; }
        if (result?.contract !== 'ActionResult/v1' || result.tool !== 'huis_status'
          || !verifiedCompleteReceipt(result) || result.changed !== false
          || result.effect_uncertain === true || result.data?.history_required === true
          || !Array.isArray(result.data?.entities)) return null;
        const hostReferenceTexts = Array.isArray(monitoring.sourceReferenceTexts)
          && monitoring.sourceReferenceTexts.length === pending.length
          ? monitoring.sourceReferenceTexts.map((value) => String(value || '').trim()) : null;
        const references = pending.map((entry, index) => {
          const action = semanticTurn.understanding.actions.find((candidate) => (
            candidate.requirementId === entry.plan.requirementId
            && candidate.capabilityId === 'home.entity.read'
            && candidate.operationId === 'read'
          ));
          if (!action || !hasUnresolvedReference(action.semanticInput?.entityId)) return null;
          const sourceText = hostReferenceTexts?.[index] || (Array.isArray(action.sourceSpans)
            ? action.sourceSpans : [])
            .flatMap((span) => Number.isInteger(span?.start) && Number.isInteger(span?.end)
              && span.start >= 0 && span.end > span.start && span.end <= userText.length
              ? [userText.slice(span.start, span.end)] : [])
            .join(' ');
          return {
            requirementId: action.requirementId,
            sourceText,
          };
        });
        if (references.some((reference) => reference == null)) return null;
        const bindings = bindHomeContextEntityReferences(references, result.data.entities);
        if (!bindings) return null;
        const basisIndex = toolResults.indexOf(discovery);
        if (basisIndex < 0) return null;
        monitoringContextBindings = new Map(bindings.map((binding) => [
          binding.requirementId,
          Object.freeze({ ...binding, basisReceiptId: `s${basisIndex + 1}` }),
        ]));
        return monitoringContextBindings;
      };
      const recoverReadEntry = async (entry, pendingSnapshot) => {
        entry.investigationRequired = semanticReadInvestigation;
        if (entry.investigationComplete === true) return true;
        if (!semanticReadInvestigation && verifiedCompleteReceipt(entry.adaptiveRecorded)) return true;
        if (BOUNDED_SELF_ATTENTION_TURNS.has(semanticTurn)) return false;
        if (semanticReadInvestigation
          && Number(readInvestigationDeadlineTs) - Date.now() < 500) {
          entry.investigationFailure = 'deadline';
          return false;
        }
        const deterministicBinding = !semanticReadInvestigation
          ? deterministicMonitoringContextBindings()?.get(entry.plan.requirementId) : null;
        if (deterministicBinding && !verifiedCompleteReceipt(entry.adaptiveRecorded)) {
          const originalAction = semanticTurn.understanding.actions.find((action) => (
            action.requirementId === entry.plan.requirementId
            && action.capabilityId === 'home.entity.read'
            && action.operationId === 'read'
          ));
          const reboundPlan = originalAction ? bindRecoveryPlan({
            ...originalAction,
            semanticInput: {
              ...originalAction.semanticInput,
              entityId: deterministicBinding.entityId,
            },
          }) : null;
          if (reboundPlan && !hasUnresolvedReference(reboundPlan.args)) {
            entry.adaptivePlan = reboundPlan;
            entry.adaptiveRecorded = await executeGovernedPlanStep(reboundPlan, {
              semanticRole: 'monitoring_binding',
              addressesRequirementIds: [entry.plan.requirementId],
              basisReceiptIds: [deterministicBinding.basisReceiptId],
            });
            if (verifiedCompleteReceipt(entry.adaptiveRecorded)) {
              entry.investigationComplete = true;
              return true;
            }
          }
        }
        const contextPlan = semanticReadInvestigation
          ? null : bindHomeContextRecoveryPlan(entry.plan, entry.recorded);
        if (contextPlan && !verifiedCompleteReceipt(entry.adaptiveRecorded)) {
          const exactUnboundFreeIngress = mode === 'autonomy' && syntheticTest !== true
            && Number.isSafeInteger(sealedAutonomyInteractionEpoch)
            && messageContext?.attention == null && messageContext?.workId == null
            && messageContext?.goalEpisodeId == null
            && Array.isArray(messageContext?.pendingSignals)
            && messageContext.pendingSignals.length === 0
            && initialOpenWorkIds.length === 0 && boundAutonomyExplorationId == null
            && durableGoalEpisode == null && semanticTurn.plans.length === 1
            && semanticTurn.plans[0] === entry.plan
            && plannedResults.length === 1 && plannedResults[0] === entry
            && pendingSnapshot?.length === 1 && pendingSnapshot[0] === entry
            && toolResults.length === 1 && toolResults[0] === entry.recorded
            && turnIsCurrent();
          if (exactUnboundFreeIngress) issuedHomeContextRecoveryPrecommits.set(
            contextPlan,
            Object.freeze({
              plan: contextPlan, args: contextPlan.args,
              parentPlan: entry.plan, parentReceipt: entry.recorded, entry,
              pendingSnapshot: Object.freeze([...pendingSnapshot]),
              plannedResults, understanding: semanticTurn.understanding, turnId,
            }),
          );
          entry.adaptivePlan = contextPlan;
          entry.adaptiveRecorded = await executeGovernedPlanStep(
            contextPlan, { semanticRole: 'recovery',
              basisReceiptIds: [`s${toolResults.indexOf(entry.recorded) + 1}`] });
          if (!semanticReadInvestigation) return verifiedCompleteReceipt(entry.adaptiveRecorded);
        }
        const seenPlans = new Set([entry.plan, entry.adaptivePlan]
          .filter(Boolean).map(planSignature));
        const maxDecisions = semanticReadInvestigation
          ? Math.max(1, readInvestigation.maxExecutions - readInvestigation.executions + 1)
          : 1;
        for (let round = 0; round < maxDecisions; round += 1) {
          const remainingMs = semanticReadInvestigation
            ? Number(readInvestigationDeadlineTs) - Date.now()
            : mode === 'autonomy' ? 30_000 : 20_000;
          if (remainingMs < 500) {
            entry.investigationFailure = 'deadline';
            return false;
          }
          const step = await decideTurnStep({
            understanding: semanticTurn.understanding,
            sourceText: userText,
            openRequirementIds: [entry.plan.requirementId],
            evidence: turnStepEvidence(toolResults, semanticTurn.understanding.sha256),
          }, {
            capabilityCatalog: capabilityCatalog(),
            identityInstructions: buildRedactedInstructions({
              sender: person,
              channel,
              mode,
              familyContext: householdContextText({ subject: person, query: userText }),
              extra: [selfContinuityContextText({ mode, query: userText }), moodActionGuidance()]
                .filter(Boolean).join('\n'),
            }),
            timeoutMs: Math.min(mode === 'autonomy' ? 30_000 : 20_000, remainingMs),
            complete: (request) => chatComplete({
              ...request,
              model: config.modelVoice,
              effort: mode === 'autonomy' ? config.reasoningAutonomy : 'low',
              cacheKey: 'eva-turn-step',
              rateLimitRetries: 0,
              transientRetries: 1,
              authenticationRetries: 1,
            }),
          });
          if (!step.ok) {
            entry.investigationFailure = step.failure || 'invalid_step';
            return false;
          }
          entry.adaptiveDecision = step.decision;
          if (step.decision.kind === 'complete') {
            entry.investigationComplete = true;
            return true;
          }
          if (step.decision.kind !== 'execute') {
            entry.investigationFailure = step.decision.reasonCode || 'blocked';
            return false;
          }
          const adaptivePlan = bindRecoveryPlan(step.decision.action);
          if (!adaptivePlan) {
            entry.investigationFailure = 'no_progress';
            return false;
          }
          if (semanticReadInvestigation) {
            const reservation = reserveInvestigationRead(adaptivePlan);
            if (!reservation.ok) {
              entry.investigationFailure = reservation.reason;
              return false;
            }
          } else if (seenPlans.has(planSignature(adaptivePlan))) {
            entry.investigationFailure = 'no_progress';
            return false;
          } else {
            seenPlans.add(planSignature(adaptivePlan));
          }
          entry.adaptivePlan = adaptivePlan;
          entry.adaptiveRecorded = await executeGovernedPlanStep(adaptivePlan, {
            semanticRole: 'recovery',
            addressesRequirementIds: step.decision.addressesRequirementIds,
            basisReceiptIds: step.decision.basisReceiptIds,
          });
          if (entry.adaptiveRecorded?.effect_uncertain === true) {
            entry.investigationFailure = 'effect_uncertain';
            return false;
          }
          if (!semanticReadInvestigation && verifiedCompleteReceipt(entry.adaptiveRecorded)) {
            entry.investigationComplete = true;
            return true;
          }
        }
        entry.investigationFailure = 'round_limit';
        return false;
      };
      const recoverPendingReads = async (nextPlan = null) => {
        const onlyRouteFailures = pendingReadRecoveries.every(({ plan, recorded }) => (
          safeReadRouteFailure(plan, recorded)
        ));
        if (onlyRouteFailures && safeReadFailuresCanPreserveIssuedAspirationPersistence({
          understanding: semanticTurn.understanding,
          pending: pendingReadRecoveries,
          nextPlan,
          issuedPlans: semanticTurn.plans,
          boundedSelfAttention: BOUNDED_SELF_ATTENTION_TURNS.has(semanticTurn),
          attentionKind: messageContext?.attention?.kind || null,
        })) return true;
        const mayYield = onlyRouteFailures && !BOUNDED_SELF_ATTENTION_TURNS.has(semanticTurn)
          && safeReadFailuresCanYieldToIssuedWorkspace(
            semanticTurn.understanding, pendingReadRecoveries, nextPlan,
          );
        while (pendingReadRecoveries.length) {
          const pendingSnapshot = [...pendingReadRecoveries];
          const entry = pendingReadRecoveries.shift();
          if (mayYield) continue;
          if (!(await recoverReadEntry(entry, pendingSnapshot))) return false;
        }
        return true;
      };
      let effectStageStarted = false, plansHalted = false;
      /** @type {any} */
      let freeAutonomyContinuation = null;
      let freeAutonomyContinuationStopped = false;
      for (let planIndex = 0; planIndex < semanticTurn.plans.length; planIndex += 1) {
        const plan = semanticTurn.plans[planIndex];
        if (freeAutonomyContinuation?.choice === 'start_exploration'
          && !freeAutonomyContinuation.selectedPlans.includes(plan)) continue;
        if (aspirationExplorationAnchor?.nextPlan === plan
          && !aspirationExplorationAnchorStillCurrent(
            aspirationExplorationAnchor,
            aspirationExplorationSettlement,
            boundAutonomyExplorationId,
          )) {
          plansHalted = true;
          break;
        }
        const signalAssessment = plan.capabilityId === 'signal.assess';
        if (signalAssessment && (!(await recoverPendingReads(plan))
          || !plannedResults.every(plannedResultComplete)
          || !ensureAutonomyCheckpointAfterReport())) { plansHalted = true; break; }
        if (signalAssessment) checkpointAutonomyProgress();
        const consumed = consumedRequirements.get(plan.requirementId);
        if (consumed) {
          plannedResults.push({
            plan,
            recorded: consumed.recorded,
            completed: true,
            consumption: consumed,
          });
          continue;
        }
        const observation = ['read', 'discovery'].includes(plan.effect);
        if (!observation && !signalAssessment && !(await recoverPendingReads(plan))) {
          plansHalted = true; break;
        }
        if (!observation) effectStageStarted = true;
        if (plan.execution?.mode === 'iterative') {
          const execution = await runIssuedExecutionPlan(
            plan,
            semanticTurn.plans.slice(planIndex + 1),
          );
          const recorded = toolResults.at(-1) || {
            name: plan.toolName, args: plan.args, output: '', ok: false,
            verified: false, changed: false,
          };
          const completed = execution.status === 'completed'
            && iterativeArtifactStatus(toolResults, plan).complete;
          plannedResults.push({ plan, recorded, execution, completed });
          if (!completed) break;
          for (const consumption of execution.consumptions) {
            consumedRequirements.set(consumption.requirementId, consumption);
          }
          continue;
        }
        const deferredBinding = observation && hasUnresolvedReference(plan.args);
        if (semanticReadInvestigation && observation && !deferredBinding) {
          const reservation = reserveInvestigationRead(plan);
          if (!reservation.ok) {
            plannedResults.push({
              plan,
              recorded: {
                name: plan.toolName, args: plan.args, output: '', ok: false,
                verified: false, changed: false, needs_followup: true, partial: false,
                failure_class: 'attempt_limit', retryable: false, effect_uncertain: false,
              },
              completed: false,
              deferredBinding: false,
              investigationRequired: true,
              investigationFailure: reservation.reason,
            });
            plansHalted = true;
            break;
          }
        }
        const recorded = deferredBinding ? {
          name: plan.toolName,
          args: plan.args,
          output: '',
          ok: false,
          verified: false,
          changed: false,
          needs_followup: true,
          partial: false,
          failure_class: 'unbound_reference',
          retryable: false,
          effect_uncertain: false,
          semanticBinding: {
            understandingSha256: semanticTurn.understanding.sha256,
            requirementId: plan.requirementId,
            capabilityId: plan.capabilityId,
            operationId: plan.operationId,
            role: 'pending_binding',
          },
        } : await executeGovernedPlanStep(plan,
          freeAutonomyContinuation?.choice === 'start_exploration'
            && freeAutonomyContinuation.selectedPlans.includes(plan)
            ? {
                continuationAudit: {
                  contract: freeAutonomyContinuation.decision.contract,
                  decisionSha256: freeAutonomyContinuation.decision.sha256,
                  basisReceiptSha256: freeAutonomyContinuation.decision.basisReceiptSha256,
                  basisReceiptId: freeAutonomyContinuation.decision.basisReceiptId,
                  explorationId: freeAutonomyContinuation.startSettlement.explorationId,
                  selectedRequirementIds: freeAutonomyContinuation.decision.remainingRequirementIds,
                },
                freeAutonomySelectedReadBinding:
                  freeAutonomyContinuation.selectedReadBinding,
              }
            : null);
        const completed = verifiedCompleteReceipt(recorded)
          || boundedVerifiedHomePartialEffect(recorded, plan.args);
        const entry = { plan, recorded, completed, deferredBinding };
        plannedResults.push(entry);
        if (freeAutonomyContinuation?.selectedReadBinding?.plan === plan
          && !issuedFreeAutonomySelectedReadSettlement(
            freeAutonomyContinuation.selectedReadBinding,
          )) {
          return finishTurn('', {
            toolFinal: true, understood: true, actionFailed: true,
          });
        }
        if (freeAutonomyContinuation?.selectedReadBinding?.plan === plan && !completed) {
          // Deze continuation heeft exact één geselecteerde readpoging. Ook een
          // mislukte, atomisch geboekte poging eindigt hier; de generieke
          // recoveryplanner mag geen tweede of provider-backed actie toevoegen.
          continue;
        }
        if (explorationTerminalContributionPrecommit?.plan === plan) {
          explorationTerminalContributionSettlement = verifiedExplorationTerminalContribution(
            explorationTerminalContributionPrecommit, recorded,
          );
          if (!explorationTerminalContributionSettlement) {
            plansHalted = true;
            break;
          }
        }
        if (aspirationExplorationAnchor?.startPlan === plan) {
          aspirationExplorationSettlement = verifiedAspirationExplorationStart(
            aspirationExplorationAnchor, recorded, cognitiveEpisode.id,
          );
          if (!aspirationExplorationSettlement) {
            plansHalted = true;
            break;
          }
          boundAutonomyExplorationId = aspirationExplorationSettlement.explorationId;
          const pendingProviderProfile = SELF_ATTENTION_EXECUTION_PROFILES.get(semanticTurn);
          if (pendingProviderProfile?.kind === 'provider_after_aspiration_start') {
            const promotedProviderProfile = pendingProviderProfile.startPlan === plan
              && pendingProviderProfile.plan === aspirationExplorationAnchor.nextPlan
              && pendingProviderProfile.aspirationId === aspirationExplorationAnchor.aspirationId
              ? selfAttentionProviderProfile(
                pendingProviderProfile.plan,
                aspirationExplorationSettlement.expected,
                pendingProviderProfile.deadlineTs,
              ) : null;
            if (!promotedProviderProfile) {
              plansHalted = true;
              break;
            }
            SELF_ATTENTION_EXECUTION_PROFILES.set(semanticTurn, promotedProviderProfile);
          }
        }
        if (semanticReadInvestigation && observation) {
          if (!(await recoverReadEntry(entry, [entry]))) plansHalted = true;
          if (plansHalted) break;
          continue;
        }
        if (completed) {
          const remainingPlans = exactFreeAutonomyContinuationOpportunity({
            semanticTurn, plan, receipt: recorded, planIndex,
            mode, messageContext, resumeContext: autonomyResume,
            schedulerEpoch: sealedAutonomyInteractionEpoch,
            initialOpenWorkIds, boundExplorationId: boundAutonomyExplorationId,
            goalEpisode: durableGoalEpisode, turnCurrent: turnIsCurrent(),
          });
          if (remainingPlans) {
            assertTurnCurrent();
            const basisReceiptIndex = toolResults.indexOf(recorded);
            const basisReceiptId = basisReceiptIndex >= 0
              ? `s${basisReceiptIndex + 1}` : '';
            const remainingMs = Number(selfAttentionProviderDeadlineTs) - Date.now();
            const continuationResult = remainingMs > 250
              ? await decideFreeAutonomyContinuation({
                understanding: semanticTurn.understanding,
                firstPlan: plan, firstReceipt: recorded, remainingPlans,
                turnId, schedulerEpoch: sealedAutonomyInteractionEpoch,
                messageContext, basisReceiptId,
                evidenceText: checkpointEvidence(recorded),
              }, {
                capabilityCatalog: capabilityCatalog(),
                identityInstructions: buildRedactedInstructions({
                  sender: person, channel, mode,
                  familyContext: householdContextText({ subject: person, query: userText }),
                  extra: [selfContinuityContextText({ mode, query: userText }), moodActionGuidance()]
                    .filter(Boolean).join('\n'),
                }),
                timeoutMs: Math.min(30_000, remainingMs),
                complete: (request) => chatComplete({
                  ...request,
                  model: config.modelVoice,
                  effort: config.reasoningAutonomy,
                  cacheKey: 'eva-free-autonomy-continuation',
                  maxOutputTokens: 4_000,
                  rateLimitRetries: 0,
                  transientRetries: 0,
                  authenticationRetries: 0,
                }),
              })
              : { ok: false, decision: null };
            assertTurnCurrent();
            const claim = continuationResult.ok
              ? consumeIssuedFreeAutonomyContinuation(continuationResult.decision, {
                understanding: semanticTurn.understanding,
                firstPlan: plan, firstReceipt: recorded, remainingPlans,
                messageContext, turnId, basisReceiptId,
                schedulerEpoch: sealedAutonomyInteractionEpoch,
                turnCurrent: turnIsCurrent(),
              }) : null;
            if (!claim) {
              freeAutonomyContinuationStopped = true;
              break;
            }
            logTimeline('turn_planning', `Zelfgekozen vrije vervolgkeuze: ${claim.choice}`, {
              internalOnly: true, autonomous: true, turnId,
              contract: claim.decision.contract,
              source: claim.decision.kind,
              continuationSha256: claim.decision.sha256,
              understandingSha256: semanticTurn.understanding.sha256,
              basisReceiptSha256: claim.decision.basisReceiptSha256,
              basisReceiptId: claim.decision.basisReceiptId,
              choice: claim.choice,
              remainingRequirementIds: claim.decision.remainingRequirementIds,
              explorationStart: claim.action ? Object.fromEntries(
                Object.entries(claim.action.semanticInput).map(([key, value]) => (
                  [key, typeof value === 'string' ? redactSensitiveText(value) : value]
                )),
              ) : null,
            });
            if (claim.choice !== 'start_exploration') {
              freeAutonomyContinuation = claim;
              freeAutonomyContinuationStopped = true;
              break;
            }
            const startPlan = planCapabilityAction(claim.action, {
              mode: 'autonomy', interactiveAuthority: 'ambient',
              fullAutonomy: config.autonomyFull, synthetic: false,
              effectPolicy: { mode: 'allow', deniedEffects: [] },
              understandingSha256: claim.decision.sha256,
            });
            const startBinding = bindIssuedFreeAutonomyStartPlan({
              claim, plan: startPlan, remainingPlans,
              firstPlan: plan, firstReceipt: recorded,
              messageContext, turnId, basisReceiptId,
              schedulerEpoch: sealedAutonomyInteractionEpoch,
            });
            if (!startPlan || !startBinding) {
              freeAutonomyContinuationStopped = true;
              break;
            }
            const startRecorded = await executeGovernedPlanStep(startPlan, {
              understandingSha256: claim.decision.sha256,
              semanticRole: 'free_autonomy_continuation',
              addressesRequirementIds: ['continuation_exploration_start'],
              basisReceiptIds: [basisReceiptId],
              freeAutonomyStartBinding: startBinding,
            });
            const startedExploration = verifiedFreeAutonomyExplorationStart(
              startBinding, startRecorded, cognitiveEpisode.id,
            );
            if (!startedExploration) {
              return finishTurn('', {
                toolFinal: true, understood: true, actionFailed: true,
              });
            }
            boundAutonomyExplorationId = startedExploration.explorationId;
            const selectedReadBinding = bindIssuedFreeAutonomySelectedRead({
              claim,
              plan: claim.selectedPlans[0],
              startBinding,
              startSettlement: startedExploration,
              turnId,
              messageContext,
              schedulerEpoch: sealedAutonomyInteractionEpoch,
            });
            if (!selectedReadBinding) {
              return finishTurn('', {
                toolFinal: true, understood: true, actionFailed: true,
              });
            }
            freeAutonomyContinuation = Object.freeze({
              ...claim, startBinding, startSettlement: startedExploration,
              selectedReadBinding, priorPlanCount: plannedResults.length,
            });
          }
          continue;
        }
        if (!safeReadRouteFailure(plan, recorded)) {
          plansHalted = true;
          break;
        }
        pendingReadRecoveries.push(entry);
        if (effectStageStarted && !(await recoverPendingReads())) {
          plansHalted = true;
          break;
        }
      }
      if (!plansHalted && !freeAutonomyContinuationStopped
        && !(await recoverPendingReads())) plansHalted = true;
      if (freeAutonomyContinuationStopped) {
        return finishTurn('', { toolFinal: true, understood: true });
      }
      const expectedPlanCount = freeAutonomyContinuation?.choice === 'start_exploration'
        ? freeAutonomyContinuation.priorPlanCount
          + freeAutonomyContinuation.selectedPlans.length
        : semanticTurn.plans.length;
      const allPlannedVerified = plannedResults.length === expectedPlanCount && !plansHalted
        && plannedResults.every(plannedResultComplete);
      const iterativePlans = plannedResults.filter(({ plan }) => plan.execution?.mode === 'iterative');
      if (iterativePlans.length) iterativeExecutionComplete = iterativePlans.every(({ completed }) => completed);
      const durablePlanVerified = plannedResults.some(({ plan, recorded }) => (
        plan.effect === 'persistent' && plan.supportsDeferred
        && verifiedCompleteReceipt(recorded) && (recorded.changed || actionData(recorded)?.item)
      )) || (aspirationExplorationAnchor != null
        && aspirationExplorationSettlement != null)
        || freeAutonomyContinuation?.startSettlement != null;
      const resumedEpisodeAlreadyDurable = semanticTurn.understanding.source.origin === 'resume'
        && Number.isFinite(Number(durableCommand?.id))
        && Number(durableGoalEpisode?.work_item_id) === Number(durableCommand.id)
        && !['closed', 'abandoned'].includes(String(durableGoalEpisode?.state || ''));
      const durableRequirementVerified = semanticTurn.understanding.durability !== 'episode'
        || durablePlanVerified || resumedEpisodeAlreadyDurable;
      let materialSealReceipt = null;
      if (materialObligation?.required === true) {
        let materialProof = null;
        try {
          const executionRows = db.prepare(`SELECT execution_seq AS rowid,*
            FROM main.turn_tool_executions
            WHERE turn_id=? ORDER BY execution_seq`).all(materialExecutionTurn);
          const discoveryRow = executionRows.find((row) => (
            row.tool === 'lijst_bestanden'
            && materialObligation.discoveryRequirementIds.includes(
              JSON.parse(String(row.semantic_binding || 'null'))?.requirementId,
            )
          )) || null;
          const manifest = sealMaterialTargetManifest({
            obligation: materialObligation,
            turnId: materialExecutionTurn,
            scopeComplete: true,
            discoveryRow,
          });
          materialProof = durableMaterialProof({
            obligation: materialObligation,
            executionRows,
            manifest,
          });
          if (allPlannedVerified && durableRequirementVerified && materialProof.complete) {
            if (!durableCommand || !durableGoalEpisode) {
              throw new Error('materiële verzegeling mist duurzaam werk');
            }
            for (const artifact of materialProof.artifacts) {
              storeWorkspaceArtifactObject(artifact.content, {
                expectedSha256: artifact.sha256,
              });
            }
            const currentWork = db.prepare('SELECT * FROM eva_work_items WHERE id=?')
              .get(durableCommand.id);
            const authorityContext = Object.freeze({});
            ISSUED_ARTIFACT_SEAL_CONTEXTS.set(authorityContext, Object.freeze({
              workItemId: Number(currentWork?.id),
              episodeId: Number(durableGoalEpisode.id),
              expectedWorkRevision: Number(currentWork?.revision),
              turnId: materialExecutionTurn,
              understandingSha256: materialObligation.understandingSha256,
              proofExecutionKeys: materialProof.proofExecutionKeys,
            }));
            let authority;
            try {
              authority = artifactSealAuthority(authorityContext);
            } finally {
              ISSUED_ARTIFACT_SEAL_CONTEXTS.delete(authorityContext);
            }
            if (!authority) throw new Error('materiële verzegeling mist hostauthority');
            const operationKey = `material-artifacts:${createHash('sha256')
              .update(canonicalJson({
                turnId: materialExecutionTurn,
                understandingSha256: materialObligation.understandingSha256,
                proofExecutionKeys: materialProof.proofExecutionKeys,
              })).digest('hex')}`;
            const sealedMaterial = sealVerifiedWorkArtifacts(authority, {
              operationKey,
              artifacts: materialProof.artifacts.map(({ content: _content, bytes: _bytes,
                ...artifact }) => artifact),
            });
            const state = durableWorkState(currentWork);
            const saved = transitionGoalEpisode(durableGoalEpisode.id, {
              state: durableGoalEpisode.state,
              checkpoint: 'De zichtbare werkstukken zijn exact geschreven, teruggelezen en als onveranderlijke versies verzegeld.',
              proofSummary: `Artifactseal ${sealedMaterial.sealRef}.`,
              sessionRef: turnId,
              idempotencyKey: `material-artifact-state:${sealedMaterial.sealRef}`,
              eventKind: 'material_artifacts_sealed',
              work: {
                state: { ...state, artifact_receipts: sealedMaterial.versions },
                evidence: `Artifactseal ${sealedMaterial.sealRef}.`,
                lastAction: 'artifact_verzegeld',
              },
            });
            durableCommand = saved.workItem;
            durableGoalEpisode = saved.episode;
            materialSealReceipt = sealedMaterial;
          }
        } catch (error) {
          logTimeline('system', `Materiële bewijsverzegeling faalde gesloten: ${errMsg(error)}`, {
            internalOnly: true, turnId, materialCompletion: true,
          });
        }
        if (!materialSealReceipt) {
          const retryOutcome = resumedInterpretationRetry
            ? handleInterpretationRetryFailure({
              error: 'material_completion_unproven',
              result: { failure: { validation: 'material_completion_unproven' } },
            }) : null;
          const handoffVerified = Boolean(initialMaterialHandoff
            || ['retry', 'delivery'].includes(retryOutcome?.kind));
          return finishTurn(fixedMaterialIncompleteReply({
            hadVerifiedRead: materialProof?.hadVerifiedRead === true
              || toolResults.some((tool) => tool.name === 'lees_bestand'
                && verifiedCompleteReceipt(tool)),
            handoffVerified,
          }), {
            error: 'material_completion_unproven',
            toolFinal: true, understood: true,
            durableHandoff: handoffVerified || undefined,
            executionPending: handoffVerified,
            actionFailed: true, durableProofMissing: true,
          });
        }
      }
      if (allPlannedVerified && durableRequirementVerified) {
        const decision = semanticTurn.understanding.firstDecision;
        const scheduledMonitoringReply = turnContract.kind === 'monitoring'
          && decision.kind === 'schedule'
          ? turnMonitoring?.deliveryRequired === false
            ? 'Ik houd dit nu in de gaten zonder je tussendoor berichten te sturen.'
            : 'Ik houd dit nu in de gaten en geef het je door zodra de afgesproken voorwaarde is bereikt.'
          : '';
        const effectiveReceipt = (entry) => verifiedCompleteReceipt(entry?.adaptiveRecorded)
          ? entry.adaptiveRecorded : entry?.recorded;
        const last = effectiveReceipt(plannedResults.at(-1));
        const lastHuman = humanResultText(last?.output || '');
        const iterative = plannedResults.find(({ plan }) => plan.execution?.mode === 'iterative');
        const proven = (iterative ? iterativeArtifactEvidence(toolResults, iterative.plan) : '')
          || evidenceFallback(toolResults, { requiredTools, userText });
        const home = plannedResults.findLast(({ plan }) => plan.toolName === 'bedien_huis');
        const voice = plannedResults.findLast(({ plan }) => plan.toolName === 'spreek_in_huis');
        let reply = scheduledMonitoringReply || (decision.kind === 'schedule'
          ? decision.replyDraft
          : home
            ? humanResultText(home.recorded.output) || renderHouseActionResult({
              target: home.plan.args.doel,
              action: home.plan.args.actie,
              ok: home.recorded.ok,
              verified: home.recorded.verified,
              changed: home.recorded.changed,
            })
            : voice
              ? decision.replyDraft || acceptedVoiceReply(voice.recorded) || lastHuman
              : iterative
                ? proven || decision.replyDraft
              : decision.kind === 'inspect'
                ? lastHuman || proven || decision.replyDraft
                : lastHuman || decision.replyDraft || proven);
        if (looksLikeInternalOutput(reply)) reply = decision.replyDraft || proven;
        if (!scheduledMonitoringReply) {
          assertTurnCurrent();
          reply = await replyInEvaVoice(reply, monitoringProofOptions, {
            guardedFallback: turnContract.kind === 'continuity'
              ? CONTINUITY_SNAPSHOT_FALLBACK : '',
          });
        }
        if (resumedInterpretationRetry) {
          assertTurnCurrent();
          const feedback = replyFeedback(reply, monitoringProofOptions);
          if (feedback) {
            const deferred = handleInterpretationRetryFailure({
              error: 'interpreter_malformed',
              result: { failure: { validation: 'invalid_reply_draft' } },
            });
            return finishTurn('', {
              toolFinal: true, understood: true,
              executionPending: deferred?.kind === 'retry', actionFailed: true,
            });
          }
          try {
            const candidate = issueInterpretationReplyCandidate({
              understanding: semanticTurn.understanding,
              item: durableCommand,
              episode: durableGoalEpisode,
              source: 'verified_execution_reply',
              executionTurnId: durableExecutionTurn || turnId,
              text: reply,
              toolResults,
            });
            const prepared = prepareIssuedInterpretationRetryDelivery(
              durableCommand, durableGoalEpisode, candidate, durableExecutionTurn || turnId,
            );
            durableCommand = prepared.workItem;
            durableGoalEpisode = prepared.episode;
            return finishTurn('', {
              toolFinal: true, understood: true,
              executionPending: true, preparedRetryDelivery: true,
            });
          } catch {
            const deferred = handleInterpretationRetryFailure({
              error: 'interpreter_malformed',
              result: { failure: { validation: 'invalid_reply_draft' } },
            });
            return finishTurn('', {
              toolFinal: true, understood: true,
              executionPending: deferred?.kind === 'retry', actionFailed: true,
            });
          }
        }
        return finishTurn(reply, {
          toolFinal: true,
          understood: true,
        });
      }
      const failed = plannedResults.find((entry) => !plannedResultComplete(entry));
      const failedTool = failed?.adaptiveRecorded || failed?.recorded
        || [...toolResults].reverse().find((tool) => (
        !verifiedCompleteReceipt(tool)
      ));
      const incompleteProof = failedTool?.ok === true && failedTool?.verified === true
        && !verifiedCompleteReceipt(failedTool);
      const modelFailureText = executionModelFailureText((/** @type {any} */ (failed))?.execution);
      const failureText = modelFailureText || (incompleteProof
        ? 'Ik kon de relevante gegevens niet volledig uitlezen, dus ik weet het antwoord nog niet zeker.'
        : failedVoiceReply(failedTool)
          || humanResultText(failedTool?.output || failed?.recorded?.output || ''));
      const progressText = partialTurnProgress(toolResults);
      const readFailure = failed && ['read', 'discovery'].includes(failed.plan.effect);
      const readBlocker = readFailure && failed?.investigationRequired === true
        ? naturalReadInvestigationBlocker(failed, failureText) : failureText;
      const groundedFailure = modelFailureText || [
        progressText ? `Dit kon ik wel vaststellen: ${progressText}` : '',
        readFailure
          ? readBlocker || 'Ik kon de relevante gegevens niet betrouwbaar uitlezen.'
          : `Nog open: ${failureText || 'de laatste benodigde controle is niet betrouwbaar afgerond.'}`,
      ].filter(Boolean).join(' ');
      const failureReply = mode === 'autonomy' ? ''
        : readFailure
          ? groundedFailure
          : await replyInEvaVoice(groundedFailure, monitoringProofOptions, {
            guardedFallback: turnContract.kind === 'continuity'
              ? CONTINUITY_SNAPSHOT_FALLBACK : progressText
              ? 'Er ligt al bruikbaar werk, maar de laatste controle ontbreekt nog. Dit is dus nog niet afgerond.'
              : 'De benodigde controle ontbreekt nog. Dit is dus nog niet afgerond.',
          });
      return finishTurn(failureReply, {
        toolFinal: true,
        understood: true,
        actionFailed: Boolean(failed),
        durableProofMissing: !durableRequirementVerified,
      });
    }

  } catch (error) {
    if (error?.code === 'EVA_TURN_UNDERSTANDING_OUTPUT_INVALID') throw error;
    if (error?.code === 'EVA_TURN_SUPERSEDED') {
      const settled = settleBoundRetrySupersession(durableCommand, durableGoalEpisode);
      if (settled) {
        durableCommand = settled.workItem;
        durableGoalEpisode = settled.episode;
      }
      return finishTurn('', { superseded: true, yielded: true });
    }
    if (mode === 'autonomy' && contributionFromTools(toolResults, mode, userText)
      && ensureAutonomyCheckpointAfterReport()) {
      logTimeline('system', `Zelfstandige beurt is na bewezen uitvoering hersteld van: ${errMsg(error)}`, {
        internalOnly: true,
        autonomous: true,
        turnId,
        recovered: true,
      });
      return finishTurn('', { recovered: true });
    }
    const detail = errMsg(error);
    const provenFallback = evidenceFallback(toolResults, { requiredTools, userText });
    let failedReason = '';
    try {
      const failed = [...toolResults].reverse().find((tool) => tool.ok !== true || tool.verified !== true);
      const parsed = JSON.parse(String(failed?.output || ''));
      failedReason = humanResultText(
        parsed?.human_message || parsed?.error || parsed?.evidence?.summary || '',
      );
    } catch { /* Malformed tool output carries no usable failure detail. */ }
    const groundedFailure = provenFallback || failedReason
      || (durableCommand
        ? `De uitvoering stopte bij ${String(durableCommand.next_step || 'de volgende stap').replace(/[.!]+$/g, '')}. De voortgang is bewaard.`
        : 'Deze beurt leverde geen betrouwbaar eindantwoord op.');
    const reply = mode === 'autonomy' ? '' : await replyInEvaVoice(groundedFailure);
    if (mode === 'autonomy') checkpointAutonomyProgress();
    return finishTurn(reply, { error: detail });
  } finally {
    runtime.active = Math.max(0, runtime.active - 1);
    if (mode === 'chat') {
      runtime.activeInteractive = Math.max(0, Number(runtime.activeInteractive || 0) - 1);
    }
    if (!runtime.active) setActivity('luistert', null);
  }
}

export function handleMessageDetailed(input) {
  const channel = input?.channel || 'telegram';
  const sender = input?.sender || 'huisgenoot';
  const suppliedMessageContext = input?.messageContext || {};
  const conversationId = conversationIdFor({
    channel,
    sender,
    messageContext: suppliedMessageContext,
  });
  let verifiedNativeSource = null;
  if (['telegram', 'voice'].includes(channel)) {
    const nativeAuthority = channel === 'telegram'
      ? suppliedMessageContext?.telegramUnderstandingAuthority
      : suppliedMessageContext?.voiceUnderstandingAuthority;
    if (nativeAuthority == null) {
      throw Object.assign(new Error(`${channel}-ingress vereist een private authority`), {
        code: 'EVA_TURN_AUTHORITY_INGRESS_UNAVAILABLE',
      });
    }
    const expected = Object.freeze({
      channel,
      conversationId,
      messageId: String(suppliedMessageContext?.messageId || ''),
      requester: String(sender || '').trim(),
      originalText: String(input?.text ?? '').trim(),
    });
    verifiedNativeSource = channel === 'telegram'
      ? verifiedTelegramUnderstandingSource(suppliedMessageContext, expected)
      : verifiedVoiceUnderstandingSource(suppliedMessageContext, expected);
    if (!verifiedNativeSource) {
      throw Object.assign(new Error(`${channel}-ingressauthority is niet exact aan deze beurt gebonden`), {
        code: 'EVA_TURN_AUTHORITY_INGRESS_MISMATCH',
      });
    }
  }
  const ingress = registerInteractiveIngress(
    channel, conversationId, suppliedMessageContext, verifiedNativeSource,
  );
  const messageContext = ingress.scope ? {
    ...suppliedMessageContext,
    ingressScope: ingress.scope,
    ingressToken: ingress.token,
  } : suppliedMessageContext;
  runtime.interactivePending += 1;
  runtime.interactionEpoch = Math.min(
    Number.MAX_SAFE_INTEGER,
    runtime.interactionEpoch + 1,
  );
  const queueKey = String(messageContext?.queueKey || conversationId);
  return queueFor(queueKey, () => executeTurn({
    ...input,
    channel,
    sender,
    conversationId,
    messageContext,
    mode: 'chat',
  })).finally(() => {
    runtime.interactivePending = Math.max(0, runtime.interactivePending - 1);
  });
}

function exactFinalDeliveryCapability(result, capability, turnId, reply) {
  const durableWebRecovery = result?.channel === 'web' && result?.replayed === true;
  if (result?.finalDeliveryBindingSha256 && result?.channel !== 'telegram' && !capability
    && !durableWebRecovery) {
    return false;
  }
  if (!capability) return true;
  return [
    String(result?.finalDeliveryBindingSha256 || '')
      === String(capability.bindingSha256 || ''),
    Number(result?.goalEpisodeId) === Number(capability.episodeId),
    Number(result?.workItemId) === Number(capability.workItemId),
    turnId === String(capability.turnId || ''),
    String(result?.channel || '') === String(capability.channel || ''),
    String(result?.conversationId || '') === String(capability.conversationId || ''),
    String(result?.originMessageId || '') === String(capability.originMessageId || ''),
    createHash('sha256').update(reply).digest('hex')
      === String(capability.resultObjectReplySha256 || ''),
  ].every(Boolean);
}

function canonicalDeliveredTransport(delivery) {
  const projected = redactSecrets(delivery || null);
  if (!projected || typeof projected !== 'object' || Array.isArray(projected)) return projected;
  if (projected.transport !== 'telegram') return projected;
  const { deduplicated: _recoveryObservation, ...durable } = projected;
  return durable;
}

function authorityDeliveredTransport(delivery) {
  return delivery?.transport === 'home-assistant-conversation-response'
    ? delivery : canonicalDeliveredTransport(delivery);
}

function recordDeliveredTurnTimeline(result, delivery, turnId, reply, issuedResult) {
  if (hasDeliveredTurn(turnId)) {
    if (deliveredReplyRecorded(result, delivery)) {
      return { alreadyRecorded: true, message: null, timeline: null };
    }
    throw new Error('delivery-turn-id is al aan andere canonieke result/deliverybytes gebonden');
  }
  const storedDelivery = redactSecrets(delivery || null);
  const deliveredDeliverySha256 = createHash('sha256')
    .update(canonicalJson(canonicalDeliveredTransport(delivery))).digest('hex');
  const deliveredAuthorityDeliverySha256 = createHash('sha256')
    .update(canonicalJson(authorityDeliveredTransport(delivery))).digest('hex');
  let message = null;
  if (result?.syntheticTest !== true) {
    message = persistMessageWithoutPublication(
      result.channel || 'unknown',
      'Eva',
      'assistant',
      reply,
      result.conversationId || null,
    );
  }
  const timeline = persistTimelineWithoutPublication(
    'chat_out', `Eva -> ${result.sender || 'huisgenoot'}: ${reply}`, {
    channel: result.channel || 'unknown',
    conversationId: result.conversationId || null,
    sender: 'Eva',
    turnId,
    goalEpisodeId: result?.goalEpisodeId || null,
    workItemId: result?.workItemId || null,
    originMessageId: result?.originMessageId || null,
    syntheticTest: result?.syntheticTest === true,
    delivery: storedDelivery,
    deliveredResultSha256: issuedResult.resultSha256,
    deliveredDeliverySha256,
    deliveredAuthorityDeliverySha256,
    finalDeliveryBindingSha256: result?.finalDeliveryBindingSha256 || null,
    tools: toolResultSummary(result.tools || []),
    },
  );
  return { alreadyRecorded: false, message, timeline };
}

function recordDeliveredBehaviorCanary(result, turnId, reply, message) {
  const capability = ISSUED_BEHAVIOR_DELIVERY_CAPABILITIES.get(result) || null;
  if (!capability) return true;
  if (!message?.id || result?.syntheticTest === true) return false;
  const context = Object.freeze({});
  ISSUED_BEHAVIOR_DELIVERY_CONTEXTS.set(context, Object.freeze({
    accepted: true, capability, turnId,
    conversationId: result.conversationId || null,
    reply, localMessageId: String(message.id), externalReplyRef: null,
  }));
  try {
    return Boolean(recordBehaviorCanaryExposure(context));
  } catch {
    return false;
  } finally {
    ISSUED_BEHAVIOR_DELIVERY_CONTEXTS.delete(context);
    ISSUED_BEHAVIOR_DELIVERY_CAPABILITIES.delete(result);
  }
}

function suppliedFinalDeliveryBinding(result, capability) {
  if (!capability && result.channel === 'web' && result.replayed === true
    && /^[a-f0-9]{64}$/u.test(String(result.finalDeliveryBindingSha256 || ''))) {
    return {
      bindingSha256: result.finalDeliveryBindingSha256,
      recovery: true,
    };
  }
  if (result.channel === 'voice' && capability?.recovery === true) {
    return {
      bindingSha256: capability.bindingSha256,
      deliverySha256: capability.deliverySha256,
      recovery: true,
      resultSha256: capability.resultSha256,
      voiceRecoveryProof: capability.voiceRecoveryProof,
    };
  }
  if (result.channel !== 'telegram') return capability;
  if (!result?.finalDeliveryBindingSha256) return null;
  return { bindingSha256: result.finalDeliveryBindingSha256 };
}

function recordPendingFinalDelivery(result, delivery, turnId, reply, episode, capability) {
  if (result?.syntheticTest === true || !episode) return true;
  const alreadyFinal = episodeHasFinalDelivery(episode.id);
  if (episode.state !== 'delivery_pending'
    && !(alreadyFinal && result?.finalDeliveryBindingSha256)) return true;
  try {
    recordEpisodeDelivery(episode.id, {
      deliveredChannel: result.channel || 'unknown',
      deliveryTarget: result.sender || episode.delivery_target,
      originConversationId: result.conversationId || null,
      originMessageId: result.originMessageId || null,
      deliveredConversationId: result.conversationId || null,
      resultText: reply,
      proofSummary: episode.proof_summary,
      // Native Telegram transport already commits its exact fanout key before
      // this receipt is recorded.  Reusing that key is what lets the durable
      // receipt prove the accepted chunks after a restart; a synthetic
      // episode key cannot be matched to the immutable transport ledger.
      idempotencyKey: result.channel === 'telegram'
        ? String(delivery?.dedupeKey || '')
        : `turn:${turnId}:episode:${episode.id}`,
      transport: delivery || {},
      deliveryTurnId: turnId,
      finalDeliveryBinding: suppliedFinalDeliveryBinding(result, capability),
    });
    return true;
  } catch (error) {
    logTimeline('system', `Delivery-receipt voor episode ${episode.id} mislukte: ${errMsg(error)}`, {
      internalOnly: true,
      turnId,
      goalEpisodeId: episode.id,
    });
    return false;
  }
}

export function recordDeliveredReply(result, delivery = null, deliveryAuthority = null) {
  const issuedResult = issuedDeliverableResult(result);
  if (!issuedResult) return false;
  const turnId = issuedResult.body.turnId.trim();
  const reply = issuedResult.body.reply.trim();
  if (!turnId || !reply || delivery?.accepted !== true) return false;
  // De oorspronkelijke issued-result provenance kiest de native verifier.
  // Caller-controlled channel/transportlabels kunnen deze grens dus niet
  // verlagen naar een webdelivery zonder private native one-shot authority.
  if (issuedResult.body.originChannel === 'telegram'
    && !verifiedTelegramDeliveryAuthority(deliveryAuthority, result, delivery)) return false;
  if (issuedResult.body.originChannel === 'voice'
    && !verifiedVoiceDeliveryAuthority(deliveryAuthority, result, delivery)) return false;
  const committed = db.transaction(() => {
    const finalDeliveryCapability = ISSUED_FINAL_DELIVERY_CAPABILITIES.get(result) || null;
    if (!exactFinalDeliveryCapability(result, finalDeliveryCapability, turnId, reply)) {
      return { accepted: false, finalDeliveryCapability, recordedDelivery: null };
    }
    const goalEpisode = goalEpisodeById(result?.goalEpisodeId);
    const exactFinalDelivery = recordPendingFinalDelivery(
      result, delivery, turnId, reply, goalEpisode, finalDeliveryCapability,
    );
    if (!exactFinalDelivery) {
      return { accepted: false, finalDeliveryCapability, recordedDelivery: null };
    }
    const recordedDelivery = recordDeliveredTurnTimeline(
      result, delivery, turnId, reply, issuedResult,
    );
    if (issuedResult.body.originChannel === 'voice' && !recordedDelivery.alreadyRecorded) {
      recordVoiceDeliveredProjection(db, {
        requestId: issuedResult.body.originMessageId,
        resultBodyJson: issuedResult.bodyJson,
        resultSha256: issuedResult.resultSha256,
        deliveryJson: canonicalJson(delivery),
        deliverySha256: createHash('sha256').update(canonicalJson(delivery)).digest('hex'),
      });
    }
    return {
      accepted: !recordedDelivery.alreadyRecorded && exactFinalDelivery,
      finalDeliveryCapability,
      recordedDelivery,
    };
  }).immediate();
  if (!committed.accepted) return false;
  const consumedNativeAuthority = issuedResult.body.originChannel === 'telegram'
    ? consumeTelegramDeliveryAuthority(deliveryAuthority, result, delivery)
    : issuedResult.body.originChannel === 'voice'
      ? consumeVoiceDeliveryAuthority(deliveryAuthority, result, delivery)
      : true;
  if (!consumedNativeAuthority) {
    throw new Error('gecommitteerde native delivery verloor haar private authority-consumptie');
  }
  if (committed.finalDeliveryCapability?.recovery === true
    && !consumeVoiceFinalDeliveryRecoveryProof(
      db, committed.finalDeliveryCapability.voiceRecoveryProof,
    )) {
    throw new Error('gecommitteerde Voice-delivery verloor haar private herstelproof');
  }
  ISSUED_DELIVERABLE_RESULTS.delete(result);
  CONSUMED_DELIVERABLE_RESULTS.add(result);
  if (committed.finalDeliveryCapability) {
    ISSUED_FINAL_DELIVERY_CAPABILITIES.delete(result);
  }
  if (committed.recordedDelivery.message) {
    publishPersistedMessage(committed.recordedDelivery.message);
  }
  publishPersistedTimeline(committed.recordedDelivery.timeline);
  const exactBehaviorDelivery = recordDeliveredBehaviorCanary(
    result, turnId, reply, committed.recordedDelivery.message,
  );
  if (!exactBehaviorDelivery) {
    logTimeline('system', 'Bezorging slaagde, maar de gedragsproef bleef fail-closed onbewezen.', {
      internalOnly: true, turnId, behaviorLearning: true,
    });
  }
  return true;
}

export function deliveredReplyRecorded(result, delivery = null) {
  const body = deliverableResultBody(result);
  const turnId = String(body?.turnId || '').trim();
  if (!body || !turnId) return false;
  const rows = db.prepare(`SELECT meta FROM main.timeline
    WHERE kind='chat_out' AND json_extract(meta,'$.turnId')=? ORDER BY id`).all(turnId);
  if (rows.length !== 1) return false;
  let meta;
  try { meta = JSON.parse(String(rows[0].meta || '')); } catch { return false; }
  const resultSha256 = createHash('sha256')
    .update(canonicalJson(body)).digest('hex');
  const storedDeliverySha256 = createHash('sha256')
    .update(canonicalJson(canonicalDeliveredTransport(meta.delivery ?? null))).digest('hex');
  if (meta.deliveredResultSha256 !== resultSha256
    || meta.deliveredDeliverySha256 !== storedDeliverySha256
    || !/^[a-f0-9]{64}$/u.test(String(meta.deliveredAuthorityDeliverySha256 || ''))) return false;
  if (delivery != null) {
    const suppliedDeliverySha256 = createHash('sha256')
      .update(canonicalJson(canonicalDeliveredTransport(delivery))).digest('hex');
    if (suppliedDeliverySha256 !== storedDeliverySha256) return false;
    const suppliedAuthorityDeliverySha256 = createHash('sha256')
      .update(canonicalJson(authorityDeliveredTransport(delivery))).digest('hex');
    if (suppliedAuthorityDeliverySha256 !== meta.deliveredAuthorityDeliverySha256) return false;
  }
  if (!result?.finalDeliveryBindingSha256 || !Number(result?.goalEpisodeId)) return true;
  return episodeHasFinalDelivery(Number(result.goalEpisodeId));
}

function invalidInterpretationRetryAutonomyIngress(
  autonomyContext, selectedWork, selectedWorkState, requestedWorkId,
) {
  if (!Object.hasOwn(selectedWorkState, 'interpretation_retry')) return false;
  const requestedEpisodeId = Number(autonomyContext.goalEpisodeId);
  const exactEpisode = Number.isSafeInteger(requestedEpisodeId) && requestedEpisodeId >= 1
    ? goalEpisodeById(requestedEpisodeId) : null;
  const retry = exactEpisode && Number(exactEpisode.work_item_id) === requestedWorkId
    ? interpretationRetryResume(selectedWork, exactEpisode) : null;
  return retry?.valid !== true;
}

function invalidExplorationEconomicAutonomyIngress(
  autonomyContext, selectedWork, selectedWorkState, requestedWorkId,
) {
  if (!Object.hasOwn(selectedWorkState, 'exploration_economic_handoff')) return false;
  const requestedEpisodeId = Number(autonomyContext.goalEpisodeId);
  const exactEpisode = Number.isSafeInteger(requestedEpisodeId) && requestedEpisodeId >= 1
    ? goalEpisodeById(requestedEpisodeId) : null;
  const boundary = exactEpisode && Number(exactEpisode.work_item_id) === requestedWorkId
    ? explorationEconomicResumeBoundary({ workItem: selectedWork, episode: exactEpisode }) : null;
  return !boundary?.present || !boundary.valid;
}

export function runAutonomousTurn({
  reason = 'vrije aandacht',
  pendingSignals = [],
  attention = null,
  workId = null,
  goalEpisodeId = null,
  schedulerContext = null,
} = {}) {
  const schedulerEpoch = schedulerContext == null
    ? null : verifiedAutonomyInteractionEpoch(schedulerContext);
  if ((schedulerContext != null && (schedulerEpoch == null
      || typeof reason !== 'string' || schedulerContext.reason !== reason))
    || (schedulerContext == null && attention !== null)) return Promise.resolve(Object.freeze({
    reply: '', turnId: null, channel: 'autonomy', sender: 'Eva', conversationId: 'autonomy:eva', tools: Object.freeze([]), contribution: null, continuation: null, progress: null, noActionReason: 'Ongeldige autonomie-scheduleringress.', interpretationFailed: true, failureStage: 'understanding', error: 'untrusted_autonomy_scheduler_context',
  }));
  if (schedulerContext != null && (schedulerEpoch !== runtime.interactionEpoch
    || runtime.interactivePending !== 0)) return Promise.resolve(Object.freeze({
    reply: '', turnId: null, channel: 'autonomy', sender: 'Eva',
    conversationId: 'autonomy:eva', tools: Object.freeze([]), contribution: null,
    continuation: null, progress: null, noActionReason: null,
    yielded: true, superseded: true,
  }));
  const unprivilegedContext = issueUnprivilegedAutonomyContext({
    pendingSignals, workId, goalEpisodeId,
  });
  const autonomyContext = schedulerContext && typeof schedulerContext === 'object'
    ? schedulerContext : unprivilegedContext;
  const autonomyAttention = schedulerContext == null ? null : autonomyContext.attention;
  const requestedWorkId = typeof autonomyContext.workId === 'number'
    && Number.isSafeInteger(autonomyContext.workId) && autonomyContext.workId > 0
    ? autonomyContext.workId : Number.NaN;
  const selectedWork = Number.isFinite(requestedWorkId)
    ? durableWorkStatus().open.find((item) => Number(item.id) === requestedWorkId) || null
    : null;
  const selectedWorkState = durableWorkState(selectedWork);
  if (invalidInterpretationRetryAutonomyIngress(
    autonomyContext, selectedWork, selectedWorkState, requestedWorkId,
  )) return Promise.resolve(Object.freeze({
    reply: '', turnId: null, channel: 'autonomy', sender: 'Eva',
    conversationId: 'autonomy:eva', tools: Object.freeze([]), contribution: null,
    continuation: null, progress: null, noActionReason: null,
    interpretationFailed: true, failureStage: 'authority',
    error: 'interpretation_retry_ingress_integrity',
  }));
  if (invalidExplorationEconomicAutonomyIngress(
    autonomyContext, selectedWork, selectedWorkState, requestedWorkId,
  )) return Promise.resolve(Object.freeze({
    reply: '', turnId: null, channel: 'autonomy', sender: 'Eva',
    conversationId: 'autonomy:eva', tools: Object.freeze([]), contribution: null,
    continuation: null, progress: null, noActionReason: null,
    interpretationFailed: true, failureStage: 'authority',
    error: 'exploration_economic_handoff_ingress_integrity',
  }));
  const workContext = selectedWork ? [
    `Je hervat nu duurzaam werk #${selectedWork.id}: ${selectedWork.title}.`,
    `Doel: ${String(selectedWork.goal || selectedWork.title).slice(0, 4_000)}`,
    selectedWork.definition_of_done
      ? `Klaar wanneer: ${String(selectedWork.definition_of_done).slice(0, 2_000)}` : '',
    selectedWork.current_step || selectedWork.next_step
      ? `Eerstvolgende onbewezen stap: ${String(selectedWork.current_step || selectedWork.next_step).slice(0, 2_000)}` : '',
    selectedWork.evidence
      ? `Reeds gecontroleerd bewijs: ${String(selectedWork.evidence).slice(0, 1_500)}` : '',
    'Maak nu aantoonbare materiële voortgang met de eerstvolgende inhoudelijke handeling. Werkstatus bijwerken volgt pas op zo’n stap met bewijs of op een concreet bewezen extern blok met hervatmoment.',
  ].filter(Boolean).join('\n') : '';
  const autonomyText = redactSensitiveText([
    `Aanleiding voor deze zelfstandige beurt: ${reason}.`,
    workContext,
    selectedWork ? ''
      : autonomyAttention?.kind === 'goal'
        ? `Herbekijk nu deze ene eigen richting en kies hoogstens één doelbewerking: bijstellen met een reviewmoment vanaf ${String(autonomyAttention.claimed_until || '')}, pauzeren, afronden of verlaten.`
        : 'Kijk naar de echte gezins-, gespreks-, internet-, werk- en huiscontext en maak een nuttig klein resultaat helemaal af.',
  ].filter(Boolean).join('\n\n')).slice(0, 15_900);
  return queueFor('autonomy', () => executeTurn({
    channel: 'autonomy',
    sender: 'Eva',
    text: autonomyText,
    mode: 'autonomy',
    persistInbound: false,
    messageContext: autonomyContext,
  }));
}

export function agentStatus({ audience = 'internal' } = {}) {
  if (audience !== 'internal' && audience !== 'family') {
    throw new TypeError('agentStatus audience moet internal of family zijn');
  }
  const familyAudience = audience === 'family';
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const work = durableWorkStatus(today, { familyVisibleOnly: familyAudience });
  const project = (item) => ({
    id: item.id,
    source: 'werk',
    topic: item.title,
    status: item.status,
    next_step: item.next_step || '',
    evidence: readableEvidence(item.result || item.evidence || ''),
    last_action: item.last_action || '',
    owner: item.owner || null,
    updated_ts: item.updated_ts,
    next_wake_ts: item.next_wake_ts || null,
  });
  const focusItem = selectPortfolioCommitment(work.open)?.item || null;
  const durableFocus = focusItem
    ? `${focusItem.title}${focusItem.next_step ? `: ${focusItem.next_step}` : ''}`
    : null;
  const statusKey = (value) => String(value || '')
    .toLowerCase()
    .replace(/^opdracht(?: van [^:]+)?:\s*/i, '')
    .replace(/[^\p{L}\p{N}]+/gu, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  const durableSteps = contributionRows(today, 1, { includeInternal: !familyAudience })
    .filter((row) => {
      const rawEvidence = String(row.meta?.evidence || '').trim();
      if (/^\{/.test(rawEvidence) && row.meta?.lastAction === 'zoek_internet') return false;
      if (/"count"\s*:\s*0|geen bruikbare resultaten/i.test(rawEvidence)) return false;
      if (row.meta?.lastAction === 'werk_bijhouden' && work.doneToday.some((item) => (
        Math.abs(Date.parse(item.updated_ts || '') - Date.parse(row.ts || '')) < 5 * 60_000
      ))) return false;
      const rowEvidence = statusKey(readableEvidence(row.meta?.evidence || ''));
      const rowTopic = statusKey(row.content);
      return !work.doneToday.some((item) => {
        const itemEvidence = statusKey(readableEvidence(item.result || item.evidence || ''));
        const itemTopic = statusKey(item.title);
        const sameEvidence = rowEvidence.length >= 40 && itemEvidence.length >= 40
          && (rowEvidence.includes(itemEvidence.slice(0, 120))
            || itemEvidence.includes(rowEvidence.slice(0, 120)));
        const sameTopic = rowTopic.length >= 40 && itemTopic.length >= 40
          && (rowTopic.includes(itemTopic.slice(0, 120))
            || itemTopic.includes(rowTopic.slice(0, 120)));
        const closeInTime = Math.abs(
          Date.parse(item.updated_ts || '') - Date.parse(row.ts || ''),
        ) < 5 * 60_000;
        const sameResult = closeInTime && tekstOverlap(
          `${row.content} ${row.meta?.evidence || ''}`,
          `${item.title} ${item.result || ''} ${item.evidence || ''}`,
        ) >= 0.4;
        return sameEvidence || sameTopic || sameResult;
      });
    })
    .map((row) => ({
      id: `contribution:${row.id}`,
      source: row.meta?.source || 'bijdrage',
      topic: row.content,
      status: row.meta?.status || 'done',
      next_step: '',
      evidence: readableEvidence(row.meta?.evidence || ''),
      last_action: row.meta?.lastAction || '',
      owner: row.meta?.owner || null,
      updated_ts: row.ts,
    }));
  const interestSteps = familyAudience ? db.prepare(`SELECT
      exploration.id, exploration.question, exploration.result, exploration.updated_ts
    FROM eva_explorations AS exploration
    JOIN aspirations AS aspiration ON aspiration.id = exploration.aspiration_id
    WHERE exploration.source = 'self' AND exploration.status = 'completed'
      AND exploration.updated_ts >= ? AND trim(coalesce(exploration.result,'')) <> ''
      AND aspiration.kind = 'interest'
    ORDER BY exploration.updated_ts DESC, exploration.id DESC LIMIT 5`).all(
    today.toISOString(),
  ).map((row) => ({
    id: `interest:${row.id}`,
    source: 'eigen interesse',
    topic: `Eigen ontdekking: ${redactSensitiveText(String(row.question || '')).slice(0, 360)}`,
    status: 'done',
    next_step: '',
    evidence: readableEvidence(redactSensitiveText(String(row.result || ''))),
    last_action: 'verkenning afgerond',
    owner: 'Eva',
    updated_ts: row.updated_ts,
  })) : [];
  const autonomousCurrent = runtime.current?.channel === 'autonomy';
  const currentFocus = familyAudience && autonomousCurrent
    ? 'Eva werkt zelfstandig aan haar huidige aandachtspunt.'
    : runtime.current?.text || (runtime.active ? runtime.activity : durableFocus);
  const lastTurn = familyAudience && runtime.lastTurn?.mode === 'autonomy'
    ? runtime.lastInteractiveTurn : runtime.lastTurn;
  return {
    ts: new Date().toISOString(),
    active_turns: runtime.active,
    interactive_pending: runtime.interactivePending,
    interaction_epoch: runtime.interactionEpoch,
    current_focus: currentFocus,
    activity: runtime.activity,
    last_turn: lastTurn,
    last_interactive_turn: runtime.lastInteractiveTurn,
    last_autonomy_turn: familyAudience ? null : runtime.lastAutonomyTurn,
    open_commitments: work.open.map(project),
    blocked_items: work.blocked.map(project),
    waiting_for_evidence: [],
    today_real_steps: [
      ...work.doneToday.map(project),
      ...durableSteps,
      ...interestSteps,
    ],
  };
}
