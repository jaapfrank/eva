"""Read-only gates for a single trial; already-dispatched attempts are rejected."""
from pathlib import Path
import json
import subprocess

stage = Path(__file__).resolve().parent
assert not (stage/'A/attempt.json').exists(), 'attempt already dispatched'
expected = json.loads((stage.parent/'au004-procedure-trial-20260912/config-before.json').read_text())
paths = json.loads((stage/'paths.json').read_text())

def inside(container, code):
    r = subprocess.run(['docker','exec','-i',container,'python','-'], input=code,
        text=True, capture_output=True, check=True)
    return json.loads(r.stdout)

image = subprocess.check_output(['docker','inspect','eva2-managed-b-slot3-core',
    '--format','{{.Image}}'], text=True).strip()
assert image == 'sha256:df675dc82f38ff386fdf9cb87062e78f9ba8231f7f277472c2bd53f1a734d072'
config = inside('eva2-managed-b-slot3-core', '''
import os,json,hashlib
from pathlib import Path
out={}
for key in ['EVA_RUNTIME_RESOURCE_BUDGET_FILE','EVA_MCP_AUTONOMY_MANDATE_FILE']:
 raw=Path(os.environ[key]).read_bytes()
 out[key]={'sha256':hashlib.sha256(raw).hexdigest(),
   'budget':json.loads(raw)['budget'] if 'BUDGET' in key else None}
print(json.dumps(out))
''')
assert config == expected, 'budget or mandate changed'
files = inside('eva2-workspace-filesystem', 'import pathlib,json\npaths='+repr(paths)+
    '\nprint(json.dumps({k:pathlib.Path(p).exists() for k,p in paths.items()}))')
assert not any(files.values()), 'output already exists'
usage = inside('eva2-managed-b-slot3-core', '''
import os,json,urllib.request
from pathlib import Path
lease=json.loads(Path(os.environ['EVA_CONVERSATION_AUTH_FILE']).read_text())
req=urllib.request.Request('https://chatgpt.com/backend-api/wham/usage',headers={
 'Authorization':'Bearer '+lease['access_token'],'ChatGPT-Account-Id':lease['account_id'],
 'User-Agent':'codex_cli_rs'})
with urllib.request.urlopen(req,timeout=20) as response:
 value=json.load(response)
print(json.dumps(value['rate_limit']))
''')
assert usage['allowed'] and not usage['limit_reached'], 'provider budget unavailable'
with (stage/'guarded-preflight-receipt.json').open('x') as receipt:
    json.dump({'config':config,'files':files,'provider_rate_limit':usage,'image':image},receipt)
