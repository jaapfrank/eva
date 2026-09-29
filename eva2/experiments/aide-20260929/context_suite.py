import json,sys,time,copy
from types import SimpleNamespace as NS
from eva.adapters.postgres.delegated_work_actions import PostgresDelegatedWorkActions
cases=json.load(open(sys.argv[1]));results=[]
for c in cases:
 class Service:
  reads=0
  def available_for(self,turn):return c["allowed"]
  def active_work_metadata(self,turn):self.reads+=1;return copy.deepcopy(c["listing"])
 service=Service();started=time.perf_counter_ns()
 try:
  context=PostgresDelegatedWorkActions(None,service).active_context(NS(text=c["text"],message_id="experiment:"+c["id"]))
  result={"id":c["id"],"context":context,"reads":service.reads,"elapsedNs":time.perf_counter_ns()-started,"error":None}
 except Exception as e:result={"id":c["id"],"error":type(e).__name__,"reads":service.reads}
 results.append(result)
print(json.dumps(results,ensure_ascii=False))
