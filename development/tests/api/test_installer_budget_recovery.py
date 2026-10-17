"""Run the real installer budget script against a small deterministic DOM."""
import json
import subprocess
import pytest
from eva.adapters.api.installer_budget_api import INSTALLER_BUDGET_SCRIPT


@pytest.mark.parametrize("phase,bound,enabled,confirmed", [
    ("staged", False, True, True), ("ready", False, True, True),
    ("ready", True, True, False), ("empty", False, True, False),
    ("ready", False, False, False),
])
def test_budget_button_can_finish_interrupted_capability_binding(phase, bound, enabled, confirmed):
    status = {"phase": phase, "capability_bound": bound, "enabled": enabled,
              "budget_sha256": "a" * 64}
    harness = r"""
const vm=require('node:vm'),assert=require('node:assert/strict');
const status=STATUS, events={}, posts=[];
const elements={};
for(const id of ['budget-stage','budget-confirm','budget-result'])elements[id]={disabled:false,addEventListener:(kind,handler)=>events[id]=handler};
let approved=true;
const context={window:{__evaBudgetEnabled:true,confirm:()=>approved,dispatchEvent:()=>{}},
 document:{querySelectorAll:()=>[],getElementById:id=>elements[id]},Event:class{},
 fetch:async(path,options={})=>{if(options.method==='POST')posts.push([path,JSON.parse(options.body)]);return {ok:true,json:async()=>status};}};
(async()=>{vm.runInNewContext(SCRIPT,context);await new Promise(setImmediate);
assert.equal(elements['budget-confirm'].disabled,!EXPECTED);
if(EXPECTED){approved=false;await events['budget-confirm']();assert.equal(posts.length,0);approved=true;}
await events['budget-confirm']();
assert.equal(posts.length,EXPECTED?1:0);
if(EXPECTED){assert.equal(posts[0][0],'/internal/v1/onboarding/budgets/confirm');assert.equal(posts[0][1].budget_sha256,status.budget_sha256);}
})().catch(error=>{console.error(error);process.exitCode=1});
"""
    harness = harness.replace("STATUS", json.dumps(status)).replace("SCRIPT", json.dumps(INSTALLER_BUDGET_SCRIPT)).replace("EXPECTED", json.dumps(confirmed))
    subprocess.run(["node", "-e", harness], check=True, capture_output=True, text=True, timeout=10)
