from pathlib import Path
import subprocess,json,sys
p=json.loads(Path('/build/role-plan.json').read_text())
subprocess.run([sys.executable,'-m','pip','uninstall','--yes',*sorted(p['changedVersions'])],check=True)
subprocess.run([sys.executable,'-m','pip','install','--no-index','--find-links=/build/wheels','--only-binary=:all:','--no-deps','--no-compile','--require-hashes','-r','/build/upgrade.lock'],check=True)
subprocess.run([sys.executable,'/build/finalize-core-site.py'],check=True)
subprocess.run([sys.executable,'-m','pip','check'],check=True)
