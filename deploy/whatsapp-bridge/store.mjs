import { DatabaseSync } from 'node:sqlite';
import { mkdirSync, lstatSync, chmodSync } from 'node:fs';
import { join } from 'node:path';
import { createHash } from 'node:crypto';

export const sourceId = key => createHash('sha256').update(key.remoteJid + '\0' + key.id).digest('hex');
export class Store {
  constructor(directory, codec = JSON) {
    mkdirSync(directory, { recursive: true, mode: 0o700 });
    const st = lstatSync(directory);
    if (!st.isDirectory() || st.isSymbolicLink() || st.uid !== process.getuid() || (st.mode & 0o077)) throw new Error('private_storage_required');
    this.codec = codec;
    const file = join(directory, 'bridge.sqlite');
    try { const prior = lstatSync(file); if (!prior.isFile() || prior.isSymbolicLink() || prior.uid !== process.getuid() || (prior.mode & 0o077)) throw new Error('private_database_required'); }
    catch (error) { if (error.code !== 'ENOENT') throw error; }
    this.db = new DatabaseSync(file); chmodSync(file, 0o600);
    this.db.exec(`PRAGMA journal_mode=DELETE; PRAGMA synchronous=FULL; PRAGMA foreign_keys=ON;
      CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS inbound (id TEXT PRIMARY KEY, contact TEXT NOT NULL, account TEXT NOT NULL,
        source TEXT NOT NULL, state TEXT NOT NULL, response TEXT, created_at TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS outbound (id TEXT PRIMARY KEY, kind TEXT NOT NULL, source_id TEXT NOT NULL,
        provider_id TEXT NOT NULL UNIQUE, state TEXT NOT NULL, receipt TEXT);
    `);
    this.db.prepare("UPDATE inbound SET state='ambiguous' WHERE state='processing'").run();
    this.db.prepare("UPDATE outbound SET state='ambiguous' WHERE state='dispatching'").run();
  }
  encode(value) { return JSON.stringify(value, this.codec.replacer); }
  decode(value) { return value === undefined ? undefined : JSON.parse(value, this.codec.reviver); }
  get(key) { return this.decode(this.db.prepare('SELECT value FROM kv WHERE key=?').get(key)?.value); }
  put(key, value) { this.db.prepare('INSERT INTO kv VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value').run(key, this.encode(value)); }
  settings() { return this.get('settings') || { enabled: false, contact_phone: null, audio_enabled: false, accept_after: 0 }; }
  authKeys(type, ids, restore) {
    return Object.fromEntries(ids.map(id => { let value = this.get('auth-key:' + type + ':' + id); if (value && type === 'app-state-sync-key') value = restore(value); return [id, value]; }));
  }
  setAuthKeys(data) {
    this.db.exec('BEGIN IMMEDIATE');
    try {
      for (const [type, values] of Object.entries(data)) for (const [id, value] of Object.entries(values)) {
        const key = 'auth-key:' + type + ':' + id;
        if (value == null) this.db.prepare('DELETE FROM kv WHERE key=?').run(key); else this.put(key, value);
      }
      this.db.exec('COMMIT');
    } catch (error) { this.db.exec('ROLLBACK'); throw error; }
  }
  clearAuth() { this.db.exec("DELETE FROM kv WHERE key='auth-creds' OR key LIKE 'auth-key:%'"); }
  admit(id, contact, account, source, at) {
    return this.db.prepare("INSERT OR IGNORE INTO inbound VALUES (?,?,?,?,'processing',NULL,?)").run(id, contact, account, this.encode(source), at).changes === 1;
  }
  inbound(id) { const row = this.db.prepare('SELECT * FROM inbound WHERE id=?').get(id); return row ? { ...row, source: this.decode(row.source), response: row.response ? this.decode(row.response) : null } : null; }
  prepared(id, response) { this.db.prepare("UPDATE inbound SET state='prepared',response=? WHERE id=? AND state='processing'").run(this.encode(response), id); }
  evidence(id, evidence) { const row = this.inbound(id); this.db.prepare('UPDATE inbound SET source=? WHERE id=?').run(this.encode({ ...row.source, evidence: { ...row.source.evidence, ...evidence } }), id); }
  finish(id, state) { this.db.prepare('UPDATE inbound SET state=? WHERE id=?').run(state, id); }
  claim(id, kind, source, provider) { return this.db.prepare("INSERT OR IGNORE INTO outbound VALUES (?,?,?,?,'dispatching',NULL)").run(id, kind, source, provider).changes === 1; }
  sent(id, receipt) { this.db.prepare("UPDATE outbound SET state='sent',receipt=? WHERE id=?").run(receipt, id); }
  ambiguous(id) { this.db.prepare("UPDATE outbound SET state='ambiguous' WHERE id=? AND state='dispatching'").run(id); }
  outbound(id) { return this.db.prepare('SELECT * FROM outbound WHERE id=?').get(id); }
  ownMessage(id) { return Boolean(this.db.prepare('SELECT id FROM outbound WHERE provider_id=?').get(id)); }
  close() { this.db.close(); }
}
