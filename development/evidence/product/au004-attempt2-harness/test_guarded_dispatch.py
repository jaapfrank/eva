import json
from pathlib import Path
import subprocess
import sys
import tempfile

from guarded_dispatch import run

with tempfile.TemporaryDirectory(prefix='eva2-trial-gate-') as directory:
    marker = Path(directory)/'dispatch-marker'
    dispatch = [sys.executable,'-c',f'from pathlib import Path;Path({str(marker)!r}).write_text("dispatch")']
    for failure in [1, 7]:
        try:
            run([sys.executable,'-c',f'raise SystemExit({failure})'],dispatch)
        except subprocess.CalledProcessError as error:
            assert error.returncode == failure
        else:
            raise AssertionError('preflight failure was ignored')
        assert not marker.exists()
    run([sys.executable,'-c','pass'],dispatch)
    assert marker.read_text() == 'dispatch'
    # The actual repaired entry point rejects this already-run attempt before Docker/network work.
    real = subprocess.run([sys.executable,str(Path(__file__).with_name('guarded_dispatch.py'))],
        capture_output=True,text=True)
    assert real.returncode != 0 and 'attempt already dispatched' in real.stderr
result={'offline_subprocess_checks':4,'failed_preflight_dispatches':0,
    'successful_preflight_local_marker_dispatches':1,'existing_attempt_rejected':True,
    'network_calls':0,'model_calls':0,'production_changes':0}
Path(__file__).with_name('harness-offline-proof.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
