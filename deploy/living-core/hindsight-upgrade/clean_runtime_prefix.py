from pathlib import Path
import importlib.metadata as md,shutil
site=Path('/app/api/.venv/lib/python3.11/site-packages')
for d in md.distributions(path=[str(site)]):
 for f in d.files or ():
  p=Path(d.locate_file(f)).resolve()
  if p.parent==Path('/app/api/.venv/bin') and p.is_file() and not p.is_symlink():p.unlink(missing_ok=True)
shutil.rmtree(site);site.mkdir(parents=True)
p=Path('/app/api/hindsight_api')
if p.exists():shutil.rmtree(p)
