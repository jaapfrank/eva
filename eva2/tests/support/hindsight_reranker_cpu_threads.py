"""Replay the fixed private fixture with native sorted-eight reranking.

Run separate installed-image processes with OMP_NUM_THREADS/MKL_NUM_THREADS
set to 2 and 4. Input is the same frozen fixture as the batching comparison.
Outputs contain numeric scores/ranks only; memory texts stay on stdin. The
comparison includes two concurrent reranking calls and has no API/model-provider calls.
"""
import asyncio,json,time,statistics,hashlib,sys
import torch
from hindsight_api.engine.cross_encoder import LocalSTCrossEncoder
raw=sys.stdin.buffer.read();f=json.loads(raw)
def ranking(scores):return sorted(range(len(scores)),key=lambda i:(-scores[i],i))
async def main():
 model=LocalSTCrossEncoder(force_cpu=True,fp16=False,bucket_batching=True,batch_size=8)
 await model.initialize()
 pairs=[[(q,t) for t in f["texts"]] for q in f["queries"]]
 result={"fixtureSha256":hashlib.sha256(raw).hexdigest(),"intraopThreads":torch.get_num_threads(),"torchVersion":torch.__version__,"candidateCount":len(f["texts"]),"tasks":[],"concurrent":{},"providerRequests":0}
 for i,batch in enumerate(pairs):
  await model.predict(batch);durations=[];cpu=[]
  for _ in range(3):
   wall=time.perf_counter();used=time.process_time();scores=await model.predict(batch);elapsed=time.perf_counter()-wall
   durations.append(elapsed*1000);cpu.append((time.process_time()-used)/elapsed)
  result["tasks"].append({"queryIndex":i,"runsMs":[round(v,3) for v in durations],"medianMs":round(statistics.median(durations),3),"effectiveCpuCores":round(statistics.median(cpu),3),"scores":scores,"ranking":ranking(scores)})
 await asyncio.gather(model.predict(pairs[0]),model.predict(pairs[1]));durations=[];cpu=[]
 for _ in range(3):
  wall=time.perf_counter();used=time.process_time();scores=await asyncio.gather(model.predict(pairs[0]),model.predict(pairs[1]));elapsed=time.perf_counter()-wall
  durations.append(elapsed*1000);cpu.append((time.process_time()-used)/elapsed)
 result["concurrent"]={"runsMs":[round(v,3) for v in durations],"medianMs":round(statistics.median(durations),3),"effectiveCpuCores":round(statistics.median(cpu),3),"sameIndividualRankings":all(ranking(s)==result["tasks"][i]["ranking"] for i,s in enumerate(scores))}
 print("BENCHMARK_RESULT "+json.dumps(result))
asyncio.run(main())
