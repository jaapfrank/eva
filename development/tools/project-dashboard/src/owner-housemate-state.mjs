const uuid = value => typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value);
const date = value => typeof value === 'string' && Number.isFinite(Date.parse(value));
const bounded = (value, maximum) => typeof value === 'string' && value.trim().length > 0 && value.length <= maximum;
const record = (item, kinds) => Boolean(item && uuid(item.source_id) && kinds.includes(item.kind)
  && bounded(item.text, item.kind === 'personality_delta' ? 400 : 1600) && bounded(item.uncertainty_reason, 600)
  && ['open', 'confirmed', 'refuted', 'unknown'].includes(item.outcome)
  && date(item.recorded_at) && (item.review_at === null || date(item.review_at))
  && typeof item.revised === 'boolean' && bounded(item.retention_state, 80) && item.is_execution_permission === false);
const innerState = value => Boolean(value && (value.current_curiosity === null || bounded(value.current_curiosity, 160))
  && ['own_project_needs_attention', 'continuing_own_project', 'adjusting_from_personal_feedback'].includes(value.recent_tone)
  && date(value.last_updated) && Number.isInteger(value.evidence_count) && value.evidence_count >= 1 && value.evidence_count <= 5
  && Array.isArray(value.open_threads) && value.open_threads.length <= 2
  && value.open_threads.every(item => item && bounded(item.intent_id, 200) && ['working', 'attention'].includes(item.status)
    && bounded(item.phase, 200) && date(item.updated_at) && /^[0-9a-f]{64}$/.test(item.source_sha256)));

export function validHousemateState(value) {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value)
    && value.contract === 'EvaOwnerHousemateState/v1' && date(value.checked_at)
    && value.learning_limit === 8 && value.learning_coverage === 'recent_current_authorized_heads'
    && ['current_open_project_questions', 'processed_current_development'].includes(value.curiosity_source)
    && value.shared_trace === null && value.shared_trace_state === 'no_current_authorized_shared_source'
    && ['available', 'not_configured', 'unavailable', 'source_changed', 'no_processed_current_sources'].includes(value.development_state)
    && typeof value.development_attention === 'boolean'
    && ((value.development_state === 'available' && innerState(value.inner_state) && value.curiosity_source === 'processed_current_development')
      || (value.development_state !== 'available' && value.inner_state === null && value.curiosity_source === 'current_open_project_questions'))
    && Array.isArray(value.personality_deltas) && value.personality_deltas.length <= 3
    && value.personality_deltas_state === (value.personality_deltas.length ? 'current_typed_sources' : 'no_current_typed_sources')
    && value.personality_deltas.every(item => record(item, ['personality_delta'])
      && item.contract === 'EvaPersonalityDeltaView/v1' && item.audience === 'own_private_context'
      && item.epistemic_status === 'model_inference_unconfirmed' && Number.isInteger(item.version) && item.version >= 1 && item.version <= 8
      && typeof item.active === 'boolean' && ['processed', 'awaiting_dream', 'inactive'].includes(item.processing_state)
      && (item.active ? item.processing_state !== 'inactive' && item.outcome !== 'refuted' : item.processing_state === 'inactive')
      && (item.processing_state !== 'processed' || value.inner_state !== null))
    && Array.isArray(value.current_learning) && value.current_learning.length <= value.learning_limit
    && value.current_learning.every(item => record(item, ['person_hypothesis', 'expectation']))
    && new Set([...value.current_learning, ...value.personality_deltas].map(item => item.source_id)).size === value.current_learning.length + value.personality_deltas.length);
}
