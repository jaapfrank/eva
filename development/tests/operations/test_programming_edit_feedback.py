"""Optional diagnostics cannot replay effects or turn missing feedback into clean code."""
import asyncio
from dataclasses import replace
import hashlib
import sys
from types import SimpleNamespace
import pytest
from eva.operations import programming_workspace as pw
from tests.operations.test_programming_workspace import boundary,SESSION,WORKSPACE,REQUEST


@pytest.mark.parametrize("failure", [False, True, "oversized"])
def test_receipt_replay_keeps_same_versioned_feedback_without_rewriting(boundary,monkeypatch,failure):
    create,config,state=boundary
    seen=[]
    class Diagnostics:
        def __init__(self,*a,**kw):pass
        async def before(self,name,args):seen.append("before");return {"before":"retained"}
        async def after(self,before,result):
            seen.append("after")
            if failure is True:raise OSError("optional server unavailable")
            return {"contract":"EvaProgrammingDiagnostics/v1","status":"available",
                "after":{"content_sha256":"a"*64,"document_version":2},"new":[{"message":"name is undefined" if failure != "oversized" else "x"*5000}]}
        async def close(self):seen.append("close")
    monkeypatch.setitem(sys.modules,"eva.operations.programming_diagnostics",SimpleNamespace(ProgrammingDiagnostics=Diagnostics))
    manager=pw.ProgrammingWorkspaces(replace(config,python_diagnostics=True,max_result_bytes=1024 if failure=="oversized" else config.max_result_bytes))
    try:
        result=manager.call_tool(SESSION,"write_file",{"path":"/workspace/a.py"},REQUEST,workspace_key=WORKSPACE)
        assert result["isError"] is False and result["eva_diagnostics"]["semantic_success_verified"] is False
        assert result["eva_diagnostics"]["status"]==("unavailable" if failure else "available")
        assert result["eva_diagnostics"]["sandbox_image"]==config.image
        assert manager.call_tool(SESSION,"write_file",{"path":"/workspace/a.py"},REQUEST,workspace_key=WORKSPACE)==result
        assert seen==["before","after"] and len(state.calls)==1
    finally:manager.close_all()
    assert "close" in seen


@pytest.mark.parametrize("kwargs", [{"python_diagnostics":"true"},{"python_diagnostics":1},
    {"diagnostics_timeout_seconds":0},{"diagnostics_timeout_seconds":31},{"diagnostics_timeout_seconds":True}])
def test_only_trusted_typed_settings_enable_diagnostics(boundary,kwargs):
    _,config,_=boundary
    with pytest.raises(ValueError,match="diagnostics"):
        replace(config,**kwargs)
