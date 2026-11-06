from pathlib import Path
import json,hashlib,shutil
expected=json.loads(Path('/build/vendor-runtime.json').read_text());assert len(expected['stdlibFiles'])==823 and not expected['stdlibSymlinks']
for name,sha in expected['stdlibFiles'].items():
 source=Path(name);assert source.is_file() and not source.is_symlink() and hashlib.sha256(source.read_bytes()).hexdigest()==sha;target=Path('/runtime')/name.lstrip('/');target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
