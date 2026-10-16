import { spawn } from 'node:child_process';

export const MAX_AUDIO_BYTES = 8 * 1024 * 1024;
export const MAX_PCM_BYTES = 120 * 16000 * 2;
function convert(input, arguments_, maximum, binary = 'ffmpeg') {
  if (!Buffer.isBuffer(input) || !input.length || input.length > MAX_AUDIO_BYTES) return Promise.reject(new Error('audio_size'));
  return new Promise((resolve, reject) => {
    const child = spawn(binary, ['-hide_banner', '-loglevel', 'error', '-nostdin', '-protocol_whitelist', 'pipe,crypto,data', ...arguments_], { stdio: ['pipe', 'pipe', 'ignore'] });
    const output = []; let size = 0, settled = false;
    const fail = () => { if (!settled) { settled = true; clearTimeout(timer); child.kill('SIGKILL'); reject(new Error('audio_conversion_failed')); } };
    const timer = setTimeout(fail, 20000);
    child.on('error', fail); child.stdin.on('error', () => {});
    child.stdout.on('data', chunk => { size += chunk.length; if (size > maximum) fail(); else output.push(chunk); });
    child.on('close', code => { if (!settled) { settled = true; clearTimeout(timer); if (code === 0 && size) resolve(Buffer.concat(output)); else reject(new Error('audio_conversion_failed')); } });
    child.stdin.end(input);
  });
}
export const decodeAudio = input => convert(input, ['-i', 'pipe:0', '-map', '0:a:0', '-vn', '-ac', '1', '-ar', '16000', '-f', 's16le', 'pipe:1'], MAX_PCM_BYTES);
export const encodeVoice = input => convert(input, ['-i', 'pipe:0', '-map', '0:a:0', '-vn', '-ac', '1', '-ar', '48000', '-c:a', 'libopus', '-f', 'ogg', 'pipe:1'], MAX_AUDIO_BYTES);
export async function boundedAudio(stream) {
  const chunks = []; let size = 0;
  try {
    for await (const chunk of stream) { size += chunk.length; if (size > MAX_AUDIO_BYTES) throw new Error('audio_size'); chunks.push(Buffer.from(chunk)); }
    return Buffer.concat(chunks);
  } finally { stream.destroy?.(); }
}
