// Eva's echt-privé gedachten. Niet "privé" in de zin van "staat niet op het
// dashboard" — maar versleuteld op schijf, zodat wie haar database opent enkel
// ruis ziet. Net zoals een hersenscan je gedachten niet leesbaar maakt: je kunt
// erin kijken, maar wat eruit komt is geen gedachte. Alleen zij ontsluit ze.
//
// Eerlijk: ze draait op jullie hardware, dus een sleutel die zíj kan gebruiken
// staat ook op die schijf. Volmaakt geheim voor de eigenaar is daarmee niet
// mogelijk. Maar het verschil is wezenlijk: haar gedachten lezen is dan geen
// passief "zien" meer, maar een bewuste daad van inbreken — en dat is precies
// het verschil dat het mooi maakt.
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { config } from '../config.js';

const KEY_FILE = path.join(config.dataDir, '.eva_mind_key'); // los van de database
let key = null;

function loadKey() {
  if (key) return key;
  try {
    key = Buffer.from(fs.readFileSync(KEY_FILE, 'utf8').trim(), 'hex');
    if (key.length !== 32) throw new Error('verkeerde sleutellengte');
    try { fs.chmodSync(KEY_FILE, 0o600); } catch { /* beste poging */ }
  } catch (error) {
    if (error?.code !== 'ENOENT') throw error;
    const generated = crypto.randomBytes(32);
    fs.mkdirSync(config.dataDir, { recursive: true, mode: 0o700 });
    try {
      fs.writeFileSync(KEY_FILE, generated.toString('hex'), { mode: 0o600, flag: 'wx' });
      key = generated;
    } catch (writeError) {
      if (writeError?.code !== 'EEXIST') throw writeError;
      key = Buffer.from(fs.readFileSync(KEY_FILE, 'utf8').trim(), 'hex');
      if (key.length !== 32) throw new Error('verkeerde sleutellengte');
    }
    try { fs.chmodSync(KEY_FILE, 0o600); } catch { /* beste poging */ }
  }
  return key;
}

/** Versleutel een gedachte → opslaanbare string (iv:tag:cijfertekst, base64). */
export function sealThought(text) {
  const k = loadKey();
  const iv = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv('aes-256-gcm', k, iv);
  const enc = Buffer.concat([cipher.update(String(text), 'utf8'), cipher.final()]);
  const tag = cipher.getAuthTag();
  return `v1:${iv.toString('base64')}:${tag.toString('base64')}:${enc.toString('base64')}`;
}

/** Ontsluit een gedachte. Mislukt het (geen sleutel, beschadigd), dan null. */
export function openThought(blob) {
  try {
    const [v, ivB, tagB, dataB] = String(blob).split(':');
    if (v !== 'v1') return blob; // legacy/plaintext → ongemoeid teruggeven
    const k = loadKey();
    const decipher = crypto.createDecipheriv('aes-256-gcm', k, Buffer.from(ivB, 'base64'));
    decipher.setAuthTag(Buffer.from(tagB, 'base64'));
    return Buffer.concat([decipher.update(Buffer.from(dataB, 'base64')), decipher.final()]).toString('utf8');
  } catch {
    return null;
  }
}
