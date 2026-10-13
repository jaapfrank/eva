// Real local SFU transport; synthetic Chromium microphone; no model or owner session.
// The coordinator owns the isolated SFU lifetime and writes recovery.json only
// after observing its replacement process healthy. No Room event is injected.
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const {chromium} = require(process.env.EVA_TEST_PLAYWRIGHT_MODULE || '/app/node_modules/playwright');
const root = path.resolve(__dirname, '../..');
const directory = process.env.EVA_TEST_RECOVERY_DIRECTORY;
assert(directory && path.isAbsolute(directory), 'Set a fresh absolute barrier directory');
const apiKey = process.env.EVA_TEST_LIVEKIT_KEY;
const apiSecret = process.env.EVA_TEST_LIVEKIT_SECRET;
assert(apiKey && apiSecret, 'Use isolated test-only credentials');
const livekitURL = process.env.EVA_TEST_LIVEKIT_URL || 'ws://127.0.0.1:7880';
assert.equal(new URL(livekitURL).hostname, '127.0.0.1');
const original = fs.readFileSync(process.env.EVA_TEST_VOICE_HTML || path.join(root, 'src/eva/voice_service/client.html'), 'utf8');
assert.equal(original.split('const localAccess=false;').length, 2);
// Exactly the local-access transformation performed by the actual media server.
const html = original.replace('const localAccess=false;', 'const localAccess=true;');
const sdk = fs.readFileSync(process.env.EVA_TEST_LIVEKIT_SDK || path.join(root, 'deploy/voice-session/vendor/livekit-client.js'));
const digest = data => crypto.createHash('sha256').update(data).digest('hex');
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const write = (name, value) => fs.writeFileSync(path.join(directory, name), JSON.stringify(value, null, 2), {flag:'wx'});
const requests = [];
let sessions = 0;
function token(room) {
  const now = Math.floor(Date.now() / 1000);
  const encode = value => Buffer.from(JSON.stringify(value)).toString('base64url');
  const body = encode({alg:'HS256',typ:'JWT'}) + '.' + encode({iss:apiKey,sub:'owner',nbf:now-5,exp:now+600,
    video:{roomJoin:true,room,canPublish:true,canSubscribe:true,canPublishData:true}});
  return body + '.' + crypto.createHmac('sha256',apiSecret).update(body).digest('base64url');
}
const server = http.createServer((request,response) => {
  requests.push({method:request.method,path:request.url});
  if(request.method === 'GET' && request.url === '/') {
    response.setHeader('Content-Type','text/html');response.end(html);return;
  }
  if(request.method === 'GET' && request.url === '/livekit-client.js') {
    response.setHeader('Content-Type','text/javascript');response.end(sdk);return;
  }
  response.setHeader('Content-Type','application/json');
  if(request.method === 'POST' && request.url === '/session') {
    const room = 'isolated-recovery-' + (++sessions);
    response.end(JSON.stringify({room,url:livekitURL,token:token(room)}));return;
  }
  if(request.method === 'POST' && /^\/session\/end\/isolated-recovery-[12]$/.test(request.url)) {
    response.end(JSON.stringify({closed:true}));return;
  }
  response.statusCode=404;response.end('{}');
});
let browser, page;
const errors = [];
async function main() {
  assert.equal(fs.readdirSync(directory).filter(n => /^(ready|disconnected|recovery|result)\.json$/.test(n)).length, 0,
    'A fresh barrier directory is required');
  await new Promise(resolve => server.listen(0,'127.0.0.1',resolve));
  browser = await chromium.launch({headless:true,executablePath:chromium.executablePath(),args:[
    '--use-fake-device-for-media-stream','--use-fake-ui-for-media-stream',
    '--disable-background-networking','--autoplay-policy=no-user-gesture-required']});
  page = await browser.newPage();
  await page.addInitScript(() => {
    window.captureObservation = {calls:0,tracks:[]};
    const nativeCapture = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
    navigator.mediaDevices.getUserMedia = async constraints => {
      window.captureObservation.calls++;
      const stream = await nativeCapture(constraints);
      window.captureObservation.tracks.push(...stream.getTracks());
      return stream;
    };
  });
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('http://127.0.0.1:' + server.address().port);
  const snapshot = () => page.evaluate(() => ({calls:captureObservation.calls,
    tracks:captureObservation.tracks.map(track => ({state:track.readyState,kind:track.kind})),
    textDisabled:document.getElementById('text').disabled,
    startDisabled:document.getElementById('start').disabled,
    state:document.getElementById('state').textContent,
    connected:room?.state || null,
    published:room?.localParticipant.audioTrackPublications.size || 0}));
  assert.equal((await snapshot()).calls,0);
  await page.locator('#start').click();
  await page.waitForFunction(() => !document.getElementById('text').disabled &&
    room?.state === 'connected' && room.localParticipant.audioTrackPublications.size === 1, null, {timeout:30000});
  const initial = await snapshot();
  assert.equal(initial.calls,1);assert.deepEqual(initial.tracks,[{state:'live',kind:'audio'}]);
  assert.equal(sessions,1);
  write('ready.json',{phase:'published_real_webrtc',...initial,clientSha256:digest(original),sdkSha256:digest(sdk)});
  // Root kills only the disposable SFU after this barrier. Observe SDK behavior.
  await page.waitForFunction(() => captureObservation.tracks[0].readyState === 'ended' &&
    document.getElementById('text').disabled, null, {timeout:45000});
  const disconnected = await snapshot();
  assert.equal(disconnected.calls,1);assert.equal(sessions,1);
  write('disconnected.json',{phase:'native_track_stopped',...disconnected});
  const deadline = Date.now()+60000;
  while(!fs.existsSync(path.join(directory,'recovery.json'))) {
    assert(Date.now()<deadline,'Coordinator did not confirm isolated SFU recovery');await sleep(100);
  }
  const recovery = JSON.parse(fs.readFileSync(path.join(directory,'recovery.json'),'utf8'));
  assert.equal(recovery.ready,true);assert(recovery.oldContainerId && recovery.newContainerId);
  assert.notEqual(recovery.oldContainerId,recovery.newContainerId);
  // Bounded observation across an actually recovered SFU, without UI actions.
  await sleep(5000);
  const recovered = await snapshot();
  assert.equal(recovered.calls,1);assert.equal(sessions,1);
  assert.deepEqual(recovered.tracks,[{state:'ended',kind:'audio'}]);assert(recovered.textDisabled);
  await page.locator('#start').click();
  await page.waitForFunction(() => !document.getElementById('text').disabled &&
    room?.state === 'connected' && room.localParticipant.audioTrackPublications.size === 1, null, {timeout:30000});
  const restarted = await snapshot();
  assert.equal(restarted.calls,2);assert.equal(sessions,2);
  assert.deepEqual(restarted.tracks,[{state:'ended',kind:'audio'},{state:'live',kind:'audio'}]);
  await page.locator('#end').click();
  await page.waitForFunction(() => captureObservation.tracks.every(track => track.readyState === 'ended'));
  assert.equal(errors.length,0,JSON.stringify(errors));
  const result={proof:'actual-client-real-livekit-transport-recovery',initial,disconnected,recovered,restarted,
    recovery,clientSha256:digest(original),sdkSha256:digest(sdk),requests,
    physicalMicrophone:false,productionCore:false,modelCalls:0,observationAfterRecoveryMs:5000};
  write('result.json',result);console.log(JSON.stringify(result));
}
main().catch(async error => {
  let diagnostic;
  try { diagnostic = page && await page.evaluate(() => ({online:navigator.onLine,hidden:document.hidden,
    state:document.getElementById('state')?.textContent,
    startDisabled:document.getElementById('start')?.disabled,
    textDisabled:document.getElementById('text')?.disabled,
    captureCalls:window.captureObservation?.calls,
    trackStates:window.captureObservation?.tracks.map(track => track.readyState)})); }
  catch(probeError) { diagnostic = {probeError:probeError.message}; }
  try { write('failure.json',{error:error.stack,diagnostic,errors,requests,sessions}); }
  catch(writeError) { console.error('Could not preserve failure diagnostic: '+writeError.message); }
  console.error(error.stack);process.exitCode=1;
}).finally(async () => {
  if(browser)await browser.close();server.close();
});
