import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,readFile,lstat,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createServer} from 'node:net';
import {execFileSync} from 'node:child_process';
import {removeStaleSocket} from './stale-socket.mjs';

test('startup removes only a stale owned socket',async()=>{
 const dir=await mkdtemp(join(tmpdir(),'eva-socket-test-'));const path=join(dir,'mcp.sock');
 try{await removeStaleSocket(path);execFileSync('python3',['-c','import socket,sys; s=socket.socket(socket.AF_UNIX); s.bind(sys.argv[1]); s.close()',path]);assert.equal((await lstat(path)).isSocket(),true);await removeStaleSocket(path);await assert.rejects(lstat(path),{code:'ENOENT'});}finally{await rm(dir,{recursive:true,force:true});}
});
test('startup preserves an active listener and regular files',async()=>{
 const dir=await mkdtemp(join(tmpdir(),'eva-socket-test-'));const path=join(dir,'mcp.sock');const server=createServer(socket=>socket.end());
 try{await new Promise(r=>server.listen(path,r));await assert.rejects(removeStaleSocket(path),/active listener/);await new Promise(r=>server.close(r));await writeFile(path,'keep');await assert.rejects(removeStaleSocket(path),/type/);assert.equal(await readFile(path,'utf8'),'keep');}finally{if(server.listening)await new Promise(r=>server.close(r));await rm(dir,{recursive:true,force:true});}
});