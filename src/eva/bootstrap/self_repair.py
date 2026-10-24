"""Failure intake using the existing owner initiative and development execution."""
import logging
from pathlib import Path

from eva.application.conversation import GroundedReply, ConversationStopped
from eva.application.self_repair import RepairObservation

_LOG = logging.getLogger(__name__)


def compose_self_repair(runtime):
    service = runtime._delegated_work_owner_service
    if service is None or service.digital_profile is None:
        return None
    authority = runtime._initiative_authority()
    try:
        policy, _ = authority.policy()
    except Exception:
        return None
    if policy.get('self_repair', {}).get('enabled') is not True:
        return None
    from eva.adapters.postgres.self_repair import PostgresSelfRepair
    return PostgresSelfRepair(authority, service)


def repair_failed_turn(runtime, turn, error):
    repair = getattr(runtime, '_self_repair', None)
    from eva.application.cancellation import WorkInterrupted
    if repair is None or isinstance(error, (ConversationStopped, WorkInterrupted)):
        return None
    try:
        observation = RepairObservation.from_error(error,
            source_root=Path(__file__).resolve().parents[1])
        journal = runtime._journal(turn)
        with journal.execution(turn):
            # A concurrent saved answer remains authoritative. No original
            # request or uncertain external effect is dispatched again.
            committed = journal.committed_reply(turn)
            if committed is not None:
                return committed
            result = repair.observe(turn, observation)
            if result is None or not result['created']:
                return None
            text = ('Ik ben tegen een probleempje aangelopen. Ik ga uitzoeken wat er misgaat '
                    'en het bij mezelf herstellen.' if result['admitted'] else
                    'Ik ben tegen een probleempje aangelopen. Ik heb het vastgelegd en pak '
                    'het herstel op zodra mijn lopende werk klaar is.')
            return journal.commit_reply(turn, GroundedReply(
                conversation_id=turn.conversation_id, message_id=turn.message_id,
                text=text, evidence_memory_ids=(), generated_at=turn.occurred_at,
                provider='runtime:self-repair-admitted', model_usage=journal.model_usage(turn)))
    except Exception:
        # Preserve the original failure when incident persistence/authority is
        # unavailable. Never recursively repair the intake itself or promise work.
        _LOG.debug('Automatic repair intake unavailable')
        return None


def observe_failed_work(store, service, source, spec, error, *, clock):
    """Capture the actual caught error before the existing worker returns failure.

    This does not replay the original work, convert failure to success or start
    another model. The same durable repair intake later claims the native lane.
    """
    from eva.contracts.delegated_digital_work import DelegatedInitiativeDigitalWorkSpec
    if (isinstance(spec, DelegatedInitiativeDigitalWorkSpec) or source is None
            or source.audience.kind != 'private_person' or source.project_id is not None):
        return None
    try:
        authority=store._initiative_authority_loader()
        from eva.adapters.postgres.self_repair import PostgresSelfRepair, record_failed_work_repair_notice
        observation=RepairObservation.from_error(error,source_root=Path(__file__).resolve().parents[1])
        result=PostgresSelfRepair(authority,service,clock=clock).observe(source,observation)
        if result is not None and result['created']:
            record_failed_work_repair_notice(store,spec,result,now=clock())
        return result
    except Exception:
        _LOG.debug('Automatic work repair intake unavailable')
        return None
