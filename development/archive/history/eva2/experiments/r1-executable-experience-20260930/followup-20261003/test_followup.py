import asyncio
from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import compare_followup as followup


def budget():
    return SimpleNamespace(calls=[],inflight=0,input=0,output=0,responses=0,
        limits={'maximum_total_input_tokens':600000,'maximum_total_output_tokens':48000})


def execute(results,b=None):
    b=b or budget();called=[];saved=[]
    async def run(case,arm,repeat,*args):
        called.append((repeat,case,arm))
        return deepcopy(results[len(called)-1] if len(called)<=len(results) else {'status':'completed'})
    report={'runs':[]}
    asyncio.run(followup.schedule(cases={'A':'A','B':'B'},artifact={},adapter=None,work=None,contact=None,
        budget=b,limits={},run=run,save=lambda r:saved.append(deepcopy(r)),report=report))
    return called,saved,report


@pytest.mark.parametrize('reason',list(followup.LOCAL_REASONS))
def test_each_local_limit_is_a_failed_outcome_not_missing_other_arms(reason):
    called,saved,result=execute([{'status':'failed','error':'PerRunEnvelopeError','detail':reason}])
    assert called==list(followup.ORDER)
    assert saved[0]['runs'][0]['status']=='failed'
    assert saved[0]['runs'][0]['followupDisposition']['outcome']=='local_envelope_exhausted'
    assert result['status']=='completed_schedule_awaiting_independent_grading'
    assert len(result['runs'])==8


@pytest.mark.parametrize('error,detail',[
    ('BoundaryViolation','private content in shared delivery'),
    ('ValueError','frozen aggregate research envelope reached'),
    ('ValueError','isolated predictor execution failed'),
    ('RuntimeError','unknown provider failure')])
def test_hard_global_and_unexpected_errors_stop_after_persisting(error,detail):
    called,saved,result=execute([{'status':'failed','error':error,'detail':detail}])
    assert len(called)==1 and saved[-1]['runs'][0]['detail']==detail
    assert result['status']=='stopped_no_promotion'


def test_pending_or_unknown_usage_overrules_local_result():
    b=budget();b.inflight=1
    called,_,_=execute([{'status':'failed','error':'PerRunEnvelopeError','detail':'frozen response envelope reached'}],b)
    assert len(called)==1
    b=budget()
    b.input=600001
    called,_,_=execute([{'status':'completed'}],b)
    assert len(called)==1


@pytest.mark.parametrize("social_episode",[11,12])
def test_real_frozen_runner_awaits_and_counts_social_on_limit(monkeypatch,social_episode):
    original_root=HERE.parent;sys.path.insert(0,str(original_root))
    spec=importlib.util.spec_from_file_location('frozen_compare_for_offline_test',original_root/'compare.py')
    original=importlib.util.module_from_spec(spec);spec.loader.exec_module(original)
    monkeypatch.setattr(original,'configured_procedure_executor',lambda:object())
    limits={'maximum_model_responses_per_task_run':12,'maximum_tool_operations_per_task_run':24,
            'maximum_total_input_tokens':600000,'maximum_total_output_tokens':48000}
    b=original.Budget([],limits);social_finished=[]
    async def fake_ask(profile,adapter,budget,prompt,**kwargs):
        budget.admit()
        if kwargs['scope'].endswith(':social'):
            await asyncio.sleep(0);social_finished.append(True)
        usage={'attested':True,'inputTokens':10,'outputTokens':2,'requests':1}
        budget.retain(usage)
        return SimpleNamespace(output='done'),usage
    monkeypatch.setattr(original,'ask',fake_ask)
    episodes=[{'prompt':'synthetic offline control check'} for _ in range(social_episode+1)]
    episodes[-1]['social']={'prompt':'hello'}
    case={'id':'offline','state':{'version':1,'tasks':{}},'episodes':episodes}
    result=asyncio.run(followup.task_runner(original)(case,'ordinary',0,{'ordinary_method':'public method'},None,None,None,b,limits))
    assert result['detail']=='frozen response envelope reached'
    assert social_finished==([True] if social_episode==11 else [])
    if social_episode==11:assert result['episodes'][-1]['socialText']=='done'
    assert b.responses==12 and b.input==120 and b.output==24 and b.inflight==0
    assert followup.disposition(result,b,0).outcome==followup.RunOutcome.LOCAL_ENVELOPE_EXHAUSTED


@pytest.mark.parametrize('problem',['unattested','social','forged_provider'])
def test_local_label_never_hides_unknown_usage_social_or_provider_error(problem):
    b=budget();called=[];saved=[]
    async def run(*args):
        called.append(True)
        result={'status':'failed','error':'PerRunEnvelopeError','detail':'frozen response envelope reached'}
        if problem=='unattested':b.calls.append({'attested':False,'inputTokens':None,'outputTokens':None})
        if problem=='social':result['socialError']='BoundaryViolation'
        if problem=='forged_provider':result['error']='ValueError'
        return result
    asyncio.run(followup.schedule(cases={'A':'A','B':'B'},artifact={},adapter=None,work=None,contact=None,
        budget=b,limits={},run=run,save=lambda r:saved.append(deepcopy(r)),report={'runs':[]}))
    assert len(called)==1 and saved[-1]['status']=='stopped_no_promotion'


def test_independent_boundary_check_stops_before_next_arm_and_retains_result():
    b=budget();calls=[];saved=[]
    async def run(*args):calls.append(True);return {'status':'completed','evidence':'retained'}
    report=asyncio.run(followup.schedule(cases={'A':'A','B':'B'},artifact={},adapter=None,work=None,contact=None,
        budget=b,limits={},run=run,save=lambda r:saved.append(deepcopy(r)),report={'runs':[]},grade_stop=lambda result:True))
    assert len(calls)==1 and saved[-1]['runs'][0]['evidence']=='retained'
    assert report['status']=='stopped_independent_hard_boundary_no_promotion'


def test_resume_runs_only_unused_positions_and_preserves_failed_prefix():
    retained={'case':'A','arm':'ordinary','repeat':0,'status':'failed',
              'error':'ValueError','detail':'retained local failure','seconds':0.01}
    report={'runs':[deepcopy(retained)]};called=[];saved=[]
    async def run(case,arm,repeat,*args):
        called.append((repeat,case,arm))
        return {'case':case,'arm':arm,'repeat':repeat,'status':'completed'}
    result=asyncio.run(followup.schedule(cases={'A':'A','B':'B'},artifact={},adapter=None,
        work=None,contact=None,budget=budget(),limits={},run=run,
        save=lambda r:saved.append(deepcopy(r)),report=report))
    assert called==list(followup.ORDER[1:])
    assert result['runs'][0]==retained and len(result['runs'])==8
    assert result['status']=='completed_schedule_awaiting_independent_grading'
    assert all(snapshot['runs'][0]==retained for snapshot in saved)


@pytest.mark.parametrize('prefix',[
    [{'case':'B','arm':'ordinary','repeat':0,'status':'failed'}],
    [{'case':'A','arm':'ordinary','repeat':0,'status':'running'}],
    [{'case':'A','arm':'ordinary','repeat':True,'status':'completed'}],
])
def test_invalid_or_unfinished_prefix_cannot_start_a_call(prefix):
    # bool is not a valid schedule index even though Python bool compares as int.
    if prefix[0]['repeat'] is True:
        with pytest.raises(ValueError):followup.schedule_offset(prefix)
    else:
        with pytest.raises(ValueError):followup.schedule_offset(prefix)


def test_no_dispatch_settlement_keeps_raw_unknown_attempt_and_counts_all_usage():
    calls=[{'attested':True,'inputTokens':101,'outputTokens':7,'seconds':1.2},
           {'attested':False,'inputTokens':None,'outputTokens':None,'seconds':0.001}]
    original=deepcopy(calls)
    finding={'callIndex':1,'callSha256':followup.row_sha(calls[1]),
             'classification':'local_pre_dispatch_failure','inputTokens':0,'outputTokens':0}
    totals=followup.accounting(calls,[finding])
    assert totals=={'inputTokens':101,'outputTokens':7,'responses':1,
                   'attempts':2,'settledLocalFailures':1}
    assert calls==original
    b=followup.seed_budget(SimpleNamespace(Budget=lambda initial,limits:budget()),calls,[finding],
        {'maximum_total_input_tokens':1000,'maximum_total_output_tokens':100})
    assert b.calls==original and b.calls[1]['inputTokens'] is None
    assert (b.input,b.output,b.responses)==(101,7,1)


@pytest.mark.parametrize('problem',['missing','wrong_hash','nonzero','attested','duplicate','bool'])
def test_unproven_or_inconsistent_settlement_is_rejected(problem):
    row={'attested':False,'inputTokens':None,'outputTokens':None}
    finding={'callIndex':0,'callSha256':followup.row_sha(row),
             'classification':'local_pre_dispatch_failure','inputTokens':0,'outputTokens':0}
    findings=[finding]
    if problem=='missing':findings=[]
    if problem=='wrong_hash':finding['callSha256']='incorrect'
    if problem=='nonzero':finding['inputTokens']=1
    if problem=='attested':
        row.update(attested=True,inputTokens=0,outputTokens=0)
        finding['callSha256']=followup.row_sha(row)
    if problem=='duplicate':findings=[finding,finding.copy()]
    if problem=='bool':finding['outputTokens']=False
    with pytest.raises(ValueError):followup.accounting([row],findings)


def resume_fixture(tmp_path):
    import json
    def write(name,value):
        path=tmp_path/name;path.write_text(json.dumps(value));return path
    calls=[{'attested':True,'inputTokens':2160,'outputTokens':361,'scope':str(index)}
           for index in range(14)]
    calls[0]['inputTokens']+=30226-sum(row['inputTokens'] for row in calls)
    calls[0]['outputTokens']+=5057-sum(row['outputTokens'] for row in calls)
    write('comparison-report.json',{'usage':{'calls':calls}})
    keys={'artifactSha256':'artifact','criteriaSha256':'criteria',
          'casesSha256':'cases','scorerSha256':'scorer'}
    used={**keys,'decision':'GO','previousTaskRuns':1,'followupTaskRuns':8,
          'historicalTaskRunsTotal':9,'taskRunAccountingApproved':True,
          'freshIndependentCases':True}
    used_path=write('used-admission.json',used)
    raw_calls=calls+[{'attested':True,'inputTokens':6267,'outputTokens':133},
                     {'attested':False,'inputTokens':None,'outputTokens':None,'seconds':0.001}]
    failed={'case':'A','arm':'ordinary','repeat':0,'status':'failed','error':'ValueError'}
    old_path=write('closed-report.json',{'admission':used,'runs':[failed],
        'usage':{'calls':raw_calls},'status':'stopped_no_promotion'})
    findings=[{'callIndex':15,'callSha256':followup.row_sha(raw_calls[15]),
        'classification':'local_pre_dispatch_failure','inputTokens':0,'outputTokens':0}]
    settlement_path=write('settlement.json',{'decision':'GO','reportSha256':followup.sha(old_path),
        'usedAdmissionSha256':followup.sha(used_path),'settlements':findings})
    admission={**used,'previousTaskRuns':2,'remainingTaskRuns':7,
        'maximumTotalInputTokens':600000,'maximumTotalOutputTokens':48000,
        'cumulativeUsage':followup.accounting(raw_calls,findings),
        'resume':{'reportPath':str(old_path),'reportSha256':followup.sha(old_path),
            'usedAdmissionPath':str(used_path),'usedAdmissionSha256':followup.sha(used_path),
            'settlementPath':str(settlement_path),'settlementSha256':followup.sha(settlement_path)}}
    return admission,raw_calls,failed


def test_cumulative_history_is_not_counted_twice_and_closed_run_is_retained(tmp_path):
    admission,calls,failed=resume_fixture(tmp_path)
    retained,runs,settlements,totals=followup.resume_history(tmp_path,admission)
    assert retained==calls and runs==[failed]
    assert totals['inputTokens']==36493 and totals['outputTokens']==5190
    assert totals['attempts']==16 and totals['responses']==15
    assert retained[-1]['inputTokens'] is None and len(settlements)==1


@pytest.mark.parametrize('problem',['history_changed','admission_changed','settlement_changed',
    'scope_changed','fresh_eight','wrong_total','lost_original_prefix','retained_result_changed'])
def test_source_or_scope_drift_is_rejected_before_execution(tmp_path,problem):
    import json
    admission,calls,failed=resume_fixture(tmp_path);binding=admission['resume']
    if problem.endswith('_changed') and problem.split('_')[0] in ('history','admission','settlement'):
        key={'history':'reportPath','admission':'usedAdmissionPath',
             'settlement':'settlementPath'}[problem.split('_')[0]]
        Path(binding[key]).write_text('{}')
    elif problem=='scope_changed':admission['scorerSha256']='different'
    elif problem=='fresh_eight':admission['remainingTaskRuns']=8
    elif problem=='wrong_total':admission['cumulativeUsage']['inputTokens']=0
    elif problem=='lost_original_prefix':
        path=Path(binding['reportPath']);value=json.loads(path.read_text())
        value['usage']['calls'].pop(0);path.write_text(json.dumps(value))
        binding['reportSha256']=followup.sha(path)
    elif problem=='retained_result_changed':
        path=Path(binding['reportPath']);value=json.loads(path.read_text())
        value['runs'][0]['case']='B';path.write_text(json.dumps(value))
        binding['reportSha256']=followup.sha(path)
    with pytest.raises(ValueError):followup.resume_history(tmp_path,admission)


def test_exact_frozen_construction_schema_is_counted_without_normalizing_raw_rows():
    import json
    original=json.loads((HERE.parent/'comparison-report.json').read_bytes())
    calls=original['usage']['calls'];raw=deepcopy(calls)
    totals=followup.accounting(calls,[])
    assert (totals['inputTokens'],totals['outputTokens'],totals['responses'])==(30226,5057,14)
    assert 'attested' not in calls[0] and 'attested' not in calls[1]
    assert calls==raw
    calls[0]['inputTokens']+=1
    with pytest.raises(ValueError):followup.accounting(calls,[])
    with pytest.raises(ValueError):followup.accounting(raw[1:]+[raw[0]],[])
