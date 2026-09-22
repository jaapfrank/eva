import subprocess,json,tempfile,os,pathlib,sys
info=json.loads(subprocess.check_output(['docker','inspect','eva2-managed-b-slot3-core']))[0]
with tempfile.TemporaryDirectory(prefix='eva-cache-env-') as directory:
 p=pathlib.Path(directory)/'env';p.write_text('\n'.join(info['Config']['Env'])+'\n');p.chmod(0o600)
 script=pathlib.Path('/home/administrator/project/eva2-prompt-cache-probe/eva2/evidence/product/prompt-cache-probe-20260922.py').read_text()
 args=['docker','run','--rm','-i','--network',('bridge' if sys.argv[1:]==['--live-four-calls'] else 'none'),'--read-only','--tmpfs','/tmp:rw,mode=1777','--volumes-from','eva2-managed-b-slot3-core:ro','--env-file',str(p),'--entrypoint','python',info['Image'],'-',*sys.argv[1:]]
 result=subprocess.run(args,input=script,capture_output=True,text=True)
 print(result.stdout);print(result.stderr)
 if result.returncode==0:(pathlib.Path('/home/administrator/project/eva2-prompt-cache-probe/eva2/evidence/product') / ('prompt-cache-live-20260922.jsonl' if sys.argv[1:] else 'prompt-cache-offline-20260922.json')).write_text(result.stdout)
 sys.exit(result.returncode)
