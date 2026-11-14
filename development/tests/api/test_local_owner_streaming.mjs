import assert from 'node:assert/strict';
import http from 'node:http';
import { once } from 'node:events';
import test from 'node:test';
import { createLocalOwner } from '../../../deploy/dashboard/local-owner.mjs';
import { createHoloStageLanProxy } from '../../../deploy/dashboard/holostage-lan.mjs';

test('receipt headers reach the browser before any model response body', {timeout: 4000}, async () => {
  let upstreamResponse;
  const upstream = http.createServer((req, res) => {
    assert.equal(req.headers.authorization, 'Bearer synthetic-test-authority');
    upstreamResponse = res;
    res.writeHead(200, {'content-type':'application/x-ndjson', 'x-eva-input-receipt':'Een momentje.',
                       'set-cookie':'must-not-reach-browser', 'www-authenticate':'private'});
    res.flushHeaders();
    // Body deliberately waits until the browser receives the receipt.
  });
  upstream.listen(0, '127.0.0.1'); await once(upstream, 'listening');
  const proxy = createLocalOwner({token:'synthetic-test-authority',port:0,upstreamPort:upstream.address().port});
  proxy.listen(0, '127.0.0.1'); await once(proxy, 'listening');
  let request;
  try {
    const headers = new Promise((resolve, reject) => {
      request = http.get({hostname:'127.0.0.1',port:proxy.address().port,
        path:'/internal/v1/dashboard/conversation/respond',headers:{host:'localhost:0'}}, resolve);
      request.on('error', reject);
    });
    let timer;
    const response = await Promise.race([headers, new Promise((_,reject) => {
      timer = setTimeout(() => reject(new Error('receipt waited for response body')), 1000);
    })]).finally(() => clearTimeout(timer));
    assert.equal(response.headers['x-eva-input-receipt'],'Een momentje.');
    assert.equal(response.headers['set-cookie'],undefined);
    assert.equal(response.headers['www-authenticate'],undefined);
    let body=''; response.on('data', chunk => {body+=chunk;});
    upstreamResponse.end('{"contract":"test-final"}\n');
    await once(response,'end');
    assert.equal(body,'{"contract":"test-final"}\n');
  } finally {
    request?.destroy(); proxy.closeAllConnections(); upstream.closeAllConnections();
    await Promise.all([new Promise(resolve=>proxy.close(resolve)),new Promise(resolve=>upstream.close(resolve))]);
  }
});

test('cross-site requests stay blocked except the exact one-time callback GET', {timeout:4000}, async()=>{
  const seen=[];
  const upstream=http.createServer((req,res)=>{seen.push(req.url);res.writeHead(200).end('fixture callback only');});
  upstream.listen(0,'127.0.0.1');await once(upstream,'listening');
  const proxy=createLocalOwner({token:'synthetic-test-authority',port:0,upstreamPort:upstream.address().port});
  proxy.listen(0,'127.0.0.1');await once(proxy,'listening');
  const call=(path,method='GET')=>new Promise((resolve,reject)=>{
    const req=http.request({hostname:'127.0.0.1',port:proxy.address().port,path,method,
      headers:{host:'localhost:0',origin:'https://foreign.invalid','sec-fetch-site':'cross-site'}},res=>{res.resume();res.on('end',()=>resolve(res.statusCode));});
    req.on('error',reject);req.end();
  });
  try{
    const path='/internal/v1/dashboard/owned-packages/'+'a'.repeat(64)+'/callback';
    assert.equal(await call('/internal/v1/dashboard/model-providers'),403);
    assert.equal(await call(path,'POST'),403);
    assert.equal(await call(path.replace('/callback','/save')),403);
    assert.equal(await call(path+'?state=fixture'),200);
    assert.equal(seen.length,1);
  }finally{
    proxy.closeAllConnections();upstream.closeAllConnections();
    await Promise.all([new Promise(r=>proxy.close(r)),new Promise(r=>upstream.close(r))]);
  }
});

test('holostage voice routes proxy strictly to LiveKit voice service with owner auth and origin rewrite', {timeout:4000}, async()=>{
  const voiceSeen=[];
  const voiceUpstream=http.createServer((req,res)=>{
    voiceSeen.push({method:req.method,url:req.url,auth:req.headers.authorization,origin:req.headers.origin,host:req.headers.host});
    res.writeHead(200,{'content-type':'application/json'}).end('{"ready":true}');
  });
  voiceUpstream.listen(0,'127.0.0.1');await once(voiceUpstream,'listening');
  const coreUpstream=http.createServer((req,res)=>res.writeHead(200).end('core'));
  coreUpstream.listen(0,'127.0.0.1');await once(coreUpstream,'listening');
  const vPort=voiceUpstream.address().port;
  const proxy=createLocalOwner({token:'synthetic-test-authority',port:0,upstreamPort:coreUpstream.address().port,voiceUpstreamPort:vPort});
  proxy.listen(0,'127.0.0.1');await once(proxy,'listening');
  const call=(path,method='GET',origin=null)=>new Promise((resolve,reject)=>{
    const headers={host:'localhost:0'};
    if(origin)headers.origin=origin;
    const req=http.request({hostname:'127.0.0.1',port:proxy.address().port,path,method,headers},res=>{
      res.resume();res.on('end',()=>resolve(res.statusCode));
    });
    req.on('error',reject);req.end();
  });
  try{
    assert.equal(await call('/internal/v1/dashboard/holostage/voice/session/ready','GET'),200);
    assert.equal(await call('/internal/v1/dashboard/holostage/voice/session','POST',null),403);
    assert.equal(await call('/internal/v1/dashboard/holostage/voice/session','POST','http://localhost:0'),200);
    assert.equal(await call('/internal/v1/dashboard/holostage/voice/../../secret','GET'),403);
    assert.equal(voiceSeen.length,2);
    assert.equal(voiceSeen[0].url,'/session/ready');
    assert.equal(voiceSeen[1].url,'/session');
    assert.equal(voiceSeen[1].origin,`http://localhost:${vPort}`);
    assert.equal(voiceSeen[1].auth,'Bearer synthetic-test-authority');
  }finally{
    proxy.closeAllConnections();voiceUpstream.closeAllConnections();coreUpstream.closeAllConnections();
    await Promise.all([new Promise(r=>proxy.close(r)),new Promise(r=>voiceUpstream.close(r)),new Promise(r=>coreUpstream.close(r))]);
  }
});

test('LAN HoloStage proxy jails strictly to holostage routes and strips owner Authorization', {timeout:4000}, async()=>{
  const seen=[];
  const coreUpstream=http.createServer((req,res)=>{
    seen.push({url:req.url,auth:req.headers.authorization,cookie:req.headers.cookie});
    res.writeHead(200,{'set-cookie':'eva_holostage_session=stg_'+'b'.repeat(43)+'; HttpOnly'}).end('ok');
  });
  coreUpstream.listen(0,'127.0.0.1');await once(coreUpstream,'listening');
  const lan=createHoloStageLanProxy({bindHost:'127.0.0.1',port:0,upstreamPort:coreUpstream.address().port});
  lan.listen(0,'127.0.0.1');await once(lan,'listening');
  const call=(path,method='GET',headers={})=>new Promise((resolve,reject)=>{
    const req=http.request({hostname:'127.0.0.1',port:lan.address().port,path,method,
      headers:{host:'127.0.0.1:0',...headers}},res=>{res.resume();res.on('end',()=>resolve({status:res.statusCode,setCookie:res.headers['set-cookie']}));});
    req.on('error',reject);req.end();
  });
  try{
    assert.equal((await call('/internal/v1/dashboard')).status,403);
    assert.equal((await call('/internal/v1/dashboard/holostage/voice/session/ready')).status,403);
    const validTok='stg_'+'a'.repeat(43);
    const stRes=await call('/internal/v1/dashboard/holostage/state?mode=tv_cast','GET',{
      authorization:'Bearer attacker-attempt',
      cookie:`other=1; eva_holostage_session=${validTok}`,
    });
    assert.equal(stRes.status,200);
    assert.equal(stRes.setCookie,undefined);
    assert.equal(seen[0].auth,undefined);
    assert.equal(seen[0].cookie,`eva_holostage_session=${validTok}`);
    const sessRes=await call('/internal/v1/dashboard/holostage/session','POST',{origin:'http://127.0.0.1:0'});
    assert.equal(sessRes.status,200);
    assert.ok(Array.isArray(sessRes.setCookie));
  }finally{
    lan.closeAllConnections();coreUpstream.closeAllConnections();
    await Promise.all([new Promise(r=>lan.close(r)),new Promise(r=>coreUpstream.close(r))]);
  }
});

test('Tailscale Funnel HTTPS stage proxy enforces private_stage grant check, rewrites LiveKit URL to wss://, and proxies /rtc WS', {timeout:5000}, async()=>{
  const privTok='stg_'+'p'.repeat(43);
  const tvTok='stg_'+'t'.repeat(43);
  const voiceTok='v'.repeat(40);
  const coreUpstream=http.createServer((req,res)=>{
    if(req.url==='/internal/v1/dashboard/holostage/voice-grant-check'){
      const ck=req.headers.cookie||'';
      if(ck.includes(privTok)){res.writeHead(200,{'content-type':'application/json'}).end('{"voice_allowed":true}');return;}
      res.writeHead(403).end('ambient denied');return;
    }
    res.writeHead(200).end('ok');
  });
  const voiceUpstream=http.createServer((req,res)=>{
    if(req.url==='/session'&&req.method==='POST'){
      res.writeHead(200,{'content-type':'application/json'}).end(JSON.stringify({
        url:'ws://localhost:7880',token:'eyJhbGci.eyJzdWIi.c2lnbmF0dXJl',room:'eva-voice-1'
      }));
      return;
    }
    res.writeHead(200).end('{"ready":true}');
  });
  let rtcUpgraded=false;
  const lkUpstream=http.createServer((req,res)=>{
    if(req.url?.startsWith('/rtc/validate')){
      res.writeHead(200,{'content-type':'text/plain'}).end('success');
      return;
    }
    res.writeHead(404).end();
  });
  lkUpstream.on('upgrade',(req,sock)=>{
    rtcUpgraded=true;
    sock.write('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n');
    sock.end();
  });
  for(const s of [coreUpstream,voiceUpstream,lkUpstream]){s.listen(0,'127.0.0.1');await once(s,'listening');}
  const funnelHost='jaap-pc.tail1234.ts.net';
  const lan=createHoloStageLanProxy({
    bindHost:'127.0.0.1',port:0,
    upstreamPort:coreUpstream.address().port,
    voiceUpstreamPort:voiceUpstream.address().port,
    livekitUpstreamPort:lkUpstream.address().port,
    voiceToken:voiceTok,
    publicHost:funnelHost,
  });
  lan.listen(0,'127.0.0.1');await once(lan,'listening');
  const reqFunnel=(path,method,headers={})=>new Promise((resolve,reject)=>{
    const req=http.request({hostname:'127.0.0.1',port:lan.address().port,path,method,headers:{
      host:funnelHost,'x-forwarded-proto':'https',origin:`https://${funnelHost}`,...headers
    }},res=>{
      let body='';res.setEncoding('utf8');res.on('data',c=>body+=c);
      res.on('end',()=>resolve({status:res.statusCode,body}));
    });
    req.on('error',reject);req.end();
  });
  try{
    // Unauthenticated voice -> 401
    assert.equal((await reqFunnel('/internal/v1/dashboard/holostage/voice/session/ready','GET')).status,401);
    // Ambient TV token -> 403
    assert.equal((await reqFunnel('/internal/v1/dashboard/holostage/voice/session','POST',{
      cookie:`eva_holostage_session=${tvTok}`
    })).status,403);
    // Private Pocket token -> 200 and URL rewritten to wss://jaap-pc.tail1234.ts.net
    const vRes=await reqFunnel('/internal/v1/dashboard/holostage/voice/session','POST',{
      cookie:`eva_holostage_session=${privTok}`
    });
    assert.equal(vRes.status,200);
    assert.equal(JSON.parse(vRes.body).url,`wss://${funnelHost}`);
    // LiveKit /rtc/validate preflight with valid JWT -> 200; spoofed other *.ts.net host -> 403
    const valRes=await reqFunnel('/rtc/validate?access_token=eyJhbGciOiJIUzI1NiJ9.eyJyb29tIjoiZXZhIn0.c2lnbmF0dXJlMTIzNDU','GET');
    assert.equal(valRes.status,200);
    assert.equal(valRes.body,'success');
    const spoofRes=await reqFunnel('/internal/v1/dashboard/holostage','GET',{host:'other-node.tail9999.ts.net',origin:'https://other-node.tail9999.ts.net'});
    assert.equal(spoofRes.status,403);
    // LiveKit /rtc WebSocket upgrade with valid JWT -> 101
    const wsStatus=await new Promise((resolve,reject)=>{
      const req=http.request({
        hostname:'127.0.0.1',port:lan.address().port,
        path:'/rtc?access_token=eyJhbGciOiJIUzI1NiJ9.eyJyb29tIjoiZXZhIn0.c2lnbmF0dXJlMTIzNDU',
        headers:{host:funnelHost,origin:`https://${funnelHost}`,connection:'Upgrade',upgrade:'websocket'}
      });
      req.on('upgrade',(_res,sock)=>{sock.destroy();resolve(101);});
      req.on('response',res=>{res.resume();resolve(res.statusCode);});
      req.on('error',reject);req.end();
    });
    assert.equal(wsStatus,101);
    assert.equal(rtcUpgraded,true);
  }finally{
    for(const s of [lan,coreUpstream,voiceUpstream,lkUpstream])s.closeAllConnections();
    await Promise.all([lan,coreUpstream,voiceUpstream,lkUpstream].map(s=>new Promise(r=>s.close(r))));
  }
});
