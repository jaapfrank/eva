import { readFileSync, openSync, closeSync, fstatSync, constants } from 'node:fs';

export function tokenFromFile(path) {
  const fd = openSync(path, constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    const stat = fstatSync(fd);
    if (!stat.isFile() || stat.size < 32 || stat.size > 1025 || (stat.mode & 0o007)) throw new Error('control_token_unavailable');
    const token = readFileSync(fd, 'utf8').trim();
    if (token.length < 32 || token.length > 1024 || /\s/.test(token)) throw new Error('control_token_unavailable');
    return token;
  } finally { closeSync(fd); }
}
export class Core {
  constructor(url, token) {
    const parsed = new URL(url);
    if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password || parsed.search || parsed.hash || parsed.pathname !== '/') throw new Error('invalid_core_url');
    this.url = parsed.origin; this.token = token;
  }
  async call(path, payload, { binary = false, pcm = false, contact } = {}) {
    const response = await fetch(this.url + '/internal/v1/whatsapp/' + path, { method: 'POST', redirect: 'error',
      signal: AbortSignal.timeout(120000), headers: { authorization: 'Bearer ' + this.token(),
        'content-type': pcm ? 'audio/pcm' : 'application/json', ...(contact ? { 'X-Eva-Contact': contact } : {}) },
      body: pcm ? payload : JSON.stringify(payload) });
    if (!response.ok) { await response.body?.cancel(); throw new Error('core_unavailable'); }
    const maximum = binary ? 8 * 1024 * 1024 : 256 * 1024;
    const chunks = []; let size = 0;
    for await (const chunk of response.body) { size += chunk.length; if (size > maximum) throw new Error('core_response_size'); chunks.push(chunk); }
    const bytes = Buffer.concat(chunks);
    return binary ? { bytes, mime: response.headers.get('content-type') } : JSON.parse(bytes.toString('utf8'));
  }
}
