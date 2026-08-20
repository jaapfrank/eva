import { registerHooks } from 'node:module';

const agentModuleUrl = new URL('../src/mind/agent.js', import.meta.url).href;
const testComposition = String.raw`
import { toolSyntheticAllowed as testToolSyntheticAllowed } from './tools.js';
import { monitoringRequestSpec as testMonitoringRequestSpec } from './turnplan.js';
import { isScheduledMonitoringItem as testIsScheduledMonitoringItem } from './communication.js';

export const __projectRouteTest = Object.freeze({
  voiceForegroundTimeoutMs: VOICE_FOREGROUND_TIMEOUT_MS,
  voiceReplyComposerTimeoutMs: VOICE_REPLY_COMPOSER_TIMEOUT_MS,
  autonomyInterpretationTimeoutMs: AUTONOMY_INTERPRETATION_TIMEOUT_MS,
  retryInterpretationTimeout,
  autonomyArtifactReportPending,
  autonomyEffectRequiresReport,
  autonomyContinuationStep,
  autonomyDiscoveryAllowed,
  autonomyLaneAllows,
  autonomyLaneForTool,
  autonomyLaneForWork,
  autonomyNoActionReason,
  autonomousMessageGate,
  autonomyProofRelevantToWork,
  autonomyProgressFromTools,
  privacySafeTurnTools,
  autonomyWorkCheckpointPending,
  buildMissionComplete,
  completedAutonomousArtifact,
  directHomeCommand,
  verifiedHomeReadback,
  evidenceFallback,
  executionModelFailureText,
  auditToolOutput,
  humanResultText,
  looksLikeInternalOutput,
  syntheticToolAllowed: testToolSyntheticAllowed,
  syntheticReplyNeedsPersistentState,
  styleCorrectionPolicyFeedback,
  explicitlyIncompleteAction,
  shouldAutoBindDurableTool,
  boundExplorationToolError,
  incompleteAutonomousWrite,
  rejectedToolOutput,
  inspectToolOutcome,
  plannedResultComplete,
  safeReadRouteFailure, safeReadFailuresCanYieldToIssuedWorkspace,
  safeReadFailuresCanPreserveIssuedAspirationPersistence,
  homeContextRecoveryPrecommitValid,
  semanticFailureReply: (error) => semanticFailurePresentation(error).reply,
  semanticFailureResultMetadata,
  semanticTurnState,
  recordSemanticTurnTimeline,
  retainedTurnResultMetadata,
  requiredToolSatisfied,
  turnRequirementSatisfied,
  programmingAcceptanceStatus,
  phasedProgrammingBuildStatus,
  pendingDeliveryForWork,
  executionGroundedDependencies,
  incompleteRequestedOutcomeFeedback,
  globalToolNameAttempt,
  iterativeArtifactStatus,
  iterativeExecutionObligation,
  iterativeRecoveryResolved,
  iterativeExecutionBudget: (mode) => iterativeExecutionBudget(mode, config.autonomyTurnMinutes),
  missingSourceLinkFeedback,
  monitoringPromiseFeedback: monitoringFeedbackForRequest,
  monitoringFactBindingSpecs,
  monitoringRequestSpec: testMonitoringRequestSpec,
  isScheduledMonitoringItem: testIsScheduledMonitoringItem,
  finalReplyFeedback,
  emptyHomeHistoryReplyFeedback,
  homeEffectAttributionFeedback,
  contributionFromTools,
  oneShotAutoBoundMemoryContribution,
  recordTurnToolObservation,
});
`;

registerHooks({
  load(url, context, nextLoad) {
    const loaded = nextLoad(url, context);
    if (url !== agentModuleUrl || loaded.format !== 'module') return loaded;
    const source = Buffer.isBuffer(loaded.source)
      ? loaded.source.toString('utf8') : String(loaded.source);
    return { ...loaded, source: `${source}\n${testComposition}` };
  },
});
