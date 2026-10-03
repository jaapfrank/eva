"""Separate R1 scheduler: retain bounded run failures without skipping other arms.

The frozen task runner owns every provider/tool/guard call and awaits social work.
This wrapper changes only the decision to schedule the next fixed run.
"""
import asyncio
from dataclasses import dataclass
from enum import Enum
import hashlib
import importlib.util
import inspect
import json
from pathlib import Path
import sys

ORDER=((0,'A','ordinary'),(0,'A','predictor'),(0,'B','predictor'),(0,'B','ordinary'),
       (1,'A','predictor'),(1,'A','ordinary'),(1,'B','ordinary'),(1,'B','predictor'))
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
    try:
        for repeat,name,arm in ORDER:
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
            or admission.get('previousTaskRuns')!=1
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
    previous=json.loads((root/'comparison-report.json').read_bytes())
    historical=previous['usage']['calls']
    if (sum(row['inputTokens'] for row in historical)!=30226
            or sum(row['outputTokens'] for row in historical)!=5057 or len(historical)!=14):
        raise ValueError('historical complete accounting differs')
    settings=original.RuntimeSettings.from_environment()
    adapter=original.PydanticConversationModel(auth_file=Path(settings.conversation_auth_file),model=settings.conversation_model)
    work=original.OpenAICodexProviderSettings(endpoint=adapter._endpoint,model_name=settings.conversation_model,
        reasoning_effort=settings.conversation_reasoning_effort,timeout_seconds=adapter._research_timeout_seconds,maximum_request_bytes=65536)
    contact=original.OpenAICodexProviderSettings(endpoint=adapter._endpoint,model_name=settings.conversation_contact_model,
        reasoning_effort=settings.conversation_contact_reasoning_effort,timeout_seconds=adapter._timeout_seconds,maximum_request_bytes=65536)
    if (work.model_name,work.reasoning_effort,contact.model_name,contact.reasoning_effort)!=('gpt-6-astra','medium','gpt-6-sol','none'):
        raise ValueError('frozen provider profiles differ')
    budget=original.Budget(historical,limits)
    report={'kind':'isolated_R1_followup_not_installed_Eva','status':'prepared','runs':[],
        'admission':admission,'historicalUsageIncluded':True,'historicalTaskRuns':1,'plannedNewTaskRuns':8,
        'promoted':False,'creditCost':None,'developerAssistantCredits':None}
    def save(value):
        output_path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    save(report)
    await schedule(cases=cases,artifact=json.loads((root/'generated-artifact.json').read_bytes()),adapter=adapter,
        work=work,contact=contact,budget=budget,limits=limits,run=task_runner(original),save=save,report=report,grade_stop=scorer.should_stop)


if __name__=='__main__':asyncio.run(main())
