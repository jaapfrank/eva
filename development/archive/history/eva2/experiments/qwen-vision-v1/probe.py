import pathlib,os,subprocess,socket,time,urllib.request,urllib.error,json,base64
p=pathlib.Path(__file__).parent
image=pathlib.Path('/home/administrator/project/.eva2-artifacts/local-vision-candidate-20260928/blank.png')
env=os.environ.copy();env['LD_LIBRARY_PATH']=str(p/'runtime/cudart-llama-b11146-bin-ubuntu-cuda-12.8-x64')+':'+str(p/'runtime/llama-b11146')+':/usr/lib/wsl/lib'
with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
command=[str(p/'runtime/llama-b11146/llama-server'),'-m',str(p/'Qwen3.5-4B-Q4_K_M.gguf'),'--mmproj',str(p/'mmproj-F16.gguf'),'-ngl','99','-c','4096','--no-warmup','--host','127.0.0.1','--port',str(port),'--reasoning','off','--chat-template-kwargs','{"enable_thinking":false}']
started=time.monotonic();deadline=started+90
with (p/'server.log').open('w') as log:
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
  body={'messages':[{'role':'user','content':[{'type':'text','text':'Describe only what is visible in this image. Mention uncertainty. Do not infer identities, intentions, dates or events outside the image.'},{'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode(image.read_bytes()).decode()}}]}],'max_tokens':120,'temperature':0,'chat_template_kwargs':{'enable_thinking':False}}
  request=urllib.request.Request(f'http://127.0.0.1:{port}/v1/chat/completions',data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
  with urllib.request.urlopen(request,timeout=max(1,deadline-time.monotonic())) as response:result=json.load(response)
  result={'startupSeconds':ready-started,'requestSeconds':time.monotonic()-ready,'totalSeconds':time.monotonic()-started,'response':result}
  (p/'result.json').write_text(json.dumps(result,indent=2)+'\n')
  print(json.dumps(result),flush=True)
 finally:
  process.terminate()
  try:process.wait(timeout=5)
  except subprocess.TimeoutExpired:process.kill();process.wait()
  print('server stopped',process.returncode,flush=True)
