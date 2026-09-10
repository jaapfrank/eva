"""Materialize an HA component with the existing voice UI and pinned SDK."""
from pathlib import Path
import argparse
import hashlib
import json
import re
import shutil

SDK_SHA256 = "7fa17e37af5e996d8a25f15a637dcc0620215bc01b394e5d209f726afe7dc04d"


def panel_script(html):
    scripts = re.findall(r"<script(?: [^>]*)?>(.*?)</script>", html, re.S)
    if len(scripts) != 2:
        raise ValueError("Voice client script layout changed")
    script = scripts[1]
    expected = "async function ownerRequest(path,options={}){return fetch(path,{...options,headers:{...options.headers,Authorization:'Bearer '+ownerToken}});}"
    if expected not in script:
        raise ValueError("Voice owner transport changed; review HA adapter")
    script = script.replace("document.getElementById(id)", "root.getElementById(id)")
    script = script.replace(expected, r"""let phoneCapability=null; async function ownerRequest(path,options={}) {
      const token=ownerToken,visit=panel._visit;
      const result=await panel.hass.callWS({type:'eva2_voice/owner_session',path:path.replace(/^\//,''),owner_token:token,capability:phoneCapability});
      if(panel._disposed||visit!==panel._visit){
        if(path==='/session'&&result.status===200)await panel.hass.callWS({type:'eva2_voice/owner_session',path:'session/end',owner_token:token,capability:result.body.capability});
        throw new Error('Het paneel is gesloten.');
      }
      if(path==='/session'&&result.status===200)phoneCapability=result.body.capability;
      if(path==='/session/end'&&result.status===200)phoneCapability=null;
      return {ok:result.status===200,json:async()=>result.body};
    }""")
    script = script.replace("adaptiveStream:true,dynacast:true", "adaptiveStream:true,dynacast:true,singlePeerConnection:false")
    connect = "await r.connect(connection.url,connection.token);"
    if connect not in script:
        raise ValueError("Voice connect options changed; review HA relay options")
    script = script.replace(connect, """const signal=new URL(connection.url,location.origin);signal.protocol=location.protocol==='https:'?'wss:':'ws:';
    const iceServers=await panel.hass.callWS({type:'cloud/webrtc/ice_servers'});
    if(panel._disposed){await ownerRequest('/session/end',{method:'POST'});throw new Error('Het paneel is gesloten.');}
    await r.connect(signal.href,connection.token,{rtcConfig:{iceServers}});""")
    body = re.sub(r"<script.*?</script>", "", html, flags=re.S)
    body = re.sub(r"<!doctype html>|</?html[^>]*>|<meta[^>]*>|<title>.*?</title>", "", body, flags=re.S)
    body = body.replace(":root{", ":host{").replace("body{", ".eva-panel{")
    body = '<div class="eva-panel">' + body + '</div>'
    return """let sdkReady;
function loadSdk(){return sdkReady??=(new Promise((resolve,reject)=>{
 const script=document.createElement('script');script.src='/api/eva2_voice/assets/livekit-client.js';script.onload=resolve;script.onerror=reject;document.head.append(script);
}));}
class EvaOwnerVoicePanel extends HTMLElement {
 set hass(value){this._hass=value;} get hass(){return this._hass;}
 async connectedCallback(){
  this._disposed=false;this._visit=(this._visit||0)+1;if(this._mounted)return;this._mounted=true;
  const root=this.attachShadow({mode:'open'});const panel=this;
  root.innerHTML=""" + json.dumps(body) + ";\nawait loadSdk();\n" + script + """
  this._stop=async()=>{const token=ownerToken,capability=phoneCapability,oldRoom=room;ownerToken='';phoneCapability=null;
    $('login').hidden=false;$('owner-key').value='';messages.clear();jobs.clear();$('chat').replaceChildren();$('work').replaceChildren();reset('De microfoon staat uit.');
    if(oldRoom)await oldRoom.disconnect();
    if(capability)await panel.hass.callWS({type:'eva2_voice/owner_session',path:'session/end',owner_token:token,capability});
  };
 }
 disconnectedCallback(){this._disposed=true;this._visit=(this._visit||0)+1;this._stop?.().catch(()=>{});}
}
customElements.define('eva-owner-voice-panel',EvaOwnerVoicePanel);
"""


def build(root, output):
    sdk = root / "deploy/voice-session/vendor/livekit-client.js"
    if hashlib.sha256(sdk.read_bytes()).hexdigest() != SDK_SHA256:
        raise ValueError("Pinned LiveKit client checksum differs")
    output.mkdir(parents=True, exist_ok=False)
    source = root / "deploy/home-assistant/custom_components/eva2_voice"
    for name in ("__init__.py", "client.py", "config_flow.py", "conversation.py", "manifest.json", "strings.json", "phone.py", "phone_transport.py"):
        shutil.copyfile(source / name, output / name)
    assets = output / "phone-assets"
    assets.mkdir()
    shutil.copyfile(sdk, assets / "livekit-client.js")
    (assets / "panel.js").write_text(panel_script((root / "src/eva/voice_service/client.html").read_text()))
    manifest = {str(p.relative_to(output)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(output.rglob("*")) if p.is_file()}
    (output / "phone-build.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(build(Path(__file__).resolve().parents[1], args.output), indent=2))
