// Check the fixed decoder through both actual query-string consumers.
import './prepare-dependencies.mjs';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdtemp, writeFile, rm, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'vite';
const root = dirname(fileURLToPath(import.meta.url));
const temporary = await mkdtemp(join(tmpdir(), 'eva-dashboard-decoder-'));
const require = createRequire(import.meta.url);
const direct = require('query-string');
async function check(query) {
  const rows = [['q=abc%20def','abc def'], ['q=%E2%9C%93','✓'], ['q=a%2Bb','a+b'], ['q=a+b','a b'], ['q=%zz','%zz'], ['q=','']];
  for (const [input,expected] of rows) assert.equal(query.parse(input).q,expected);
  assert.deepEqual([...query.parse('a=1&a=2').a], ['1','2']);
  assert.equal(query.parse(query.stringify({ q:'a + ✓' })).q,'a + ✓');
  return rows.length + 2;
}
try {
  const entry = join(temporary,'entry.mjs');
  await writeFile(entry, `import q from ${JSON.stringify(join(root,'node_modules/query-string/index.js'))}; export const parse=q.parse; export const stringify=q.stringify;\n`);
  await build({ configFile:join(root,'vite.config.mjs'), build:{ outDir:join(temporary,'output'), emptyOutDir:true, manifest:false, lib:{ entry, formats:['es'], fileName:()=> 'decoder.mjs' } } });
  const bundled = await import(pathToFileURL(join(temporary,'output/decoder.mjs')));
  const cases = await check(direct) + await check(bundled);
  // The advisory input must finish within a bounded separate process.
  const { spawnSync } = await import('node:child_process');
  const smoke = join(temporary,'malformed.mjs');
  await writeFile(smoke, `import { parse } from ${JSON.stringify(pathToFileURL(join(temporary,'output/decoder.mjs')).href)};let p='';for(let i=0;i<32000;i++)p+='%' + Math.floor(i/256).toString(16).padStart(2,'0') + '%' + (i%256).toString(16).padStart(2,'0')+'x';p+='%zz';if(typeof parse('q='+p).q!=='string')throw Error('decoder result');\n`);
  const result = spawnSync(process.execPath,[smoke],{timeout:4000,encoding:'utf8'});
  assert.equal(result.error,undefined);assert.equal(result.status,0);
  console.log(JSON.stringify({contract:'EvaDashboardDecoderRegression/v1',passed:true,casesPassed:cases+1,malformedInputBytes:224003,noProviderCalls:true}));
} finally {
  if (dirname(resolve(temporary)) !== resolve(tmpdir()) || !temporary.split(/[\\/]/).at(-1).startsWith('eva-dashboard-decoder-')) throw Error('Unexpected test directory');
  await rm(temporary,{recursive:true,force:true});
}
