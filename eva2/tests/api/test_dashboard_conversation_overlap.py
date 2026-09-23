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
 append(...nodes){for(const n of nodes){n.parent=this;this.children.push(n);}}
 after(n){n.parent=this.parent;this.parent.children.splice(this.parent.children.indexOf(this)+1,0,n);}
 remove(){this.parent.children.splice(this.parent.children.indexOf(this),1);}
 addEventListener(k,f){this.handlers[k]=f;}
 scrollIntoView(){} focus(){}
}
const ids=Object.fromEntries(['chat','form','text','send','stop'].map(k=>[k,new Node()]));
const requests=[];
const context={document:{getElementById:id=>ids[id],createElement:()=>new Node()},
 fetch:(url,options)=>new Promise(resolve=>requests.push({url,body:JSON.parse(options.body),resolve}))};
vm.runInNewContext(SCRIPT,context);
function submit(text){assert.equal(ids.send.disabled,false);ids.text.value=text;return ids.form.handlers.submit({preventDefault(){}});}
function finish(index,text){requests[index].resolve({ok:true,json:async()=>({contract:'EvaDashboardGroundedReply/v1',text,message_id:'reply-'+index})});}
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
 for(let i=0;i<8;i++)finish(i+2,'Result '+i);await Promise.all(tasks);assert.equal(ids.send.disabled,false);
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    import json
    subprocess.run(["node", "-e", "const SCRIPT=" + json.dumps(script) + ";" + harness], check=True)
