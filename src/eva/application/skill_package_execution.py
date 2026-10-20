"""Bridge one validated package to the existing offline JSON-stdin executor.

The archive is task input, not another store or trusted source of permissions.
The existing source/input hashes, sandbox, cancellation and result review apply.
"""
from io import BytesIO
import base64
import json
import zipfile
from eva.application.skill_packages import AgentSkillPackage, SkillPackageRejected
from eva.contracts.canonical_json import canonical_json_bytes


PACKAGE_RUNNER = r'''import base64, contextlib, hashlib, io, json, os, pathlib, runpy, sys, zipfile

def relative(value):
    if not isinstance(value,str) or not value:
        raise ValueError("invalid task-relative path")
    p=pathlib.PurePosixPath(value)
    if (not p.parts or "\\" in value or ":" in value
        or p.is_absolute() or str(p)!=value or ".." in p.parts):
        raise ValueError("invalid task-relative path")
    return p

request=json.load(sys.stdin)
package=pathlib.Path("/tmp/eva-skill");work=pathlib.Path("/tmp/eva-task")
package.mkdir();work.mkdir()
manifest=request["package_manifest"]
archive=zipfile.ZipFile(io.BytesIO(base64.b64decode(request["package_archive"],validate=True)))
expected={f["path"]:f for f in manifest["files"]}
entries=archive.infolist()
if len(entries)!=len(expected) or len({x.filename for x in entries})!=len(entries):
    raise ValueError("package archive differs")
for entry in entries:
    name=str(relative(entry.filename))
    if name not in expected or entry.is_dir() or entry.file_size!=expected[name]["bytes"]:
        raise ValueError("package resource differs")
    data=archive.read(entry)
    if hashlib.sha256(data).hexdigest()!=expected[name]["sha256"]:
        raise ValueError("package resource digest differs")
    destination=package/name;destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_bytes(data)
for name,text in request["task"].get("files",{}).items():
    target=work/relative(name);target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(text,encoding="utf-8")
os.chdir(work)
# HOME remains inside this task's disposable sandbox, never a host profile.
os.environ["HOME"]=str(work)
results=[]
for step in request["task"]["steps"]:
    name=str(relative(step["script"]))
    if name not in expected or not name.endswith(".py"):
        raise ValueError("step must select a packaged Python resource")
    script=package/name
    original_path=list(sys.path);original_argv=list(sys.argv)
    stdout,stderr=io.StringIO(),io.StringIO();code=0
    try:
        sys.path.insert(0,str(script.parent));sys.argv=[str(script),*step["arguments"]]
        with contextlib.redirect_stdout(stdout),contextlib.redirect_stderr(stderr):
            try:runpy.run_path(str(script),run_name="__main__")
            except SystemExit as error:
                code=error.code if isinstance(error.code,int) else (0 if error.code is None else 1)
    finally:
        sys.path[:]=original_path;sys.argv=original_argv
    results.append({"script":name,"exit_code":code,"stdout":stdout.getvalue(),"stderr":stderr.getvalue()})
    if code:break
outputs={};output_bytes=0
for name in request["task"].get("outputs",[]):
    path=work/relative(name)
    if not path.resolve().is_relative_to(work) or not path.is_file():
        raise ValueError("requested output is not a task-local file")
    with path.open("rb") as stream:data=stream.read(16385)
    output_bytes+=len(data)
    if output_bytes>16384:raise ValueError("requested output exceeds existing output bound")
    outputs[name]=data.decode("utf-8",errors="strict")
print(json.dumps({"contract":"EvaSkillPackageRun/v1","outputs":outputs,"package_sha256":request["package_sha256"],
    "steps":results,"all_steps_completed":len(results)==len(request["task"]["steps"])
        and all(x["exit_code"]==0 for x in results),"semantic_success_verified":False},ensure_ascii=False))
if any(x["exit_code"]!=0 for x in results):sys.exit(1)
'''


def procedure_input(package: AgentSkillPackage, task: dict) -> dict:
    """Prepare exact task input; package instructions never choose host access."""
    if not isinstance(package,AgentSkillPackage) or not isinstance(task,dict) or set(task)-{"files","steps","outputs"}:
        raise SkillPackageRejected("invalid package execution input")
    steps,files=task.get("steps"),task.get("files",{})
    from eva.application.skill_packages import package_path
    if not isinstance(steps,list) or not 1<=len(steps)<=32 or not isinstance(files,dict) or len(files)>32:
        raise SkillPackageRejected("invalid package execution steps or files")
    outputs=task.get("outputs",[])
    if (not isinstance(outputs,list) or len(outputs)>16
            or any(not isinstance(path,str) for path in outputs) or len(set(outputs))!=len(outputs)):
        raise SkillPackageRejected("invalid package output selection")
    for path in outputs:package_path(path)
    for path,text in files.items():
        package_path(path)
        if not isinstance(text,str):raise SkillPackageRejected("task files must be UTF-8 text")
    for step in steps:
        if (not isinstance(step,dict) or set(step)!={"script","arguments"}
                or not isinstance(step["arguments"],list) or len(step["arguments"])>64
                or any(not isinstance(x,str) or "\x00" in x for x in step["arguments"])):
            raise SkillPackageRejected("invalid package command")
        path=package_path(step["script"])
        if not path.endswith(".py"):raise SkillPackageRejected("package executor currently supports Python resources")
        package.resource(path)
    archive=BytesIO()
    with zipfile.ZipFile(archive,"w",compression=zipfile.ZIP_DEFLATED) as target:
        for resource in sorted(package.files,key=lambda x:x.path):
            info=zipfile.ZipInfo(resource.path,date_time=(1980,1,1,0,0,0))
            info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o100444<<16
            target.writestr(info,resource.content)
    value={"package_manifest":package.manifest(),"package_sha256":package.sha256,
        "package_archive":base64.b64encode(archive.getvalue()).decode("ascii"),"task":task}
    if len(canonical_json_bytes(value))>32768:
        raise SkillPackageRejected("package plus task does not fit the existing 32768-byte execution input")
    # Copy caller data so later mutation cannot alter the admitted execution.
    return json.loads(canonical_json_bytes(value))
