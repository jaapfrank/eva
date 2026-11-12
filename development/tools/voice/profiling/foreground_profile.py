"""Temporary content-free profiler; ordinary foreground authority remains enforced."""
import functools,inspect,json,time,sys
import httpx2
from sqlalchemy import event
from sqlalchemy.engine import Engine
def emit(stage, **fields):
 print(json.dumps(dict(profile="foreground_stages",stage=stage,wall_ns=time.time_ns(),**fields)),flush=True)
@event.listens_for(Engine,"before_cursor_execute")
def sql_start(conn,cursor,statement,parameters,context,executemany):
 context.profile_start=time.monotonic()
@event.listens_for(Engine,"after_cursor_execute")
def sql_end(conn,cursor,statement,parameters,context,executemany):
 emit("sql",duration_ms=round((time.monotonic()-context.profile_start)*1000))
def wrap(cls,name):
 original=vars(cls).get(name)
 if not inspect.isfunction(original) or inspect.iscoroutinefunction(original): return
 @functools.wraps(original)
 def traced(*a,**k):
  t=time.monotonic();label=cls.__name__+"."+name;emit(label+".start")
  try:return original(*a,**k)
  finally:emit(label+".end",duration_ms=round((time.monotonic()-t)*1000))
 setattr(cls,name,traced)
old_send=httpx2.AsyncClient.send
async def send(self,*a,**k):
 t=time.monotonic();emit("http_headers.start")
 try:return await old_send(self,*a,**k)
 finally:emit("http_headers.end",duration_ms=round((time.monotonic()-t)*1000))
httpx2.AsyncClient.send=send
old_iter=httpx2.Response.aiter_bytes
async def it(self,*a,**k):
 first=True
 async for chunk in old_iter(self,*a,**k):
  if first:emit("http_first_body");first=False
  yield chunk
 emit("http_body_done")
httpx2.Response.aiter_bytes=it
from eva.bootstrap.runtime import create_runtime_app
import os
assert os.environ.get("EVA_BACKGROUND_WORK_ENABLED") == "false", "Profiling requires background work disabled"
app=create_runtime_app()
seen=set()
names={"respond","recall_for_presence","recall_for_presence_fast","ensure_bank",
 "_request","_recall_response","pending","active_context","available_action_names",
 "execute","generate","_admit_request","_run_pydantic_phase","begin_model_request",
 "complete_model_request","reply_guard"}
for mod in list(sys.modules.values()):
 if not getattr(mod,"__name__","").startswith("eva."):continue
 for cls in list(vars(mod).values()):
  if inspect.isclass(cls) and cls.__module__.startswith("eva.") and cls not in seen:
   seen.add(cls)
   for name in names:wrap(cls,name)
emit("ready")
import uvicorn
uvicorn.run(app,host="0.0.0.0",port=8080,access_log=False)
