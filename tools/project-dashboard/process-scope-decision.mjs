import {fileURLToPath} from 'node:url';
import {processDecision} from './scope-decisions.mjs';
const path=process.env.EVA_PROJECT_DECISIONS_FILE || fileURLToPath(new URL('../../design/product/optional-scope-decisions.json',import.meta.url));
const [id,expectedStatus,status,consumer,taskId]=process.argv.slice(2);
try {
  const result=await processDecision(path,{id,expectedStatus,status,consumer,taskId});
  console.log(JSON.stringify({id:result.event.id,processing:result.event.processing}));
}catch(error){console.error(error.message);process.exitCode=1;}
