"""Start scheduled execution budgets at admission while preserving replay identity."""
from eva.adapters.postgres.delegated_work import DelegatedWorkNotFound
from eva.contracts.timestamps import parse_utc_timestamp


def scheduled_creation_time(reserved_at, requested_at, now, load_existing):
    if reserved_at is None:
        return parse_utc_timestamp(requested_at)
    try:
        existing = load_existing()
    except DelegatedWorkNotFound:
        # A durable launch reservation is not an admitted task. Downtime before
        # admission must not consume the execution budget of a never-started step.
        return now()
    # Never refresh the deadline of an already admitted attempt. Full spec and
    # current authority validation remain the responsibility of store.admit.
    return parse_utc_timestamp(existing.spec.created_at)
