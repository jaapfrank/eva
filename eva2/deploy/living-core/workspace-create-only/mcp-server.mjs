import fs from 'node:fs';
import {createHash} from 'node:crypto';
import {Server} from '/opt/eva2/filesystem-mcp/node_modules/@modelcontextprotocol/sdk/dist/esm/server/index.js';
import {StdioServerTransport} from '/opt/eva2/filesystem-mcp/node_modules/@modelcontextprotocol/sdk/dist/esm/server/stdio.js';
import {CallToolRequestSchema,ListToolsRequestSchema} from '/opt/eva2/filesystem-mcp/node_modules/@modelcontextprotocol/sdk/dist/esm/types.js';
import {runBrokerResult,requestFrame} from './broker-client.mjs';
const executable='/opt/eva2/atomic/workspace-writer-broker';
const digest=createHash('sha256').update(fs.readFileSync(executable)).digest('hex');
if(digest!==fs.readFileSync(executable+'.sha256','utf8').trim()) throw Error('binary binding mismatch');
const flags=fs.constants.O_RDONLY|fs.constants.O_DIRECTORY|fs.constants.O_NOFOLLOW;
const rootFd=fs.openSync('/workspace',flags),journalFd=fs.openSync('/journal',flags);
const bindingFd=fs.openSync('/run/eva2/atomic-binding.json',fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW);
const bindingStat=fs.fstatSync(bindingFd);
if(!bindingStat.isFile()||bindingStat.uid!==process.getuid()||(bindingStat.mode&0o777)!==0o400) throw Error('invalid host binding file');
const bindingBytes=fs.readFileSync(bindingFd);fs.closeSync(bindingFd);
const binding=JSON.parse(bindingBytes.toString('utf8'));
const statMatches=(fd,expected)=>{const st=fs.fstatSync(fd);return st.dev===expected.dev&&st.ino===expected.ino;};
const clientHash=createHash('sha256').update(fs.readFileSync(new URL('./broker-client.mjs',import.meta.url))).digest('hex');
if(binding.version!==1||typeof binding.project_journal_identity!=='string'||!/^[a-f0-9]{64}$/.test(binding.project_journal_identity)||binding.binary_sha256!==digest||binding.client_sha256!==clientHash||!statMatches(rootFd,binding.root)||!statMatches(journalFd,binding.journal)) throw Error('host binding mismatch');
const providerBinding=createHash('sha256').update(bindingBytes).digest('hex');
const properties={operation_id:{type:'string',pattern:'^[0-9a-f]{64}$'},request_binding_sha256:{type:'string',pattern:'^[0-9a-f]{64}$'},expected_generation:{type:'integer',minimum:0,maximum:Number.MAX_SAFE_INTEGER},path:{type:'string',pattern:'^[A-Za-z0-9_-][A-Za-z0-9_.-]*$',maxLength:255},content:{type:'string',maxLength:1048576}};
const inputSchema={type:'object',properties,required:Object.keys(properties),additionalProperties:false};
const names=['workspace_create_bound','workspace_observe_bound'];
const server=new Server({name:'eva-bound-workspace',version:'2.0.0'},{capabilities:{tools:{}}});
server.setRequestHandler(ListToolsRequestSchema,async()=>({tools:names.map(name=>({name,description:(name===names[0]?'Create immutable private artifact with bound durable receipt.':'Observe existing bound receipt without recovery or publication.')+' Provider binding SHA256 '+providerBinding,inputSchema}))}));
server.setRequestHandler(CallToolRequestSchema,async({params})=>{
 const args=params.arguments;
 let result;
 try {
  if(!names.includes(params.name)||!args||Object.keys(args).length!==5||Object.keys(args).some(k=>!Object.hasOwn(properties,k))||typeof args.path!=='string'||!/^[A-Za-z0-9_-][A-Za-z0-9_.-]*$/.test(args.path)||args.path.length>255) throw Error('invalid');
  requestFrame(args);
  result=runBrokerResult({executable,rootFd,journalFd,request:args,observe:params.name===names[1]});
 } catch {result={status:'blocked',code:'invalid_request'};}
 const verified=result.status==='receipt';
 const data=verified?{...result,...result.receipt,content:args.content}:result;
 return {content:[{type:'text',text:JSON.stringify(data)}],structuredContent:data,isError:!verified||result.receipt.effect!=='created'};
});
await server.connect(new StdioServerTransport());
