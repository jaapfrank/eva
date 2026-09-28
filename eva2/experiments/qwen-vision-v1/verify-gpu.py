import pathlib,os,subprocess,socket,time,urllib.request,urllib.error,json,base64
p=pathlib.Path(__file__).parent
image=pathlib.Path('/home/administrator/project/.eva2-artifacts/local-vision-candidate-20260928/blank.png')
env=os.environ.copy();env['LD_LIBRARY_PATH']=str(p/'runtime/cudart-llama-b11146-bin-ubuntu-cuda-12.8-x64')+':'+str(p/'runtime/llama-b11146')+':/usr/lib/wsl/lib'
with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
command=[str(p/'runtime/llama-b11146/llama-server'),'-m',str(p/'Qwen3.5-4B-Q4_K_M.gguf'),'--mmproj',str(p/'mmproj-F16.gguf'),'-ngl','99','-c','4096','--no-warmup','--host','127.0.0.1','--port',str(port),'--reasoning','off','-lv','5']
started=time.monotonic();deadline=started+90
with (p/'gpu-startup.log').open('w') as log:
 process=subprocess.Popen(command,stdout=log,stderr=log,env=env)
 try:
  while True:
   if process.poll() is not None:raise RuntimeError('server exited '+str(process.returncode))
   if time.monotonic()>=deadline:raise TimeoutError('startup deadline')
   try:
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/health',timeout=1) as response:
     if response.status==200:break
   except (urllib.error.URLError,TimeoutError):time.sleep(.25)
  ready=time.monotonic()
  gpu=subprocess.run(['/usr/lib/wsl/lib/nvidia-smi','--query-compute-apps=pid,process_name,used_memory','--format=csv,noheader'],capture_output=True,text=True,timeout=10)
  (p/'gpu-resident.txt').write_text(gpu.stdout+gpu.stderr)
  print(gpu.stdout,flush=True)

 finally:
  process.terminate()
  try:process.wait(timeout=5)
  except subprocess.TimeoutExpired:process.kill();process.wait()
  print('server stopped',process.returncode,flush=True)
