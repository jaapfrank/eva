import {readFile, open, rename, unlink} from 'node:fs/promises';
import {createHash} from 'node:crypto';

export const optionalOptions = {
  'CO-004': {
    title:'Geheugen inzien',
    explanation:'Bekijk welke eigen gespreksinformatie Eva bewaart en waar die vandaan komt. Het bestaande basisscherm toont gespreksfragmenten; nog niet alles wat Eva daaruit heeft geleerd.',
    benefit:'Zelf iets terugvinden of controleren, zonder dat je Eva’s geheugen hoeft te onderhouden.',
  },
  'CO-005': {
    title:'Zelf aanpassen of vergeten',
    explanation:'Wijs bewaarde informatie aan die Eva moet aanpassen of vergeten, met een duidelijke terugmelding. Gewone correcties in een gesprek werken al; deze extra bediening is een aparte keuze. Er bestaat techniek om één eigen dashboardgesprek te wissen; complete bediening voor de gebruikte routes is nog niet bewezen.',
    benefit:'Zelf bepalen wat Eva blijft onthouden en kunnen controleren dat vergeten informatie niet terugkomt.',
  },
};
const hash = raw => createHash('sha256').update(raw).digest('hex');
const empty = () => ({contract:'EvaOptionalScopeDecisions/v1',decisions:{},events:[]});
export async function readDecisions(path) {
  let raw;
  try { raw=await readFile(path,'utf8'); } catch(error) { if(error.code!=='ENOENT')throw error;raw=''; }
  const data=raw ? JSON.parse(raw) : empty();
  if(data.contract!=='EvaOptionalScopeDecisions/v1' || !Array.isArray(data.events) || !data.decisions)throw new Error('Invalid decision source');
  return {data,revision:hash(raw)};
}
const fail=(status,message)=>Object.assign(new Error(message),{status});
async function lockedUpdate(path, change) {
  let lock;
  try {lock=await open(path+'.lock','wx',0o600);}catch(error){if(error.code==='EEXIST')throw fail(409,'Besluitbron wordt bijgewerkt; ververs en probeer opnieuw.');throw error;}
  const temp=path+'.tmp';
  try {
    const current=await readDecisions(path);
    const result=await change(current);
    if(result.unchanged)return {...current,event:result.event};
    const raw=JSON.stringify(current.data,null,2)+'\n';
    const handle=await open(temp,'w',0o600);
    try{await handle.writeFile(raw);await handle.sync();}finally{await handle.close();}
    // Catch an editor that doesn't participate in the lock protocol.
    if((await readDecisions(path)).revision!==current.revision)throw fail(409,'Besluitbron is gewijzigd; ververs eerst.');
    await rename(temp,path);
    return {data:current.data,revision:hash(raw),event:result.event};
  }finally{await unlink(temp).catch(()=>{});await lock.close();await unlink(path+'.lock');}
}
export async function saveDecision(path,{optionId,decision,requestId,revision}) {
  if(!optionalOptions[optionId] || !['undecided','included','excluded'].includes(decision) || !/^[a-zA-Z0-9-]{16,80}$/.test(requestId || ''))throw fail(400,'Ongeldig besluit.');
  return lockedUpdate(path,current=>{
    const existing=current.data.events.find(event=>event.id===requestId);
    if(existing){if(existing.optionId!==optionId || existing.decision!==decision)throw fail(409,'Besluitnummer is al gebruikt.');return {unchanged:true,event:existing};}
    if(revision!==current.revision)throw fail(409,'Er is intussen een ander besluit opgeslagen. Ververs eerst.');
    const previous=current.data.decisions[optionId];
    if(previous?.decision===decision)return {unchanged:true,event:current.data.events.find(event=>event.id===previous.decisionId)};
    const at=new Date().toISOString();
    const event={id:requestId,optionId,decision,previousDecision:previous?.decision || 'undecided',createdAt:at,actor:'owner',processing:{status:'pending'}};
    current.data.events.push(event);
    current.data.decisions[optionId]={decision,decisionId:requestId,updatedAt:at};
    return {event};
  });
}
// Coordinator uses this same lock/CAS protocol; scope decisions and execution stay separate.
export async function processDecision(path,{id,expectedStatus,status,consumer,taskId}) {
  if(!consumer || !['acknowledged','queued','done'].includes(status))throw fail(400,'Invalid processing update');
  return lockedUpdate(path,current=>{
    const event=current.data.events.find(value=>value.id===id);
    if(!event || event.processing.status!==expectedStatus)throw fail(409,'Decision was already claimed or changed');
    if(event.processing.consumer && event.processing.consumer!==consumer)throw fail(409,'Another consumer owns this decision');
    const allowed={pending:['acknowledged'],acknowledged:['queued','done'],queued:['done']};
    if(!allowed[expectedStatus]?.includes(status))throw fail(409,'Invalid processing transition');
    if(status==='queued' && (event.decision!=='included' || current.data.decisions[event.optionId]?.decisionId!==id))throw fail(409,'Only the current included decision can queue work');
    event.processing={...event.processing,status,consumer,updatedAt:new Date().toISOString(),...(taskId ? {taskId}: {})};
    return {event};
  });
}

export async function authorizeOwner(url, authorization, request=fetch) {
  const anonymous=await request(url,{redirect:'error',signal:AbortSignal.timeout(3000)});
  if(anonymous.status!==401 || !anonymous.headers.get('www-authenticate')?.includes('Eva cockpit'))return false;
  const response=await request(url,{headers:{Authorization:authorization},redirect:'error',signal:AbortSignal.timeout(3000)});
  return response.ok && (await response.json()).contract==='EvaDashboardStatus/v1';
}
export function decisionMiddleware({path,authorize}) {
  return async(request,response)=>{
    response.setHeader('Content-Type','application/json; charset=utf-8');response.setHeader('Cache-Control','no-store');
    const reject=(status,message)=>{response.writeHead(status);response.end(JSON.stringify({error:message}));};
    try {
      const host=request.headers.host || '';
      if(!/^(localhost|127\.0\.0\.1):\d+$/.test(host) || request.headers.origin!==`http://${host}`)return reject(403,'Ongeldige herkomst.');
      if(request.method!=='POST')return reject(405,'Alleen POST toegestaan.');
      if(!request.headers['content-type']?.startsWith('application/json'))return reject(415,'JSON vereist.');
      const authorization=request.headers.authorization || '';
      if(!/^Bearer [^\s]{32,1024}$/.test(authorization))return reject(401,'Meld aan met je bestaande eigenaar-dashboardtoken.');
      if(!await authorize(authorization))return reject(401,'Eigenaarsaanmelding niet bevestigd.');
      let raw='';for await(const chunk of request){raw+=chunk;if(raw.length>4096)return reject(413,'Besluit te groot.');}
      let input;try{input=JSON.parse(raw);}catch{return reject(400,'Ongeldige JSON.');}
      const result=await saveDecision(path,input);
      response.end(JSON.stringify({revision:result.revision,decision:result.data.decisions[input.optionId],eventId:result.event?.id}));
    }catch(error){reject(error.status || 503,error.status ? error.message : 'Besluit opslaan is tijdelijk niet mogelijk.');}
  };
}
