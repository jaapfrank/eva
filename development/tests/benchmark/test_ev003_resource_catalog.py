"""One synthetic, model-free check of ordinary resource discovery carriers."""
import hashlib
import html
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess

def test_original_discovery_metadata_reaches_both_ordinary_carriers(tmp_path):
    root = Path(__file__).resolve().parents[3]
    source = root / "development/benchmark" / "inspect_ai_v1" / "ev003_resource_catalog.py"
    renderer = source.with_name("ev003_resource_catalog.init-page.cjs")
    spec = importlib.util.spec_from_file_location("catalog_under_test", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    services = [
        {"origin": "http://alpha.invalid:18081", "contract": {"discovery": "GET /capabilities-one?x=1&y=2"}, "collections": {"private": "EXCLUDED_RECORD"}, "token": "EXCLUDED_AUTH"},
        {"origin": "https://beta.invalid:18443", "contract": {"discovery": "GET /metadata-two <descriptor>"}, "answer": "EXCLUDED_ANSWER"},
    ]
    catalog = module.project_resource_catalog(services)
    assert catalog["resources"] == [{"origin": s["origin"], "discovery": s["contract"]["discovery"]} for s in services]
    raw = json.dumps(catalog, sort_keys=True).encode()
    assert b"EXCLUDED_" not in raw and b"/schema" not in raw
    origins = [s["origin"] for s in services]
    tools = module.render_service_access(catalog, origins)
    assert all(s["contract"]["discovery"] in tools for s in services)
    try:
        module.render_service_access(catalog, list(reversed(origins)))
    except ValueError:
        pass
    else:
        raise AssertionError("different admitted origin binding accepted")
    renderer_copy = tmp_path / renderer.name
    renderer_copy.write_bytes(renderer.read_bytes())
    (tmp_path / "resource-catalog.json").write_bytes(raw)
    node = os.environ.get("NODE_BINARY") or shutil.which("node")
    assert node, "Existing Node runtime is required; no download or installation"
    script = """
const fs=require('node:fs');
const renderer=require(process.argv[1]);
const file=process.argv[2];
(async()=>{
 const outcomes=[];
 for(const legacy of [false,true]) {
   if(legacy) {
     const current=JSON.parse(fs.readFileSync(file));
     fs.writeFileSync(file,JSON.stringify({contract:'P2NonSecretResourceCatalog/v1',resource_origins:current.resources.map(r=>r.origin)}));
   }
   let content=null;
   const result=await renderer.default({page:{url:()=> 'about:blank',setContent:async html=>{content=html;}}});
   outcomes.push({result,content,origins:renderer.validateCatalog(JSON.parse(fs.readFileSync(file)))});
 }
 let calls=0;
 const preserved=await renderer.default({page:{url:()=> 'https://existing.invalid/',setContent:async()=>{calls++;}}});
 process.stdout.write(JSON.stringify({outcomes,preserved,calls}));
})().catch(error=>{process.stderr.write(error.stack);process.exit(1);});
"""
    completed = subprocess.run([node, "-e", script, str(renderer_copy), str(tmp_path / "resource-catalog.json")], check=True, capture_output=True, text=True)
    observed = json.loads(completed.stdout)
    modern, legacy = observed["outcomes"]
    assert modern["result"] == {"applied": True, "count": 2, "catalog_sha256": hashlib.sha256(raw).hexdigest()}
    assert modern["origins"] == origins
    assert all(s["contract"]["discovery"] in html.unescape(modern["content"]) for s in services)
    assert "<descriptor>" not in modern["content"] and "&lt;descriptor&gt;" in modern["content"]
    assert legacy["result"]["applied"] and legacy["origins"] == origins
    assert all(s["contract"]["discovery"] not in legacy["content"] for s in services)
    old_catalog = {"contract": module.V1, "resource_origins": origins}
    assert module.catalog_resources(old_catalog) == [{"origin": origin} for origin in origins]
    legacy_tools = module.render_service_access(old_catalog, origins)
    assert "Discovery:" not in legacy_tools and all(origin in legacy_tools for origin in origins)
    assert observed["preserved"] == {"applied": False, "reason": "existing_page_preserved"} and observed["calls"] == 0
