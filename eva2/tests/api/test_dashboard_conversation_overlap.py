"""Exercise the actual conversation-page script with controlled HTTP responses."""
import re
import subprocess
from eva.adapters.api.dashboard_conversation import DASHBOARD_CONVERSATION_HTML


def test_questions_overlap_and_out_of_order_answers_keep_their_question():
    script = re.search(r"<script>(.*?)</script>", DASHBOARD_CONVERSATION_HTML, re.S)[1]
    harness = r"""
const assert=require('node:assert/strict'),vm=require('node:vm');
class Node {
 constructor(){this.children=[];this.handlers={};this.disabled=false;this.value='';this.textContent='';this.classList={add(){}};}
 detach(){if(this.parent){this.parent.children.splice(this.parent.children.indexOf(this),1);this.parent=null;}}
 append(...nodes){for(const n of nodes){n.detach();n.parent=this;this.children.push(n);}}
 after(n){n.detach();n.parent=this.parent;this.parent.children.splice(this.parent.children.indexOf(this)+1,0,n);}
 replaceWith(n){const parent=this.parent;n.detach();const index=parent.children.indexOf(this);parent.children.splice(index,1,n);n.parent=parent;this.parent=null;}
 remove(){this.parent.children.splice(this.parent.children.indexOf(this),1);}
 addEventListener(k,f){this.handlers[k]=f;}
 scrollIntoView(){} focus(){}
}
const ids=Object.fromEntries(['chat','form','text','send','stop'].map(k=>[k,new Node()]));
const requests=[];
const context={TextDecoder,document:{getElementById:id=>ids[id],createElement:()=>new Node()},
 fetch:(url,options)=>new Promise(resolve=>requests.push({url,body:JSON.parse(options.body),resolve}))};
vm.runInNewContext(SCRIPT,context);
function submit(text){assert.equal(ids.send.disabled,false);ids.text.value=text;return ids.form.handlers.submit({preventDefault(){}});}
function finish(index,text){
 const bytes=new TextEncoder().encode(JSON.stringify({contract:'EvaInternalGroundedReply/v1',text,message_id:'reply-'+index})+'\n');
 let read=false;
 requests[index].resolve({ok:true,headers:{get:key=>key==='content-type'?'application/x-ndjson':null},body:{getReader:()=>({read:async()=>read?{done:true}:(read=true,{value:bytes,done:false}),cancel:async()=>{},releaseLock(){}})}});
}
(async()=>{
 const first=submit('Research'),second=submit('How are you?');
 assert.equal(requests.length,2);assert.deepEqual(requests.map(r=>r.body.text),['Research','How are you?']);
 finish(1,'I am here');await second;
 let rows=ids.chat.children.filter(n=>n.className.startsWith('message'));
 assert.deepEqual(rows.map(n=>n.textContent),['Research','How are you?','I am here']);
 finish(0,'Research result');await first;
 rows=ids.chat.children.filter(n=>n.className.startsWith('message'));
 assert.deepEqual(rows.map(n=>n.textContent),['Research','Research result','How are you?','I am here']);
 const tasks=Array.from({length:8},(_,i)=>submit('Task '+i));assert.equal(ids.send.disabled,true);
 ids.text.value='Keep this draft';await ids.form.handlers.submit({preventDefault(){}});
 assert.equal(requests.length,10);assert.equal(ids.text.value,'Keep this draft');
 const stopping=ids.stop.handlers.click();
 assert.equal(ids.stop.disabled,true);assert.equal(ids.text.value,'Keep this draft');
 assert.equal(requests[10].url,'/internal/v1/dashboard/conversation/respond');
 assert.equal(requests[10].body.text,'Stop deze opdracht.');
 finish(10,'Welke van de twee opdrachten wil je stoppen?');await stopping;
 assert.equal(ids.stop.disabled,false);assert.equal(ids.text.value,'Keep this draft');
 assert.ok(ids.chat.children.some(n=>n.textContent==='Welke van de twee opdrachten wil je stoppen?'));
 for(let i=0;i<8;i++)finish(i+2,'Result '+i);await Promise.all(tasks);assert.equal(ids.send.disabled,false);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    import json
    subprocess.run(["node", "-e", "const SCRIPT=" + json.dumps(script) + ";" + harness], check=True)
