const uuid = value => typeof value === 'string' && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value);
const date = value => typeof value === 'string' && Number.isFinite(Date.parse(value));
const bounded = (value, maximum) => typeof value === 'string' && value.trim().length > 0 && value.length <= maximum;

export function validHousemateState(value) {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value)
    && value.contract === 'EvaOwnerHousemateState/v1' && date(value.checked_at)
    && value.learning_limit === 8 && value.learning_coverage === 'recent_current_authorized_heads'
    && value.curiosity_source === 'current_open_project_questions'
    && value.shared_trace === null && value.shared_trace_state === 'no_separate_registered_trace_route'
    && Array.isArray(value.personality_deltas) && value.personality_deltas.length === 0
    && value.personality_deltas_state === 'no_separate_typed_delta_route'
    && Array.isArray(value.current_learning) && value.current_learning.length <= value.learning_limit
    && value.current_learning.every(item => item && uuid(item.source_id)
      && ['person_hypothesis', 'expectation'].includes(item.kind)
      && bounded(item.text, 1600) && bounded(item.uncertainty_reason, 600)
      && ['open', 'confirmed', 'refuted', 'unknown'].includes(item.outcome)
      && date(item.recorded_at) && (item.review_at === null || date(item.review_at))
      && typeof item.revised === 'boolean' && typeof item.retention_state === 'string'
      && item.is_execution_permission === false));
}
