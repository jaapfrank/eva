"""Resume only the admitted zero-call CM pair after host import-path failure.
No participant input, expected answer, scorer or provider replacement is defined.
Existing containers/configuration/credentials/clock are preserved.
"""
import hashlib, importlib.util, json, os, subprocess, sys, time
from pathlib import Path
from urllib.parse import urlparse
from cp001_process_accounting import ParentProcessAccounting
from p1_model_tunnel import ModelTunnel
from p2_provider_history_native_glue import ledger_for, private_json
from p2_provider_history_native_driver import ROOT, BENCH, RESOURCE, CORE, MEMORY, NETWORK, inspect, ready

PRIVATE=Path("/home/administrator/project/.eva2-artifacts/ev003-provider-history-final-20261006")
PROCESSOR_SHA="46b3f7830bcd2e434efdcf822b0b2262f8e72ab2a3b45a9fe99eaa2afe01666f"
def digest(value):
    value={**value,"Mounts":sorted(value["Mounts"],key=lambda row:row["Destination"])}
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":")).encode()).hexdigest()
def main():
    prior=json.loads((RESOURCE/"driver-failure-b5.json").read_text())
    if prior["class"]!="ModuleNotFoundError" or prior["ledger"]["provider_attempts"]!=0:
        raise ValueError("Only recorded zero-call import failure may resume")
    if json.loads((RESOURCE/"resume-failure-b5.json").read_text())["ledger"]["provider_attempts"]!=0:
        raise ValueError("Earlier resume already used inference")
    if (PRIVATE/"pair-private-result.json").exists():
        raise ValueError("Pair has already executed")
    if json.loads((RESOURCE/"clock.json").read_text())!={"contract":"EvaScenarioCalendar/v1","generation":0,"offsetMilliseconds":0}:
        raise ValueError("Scenario is no longer unused")
    processor=PRIVATE/"one_pair_processor.py"
    if hashlib.sha256(processor.read_bytes()).hexdigest()!=PROCESSOR_SHA:
        raise ValueError("Pre-call processor binding differs")
    # Existing public source path is the entire host environment correction.
    sys.path[:0]=[str(ROOT/"eva2/src"),str(BENCH)]
    spec=importlib.util.spec_from_file_location("p2_independent_processor",processor)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    data={name:inspect(name) for name in (CORE,MEMORY)}
    if any(row["State"]["Running"] for row in data.values()):
        raise ValueError("Prior zero-call workers must be stopped")
    configurations={name:digest({k:row[k] for k in ("Image","Config","HostConfig","Mounts")}) for name,row in data.items()}
    ports=[]
    for row in data.values():
        cmd=row["Config"]["Cmd"]
        ports.append(urlparse(cmd[cmd.index("--provider-proxy")+1]).port)
    if len(set(ports))!=1 or ports[0] is None:raise ValueError("Existing proxy binding differs")
    # Preserve the original zero-call terminal evidence. No fixture DB is reset.
    for name in ("core","memory"):
        directory=RESOURCE/(name+"-output-b5")
        for filename in ("native.json","clock-receipt.json"):
            p=directory/filename
            if p.exists():
                target=directory/(filename+".pre-mount-order-check")
                if target.exists():raise ValueError("Already resumed output")
                p.rename(target)
    plan=json.loads((BENCH/"ev003_execution_plan_20261004.json").read_text())
    ledger=ledger_for("eva2-provider-history-cm01-epoch1",plan)
    directory=RESOURCE/"accounting-b5"
    credentials={name:json.loads((directory/(name+"-control.json")).read_text()) for name in ("core","memory")}
    if any(x["request_id"]!=ledger.sample_id or x["namespace"]!=name for name,x in credentials.items()):
        raise ValueError("Existing native accounting identity differs")
    os.chmod(directory,0o700)
    parent=ParentProcessAccounting(ledger,ledger.sample_id,directory/"accounting.sock",
        workers={"core":("contact","helper"),"memory":("helper",)})
    # Rebind the exact existing private namespace tokens to the new zero-call parent.
    for name,value in credentials.items():
        if value["socket_path"]!=str(parent.path):raise ValueError("Native accounting socket differs")
        parent.workers[name]["token"]=value["token"]
    parent.start()
    os.chown(parent.path,65532,65532);os.chmod(parent.path,0o660);os.chmod(directory,0o710)
    tunnel=ModelTunnel(("198.18.221.1",ports[0]),"198.18.221.0/24").start()
    try:
        receipt={"contract":"EvaP2ZeroCallResume/v1","configurationSha256":configurations,
            "processorSha256":PROCESSOR_SHA,"priorAttempts":0,"hostPythonPath":[str(ROOT/"eva2/src"),str(BENCH)],
            "nativeCredentialsUnchanged":True,"scenarioClockGeneration":0,
            "datasetCommitmentSha256":"52da8464812d5a6f052d3429615d60f64f6e05b42e22e385a4f52ec007917077"}
        private_json(RESOURCE/"zero-call-resume-binding-canonical.json",receipt)
        subprocess.check_call(["docker","start",MEMORY],stdout=subprocess.DEVNULL)
        memory_url=ready(MEMORY,RESOURCE/"memory-output-b5/clock-receipt.json",time.monotonic()+180)
        subprocess.check_call(["docker","start",CORE],stdout=subprocess.DEVNULL)
        core_url=ready(CORE,RESOURCE/"core-output-b5/clock-receipt.json",time.monotonic()+90)
        for name in (CORE,MEMORY):
            now=inspect(name)
            if digest({k:now[k] for k in ("Image","Config","HostConfig","Mounts")})!=configurations[name]:
                raise ValueError("Native configuration changed during restart")
        resources=json.loads((RESOURCE/"resources-ready-b5.json").read_text())
        resources.update(core_url=core_url,memory_url=memory_url,parent_report=parent.report(),model_attempts_before_curation=0)
        private_json(RESOURCE/"resources-ready-b5-resume-canonical.json",resources)
        print(json.dumps({"status":"same_unused_pair_native_ready","controlled_model_calls":len(ledger.attempts)}),flush=True)
        module.run(resources,ledger,parent)
    except BaseException as error:
        private_json(RESOURCE/"resume-failure-b5-canonical.json",{"class":type(error).__name__,"ledger":ledger.report()})
        print(json.dumps({"status":"native_resume_failed","failureClass":type(error).__name__,"controlled_model_calls":len(ledger.attempts)}),flush=True)
    finally:
        for name in (CORE,MEMORY):
            subprocess.run(["docker","stop","-t","30",name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        report=parent.report();parent.close()
        private_json(RESOURCE/"parent-final-b5-resume-canonical.json",{"beforeClose":report,"afterClose":parent.report(),"tunnel":tunnel.close()})
        print(json.dumps({"status":"native_resume_settled","controlled_model_calls":len(ledger.attempts)}),flush=True)
if __name__=="__main__":main()
