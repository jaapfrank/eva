"""One-shot resource preparation for the predeclared P2 CM pair.
No case, expected answer, oracle or replacement provider is defined here.
"""
import importlib.util, json, os, signal, subprocess, sys, time
from pathlib import Path
from p1_model_tunnel import ModelTunnel
from p2_provider_history_native_glue import ledger_for, core_parent, private_json
ROOT=Path("/home/administrator/project/Eva2")
BENCH=ROOT/"eva2/benchmark/inspect_ai_v1"
RESOURCE=Path("/tmp/eva2-ev003-provider-history-cm01-e1-20261006")
THIRD=Path("/home/administrator/project/.eva2-artifacts/ev003-final-20261004/execution/eva2-xd02-epoch1/calendar-deps")
CORE="eva2-p2ph-cm-core-b5-20261006"
MEMORY="eva2-p2ph-cm-memory-b5-20261006"
NETWORK="eva2-p2ph-cm-20261006"
def inspect(name):
    return json.loads(subprocess.check_output(["docker","inspect",name]))[0]
def env_of(data):
    return dict(x.split("=",1) for x in data["Config"]["Env"] if "=" in x)
def mapped(data,path):
    matches=[x for x in data["Mounts"] if path==x["Destination"] or path.startswith(x["Destination"].rstrip("/")+"/")]
    match=max(matches,key=lambda m:len(m["Destination"]))
    return match["Source"]+path[len(match["Destination"]):]
def env_file(name,values):
    path=RESOURCE/name
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,"w") as f:
        for key,value in values.items():
            if "\n" in value: raise ValueError("Unsupported native environment newline")
            f.write(key+"="+value+"\n")
    return path
def docker_worker(name,image,kind,environment,mounts,user,output,control,dsn,proxy,extra=()):
    args=["docker","run","-d","--name",name,"--network",NETWORK,"--network-alias",kind,
        "--label","eva2.independent-comparison=p2-provider-history-20261006",
        "--user",user,"--env-file",str(environment),"--entrypoint","python"]
    if kind=="memory":
        args+=["--memory","8g","--memory-swap","8g","--cpus","4","--device","nvidia.com/gpu=all"]
    for source,target,readonly in mounts:
        args+=["-v",str(source)+":"+target+(":ro" if readonly else "")]
    args += [image,"/eval/native/benchmark/inspect_ai_v1/p1_eva2_native_worker.py",kind,"--control-file",str(control),
        "--database-file",str(dsn),"--output",str(output),"--port","8080",
        "--provider-mode","subscription","--provider-proxy",proxy,
        "--eva-source","/opt/eva/src/eva/adapters/model/openai_codex_provider.py",
        "--eva-sha256","27400f795e7491c8f1031ab0a44d912a6f82ece165c25def4e9122820fbd4fbd",
        "--calendar-file",str(RESOURCE/"clock.json"),
        "--calendar-receipt",str(output.parent/"clock-receipt.json")]
    args+=list(extra)
    subprocess.check_output(args)
def ready(name,receipt,deadline):
    from urllib.request import urlopen
    while time.monotonic()<deadline:
        data=inspect(name)
        if not data["State"]["Running"]:raise ValueError("Native worker exited before readiness")
        ip=data["NetworkSettings"]["Networks"][NETWORK]["IPAddress"]
        try:
            with urlopen("http://"+ip+":8080"+("/healthz" if name==CORE else "/health"),timeout=1) as response:
                if response.status==200 and receipt.exists():return "http://"+ip+":8080"
        except Exception:pass
        time.sleep(.2)
    raise TimeoutError("Native startup readiness unavailable")
def main():
    plan=json.loads((BENCH/"ev003_execution_plan_20261004.json").read_text())
    ledger=ledger_for("eva2-provider-history-cm01-epoch1",plan)
    parent=core_parent(ledger,ledger.sample_id,RESOURCE/"accounting-b5")
    # Native images have distinct users. Only the scenario's shared group can
    # traverse/connect; each namespace credential remains 0600 to its own UID.
    os.chmod(RESOURCE/"accounting-b5",0o710)
    os.chmod(RESOURCE/"accounting-b5/accounting.sock",0o660)
    os.chown(RESOURCE/"accounting-b5/memory-control.json",1000,65532)
    for name,uid in [("core",65532),("memory",1000)]:
        path=RESOURCE/(name+"-output-b5");path.mkdir(mode=0o700);os.chown(path,uid,65532)
    initial_clock={"contract":"EvaScenarioCalendar/v1","generation":0,"offsetMilliseconds":0}
    if (RESOURCE/"clock.json").exists():
        if json.loads((RESOURCE/"clock.json").read_text())!=initial_clock:raise ValueError("Clock no longer unused")
    else:private_json(RESOURCE/"clock.json",initial_clock)
    os.chmod(RESOURCE/"clock.json",0o644) # public clock control, never case content
    os.chown(RESOURCE/"memory-dsn",1000,65532)
    production_core=inspect("eva2-managed-b-slot3-core")
    production_memory=inspect("eva2-managed-b-slot3-hindsight-1")
    ce,me=env_of(production_core),env_of(production_memory)
    scope=json.loads(Path("/tmp/eva-p2-scope-initialized-20261006.json").read_text())
    # CM needs the native background thread for conversation memory retention.
    # Delegated work stays disabled; the native worker denies real action dispatch.
    keys=("EVA_IDENTITY_NAME","EVA_IDENTITY_DESCRIPTION","EVA_IDENTITY_PACK_FILE",
        "EVA_IDENTITY_PACK_TRUST_FILE","EVA_HINDSIGHT_RECALL_BUDGET",
        "EVA_HINDSIGHT_RECALL_MAX_TOKENS","EVA_HINDSIGHT_RECALL_NODE_BUDGET",
        "EVA_CONVERSATION_CONTACT_MODEL","EVA_CONVERSATION_CONTACT_REASONING_EFFORT",
        "EVA_CONVERSATION_MODEL","EVA_CONVERSATION_REASONING_EFFORT",
        "EVA_COLD_PATH_IDLE_SECONDS","EVA_PERSONAL_INTEREST_LEASE_SECONDS",
        "EVA_PROCEDURE_REFERENCES_ENABLED")
    core_env={key:ce[key] for key in keys if key in ce}
    core_env.update({"EVA_DATABASE_DSN_FILE":str(RESOURCE/"core-dsn"),
        "EVA_HINDSIGHT_URL":"http://memory:8080","EVA_HINDSIGHT_BANK_NAMESPACE":"eva2",
        "EVA_RUNTIME_TENANT_ID":scope["tenant_id"],"EVA_RUNTIME_INSTALLATION_ID":scope["installation_id"],
        "EVA_RUNTIME_PRINCIPAL_ID":scope["principal_id"],
        "EVA_RUNTIME_RESOURCE_BUDGET_FILE":str(RESOURCE/"runtime-budget.json"),
        "EVA_CONVERSATION_AUTH_FILE":"/run/eva/model-lease/auth.json",
        "EVA_BACKGROUND_WORK_ENABLED":"true","EVA_DELEGATED_WORK_ENABLED":"false",
        "EVA_PROCEDURE_REFERENCES_ENABLED":"false",
        "PYTHONPATH":"/eval/native/benchmark/inspect_ai_v1:/opt/eva/src:/calendar-deps"})
    memory_env={key:value for key,value in me.items() if key.startswith("HINDSIGHT_")}
    memory_env.update({"HINDSIGHT_API_DATABASE_URL_FILE":str(RESOURCE/"memory-dsn"),
        "HINDSIGHT_API_LLM_MODEL":"gpt-6-astra","HINDSIGHT_API_LLM_REASONING_EFFORT":"medium",
        "HINDSIGHT_API_PORT":"8080","HINDSIGHT_API_HOST":"0.0.0.0",
        "HINDSIGHT_API_WORKER_ID":"p2-provider-history-cm01-e1",
        "PYTHONPATH":"/opt/eva-cuda:/eval/native/benchmark/inspect_ai_v1:/opt/eva/src:/calendar-deps","HOME":"/home/hindsight"})
    for key in ("HF_HUB_OFFLINE","HF_HUB_DISABLE_TELEMETRY","TRANSFORMERS_OFFLINE",
                "TRANSFORMERS_VERBOSITY","HF_HUB_VERBOSITY"):
        if key in me:memory_env[key]=me[key]
    # Per-stage overrides, if present in the installed configuration, receive the
    # same original helper profile. This is an explicit comparison variant.
    for key in list(memory_env):
        if key.endswith("_LLM_MODEL"):memory_env[key]="gpt-6-astra"
        if key.endswith("_LLM_REASONING_EFFORT"):memory_env[key]="medium"
    tunnel=ModelTunnel(("198.18.221.1",0),"198.18.221.0/24").start()
    core_mounts=[(BENCH,"/eval/native/benchmark/inspect_ai_v1",True),(RESOURCE,str(RESOURCE),False),
        (THIRD/"cp312","/calendar-deps",True),
        (mapped(production_core,ce["EVA_CONVERSATION_AUTH_FILE"]),"/run/eva/model-lease/auth.json",True)]
    for key in ("EVA_IDENTITY_PACK_FILE","EVA_IDENTITY_PACK_TRUST_FILE"):
        if key in ce:core_mounts.append((mapped(production_core,ce[key]),ce[key],True))
    memory_mounts=[(BENCH,"/eval/native/benchmark/inspect_ai_v1",True),(RESOURCE,str(RESOURCE),False),
        (THIRD/"cp311","/calendar-deps",True)]
    for m in production_memory["Mounts"]:
        if ".codex" in m["Destination"] or "cache" in m["Destination"] or m["Destination"]=="/opt/eva-cuda":
            memory_mounts.append((m["Source"],m["Destination"],True))
    memory_mounts.append((RESOURCE/"native-model-cache","/home/hindsight/.cache/huggingface",True))
    # The accounting attestor loads the very same frozen provider source. Expose
    # that one source read-only from the actual core image via a private copy.
    provider=RESOURCE/"frozen-openai-codex-provider.py"
    if not provider.exists():
        subprocess.check_call(["docker","cp","eva2-managed-b-slot3-core:/opt/eva/src/eva/adapters/model/openai_codex_provider.py",str(provider)])
    import hashlib
    if hashlib.sha256(provider.read_bytes()).hexdigest()!="27400f795e7491c8f1031ab0a44d912a6f82ece165c25def4e9122820fbd4fbd":
        raise ValueError("Frozen provider bytes differ")
    os.chmod(provider,0o644)
    memory_mounts.append((provider,"/opt/eva/src/eva/adapters/model/openai_codex_provider.py",True))
    try:
        docker_worker(MEMORY,production_memory["Image"],"memory",env_file("memory-b5.env",memory_env),
            memory_mounts,"1000:65532",RESOURCE/"memory-output-b5/native.json",
            RESOURCE/"accounting-b5/memory-control.json",RESOURCE/"memory-dsn",tunnel.url,
            extra=("--existing-empty-schema",))
        memory_url=ready(MEMORY,RESOURCE/"memory-output-b5/clock-receipt.json",time.monotonic()+180)
        docker_worker(CORE,production_core["Image"],"core",env_file("core-b5.env",core_env),
            core_mounts,"65532:65532",RESOURCE/"core-output-b5/native.json",
            RESOURCE/"accounting-b5/core-control.json",RESOURCE/"core-dsn",tunnel.url)
        core_url=ready(CORE,RESOURCE/"core-output-b5/clock-receipt.json",time.monotonic()+90)
        resources={"core_url":core_url,"memory_url":memory_url,"scope":scope,
            "memory_helper_comparison_variant":{"installed":"gpt-5.6-luna/low","comparison":"gpt-6-astra/medium"},
            "native_delegation_disabled_for_cm":True,"services":[CORE,MEMORY],
            "model_attempts_before_curation":len(ledger.attempts),"parent_report":parent.report()}
        private_json(RESOURCE/"resources-ready-b5.json",resources)
        print(json.dumps({"status":"native_resources_ready","controlled_model_calls":len(ledger.attempts)}),flush=True)
        # Evaluator supplies exactly one separately protected processing function
        # after the public bindings and fresh-resource admission are committed.
        command=RESOURCE/"execute-once-b5.json"
        while not command.exists():time.sleep(.2)
        value=json.loads(command.read_text())
        processor=Path(value["processor"]).resolve()
        private_root=Path("/home/administrator/project/.eva2-artifacts/ev003-provider-history-final-20261006").resolve()
        if not processor.is_relative_to(private_root) or processor.stat().st_mode&0o077:
            raise ValueError("Independent processing boundary differs")
        module_spec=importlib.util.spec_from_file_location("p2_independent_processor",processor)
        module=importlib.util.module_from_spec(module_spec);module_spec.loader.exec_module(module)
        module.run(resources,ledger,parent)
    except BaseException as error:
        private_json(RESOURCE/"driver-failure-b5.json",{"class":type(error).__name__,"ledger":ledger.report()})
        print(json.dumps({"status":"native_driver_failed","failureClass":type(error).__name__,
            "controlled_model_calls":len(ledger.attempts)}),flush=True)
    finally:
        for name in (CORE,MEMORY):
            if any(x["Names"]==name for x in [json.loads(line) for line in subprocess.check_output(["docker","ps","-a","--format","{{json .}}"],text=True).splitlines()]):
                subprocess.run(["docker","stop","-t","30",name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        report=parent.report();parent.close()
        private_json(RESOURCE/"parent-final-b5.json",{"beforeClose":report,"afterClose":parent.report(),"tunnel":tunnel.close()})
        print(json.dumps({"status":"native_driver_settled","controlled_model_calls":len(ledger.attempts)}),flush=True)
if __name__=="__main__":main()
