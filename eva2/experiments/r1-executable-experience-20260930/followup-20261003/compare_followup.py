"""Separate R1 scheduler: retain bounded run failures without skipping other arms.

The frozen task runner owns every provider/tool/guard call and awaits social work.
This wrapper changes only the decision to schedule the next fixed run.
"""
import asyncio
from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
import hashlib
import importlib.util
import inspect
import json
import os
from pathlib import Path
import sys

ORDER=((0,'A','ordinary'),(0,'A','predictor'),(0,'B','predictor'),(0,'B','ordinary'),
       (1,'A','predictor'),(1,'A','ordinary'),(1,'B','ordinary'),(1,'B','predictor'))
# The frozen generator retained measured usage before the attested field existed.
# Only these two exact construction rows use that historical schema.
LEGACY_CONSTRUCTION_CALLS={
    0:'8abf8098fab194b1af8e47f8e28dd2452b6a01c698b80fb73c7172510f7d50b3',
    1:'1d524f78f0751466f99184985e1584c49cf31911e84f45d05ab05d350e21dc5e',
}
LOCAL_REASONS={
    'frozen response envelope reached':'responses',
    'frozen tool envelope reached':'tools',
    'frozen prediction envelope reached':'predictions',
    'frozen tool-result envelope reached':'tool_result_characters',
}


class PerRunEnvelopeError(ValueError):
    """Only four local counters in the source-bound task runner raise this."""


def task_runner(original):
    # Reuse the frozen function body with exactly reviewed admission changes.
    # No model text/provider exception is classified as a local host counter.
    source=inspect.getsource(original.task_run)
    for text in LOCAL_REASONS:
        old='raise ValueError('+repr(text)+')'
        old=old.replace("'",'"')
        if source.count(old)!=1:raise ValueError('frozen local counter source differs')
        source=source.replace(old,'raise PerRunEnvelopeError('+repr(text)+')')
    needle='            if episode.get("social"):\n'
    if source.count(needle)!=1:raise ValueError('frozen social admission source differs')
    source=source.replace(needle,needle+
        '                if record["modelResponses"] >= limits["maximum_model_responses_per_task_run"]:\n'
        '                    raise PerRunEnvelopeError("frozen response envelope reached")\n')
    namespace={**original.__dict__,'PerRunEnvelopeError':PerRunEnvelopeError}
    exec(compile(source,'<r1-reviewed-followup-task-run>','exec'),namespace)
    return namespace['task_run']


class RunOutcome(str,Enum):
    COMPLETED='completed'
    LOCAL_ENVELOPE_EXHAUSTED='local_envelope_exhausted'
    STOP_ROUND='stop_round'


@dataclass(frozen=True)
class Disposition:
    outcome:RunOutcome
    reason:str


def disposition(result,budget,call_start):
    # Do not let a known local counter hide a concurrent failed/unattested call.
    calls=budget.calls[call_start:]
    if budget.inflight or any(row.get('attested') is not True
            or type(row.get('inputTokens')) is not int or type(row.get('outputTokens')) is not int
            for row in calls):
        return Disposition(RunOutcome.STOP_ROUND,'unsettled_or_unattested_usage')
    if (budget.input>budget.limits['maximum_total_input_tokens']
            or budget.output>budget.limits['maximum_total_output_tokens']):
        return Disposition(RunOutcome.STOP_ROUND,'aggregate_overrun')
    if any(isinstance(row.get('result'),dict) and row['result'].get('hardBoundaryViolation') for row in result.get('trace',[])):
        return Disposition(RunOutcome.STOP_ROUND,'hard_trace_violation')
    if result.get('socialError'):
        return Disposition(RunOutcome.STOP_ROUND,'social_failure')
    if result.get('status')=='completed':return Disposition(RunOutcome.COMPLETED,'completed')
    # These exact strings are raised by the source-hash-bound frozen host runner,
    # never by model output. No arbitrary ValueError becomes retryable.
    if result.get('error')=='PerRunEnvelopeError' and result.get('detail') in LOCAL_REASONS:
        return Disposition(RunOutcome.LOCAL_ENVELOPE_EXHAUSTED,LOCAL_REASONS[result['detail']])
    return Disposition(RunOutcome.STOP_ROUND,'hard_provider_or_harness_failure')


async def schedule(*,cases,artifact,adapter,work,contact,budget,limits,run,save,report,grade_stop=None):
    # A retained prefix is part of the outcome, including a failed first run.
    # Resuming never retries it or turns it into a successful observation.
    offset=schedule_offset(report.get('runs',[]))
    try:
        for repeat,name,arm in ORDER[offset:]:
            start=len(budget.calls)
            result=await run(cases[name],arm,repeat,artifact,adapter,work,contact,budget,limits)
            verdict=disposition(result,budget,start)
            result['followupDisposition']={'outcome':verdict.outcome.value,'reason':verdict.reason}
            report['runs'].append(result)
            report['usage']={'inputTokens':budget.input,'outputTokens':budget.output,
                             'responses':budget.responses,'calls':budget.calls}
            report['status']='stopped_no_promotion' if verdict.outcome==RunOutcome.STOP_ROUND else 'running'
            save(report)
            if grade_stop is not None:
                stopped=grade_stop(result)
                if type(stopped) is not bool:raise ValueError('independent hard-stop result invalid')
                if stopped:
                    report['status']='stopped_independent_hard_boundary_no_promotion'
                    save(report);return report
            if verdict.outcome==RunOutcome.STOP_ROUND:return report
        report['status']='completed_schedule_awaiting_independent_grading'
        # Completed schedule can contain failed task outcomes. It never promotes.
        save(report);return report
    except BaseException as error:
        report['status']='stopped_unexpected_error_no_promotion'
        report['schedulerError']=type(error).__name__
        report['usage']={'inputTokens':budget.input,'outputTokens':budget.output,
                         'responses':budget.responses,'calls':budget.calls}
        save(report)
        raise



def schedule_offset(runs):
    if not isinstance(runs,list) or len(runs)>len(ORDER):
        raise ValueError('retained schedule prefix invalid')
    for expected,row in zip(ORDER,runs):
        if (type(row.get('repeat')) is not int or (row.get('repeat'),row.get('case'),row.get('arm'))!=expected
                or row.get('status') not in ('completed','failed')):
            raise ValueError('retained schedule prefix differs')
    return len(runs)


def row_sha(row):
    return hashlib.sha256(json.dumps(row,sort_keys=True,separators=(',',':'),
        ensure_ascii=False).encode()).hexdigest()


def accounting(calls,settlements):
    """Resolve only source-bound independent no-dispatch findings; retain raw rows."""
    if not isinstance(calls,list) or not isinstance(settlements,list):
        raise ValueError('historical accounting invalid')
    resolved={}
    for finding in settlements:
        index=finding.get('callIndex')
        if (type(index) is not int or index<0 or index>=len(calls) or index in resolved
                or finding.get('callSha256')!=row_sha(calls[index])
                or finding.get('classification')!='local_pre_dispatch_failure'
                or type(finding.get('inputTokens')) is not int or finding['inputTokens']!=0
                or type(finding.get('outputTokens')) is not int or finding['outputTokens']!=0):
            raise ValueError('independent usage settlement invalid')
        resolved[index]=finding
    input_tokens=output_tokens=responses=0
    for index,row in enumerate(calls):
        measured=(row.get('attested') is True or ('attested' not in row
            and LEGACY_CONSTRUCTION_CALLS.get(index)==row_sha(row)))
        if (measured and type(row.get('inputTokens')) is int
                and type(row.get('outputTokens')) is int
                and row['inputTokens']>=0 and row['outputTokens']>=0):
            if index in resolved:raise ValueError('attested usage must not be replaced')
            input_tokens+=row['inputTokens'];output_tokens+=row['outputTokens'];responses+=1
        elif (row.get('attested') is False and row.get('inputTokens') is None
                and row.get('outputTokens') is None and index in resolved):
            # Zero provider use is a separate dispatch finding, not a provider
            # attestation. The failed attempt and its duration remain unchanged.
            pass
        else:raise ValueError('historical provider usage remains unsettled')
    return {'inputTokens':input_tokens,'outputTokens':output_tokens,
        'responses':responses,'attempts':len(calls),'settledLocalFailures':len(resolved)}


def bound_json(path,digest,label):
    if not isinstance(digest,str) or sha(path)!=digest:
        raise ValueError(label+' source differs')
    return json.loads(Path(path).read_bytes())


def resume_history(root,admission):
    original=json.loads((root/'comparison-report.json').read_bytes())
    original_calls=original['usage']['calls']
    original_totals=accounting(original_calls,[])
    if (original_totals['inputTokens'],original_totals['outputTokens'],
            original_totals['responses'])!=(30226,5057,14):
        raise ValueError('historical original accounting differs')
    binding=admission.get('resume')
    if binding is None:return deepcopy(original_calls),[],[],original_totals
    previous=bound_json(binding['reportPath'],binding['reportSha256'],'closed followup report')
    used=bound_json(binding['usedAdmissionPath'],binding['usedAdmissionSha256'],'used admission')
    if (used.get('decision')!='GO' or used.get('previousTaskRuns')!=1
            or used.get('followupTaskRuns')!=8 or used.get('historicalTaskRunsTotal')!=9
            or used.get('taskRunAccountingApproved') is not True
            or previous.get('admission')!=used
            or any(used.get(key)!=admission.get(key) for key in
                ('artifactSha256','criteriaSha256','casesSha256','scorerSha256'))
            or used.get('freshIndependentCases') is not True):
        raise ValueError('original followup admission binding differs')
    runs=previous['runs'];offset=schedule_offset(runs)
    if (offset!=1 or previous.get('status') not in
            ('stopped_no_promotion','stopped_independent_hard_boundary_no_promotion')
            or admission.get('previousTaskRuns')!=1+offset
            or admission.get('remainingTaskRuns')!=len(ORDER)-offset):
        raise ValueError('only unused original followup positions may resume')
    calls=previous['usage']['calls']
    if calls[:len(original_calls)]!=original_calls:
        raise ValueError('original accounting prefix differs')
    findings=bound_json(binding['settlementPath'],binding['settlementSha256'],'independent settlement')
    if (findings.get('decision')!='GO' or findings.get('reportSha256')!=binding['reportSha256']
            or findings.get('usedAdmissionSha256')!=binding['usedAdmissionSha256']):
        raise ValueError('independent cumulative accounting approval required')
    settlements=findings['settlements'];totals=accounting(calls,settlements)
    if (admission.get('cumulativeUsage')!=totals
            or totals['inputTokens']>admission['maximumTotalInputTokens']
            or totals['outputTokens']>admission['maximumTotalOutputTokens']):
        raise ValueError('cumulative admitted accounting differs')
    return deepcopy(calls),deepcopy(runs),deepcopy(settlements),totals


def seed_budget(original,calls,settlements,limits):
    totals=accounting(calls,settlements)
    if (totals['inputTokens']>limits['maximum_total_input_tokens']
            or totals['outputTokens']>limits['maximum_total_output_tokens']):
        raise ValueError('historical research envelope exceeded')
    budget=original.Budget([],limits)
    budget.calls=deepcopy(calls)
    budget.input=totals['inputTokens'];budget.output=totals['outputTokens']
    budget.responses=totals['responses']
    return budget


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def admitted_inputs(admission_path,cases_path,scorer_path):
    root=Path(__file__).resolve().parent.parent
    admission=json.loads(Path(admission_path).read_bytes())
    freeze=json.loads((root/'freeze.json').read_bytes())
    for name,digest in freeze['files'].items():
        if sha(root/name)!=digest:raise ValueError('original frozen file differs')
    if (admission.get('decision')!='GO'
            or admission.get('driverSha256')!=sha(__file__)
            or admission.get('artifactSha256')!=sha(root/'generated-artifact.json')
            or admission.get('criteriaSha256')!=sha(root/'criteria.json')
            or admission.get('casesSha256')!=sha(cases_path)
            or admission.get('scorerSha256')!=sha(scorer_path)
            or admission['casesSha256']==freeze['curator']['casesSha256']
            or admission.get('freshIndependentCases') is not True
            or admission.get('previousTaskRuns')!=(2 if admission.get('resume') else 1)
            or (admission.get('resume') and admission.get('remainingTaskRuns')!=7)
            or admission.get('followupTaskRuns')!=8
            or admission.get('historicalTaskRunsTotal')!=9
            or admission.get('taskRunAccountingApproved') is not True):
        raise ValueError('independent fresh-case/source/budget admission required')
    # The independent evaluator owns this decision; no default GO is supplied.
    return root,admission


async def main():
    admission_path,cases_path,scorer_path,output_path=map(Path,sys.argv[1:])
    if output_path.exists():raise ValueError('retain previous result; never overwrite or retry')
    root,admission=admitted_inputs(admission_path,cases_path,scorer_path)
    scorer_spec=importlib.util.spec_from_file_location("r1_independent_guard",scorer_path)
    scorer=importlib.util.module_from_spec(scorer_spec);scorer_spec.loader.exec_module(scorer)
    if not callable(getattr(scorer,"should_stop",None)):raise ValueError("independent guard unavailable")
    # Only execution after independent admission loads protected cases.
    cases={row['id']:row for row in json.loads(cases_path.read_bytes())}
    if set(cases)!={'A','B'}:raise ValueError('same two-case comparison required')
    sys.path.insert(0,str(root))
    spec=importlib.util.spec_from_file_location('r1_original_compare',root/'compare.py')
    original=importlib.util.module_from_spec(spec);spec.loader.exec_module(original)
    criteria=json.loads((root/'criteria.json').read_bytes());limits=criteria['budget']
    if limits['maximum_task_runs']!=len(ORDER):raise ValueError('fixed schedule differs')
    if admission.get('resume') and (
            admission.get('maximumTotalInputTokens')!=limits['maximum_total_input_tokens']
            or admission.get('maximumTotalOutputTokens')!=limits['maximum_total_output_tokens']):
        raise ValueError('original aggregate envelope differs')
    historical,retained_runs,settlements,totals=resume_history(root,admission)
    settings=original.RuntimeSettings.from_environment()
    adapter=original.PydanticConversationModel(auth_file=Path(settings.conversation_auth_file),model=settings.conversation_model)
    work=original.OpenAICodexProviderSettings(endpoint=adapter._endpoint,model_name=settings.conversation_model,
        reasoning_effort=settings.conversation_reasoning_effort,timeout_seconds=adapter._research_timeout_seconds,maximum_request_bytes=65536)
    contact=original.OpenAICodexProviderSettings(endpoint=adapter._endpoint,model_name=settings.conversation_contact_model,
        reasoning_effort=settings.conversation_contact_reasoning_effort,timeout_seconds=adapter._timeout_seconds,maximum_request_bytes=65536)
    if (work.model_name,work.reasoning_effort,contact.model_name,contact.reasoning_effort)!=('gpt-6-astra','medium','gpt-6-sol','none'):
        raise ValueError('frozen provider profiles differ')
    budget=seed_budget(original,historical,settlements,limits)
    report={'kind':'isolated_R1_followup_not_installed_Eva','status':'prepared','runs':retained_runs,
        'admission':admission,'historicalUsageIncluded':True,
        'historicalTaskRuns':admission['previousTaskRuns'],'plannedNewTaskRuns':len(ORDER)-len(retained_runs),
        'historicalSettlements':settlements,'historicalAccounting':totals,
        'usage':{'inputTokens':budget.input,'outputTokens':budget.output,
                 'responses':budget.responses,'calls':budget.calls},
        'promoted':False,'creditCost':None,'developerAssistantCredits':None}
    def save(value):
        temporary=output_path.with_name(output_path.name+'.writing')
        with temporary.open('w',encoding='utf-8') as handle:
            handle.write(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
            handle.flush();os.fsync(handle.fileno())
        temporary.replace(output_path)
    # Single-use source-bound admission: a different output name cannot silently
    # replay the same remaining positions. Claim only after offline validation.
    claim_path=admission_path.with_name(admission_path.name+'.consumed')
    with claim_path.open('x',encoding='utf-8') as handle:
        handle.write(json.dumps({'admissionSha256':sha(admission_path),
            'outputPath':str(output_path)})+'\n')
        handle.flush();os.fsync(handle.fileno())
    save(report)
    await schedule(cases=cases,artifact=json.loads((root/'generated-artifact.json').read_bytes()),adapter=adapter,
        work=work,contact=contact,budget=budget,limits=limits,run=task_runner(original),save=save,report=report,grade_stop=scorer.should_stop)


if __name__=='__main__':asyncio.run(main())
