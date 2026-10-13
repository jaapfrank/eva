import tempfile,json,hashlib,copy
from pathlib import Path
from unittest.mock import patch
from contextlib import ExitStack,redirect_stdout
import io
import install_reviewed as m
results=[]
for case in ['success','candidate_not_ready','bridge_restart_failure','rollback_failure']:
 with tempfile.TemporaryDirectory() as directory:
  root=Path(directory);parent=b'parent';candidate=b'candidate';(root/'bridge.original.js').write_bytes(parent);(root/'bridge.candidate.js').write_bytes(candidate)
  h=lambda raw:hashlib.sha256(raw).hexdigest()
  plan={'core_parent':'parent','core_image':'candidate','bridge_parent_sha256':h(parent),'bridge_candidate_sha256':h(candidate),'core_activation_command':['core-up'],'core_rollback_command':['core-parent'],'bridge_restart_command':['bridge-restart']}
  (root/'installation-plan.json').write_text(json.dumps(plan));(root/'installation-go.json').write_text(json.dumps({'decision':'GO','plan_sha256':h((root/'installation-plan.json').read_bytes()),'script_sha256':h(Path(m.__file__).read_bytes()),'core_image':'candidate','bridge_sha256':h(candidate),'restart_eva_service':True}))
  actions=[];bridge=['parent'];restarts=[0]
  def command(cmd):
   actions.append(cmd)
   if cmd==['bridge-restart']:
    restarts[0]+=1
    if case=='bridge_restart_failure' and restarts[0]==1:raise RuntimeError('restart failed')
   return 'mock'
  def wait_guard(image):
   if image=='candidate' and case in ['candidate_not_ready','rollback_failure']:raise RuntimeError('candidate not ready')
   if image=='parent' and case=='rollback_failure':raise RuntimeError('parent not verified')
   return {'image':image,'verified':True}
  def replace(content,expected):
   actual=h(parent if bridge[0]=='parent' else candidate)
   if actual!=expected:raise RuntimeError('replace precondition')
   bridge[0]='parent' if content==parent else 'candidate'
  def state(expected):
   if expected!=h(parent if bridge[0]=='parent' else candidate):raise RuntimeError('wrong bridge')
   return {'MainPID':'old' if restarts[0]==0 else 'new'}
  failure=None
  with ExitStack() as stack:
   for name,value in [('S',root),('guard',lambda *a,**k:{'verified':True}),('command',command),('wait_guard',wait_guard),('replace_bridge',replace),('bridge_state',state)]:stack.enter_context(patch.object(m,name,value))
   stack.enter_context(patch.object(m.time,'sleep',lambda _:None));stack.enter_context(redirect_stdout(io.StringIO()))
   try:m.main()
   except RuntimeError as error:failure=str(error)
  if case=='success':
   if failure or not(root/'installation-final.json').exists():raise RuntimeError('Success failed')
  else:
   receipt=json.loads((root/'installation-failed.json').read_text())
   if not failure or receipt['rollback_verified']!=(case!='rollback_failure'):raise RuntimeError('Failure receipt differs')
   if case=='rollback_failure' and 'ROLLBACK NOT VERIFIED' not in failure:raise RuntimeError('Rollback failure hidden')
   if case=='bridge_restart_failure' and bridge[0]!='parent':raise RuntimeError('Bridge not restored')
  results.append({'case':case,'commands':actions,'real_runtime_calls':0})
print(json.dumps({'cases':results,'model_calls':0}))
