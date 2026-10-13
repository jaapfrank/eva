"""Read public R1 source/provenance only. Never load cases or call a provider."""
import hashlib
import json
from pathlib import Path
import time


def main():
    started=time.monotonic()
    original=Path(__file__).resolve().parent.parent
    project=original.parent.parent
    freeze=json.loads((original/'freeze.json').read_bytes())
    criteria=json.loads((original/'criteria.json').read_bytes())
    provenance=json.loads((original/'runtime-provenance.json').read_bytes())
    hashes={name:hashlib.sha256((original/name).read_bytes()).hexdigest() for name in freeze['files']}
    matches={name:hashes[name]==digest for name,digest in freeze['files'].items()}
    sources={name:hashlib.sha256((project/'src/eva'/name).read_bytes()).hexdigest()
             for name in provenance['sourceHashes']}
    compare=(original/'compare.py').read_text()
    required=['executor = configured_procedure_executor()',
        'raise ValueError("frozen response envelope reached")',
        'record["modelResponses"] += 1  # Reserve its single response',
        'Agent(session.model, instructions=instructions, retries=0)',
        'service = ReservationService(case)']
    if not all(text in compare for text in required):
        raise ValueError('public harness changed; this diagnosis needs review')
    if not all(matches.values()):raise ValueError('original frozen source differs')
    result={'contract':'EvaR1FollowupPreparation/v1','decision':'NO_UNCHANGED_RETRY',
        'originalFrozenFilesUnchanged':matches,'currentSourceHashes':sources,
        'sameProcedureClient':sources['adapters/connectors/procedure_execution.py']==provenance['sourceHashes']['adapters/connectors/procedure_execution.py'],
        'sameProviderTransport':sources['adapters/model/openai_codex_provider.py']==provenance['sourceHashes']['adapters/model/openai_codex_provider.py'],
        'existingLimits':criteria['budget'],
        'previousAccountedUsage':{'inputTokens':30226,'outputTokens':5057,'attestedRequests':14,'taskRunsStarted':1},
        'liveRuntimeHealthChecked':False,'protectedInputsRead':False,'modelCalls':0,
        'executorCalls':0,'providerRequests':0,'creditCost':None,
        'preparationSeconds':time.monotonic()-started,
        'blocker':'New programming actions are absent from the frozen harness; its shared 12-response run envelope remains unchanged.'}
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
