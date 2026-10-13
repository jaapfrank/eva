"""Explicit opt-in launcher; never rerun without a changed cause."""
from pathlib import Path
import json
import subprocess
p=Path(__file__).parent
history=json.loads((p/'history.json').read_text())['history']
code=(p/'decision.py').read_text().replace('HISTORY',repr(history))
with (p/'decision-result.json').open('w') as out, (p/'decision-stderr.private.txt').open('w') as err:
    result=subprocess.run(['docker','exec','-i','eva2-managed-b-slot3-core','python','-'],input=code,text=True,stdout=out,stderr=err)
raise SystemExit(result.returncode)
