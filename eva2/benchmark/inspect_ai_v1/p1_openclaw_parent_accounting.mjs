/** Evaluator-only OpenClaw binding to the existing parent IPC and native proxy.
 * No catalog edits, credential adoption, database access or second inference relay.
 */
import net from 'node:net';
import { createHash } from 'node:crypto';
import { registerHooks } from 'node:module';
import { fileURLToPath } from 'node:url';

const KEY = Symbol.for('eva.cp001.openclaw.accounting');
const MAX_FRAME = 65536;
const MAX_NATIVE_BODY = 33554432; // The installed native proxy's existing bound.
export const NATIVE_PINS = Object.freeze({
  'client-Cs08OXVQ.mjs': 'f661febe01d4708bdafd2c0f49be288b6466c07cbedd74b79c887cbe0deb77da',
  'inference-proxy-b-aJMSxm.mjs': 'eb514a3aba831f47742be8e23db369280ebd020da27b7c52d1e8029066dd9b1d',
});
const monotonic = () => Number(process.hrtime.bigint()) / 1e9;
const safeUsage = (value) => {
  const u = value?.response?.usage ?? value?.usage;
  const values = [u?.input_tokens, u?.output_tokens, u?.total_tokens];
  return values.every(n => Number.isSafeInteger(n) && n >= 0) && values[0] + values[1] === values[2]
    ? { input_tokens: values[0], output_tokens: values[1], total_tokens: values[2] } : null;
};
const terminal = v => ['response.completed', 'response.failed', 'response.incomplete', 'error'].includes(v?.type);
// Transport exception metadata only; messages, provider bodies and credentials are excluded.
const nativeFailure = error => {
  const klass = error?.name ?? error?.constructor?.name;
  const code = error?.code;
  const status = error?.statusCode ?? error?.status_code ?? error?.status;
  return { error_class: typeof klass === 'string' && /^[A-Za-z][A-Za-z0-9_]{0,79}$/.test(klass) ? klass : 'UNKNOWN',
    error_code: typeof code === 'string' && /^[A-Za-z0-9_.:-]{1,96}$/.test(code) ? code : null,
    http_status: Number.isInteger(status) && status >= 100 && status <= 599 ? status : null };
};


/** Same authenticated frame protocol as ProcessSampleLedger; the parent owns storage. */
export class OpenClawProcessLedger {
  constructor(control, { timeoutMs = 10000 } = {}) {
    this.control = control; this.timeoutMs = timeoutMs;
    this.sequence = 0; this.eventSequence = 0; this.pending = new Map(); this.attempts = new Map();
    this.stopReason = null; this.stopHandlers = new Set(); this.closed = false;
  }
  stop(reason) {
    if (!this.stopReason) this.stopReason = reason;
    for (const handler of this.stopHandlers) handler();
  }
  async connect() {
    const { socket_path, namespace, request_id, token } = this.control;
    this.socket = net.createConnection(socket_path);
    this.socket.on('error', () => this.stop('parent connection failed'));
    this.socket.on('close', () => { if (!this.closed) this.stop('parent connection closed'); });
    let buffer = '';
    const hello = new Promise((resolve, reject) => { this.hello = { resolve, reject }; });
    this.socket.on('data', bytes => {
      buffer += bytes.toString('utf8');
      if (Buffer.byteLength(buffer) > MAX_FRAME && !buffer.includes('\n')) return this.stop('invalid parent frame');
      while (buffer.includes('\n')) {
        const end = buffer.indexOf('\n'); const raw = buffer.slice(0, end); buffer = buffer.slice(end + 1);
        try {
          if (Buffer.byteLength(raw) >= MAX_FRAME) throw new Error();
          const value = JSON.parse(raw);
          if (value.hello === true && this.hello) {
            if (value.stopped) this.stop('parent already stopped');
            this.hello.resolve(); this.hello = null;
          } else if (value.stop) this.stop('parent stopped');
          else {
            const waiter = this.pending.get(value.rpc);
            if (!waiter) throw new Error();
            waiter.resolve(value); this.pending.delete(value.rpc);
          }
        } catch { this.stop('invalid parent frame'); }
      }
    });
    await new Promise((resolve, reject) => { this.socket.once('connect', resolve); this.socket.once('error', reject); });
    this.write({ namespace, request_id, token });
    await Promise.race([hello, new Promise((_, reject) => {
      const timer = setTimeout(() => reject(new Error('parent hello unavailable')), this.timeoutMs); timer.unref();
    })]);
    this.check(); return this;
  }
  check() { if (this.stopReason || this.closed) throw new Error('OpenClaw parent accounting stopped'); }
  write(value) {
    const frame = JSON.stringify(value) + '\n';
    if (Buffer.byteLength(frame) > MAX_FRAME) throw new Error('parent frame too large');
    this.socket.write(frame);
  }
  async rpc(message) {
    const rpc = ++this.sequence;
    return await new Promise((resolve, reject) => {
      const timer = setTimeout(() => { this.pending.delete(rpc); this.stop('parent RPC unavailable'); reject(new Error('parent RPC unavailable')); }, this.timeoutMs);
      this.pending.set(rpc, { resolve: value => { clearTimeout(timer); resolve(value); } });
      try { this.write({ rpc, ...message }); }
      catch (error) { clearTimeout(timer); this.pending.delete(rpc); this.stop('parent write failed'); reject(error); }
    });
  }
  async begin(role, model, effort) {
    this.check();
    const base = { event_id: `${this.control.namespace}:${++this.eventSequence}`, request_id: this.control.request_id };
    const decision = (await this.rpc({ op: 'event', event: { ...base, provider_event: 'begin', role, model, reasoning_effort: effort } })).decision;
    if (!decision?.allow) {
      await this.rpc({ op: 'event', event: { ...base, provider_event: 'settle', attempt_id: null, status: 'aborted', usage: null } });
      this.stop('parent refused attempt'); throw new Error('parent refused attempt');
    }
    const id = decision.attempt_id;
    this.attempts.set(id, { ...base, status: 'pending' });
    if (this.stopReason) { await this.incomplete(id); this.check(); }
    return id;
  }
  async settle(id, usage) {
    const row = this.attempts.get(id);
    if (!row || row.status !== 'pending') return;
    row.status = 'settling';
    const decision = (await this.rpc({ op: 'event', event: { event_id: row.event_id, request_id: row.request_id,
      provider_event: 'settle', attempt_id: id, status: usage ? 'completed' : 'aborted', usage } })).decision;
    row.status = usage && decision?.allow ? 'completed' : 'usage_unknown'; row.usage = usage;
    if (!usage || !decision?.allow) this.stop('provider usage unknown or settlement refused');
    return decision?.allow === true;
  }
  incomplete(id) { return this.settle(id, null); }
  async close() {
    const count = [...this.attempts.values()].filter(r => ['pending','settling'].includes(r.status)).length;
    const answer = await this.rpc({ op: 'close', settled: count === 0, active_tasks: 0, pending_local: count, cancel_errors: [] });
    this.closed = true; this.socket.end(); this.socket.destroy(); return answer.closed === true;
  }
}

/** Observes original Responses bytes, never transcript/model-catalog/rate-limit summaries. */
export class OpenClawAccounting {
  constructor(ledger, deadline, { now = monotonic, save = () => {} } = {}) {
    if (!Number.isFinite(deadline)) throw new Error('original absolute deadline required');
    this.ledger = ledger; this.deadline = deadline; this.now = now; this.save = save;
    this.calls = []; this.active = new Set(); this.wsAttempts = new WeakMap();
    this.stop = () => { for (const cancel of this.active) cancel(); };
    ledger.stopHandlers?.add(this.stop);
  }
  check() { this.ledger.check(); if (this.now() >= this.deadline) { this.ledger.stop('original deadline'); throw new Error('original deadline'); } }
  turnParams(method, params) {
    if (method !== 'turn/start') return params;
    this.check();
    // Normal foreground turn; native child/compaction/memory operation roles are bound at transport.
    return { ...params, model: 'gpt-6-sol', effort: 'none', ...(params.collaborationMode ? {
      collaborationMode: { ...params.collaborationMode, settings: { ...params.collaborationMode.settings,
        model: 'gpt-6-sol', reasoning_effort: 'none' } } } : {}) };
  }
  prepareBody(body) {
    const metadata = body.client_metadata;
    const native = JSON.parse(metadata?.['x-codex-turn-metadata']);
    const helper = Boolean(native.parent_thread_id || native.subagent_kind || metadata?.['x-openai-subagent'] || metadata?.['x-codex-parent-thread-id'])
      || ['compaction','memory'].includes(native.request_kind);
    if (!helper && !['turn','prewarm'].includes(native.request_kind)) throw new Error('unbound native operation');
    const role = helper ? 'helper' : 'contact';
    const model = helper ? 'gpt-6-astra' : 'gpt-6-sol'; const effort = helper ? 'medium' : 'none';
    return { body: { ...body, model, reasoning: { ...(body.reasoning ?? {}), effort } }, accounting: { role, model, effort } };
  }
  async begin(binding, transport) {
    this.check();
    const id = await this.ledger.begin(binding.role, binding.model, binding.effort);
    const row = { attempt_id: id, role: binding.role, model: binding.model, reasoning_effort: binding.effort,
      transport, status: 'pending', usage: null, started_monotonic: this.now() };
    this.calls.push(row); this.save(this.calls.map(({ cleanup, ...row }) => row));
    // A durable parent permit may race stop. Such a reservation is never sent.
    try { this.check(); } catch (error) { await this.finish(row, null); throw error; }
    return row;
  }
  async finish(row, usage, extra = {}) {
    if (row.status !== 'pending') return;
    row.status = 'settling'; row.usage = usage;
    Object.assign(row, extra, { settled_monotonic: this.now() }); this.save(this.calls.map(({ cleanup, ...row }) => row));
    try {
      const accepted = await this.ledger.settle(row.attempt_id, usage);
      row.status = usage ? (accepted === false ? 'parent_settlement_failed' : 'completed') : 'sdk_failed_usage_unknown';
    } catch (error) { row.status = 'accounting_unavailable'; this.ledger.stop('settlement unavailable'); throw error; }
    finally { this.save(this.calls.map(({ cleanup, ...row }) => row)); }
  }
  async event(row, value) {
    if (!terminal(value)) return;
    const extra = { native_terminal_type: value.type };
    await this.finish(row, safeUsage(value), extra);
  }
  async fetch(original, params, binding) {
    if (!binding || params.auditContext !== 'codex-parent-local-inference' || !new URL(params.url).pathname.endsWith('/responses')) {
      throw new Error('unbound native HTTP route');
    }
    const row = await this.begin(binding, 'http');
    const controller = new AbortController(); const cancel = () => controller.abort(); this.active.add(cancel);
    const timeout = setTimeout(cancel, Math.max(0, (this.deadline - this.now()) * 1000));
    let guarded;
    try {
      this.check();
      row.dispatched = true; row.dispatched_monotonic = this.now(); this.save(this.calls.map(({ cleanup, ...row }) => row));
      guarded = await original({ ...params, signal: AbortSignal.any([controller.signal, ...params.signal ? [params.signal] : []]),
        init: { ...params.init, signal: AbortSignal.any([controller.signal, ...params.init.signal ? [params.init.signal] : []]) } });
      row.http_status = guarded.response.status; this.save(this.calls.map(({ cleanup, ...row }) => row));
      const cleanup = async () => { clearTimeout(timeout); this.active.delete(cancel); await this.finish(row, null); };
      const release = guarded.release.bind(guarded);
      const observer = this;
      const isSSE = guarded.response.headers.get('content-type')?.includes('text/event-stream');
      let text = ''; let overflow = false; const decoder = new TextDecoder();
      const parse = async (flush) => {
        if (overflow) return;
        if (isSSE) {
          const frames = text.replaceAll('\r\n','\n').split('\n\n'); text = frames.pop();
          if (flush && text) { frames.push(text); text = ''; }
          for (const frame of frames) {
            const data = frame.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n');
            if (data && data !== '[DONE]') { try { await observer.event(row, JSON.parse(data)); } catch (error) { if (row.status !== 'pending') throw error; } }
          }
        } else if (flush) { try { await observer.finish(row, safeUsage(JSON.parse(text))); } catch (error) { if (row.status !== 'pending') throw error; } }
      };
      const body = guarded.response.body?.pipeThrough(new TransformStream({
        async transform(bytes, target) {
          text += decoder.decode(bytes, { stream: true });
          if (Buffer.byteLength(text) > MAX_NATIVE_BODY) { overflow = true; text = ''; }
          await parse(false); target.enqueue(bytes);
        },
        async flush() { text += decoder.decode(); await parse(true); await cleanup(); },
      }));
      return { ...guarded, response: new Response(body, { status: guarded.response.status, statusText: guarded.response.statusText, headers: guarded.response.headers }),
        release: async () => { try { await cleanup(); } finally { await release(); } } };
    } catch (error) {
      clearTimeout(timeout); this.active.delete(cancel); await this.finish(row, null, { failure_category: 'native_transport_exception', transport_failure: nativeFailure(error), upstream_sdk_error_class: 'UNKNOWN' }); throw error;
    }
  }
  async wsSend(remote, prepared, options, callback) {
    if (this.wsAttempts.has(remote)) throw new Error('native websocket already has unresolved response');
    const row = await this.begin(prepared.accounting, 'websocket');
    this.wsAttempts.set(remote, row);
    const cancel = () => remote.terminate(); this.active.add(cancel);
    const timer = setTimeout(cancel, Math.max(0, (this.deadline-this.now())*1000));
    row.cleanup = () => {
      clearTimeout(timer); this.active.delete(cancel); this.wsAttempts.delete(remote);
      remote.off('close', closed); remote.off('error', closed);
    };
    const closed = error => {
      row.cleanup();
      this.finish(row, null, error instanceof Error ? {transport_failure:nativeFailure(error),upstream_sdk_error_class:'UNKNOWN'} : {}).catch(() => this.ledger.stop('websocket settlement unavailable'));
    };
    remote.once('close', closed); remote.once('error', closed);
    try {
      this.check();
      row.dispatched = true; row.dispatched_monotonic = this.now(); this.save(this.calls.map(({ cleanup, ...row }) => row));
      remote.send(prepared.bytes, options, error => { if (error) closed(error); callback(error); });
    } catch (error) { closed(error); throw error; }
  }
  async wsEvent(remote, bytes, binary) {
    const row = this.wsAttempts.get(remote);
    if (!row || binary) return;
    let value; try { value = JSON.parse(bytes.toString('utf8')); } catch { return; }
    if (terminal(value)) {
      // Parent settles before the existing proxy forwards this terminal or permits a retry.
      await this.event(row, value); row.cleanup();
    }
  }

  async settled() { return this.calls.every(row => !['pending','settling'].includes(row.status)) && this.active.size === 0; }
}

function replaceOnce(source, before, after) {
  if (source.split(before).length !== 2) throw new Error('native instrumentation anchor differs');
  return source.replace(before, after);
}
/** Exact evaluator overlay on installed native code; installed bytes remain untouched. */
export function installOpenClawAccounting(accounting, pluginRoot, pins) {
  if (globalThis[KEY]) throw new Error('one native accounting owner required');
  globalThis[KEY] = accounting;
  const root = fileURLToPath(new URL('./dist/.setup/', pluginRoot.endsWith('/') ? `file://${pluginRoot}` : `file://${pluginRoot}/`));
  const loaded = new Set();
  const hooks = registerHooks({ load(url, context, nextLoad) {
    const result = nextLoad(url, context);
    if (!url.startsWith('file:')) return result;
    const path = fileURLToPath(url); const name = path.slice(root.length);
    if (!['client-Cs08OXVQ.mjs','inference-proxy-b-aJMSxm.mjs'].includes(name)) return result;
    const original = Buffer.from(result.source).toString('utf8');
    if (createHash('sha256').update(original).digest('hex') !== pins[name]) throw new Error('installed native source pin differs');
    loaded.add(name);
    let source = `const __evaAccounting = globalThis[Symbol.for('eva.cp001.openclaw.accounting')];\n` + original;
    if (name.startsWith('client')) {
      source = replaceOnce(source, 'request(method, params, optionsInput) {', "request(method, params, optionsInput) {\n\t\tparams = __evaAccounting.turnParams(method, params);");
    } else {
      source = replaceOnce(source, 'const rewritten = Buffer.from(JSON.stringify(prepared.body));', 'const normalized = __evaAccounting.prepareBody(prepared.body);\n\t\tconst rewritten = Buffer.from(JSON.stringify(normalized.body));');
      source = replaceOnce(source, 'bytes: rewritten,', 'bytes: rewritten,\n\t\t\taccounting: normalized.accounting,');
      source = replaceOnce(source, '...createUploadBody(body, requestSignal, release),', '...createUploadBody(body, requestSignal, release),\n\t\t\taccounting: prepared.accounting,');
      source = replaceOnce(source, 'guarded = await fetchWithSsrFGuard({', 'guarded = await __evaAccounting.fetch(fetchWithSsrFGuard, {');
      source = replaceOnce(source, 'auditContext: "codex-parent-local-inference"\n\t\t\t\t});', 'auditContext: "codex-parent-local-inference"\n\t\t\t\t}, upload.accounting);');
      source = replaceOnce(source, 'remote.on("message", (data, binary) => {', 'remote.on("message", async (data, binary) => {\n\t\t\t\t\t\t\t\ttry { await __evaAccounting.wsEvent(remote, rawBytes(data), binary); } catch { close(); return; }');
      source = replaceOnce(source, 'remote.send(prepared.bytes, { binary: false }, (error) => {', 'await __evaAccounting.wsSend(remote, prepared, { binary: false }, (error) => {');
    }
    return { ...result, source };
  } });
  return { loaded, close() { hooks.deregister(); delete globalThis[KEY]; } };
}
