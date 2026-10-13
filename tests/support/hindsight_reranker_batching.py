"""Replay fixed private candidate texts against existing native batching options.

Run inside the installed Hindsight image with the fixture JSON on stdin and
HF_HUB_OFFLINE/TRANSFORMERS_OFFLINE enabled. Output contains only aggregate
latency and ranking equivalence; source texts remain private. No API calls or
runtime settings are changed. Use the fixture digest to bind the comparison.
"""
import asyncio,json,time,statistics,hashlib,sys
from pathlib import Path
import torch
from hindsight_api.engine.cross_encoder import LocalSTCrossEncoder
raw_fixture=sys.stdin.buffer.read();fixture=json.loads(raw_fixture)
fixture_sha=hashlib.sha256(raw_fixture).hexdigest()
async def main():
 model=LocalSTCrossEncoder(force_cpu=True,fp16=False,bucket_batching=False,batch_size=32)
 await model.initialize()
 settings=[('baseline',False,32),('bucket16',True,16),('bucket8',True,8)]
 result={'fixtureSha256':fixture_sha,'candidateCount':len(fixture['texts']),'queries':len(fixture['queries']),'torchVersion':torch.__version__,'intraopThreads':torch.get_num_threads(),'settings':[],'providerRequests':0}
 baselines={}
 for name,bucket,size in settings:
  model.bucket_batching=bucket;model.batch_size=size
  measurements=[]
  for i,query in enumerate(fixture['queries']):
   pairs=[(query,text) for text in fixture['texts']]
   await model.predict(pairs)
   scores=None;durations=[]
   for _ in range(3):
    started=time.perf_counter();scores=await model.predict(pairs);durations.append((time.perf_counter()-started)*1000)
   rank=sorted(range(len(scores)),key=lambda j:(-scores[j],j))
   if name=='baseline':baselines[i]=(scores,rank)
   reference,reference_rank=baselines[i]
   measurements.append({'queryIndex':i,'runsMs':[round(v,3) for v in durations],'medianMs':round(statistics.median(durations),3),'sameFullRanking':rank==reference_rank,'maximumScoreDifference':max(abs(a-b) for a,b in zip(reference,scores))})
  result['settings'].append({'name':name,'bucketBatching':bucket,'batchSize':size,'tasks':measurements})
 print('BENCHMARK_RESULT '+json.dumps(result))
asyncio.run(main())
