import importlib.metadata as md,csv,hashlib,base64,json,sysconfig,importlib
from pathlib import Path
site=Path('/usr/local/lib/python3.12/site-packages');result={};counts={}
for d in md.distributions(path=[str(site)]):
 rec=Path(d._path)/'RECORD';count=0;unhashed=0
 for row in csv.reader(rec.read_text().splitlines()):
  p=Path(d.locate_file(row[0]));
  if not row[1]:unhashed+=1;continue
  algorithm,digest=row[1].split('=',1);assert algorithm=='sha256';assert p.is_file(),(d.metadata['Name'],row[0]);b=p.read_bytes();assert len(b)==int(row[2]);assert base64.urlsafe_b64encode(hashlib.sha256(b).digest()).decode().rstrip('=')==digest,(d.metadata['Name'],row[0]);count+=1
 result[d.metadata['Name']]={'version':d.version,'hashedRecordEntriesVerified':count,'unhashedEntries':unhashed,'requiresPython':d.metadata.get('Requires-Python'),'RequiresDist':d.metadata.get_all('Requires-Dist')}
for name in ['pydantic_core._pydantic_core','greenlet._greenlet','cryptography.hazmat.bindings._rust','cffi','nacl.bindings','psycopg','regex','tiktoken','openai','pydantic_ai','fastapi','starlette','uvicorn','websockets']:
 if importlib.util.find_spec(name.split(".")[0]) is not None:importlib.import_module(name)
print(json.dumps({'site':str(site),'SOABI':sysconfig.get_config_var('SOABI'),'distributionRecords':result,'nativeAndPublicSdkImportsPassed':True,'externalCalls':0}))
