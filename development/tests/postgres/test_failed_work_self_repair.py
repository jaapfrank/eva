"""A real caught work error becomes a repair goal while failure evidence stays intact."""
from dataclasses import replace

import pytest

from eva.application.work_failure_notice import delivery_outcome
from eva.bootstrap.self_repair import observe_failed_work
from eva.contracts.timestamps import parse_utc_timestamp
from eva.contracts.delegated_work import DelegatedWorkUsage
from tests.postgres.test_autonomous_self_repair import database,postgres_engine,native_digital,costless_native,prepare,NOW
from tests.postgres.test_native_initiative_digital_work import started


def test_caught_private_work_error_queues_once_and_returns_a_truthful_short_notice(native_digital):
    f,repair,turn,journal,_=prepare(native_digital)
    admitted=f.service.execute('digital',turn,task='Synthetic private work failure intake',request_key='repair-intake-work')
    work=f.store.load(admitted['work']['work_id'],**f.service._owner)
    call=started(f,work)
    try:parse_utc_timestamp('PRIVATE_TEST_INVALID_TIMESTAMP')
    except ValueError as error:
        first=observe_failed_work(f.store,f.service,turn,work.spec,error,clock=lambda:NOW)
        second=observe_failed_work(f.store,f.service,turn,work.spec,error,clock=lambda:NOW)
    assert first['created'] and first['admitted'] and not second['created']
    usage=DelegatedWorkUsage.unmeasured()
    failed=f.store.record_failure(call,usage=usage,error_code='internal_error',
        error_fingerprint='9'*64,ambiguous=False,now=NOW)
    assert failed.state=='failed' and failed.result is None and failed.usage==usage
    assert failed.error_code=='internal_error' and failed.error_fingerprint=='9'*64
    notice=delivery_outcome(failed)
    assert notice.state=='failed' and 'herstel voor mezelf' in notice.output
    assert len(notice.output)<180 and 'PRIVATE_TEST' not in notice.output
    assert f.store.load(work.spec.work_id,**f.service._owner).repair_notice==failed.repair_notice
    assert len([e for e in repair.timeline.events(first['incident_id']) if e.event_type=='SELF_REPAIR_OBSERVED'])==1
    # An uncertain effect keeps its warning; the sidecar is never an outcome.
    uncertain=replace(failed,state='ambiguous')
    assert 'niet nog eens' in delivery_outcome(uncertain).output
