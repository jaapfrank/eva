import assert from 'node:assert/strict';
import http from 'node:http';
import { once } from 'node:events';
import test from 'node:test';
import { createLocalOwner } from '../../deploy/dashboard/local-owner.mjs';

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
