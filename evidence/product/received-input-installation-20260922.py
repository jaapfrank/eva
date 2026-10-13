"""Exact media-only receipt installation, graceful stop and retained rollback."""
import argparse,hashlib,json,os,subprocess,time,urllib.request
from pathlib import Path
S=Path(__file__).parent
NAME="eva2-owner-voice-media-1"
IMAGE="sha256:daae43c50b211955aaf75a598be16246dca8bcf530f878991e572f8e1312a53d"
ENV=Path("/var/lib/eva2/voice-session/runtime.env")
CMD=["docker","compose","--project-name","eva2-owner-voice","--env-file",str(ENV),"-f","/var/lib/eva2/voice-session/compose.yaml"]
def need(value,reason):
    if not value: raise RuntimeError(reason)
def command(args,timeout=120):
    return subprocess.check_output(args,timeout=timeout,stderr=subprocess.STDOUT).decode()
def inspect(name): return json.loads(command(["docker","inspect",name]))[0]
def save(name,value):
    (S/name).write_text(json.dumps(value,indent=2,default=str))
def config(value):
    c=dict(value["Config"])
    for key in ("Image","Labels","Hostname"): c.pop(key,None)
    h=dict(value["HostConfig"])
    for key,field in (("Mounts","Target"),):
        if key in h: h[key]=sorted(h[key],key=lambda x:x[field])
    if h.get("Binds"): h["Binds"]=sorted(h["Binds"])
    return c,h,sorted(value["Mounts"],key=lambda x:x["Destination"])
def ready():
    end=time.monotonic()+45
    while time.monotonic()<end:
        try:
            with urllib.request.urlopen("http://127.0.0.1:44716/session/ready",timeout=5) as response:
                if json.load(response).get("ready") is True: return
        except Exception: pass
        time.sleep(.5)
    raise RuntimeError("media readiness not confirmed")
def stop(expected_id, expected_image):
    current = inspect(NAME)
    need(current["Id"] == expected_id and current["Image"] == expected_image
         and config(current) == config(before), "refuse to stop an unowned replacement")
    command(["docker","stop","--timeout=-1",NAME])
    state=inspect(NAME)["State"]
    need(not state["Running"] and state["ExitCode"]==0 and not state["OOMKilled"],"media did not stop gracefully")
def verify(before,image):
    after=inspect(NAME)
    need(after["Image"]==image and after["State"]["Running"],"media image not running")
    need(config(after)==config(before),"media effective configuration changed")
    ready()
    for name,expected in protected.items():
        value=inspect(name)
        need(value["Id"]==expected and value["State"]["Running"],"protected service changed: "+name)
    return after
before=json.loads((S/"media.before.private.json").read_text())
protected={x["Name"].lstrip("/"):x["Id"] for x in json.loads((S/"running.before.private.json").read_text())
    if x["Name"].lstrip("/") in {"eva2-managed-b-slot3-core","eva2-managed-b-slot3-delegated-work-worker-1","eva2-owner-voice-livekit-1","eva2-managed-b-slot3-postgres-1","eva2-document-workspace-au006","eva2-document-goal-worker-au006","eva2-mcp-gateway","eva2-household-speaker-1"}}
parser=argparse.ArgumentParser();parser.add_argument("--execute",action="store_true");args=parser.parse_args()
current=inspect(NAME)
need(current["Id"]==before["Id"] and config(current)==config(before),"baseline changed")
need(current["Image"]==before["Image"] and current["State"]["Running"],"baseline not running")
for name,expected in protected.items(): need(inspect(name)["Id"]==expected,"protected baseline changed")
old_config=inspect(before["Image"])["Config"];new_config=inspect(IMAGE)["Config"]
need({k:v for k,v in old_config.items() if k!="Labels"}=={k:v for k,v in new_config.items() if k!="Labels"},"image configuration differs")
original=ENV.read_bytes();st=ENV.stat()
lines=original.decode().splitlines(keepends=True)
need(sum(line.startswith("EVA_VOICE_MEDIA_IMAGE=") for line in lines)==1,"image pin missing or duplicate")
old_pin=next(line.split("=",1)[1].strip() for line in lines if line.startswith("EVA_VOICE_MEDIA_IMAGE="))
need(old_pin == before["Image"], "runtime image pin differs from running parent")
def rendered(image=None):
    environment=dict(os.environ)
    if image is not None: environment["EVA_VOICE_MEDIA_IMAGE"]=image
    return json.loads(subprocess.check_output(CMD+["config","--format","json"],env=environment,timeout=30))
base=rendered();proposed=rendered(IMAGE)
need(base["services"]["media"]["image"] == before["Image"], "Compose baseline image differs")
expected=json.loads(json.dumps(base));expected["services"]["media"]["image"]=IMAGE
need(proposed == expected, "Compose changes more than the media image")
compose_hash=command(CMD+["config","--hash","media"]).strip().split()[-1]
need(compose_hash == before["Config"]["Labels"]["com.docker.compose.config-hash"], "Compose baseline differs from running service")
changed="".join("EVA_VOICE_MEDIA_IMAGE="+IMAGE+"\n" if line.startswith("EVA_VOICE_MEDIA_IMAGE=") else line for line in lines).encode()
ready()
if not args.execute:
    save("preflight.json",{"passed":True,"image":IMAGE,"parent":before["Image"],"protectedServices":len(protected),"runtimeMutated":False})
    print("media-only preflight passed; no runtime changes")
else:
    review=json.loads((S/"review.json").read_text())
    need(review.get("approved") is True and review.get("installer_sha256")==hashlib.sha256(Path(__file__).read_bytes()).hexdigest() and review.get("image")==IMAGE,"exact reviewed installer required")
    (S/"execution.lock").open("x").close()
    (S/"runtime.env.before.private").write_bytes(original)
    mutated=False
    owned_id=before["Id"]
    owned_image=before["Image"]
    try:
        code="""import asyncio,json,os
from pathlib import Path
from livekit import api
c=json.loads(Path(os.environ['EVA_VOICE_CONFIG']).read_text())
async def main():
 async with api.LiveKitAPI(c['livekit_url'],c['livekit_api_key'],Path(c['livekit_secret_file']).read_text().strip()) as client:
  result=await client.room.list_rooms(api.ListRoomsRequest())
  print(json.dumps({'rooms':len(result.rooms)}))
asyncio.run(main())
"""
        room_state=json.loads(subprocess.check_output(["docker","exec","-i",NAME,"python","-"],input=code.encode(),timeout=15))
        need(room_state == {"rooms":0}, "active voice room; defer installation")
        save("rooms-before-stop.json",room_state)
        stop(owned_id, owned_image)
        mutated=True
        ENV.write_bytes(changed);os.chmod(ENV,st.st_mode&0o777);os.chown(ENV,st.st_uid,st.st_gid)
        command(CMD+["up","-d","--no-deps","--force-recreate","media"])
        candidate=inspect(NAME)
        need(candidate["Image"] == IMAGE and config(candidate) == config(before),
             "new media does not match the intended candidate")
        owned_id,owned_image=candidate["Id"],IMAGE
        after=verify(before,IMAGE)
        save("installed.json",{"installed":True,"container":after["Id"],"image":IMAGE,"parent":before["Image"],"ready":True,"protectedServices":len(protected),"rollbackAvailable":True})
        print("media receipt installed; readiness and protected services verified")
    except BaseException as error:
        save("failure.json",{"error_type":type(error).__name__,"error":str(error),"mutated":mutated})
        if mutated:
            active=inspect(NAME)
            need(active["Id"] == owned_id and active["Image"] == owned_image
                 and config(active) == config(before), "rollback refuses an unknown replacement")
            if active["State"]["Running"]: stop(owned_id, owned_image)
            ENV.write_bytes(original);os.chmod(ENV,st.st_mode&0o777);os.chown(ENV,st.st_uid,st.st_gid)
            command(CMD+["up","-d","--no-deps","--force-recreate","media"])
            after=verify(before,before["Image"])
            save("rollback.json",{"verified":True,"container":after["Id"],"image":after["Image"]})
        raise
