import urllib.request,hashlib,json,pathlib,tarfile
p=pathlib.Path(__file__).parent
rev="e87f176479d0855a907a41277aca2f8ee7a09523"
files=[("llama.tar.gz","https://github.com/ggml-org/llama.cpp/releases/download/b11146/llama-b11146-bin-ubuntu-cuda-12.8-x64.tar.gz","c2ab9e19838513ff69d1af8d999ad717dd3c7ee4714ac04c7ed5ab9077c50e4e"),("cudart.tar.gz","https://github.com/ggml-org/llama.cpp/releases/download/b11146/cudart-llama-b11146-bin-ubuntu-cuda-12.8-x64.tar.gz","1466daea60aad1144819e151b2bae19d54556cf1da6c129c4f55a5ded2637c25")]
for item in json.loads((p/"model-files.json").read_text()):
 files.append((item["path"],"https://huggingface.co/unsloth/Qwen3.5-4B-GGUF/resolve/"+rev+"/"+item["path"],item["lfs"]["oid"]))
for name,url,expected in files:
 target=p/name
 if not target.exists():
  print("Downloading",name,flush=True)
  with urllib.request.urlopen(url,timeout=30) as response, (p/(name+".partial")).open("wb") as out:
   while chunk:=response.read(1024*1024):out.write(chunk)
  (p/(name+".partial")).rename(target)
 with target.open("rb") as f: actual=hashlib.file_digest(f,"sha256").hexdigest()
 if actual!=expected:raise RuntimeError("Hash mismatch: "+name)
 print("Verified",name,flush=True)
for name in ["llama.tar.gz","cudart.tar.gz"]:
 with tarfile.open(p/name) as archive:archive.extractall(p/"runtime",filter="data")
print("Prepared pinned runtime and weights",flush=True)
