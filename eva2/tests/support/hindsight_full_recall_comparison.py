"""Compare the existing local Hindsight recall route with a frozen request.

Run in the installed core container with its existing EVA_HINDSIGHT_URL. Read
bank/body/two queries from a private JSON file on stdin; do not widen its scope.
This read-only API replay includes embeddings and concurrent full recalls.
Output contains private facts: save outside the repository, retain only hashes
and numeric aggregates in public evidence. No paid model calls are requested.
"""
import os,json,sys,time,statistics,urllib.request,hashlib,concurrent.futures
f=json.load(sys.stdin)
def call(query):
 body={**f["body"],"query":query};data=json.dumps(body).encode();url=os.environ["EVA_HINDSIGHT_URL"].rstrip("/")+"/v1/default/banks/"+f["bank"]+"/memories/recall"
 request=urllib.request.Request(url,data=data,headers={"Content-Type":"application/json"},method="POST");start=time.perf_counter()
 with urllib.request.urlopen(request,timeout=90) as resp:result=json.load(resp)
 return {"elapsedMs":round((time.perf_counter()-start)*1000,3),"response":result}
def pair(pool):
 start=time.perf_counter();futures=[pool.submit(call,q) for q in f["queries"]];results=[v.result() for v in futures];return {"elapsedMs":round((time.perf_counter()-start)*1000,3),"calls":results}
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
 warmup=pair(pool);runs=[pair(pool) for _ in range(3)]
print(json.dumps({"warmup":warmup,"runs":runs,"pairedMedianMs":statistics.median(r["elapsedMs"] for r in runs),"requestConfigSha256":hashlib.sha256(json.dumps(f,sort_keys=True).encode()).hexdigest()}))
