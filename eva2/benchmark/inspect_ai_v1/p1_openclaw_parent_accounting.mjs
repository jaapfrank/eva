/** Evaluator-only OpenClaw binding to the existing parent IPC and native proxy.
 * No catalog edits, credential adoption, database access or second inference relay.
 */
import net from 'node:net';
import { createHash } from 'node:crypto';
import { registerHooks } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { openSync, writeFileSync, fsyncSync, closeSync, lstatSync, readFileSync, realpathSync, constants } from 'node:fs';
import { join, resolve, dirname } from 'node:path';
import { installScenarioCalendar } from './ev003_node_calendar.mjs';

const KEY = Symbol.for('eva.cp001.openclaw.accounting');
const MAX_FRAME = 65536;
const MAX_NATIVE_BODY = 33554432; // The installed native proxy's existing bound.
export const NATIVE_PINS = Object.freeze({
  'client-Cs08OXVQ.mjs': 'f661febe01d4708bdafd2c0f49be288b6466c07cbedd74b79c887cbe0deb77da',
  'inference-proxy-b-aJMSxm.mjs': 'eb514a3aba831f47742be8e23db369280ebd020da27b7c52d1e8029066dd9b1d',
});
export const NATIVE_MANIFEST_PIN = '487c9e30a10e22b167be27884eba958e5204296ae799caa16138090e489a5315';
export const NATIVE_QUEUE_PIN = '079bf068c24205f3ff6dd359f5edde2a14359bcef80608f32e68706ff47a334d';
export const NATIVE_SCENARIO_PIN = 'd278e65190cb7e9a9187962fd413187bdd09e5e431454e9287f29ab15f6d9bce';
export const NATIVE_LOOP_PINS = Object.freeze({
  'run-attempt-BHu11r7h.mjs': '1232e529e6309af58f13f5f4ec88673231507304ce698d4c50f1021c1c8de376',
  'native-hook-relay-Bfyv8cuh.mjs': 'e58fa02170a4d63f7c2e98de8c7514ac46906fae19538b131fd6e2f2b1fedb6a',
});
const digest = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');
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
  async close({ activeTasks = 0, pendingLocal = 0, cancelErrors = [], nativeSettled = true } = {}) {
    const count = [...this.attempts.values()].filter(r => ['pending','settling'].includes(r.status)).length;
    const answer = await this.rpc({ op: 'close', settled: count === 0 && pendingLocal === 0 && activeTasks === 0 && nativeSettled, active_tasks: activeTasks, pending_local: count + pendingLocal, cancel_errors: cancelErrors });
    this.closed = true; this.socket.end(); this.socket.destroy(); return answer.closed === true;
  }
}

/** Observes original Responses bytes, never transcript/model-catalog/rate-limit summaries. */
export class OpenClawAccounting {
  constructor(ledger, deadline, { now = monotonic, save = () => {}, clockFile = null, privateModelContextDir = null, observeTools = false } = {}) {
    if (!Number.isFinite(deadline)) throw new Error('original absolute deadline required');
    this.ledger = ledger; this.deadline = deadline; this.now = now; this.save = save;
    this.calls = []; this.active = new Set(); this.wsAttempts = new WeakMap();
    this.clockFile = clockFile; this.scenarioReadbacks = []; this.previousScenario = null;
    this.privateModelContextDir = privateModelContextDir; this.observeTools = observeTools;
    this.tools = new Map(); this.clients = new Map(); this.nativeReadbacks = [];
    this.nativeModuleBindings = new Map(); this.nativeQueues = new Map();
    this.toolCoverage = true; this.nativeCoverageFailure = null; this.privateSequence = 0; this.nativeCloseReads = new Map(); this.nativeClosed = new Map();
    this.stop = () => { for (const cancel of this.active) cancel(); };
    ledger.stopHandlers?.add(this.stop);
  }
  check() { this.ledger.check(); if (this.now() >= this.deadline) { this.ledger.stop('original deadline'); throw new Error('original deadline'); } }
  /** Native temporal formatter only: auth/TLS/Rust clocks and monotonic time stay actual. */
  temporalContext(build) {
    if (!this.clockFile) return build();
    this.check();
    if (this.active.size || this.calls.some(row => ['pending','settling'].includes(row.status))
        || [...this.tools.values()].some(row => ['reserved','running'].includes(row.status))) {
      throw new Error('native work must settle before scenario context');
    }
    const calendar = installScenarioCalendar(this.clockFile);
    try {
      const state = calendar.refresh(); const previous = this.previousScenario;
      if (previous && (state.generation < previous.generation || state.offsetMilliseconds < previous.offsetMilliseconds
          || (state.generation === previous.generation && state.offsetMilliseconds !== previous.offsetMilliseconds))) {
        throw new Error('scenario calendar moved backwards or changed without a generation');
      }
      const result = build();
      const temporal = result?.openclaw_temporal_context;
      if (result?.then || temporal?.kind !== 'application' || typeof temporal.value !== 'string'
          || digest(calendar.refresh()) !== digest(state)) throw new Error('native temporal context contract differs');
      this.previousScenario = state;
      this.scenarioReadbacks.push({ generation: state.generation, canonical_clock_state_sha256: digest(state),
        native_temporal_context_sha256: digest(temporal) });
      return result;
    } finally { calendar.restore(); }
  }
  turnParams(method, params, client) {
    if (this.observeTools && client && !this.clients.has(client)) this.clients.set(client, new Set());
    if (this.observeTools && client && method === 'turn/start' && typeof params?.threadId === 'string') this.clients.get(client).add(params.threadId);
    if (method !== 'turn/start') return params;
    this.check();
    if (this.clockFile && (!params.additionalContext?.openclaw_temporal_context
        || digest(params.additionalContext.openclaw_temporal_context) !== this.scenarioReadbacks.at(-1)?.native_temporal_context_sha256)) {
      throw new Error('native scenario temporal context not bound');
    }
    // Normal foreground turn; native child/compaction/memory operation roles are bound at transport.
    return { ...params, model: 'gpt-6-sol', effort: 'none', ...(params.collaborationMode ? {
      collaborationMode: { ...params.collaborationMode, settings: { ...params.collaborationMode.settings,
        model: 'gpt-6-sol', reasoning_effort: 'none' } } } : {}) };
  }
  /** Bind only the native environment date belonging to this ordinary turn. */
  scenarioBody(body, native, originalBytes) {
    if (!this.clockFile) return { body };
    this.check();
    const turnId = body.client_metadata?.turn_id;
    if (typeof turnId !== 'string' || !turnId || native.turn_id !== turnId
        || !Buffer.isBuffer(originalBytes) || originalBytes.length > MAX_NATIVE_BODY) {
      throw new Error('exact native turn and original bytes required for calendar');
    }
    const readback = this.scenarioReadbacks.at(-1);
    const state = JSON.parse(readFileSync(this.clockFile, 'utf8'));
    if (!readback || digest(state) !== readback.canonical_clock_state_sha256) {
      throw new Error('scenario calendar generation changed before dispatch');
    }
    const carriers = new Map();
    for (const [i, message] of (body.input ?? []).entries()) {
      const metadata = message.internal_chat_message_metadata_passthrough;
      if (metadata?.turn_id !== turnId) continue;
      const kinds = metadata.content_item_kinds;
      if (!Array.isArray(kinds) || !Array.isArray(message.content)
          || kinds.length !== message.content.length || !Number.isFinite(metadata.create_time)) {
        throw new Error('native content metadata differs');
      }
      for (const [j, kind] of kinds.entries()) {
        if (!['environments.environment_context', 'additional_content.openclaw_temporal_context'].includes(kind)) continue;
        const content = message.content[j];
        if (carriers.has(kind) || content.type !== 'input_text' || typeof content.text !== 'string'
            || message.role !== (kind.startsWith('environments.') ? 'user' : 'developer')) {
          throw new Error('native calendar carrier missing or ambiguous');
        }
        carriers.set(kind, { i, j, text: content.text });
      }
    }
    const environment = carriers.get('environments.environment_context');
    const application = carriers.get('additional_content.openclaw_temporal_context');
    const temporal = application?.text.match(/^<openclaw_temporal_context>([\s\S]*)<\/openclaw_temporal_context>$/);
    const date = temporal?.[1].match(/^Current date: (\d{4}-\d{2}-\d{2})$/m)?.[1];
    const zone = temporal?.[1].match(/^Time zone: ([^\r\n]+)$/m)?.[1];
    const nativeDates = [...(environment?.text ?? '').matchAll(/<current_date>(\d{4}-\d{2}-\d{2})<\/current_date>/g)];
    const nativeZones = [...(environment?.text ?? '').matchAll(/<timezone>([^<]+)<\/timezone>/g)];
    if (!environment || !date || !zone || nativeDates.length !== 1 || nativeZones.length !== 1
        || nativeZones[0][1] !== zone
        || digest({kind:'application', value:temporal[1]}) !== readback.native_temporal_context_sha256) {
      throw new Error('same-turn native calendar context differs');
    }
    // Offset ticking is local to this date value; all OS/auth/deadline clocks stay real.
    const parts = new Intl.DateTimeFormat('en-CA', {timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit'})
      .formatToParts(new Date(Date.now() + state.offsetMilliseconds));
    const civilDate = ['year','month','day'].map(kind => parts.find(part => part.type === kind)?.value).join('-');
    if (date !== civilDate) throw new Error('scenario date changed before dispatch');
    const text = environment.text.replace(nativeDates[0][0], '<current_date>'+date+'</current_date>');
    const input = body.input.map((message, i) => i !== environment.i ? message : {...message,
      content:message.content.map((content,j) => j !== environment.j ? content : {...content,text})});
    if (digest(JSON.parse(readFileSync(this.clockFile, 'utf8'))) !== digest(state)) {
      throw new Error('scenario calendar changed during binding');
    }
    return { body:{...body,input}, calendarBinding:{
      turn_id:turnId,generation:state.generation,canonical_clock_state_sha256:digest(state),
      native_temporal_context_sha256:readback.native_temporal_context_sha256,
      changed_field:'/input/'+environment.i+'/content/'+environment.j+'/text/current_date',
      native_date_changed:nativeDates[0][1] !== date
    }, nativeOriginalBodyBytes:originalBytes, nativeCalendarPrebindingBodyBytes:Buffer.from(JSON.stringify(body)) };
  }
  prepareBody(body, originalBytes) {
    const metadata = body.client_metadata;
    const native = JSON.parse(metadata?.['x-codex-turn-metadata']);
    const helper = Boolean(native.parent_thread_id || native.subagent_kind || metadata?.['x-openai-subagent'] || metadata?.['x-codex-parent-thread-id'])
      || ['compaction','memory'].includes(native.request_kind);
    if (!helper && !['turn','prewarm'].includes(native.request_kind)) throw new Error('unbound native operation');
    const role = helper ? 'helper' : 'contact';
    const model = helper ? 'gpt-6-astra' : 'gpt-6-sol'; const effort = helper ? 'medium' : 'none';
    const selected = { ...body, model, reasoning: { ...(body.reasoning ?? {}), effort } };
    const {body:bound,...calendar} = this.scenarioBody(selected,native,originalBytes);
    return { body:bound, accounting:{role,model,effort,...calendar} };
  }
  retainPrivate(bytes, label) {
    if (!this.privateModelContextDir) throw new Error('private native context directory required');
    const dir = resolve(this.privateModelContextDir); const stat = lstatSync(dir);
    if (!stat.isDirectory() || stat.isSymbolicLink() || (stat.mode & 0o777) !== 0o700 || stat.uid !== process.getuid()) {
      throw new Error('private native context directory ownership or mode differs');
    }
    if (!Buffer.isBuffer(bytes) || bytes.length > MAX_NATIVE_BODY) throw new Error('exact native context bytes required');
    const path = join(dir, `${++this.privateSequence}-${label}.json`);
    const fd = openSync(path, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW, 0o600);
    try { writeFileSync(fd, bytes); fsyncSync(fd); } finally { closeSync(fd); }
    const directory = openSync(dir, constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW);
    try { fsyncSync(directory); } finally { closeSync(directory); }
    return { path, sha256: createHash('sha256').update(bytes).digest('hex'), bytes: bytes.length };
  }
  captureContext(row, bytes, binding) {
    if (!this.privateModelContextDir) {
      if (binding?.calendarBinding) throw new Error('private calendar evidence required');
      return;
    }
    if (binding?.calendarBinding) {
      row.native_original_model_context = this.retainPrivate(binding.nativeOriginalBodyBytes, `attempt-${this.calls.length}-original`);
      row.native_calendar_prebinding_context = this.retainPrivate(binding.nativeCalendarPrebindingBodyBytes, `attempt-${this.calls.length}-calendar-before`);
      row.calendar_binding = binding.calendarBinding;
    }
    row.native_model_context = this.retainPrivate(bytes, `attempt-${this.calls.length}-${row.transport}`);
    this.save(this.calls.map(({ cleanup, ...row }) => row));
  }
  /** One cap for existing native PreToolUse and unique dynamic callback executions. */
  reserveTool(id, name, threadId) {
    this.check();
    if (!this.observeTools) return null;
    if (typeof id !== 'string' || !id || typeof name !== 'string' || !name) throw new Error('exact native tool identity required');
    let row = this.tools.get(id);
    if (row) {
      if (row.name_sha256 !== digest(name) || (threadId && row.thread_id && row.thread_id !== threadId)) throw new Error('native tool identity changed');
      return row;
    }
    if ([...this.tools.values()].filter(row => row.status !== 'cancelled').length >= 16) {
      this.ledger.stop('original tool cap'); throw new Error('original tool cap');
    }
    row = { tool_id_sha256: digest(id), name_sha256: digest(name), thread_id: threadId ?? null,
      status: 'reserved', dispatched: false, reserved_monotonic: this.now() };
    this.tools.set(id, row); return row;
  }
  nativeToolBefore(invocation) {
    const payload = invocation.rawPayload;
    const threadId = payload?.child_thread_id ?? payload?.session_id;
    return this.reserveTool(invocation.toolUseId, invocation.toolName, threadId);
  }
  nativeToolAfter(invocation) {
    if (!this.observeTools) return;
    const row = this.tools.get(invocation.toolUseId);
    if (!row) { this.toolCoverage = false; this.ledger.stop('native tool was not admitted'); throw new Error('native tool was not admitted'); }
    row.dispatched = true; row.status = 'settled'; row.settled_monotonic = this.now();
  }
  cancelNativeTool(invocation) {
    const row = this.tools.get(invocation.toolUseId);
    if (row?.status === 'reserved' && !row.dispatched) row.status = 'cancelled';
  }
  async dynamicTool(call, body) {
    const row = this.reserveTool(call.callId, call.tool, call.threadId);
    if (row) {
      if (row.dispatched) throw new Error('native dynamic tool executed twice');
      row.status = 'running'; row.dispatched = true;
    }
    try { return await body(); }
    finally { if (row) { row.status = 'settled'; row.settled_monotonic = this.now(); } }
  }
  nativeNotification(client, notification) {
    if (!this.observeTools) return;
    const p = notification.params; const threadId = p?.threadId ?? p?.thread?.id;
    if (typeof threadId === 'string') this.clients.get(client)?.add(threadId);
    if (notification.method !== 'item/completed' || !p?.item) return;
    // All installed non-tool item variants; a new variant cannot silently escape coverage.
    if (['userMessage','agentMessage','plan','reasoning','contextCompaction','enteredReviewMode','exitedReviewMode'].includes(p.item.type)) return;
    const row = this.tools.get(p.item.id);
    if (!row || ['reserved','cancelled'].includes(row.status)) {
      this.toolCoverage = false;
      this.nativeCoverageFailure ??= { item_type: typeof p.item.type === 'string' && /^[A-Za-z][A-Za-z0-9]{0,63}$/.test(p.item.type) ? p.item.type : 'UNKNOWN',
        item_id_sha256: digest(p.item.id ?? null), thread_id_sha256: digest(threadId ?? null), observed_status: row?.status ?? 'unreserved' };
      this.ledger.stop('native tool completion without dispatch observation');
    }
  }
  async readNativeClient(client) {
    if (client.serverRequests.active.size || client.pending.size) throw new Error('native request remains pending');
    const reads = [];
    for (const threadId of this.clients.get(client) ?? []) {
      const options = { timeoutMs: Math.max(1, Math.min(5000, (this.deadline - this.now()) * 1000)) };
      const { thread } = await client.request('thread/read', {threadId,includeTurns:true}, options);
      const { data } = await client.request('thread/backgroundTerminals/list', {threadId}, options);
      if (thread.id !== threadId || !['idle','notLoaded'].includes(thread.status?.type)
          || !Array.isArray(thread.turns) || thread.turns.some(turn => turn.status === 'inProgress')
          || !Array.isArray(data) || data.length) throw new Error('native turn or background terminal remains active');
      reads.push({ thread_id: threadId, turn_count: thread.turns.length, active_turns: 0, background_terminals: 0,
        private_native_thread: this.retainPrivate(Buffer.from(JSON.stringify({thread})), 'native-thread-readback') });
    }
    return reads;
  }
  async beforeNativeClose(client) {
    if (!this.observeTools || !this.clients.has(client) || client.closed) return;
    if (!await this.settled()) throw new Error('native work remains unsettled at close');
    this.nativeCloseReads.set(client, await this.readNativeClient(client));
  }
  async nativeSettlement() {
    this.check();
    if (!this.observeTools || !this.clients.size || !await this.settled()) throw new Error('native actor lifecycle not settled');
    await this.flushNativeQueues();
    const reads = [];
    for (const client of this.clients.keys()) {
      if (client.closed) {
        const retained = this.nativeCloseReads.get(client);
        if (!retained || this.nativeClosed.get(client)?.cleanup !== 'closed') throw new Error('native close/readback was not confirmed');
        reads.push(...retained);
      } else reads.push(...await this.readNativeClient(client));
    }
    if (!reads.length) throw new Error('native original turn readback unavailable');
    this.nativeReadbacks.push(reads); return reads;
  }
  async flushNativeQueues() {
    if (this.clients.size && !this.nativeQueues.size) throw new Error('actual native unregister queue unavailable');
    const results = [];
    for (const { packageRoot, queue } of this.nativeQueues.values()) {
      await queue.flush(); results.push({ package_root: packageRoot, flushed: true });
    }
    return results;
  }
  async closeNative() {
    const results = [];
    this.stop();
    const errors = [];
    for (const client of this.clients.keys()) {
      try { results.push(client.closed ? this.nativeClosed.get(client) : await client.closeAndWait()); }
      catch (error) { results.push(this.nativeClosed.get(client) ?? null); errors.push(nativeFailure(error)); }
    }
    let queues = [];
    try { queues = await this.flushNativeQueues(); } catch(error) { errors.push(nativeFailure(error)); }
    return { clients: results.length, transport_results: results, cancel_errors: errors, native_closed: results.length > 0 && errors.length === 0 && results.every(result => result?.cleanup === 'closed'), pending_sdk: this.calls.filter(r=>['pending','settling'].includes(r.status)).length,
      pending_tools: [...this.tools.values()].filter(r=>['reserved','running'].includes(r.status)).length,
      native_unregister_queues: queues, native_module_bindings: [...this.nativeModuleBindings.values()],
      tool_coverage: this.toolCoverage, native_dispatched_tools: [...this.tools.values()].filter(r=>r.dispatched).length };
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
      this.captureContext(row, binding.nativeBodyBytes, binding);
      this.save(this.calls.map(({ cleanup, ...row }) => row));
      this.check(); // Retention/save are synchronous: recheck immediately before physical dispatch.
      row.dispatched = true; row.dispatched_monotonic = this.now();
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
      this.captureContext(row, prepared.bytes, prepared.accounting);
      this.save(this.calls.map(({ cleanup, ...row }) => row));
      this.check(); // Retention/save are synchronous: recheck immediately before physical dispatch.
      row.dispatched = true; row.dispatched_monotonic = this.now();
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

  async settled() { return this.toolCoverage && this.calls.every(row => !['pending','settling'].includes(row.status)) && this.active.size === 0
    && [...this.tools.values()].every(row => !['reserved','running'].includes(row.status)); }
}

function replaceOnce(source, before, after) {
  if (source.split(before).length !== 2) throw new Error('native instrumentation anchor differs');
  return source.replace(before, after);
}
/** Exact evaluator overlay on installed native code; installed bytes remain untouched. */
export function installOpenClawAccounting(accounting, pluginRoot, pins, { hostRoot = null } = {}) {
  if (accounting.observeTools && !hostRoot) throw new Error('installed ordinary host required for tool observation');
  if (globalThis[KEY]) throw new Error('one native accounting owner required');
  globalThis[KEY] = accounting;
  const packageRoot = realpathSync(pluginRoot);
  const root = join(packageRoot, 'dist/.setup') + '/';
  const captureRoot = process.env.OPENCLAW_STATE_DIR ? join(resolve(process.env.OPENCLAW_STATE_DIR), 'tmp/plugin-captures') : null;
  const verifiedPackages = new Set();
  const verifyPackage = candidate => {
    if (verifiedPackages.has(candidate)) return;
    if (createHash('sha256').update(readFileSync(join(candidate, 'package.json'))).digest('hex') !== NATIVE_MANIFEST_PIN) throw new Error('official native package manifest differs');
    verifiedPackages.add(candidate);
  };
  verifyPackage(packageRoot);
  const loaded = new Set();
  const hooks = registerHooks({ load(url, context, nextLoad) {
    const result = nextLoad(url, context);
    if (!url.startsWith('file:')) return result;
    const path = fileURLToPath(url);
    let name = path.startsWith(root) ? path.slice(root.length) : null;
    let actualPackage = name ? packageRoot : null;
    if (!name && captureRoot && path.startsWith(captureRoot + '/')) {
      const suffix = '/node_modules/@openclaw/codex/dist/.setup/';
      const marker = path.lastIndexOf(suffix);
      if (marker >= captureRoot.length) {
        name = path.slice(marker + suffix.length);
        actualPackage = path.slice(0, marker) + '/node_modules/@openclaw/codex';
        if (realpathSync(captureRoot) !== captureRoot || realpathSync(actualPackage) !== actualPackage || !realpathSync(path).startsWith(captureRoot + '/')) throw new Error('native capture escapes fresh state');
      }
    }
    const scenarioModule = accounting.clockFile && name === 'thread-lifecycle-B3mfz1TG.mjs';
    const dynamicModule = accounting.observeTools && name === 'run-attempt-BHu11r7h.mjs';
    const nativeHookModule = accounting.observeTools && path === join(hostRoot,'dist/native-hook-relay-Bfyv8cuh.mjs');
    const queueModule = accounting.observeTools && name === 'shared-client-DA4VR4Eb.mjs';
    if (!scenarioModule && !dynamicModule && !nativeHookModule && !queueModule && !['client-Cs08OXVQ.mjs','inference-proxy-b-aJMSxm.mjs'].includes(name)) return result;
    if (actualPackage) verifyPackage(actualPackage);
    const original = Buffer.from(result.source).toString('utf8');
    const sourceHash = createHash('sha256').update(original).digest('hex');
    if (sourceHash !== (queueModule ? NATIVE_QUEUE_PIN : scenarioModule ? NATIVE_SCENARIO_PIN : nativeHookModule ? NATIVE_LOOP_PINS['native-hook-relay-Bfyv8cuh.mjs'] : dynamicModule ? NATIVE_LOOP_PINS[name] : pins[name])) throw new Error('installed native source pin differs');
    loaded.add(nativeHookModule ? 'native-hook-relay-Bfyv8cuh.mjs' : name);
    accounting.nativeModuleBindings.set(url, { module_url: url, package_root: actualPackage, captured: actualPackage !== null && actualPackage !== packageRoot, source_sha256: sourceHash });
    let source = `const __evaAccounting = globalThis[Symbol.for('eva.cp001.openclaw.accounting')];\n` + original;
    if (queueModule) {
      source += `\n__evaAccounting.nativeQueues.set(import.meta.url, {packageRoot:${JSON.stringify(actualPackage)},queue:nativeHookRelayUnregisterQueue});\n`;
    } else if (scenarioModule) {
      source = replaceOnce(source, 'let additionalContext = buildCodexTemporalAdditionalContext(params, { sessionStatusAvailable: options.sessionStatusAvailable === true });',
        'let additionalContext = __evaAccounting.temporalContext(() => buildCodexTemporalAdditionalContext(params, { sessionStatusAvailable: options.sessionStatusAvailable === true }));');
    } else if (dynamicModule) {
      // The same ordinary registration and trusted native config share this event list.
      // Observation adds no tool authority and leaves disabled/restricted native policy intact.
      source = replaceOnce(source,
        'const relayEvents = requiresProcessAdmission && !nativeHookRelayEvents.includes("pre_tool_use") ? [...nativeHookRelayEvents, "pre_tool_use"] : nativeHookRelayEvents;',
        'const relayEvents = [...new Set([...(requiresProcessAdmission && !nativeHookRelayEvents.includes("pre_tool_use") ? [...nativeHookRelayEvents, "pre_tool_use"] : nativeHookRelayEvents), "pre_tool_use", "post_tool_use"])];');
      source = replaceOnce(source, 'const response = await handleDynamicToolCallWithTimeout({',
        'const response = await __evaAccounting.dynamicTool(call, () => handleDynamicToolCallWithTimeout({');
      source = replaceOnce(source, '\t\t\t\t\t});\n\t\t\t\t\trecordCodexDynamicToolResult(projector, call, response, toCodexDynamicToolProtocolResponse(response));',
        '\t\t\t\t\t}));\n\t\t\t\t\trecordCodexDynamicToolResult(projector, call, response, toCodexDynamicToolProtocolResponse(response));');
    } else if (nativeHookModule) {
      source = "const __evaOwnedToolAccounting = Symbol('evaluator-owned native tool accounting');\n" + source;
      // Mark only the existing owned relay. Its ordinary synchronous hook builder then
      // enables all-tool observation while native policy, lifetime and admission remain.
      source = replaceOnce(source, 'function registerOwnedNativeHookRelay(params) {',
        'function registerOwnedNativeHookRelay(params) { params = {...params, [__evaOwnedToolAccounting]: true};');
      source = replaceOnce(source, '\t\tpartialRegistration = registration;',
        '\t\tregistration[__evaOwnedToolAccounting] = params[__evaOwnedToolAccounting] === true;\n\t\tpartialRegistration = registration;');
      source = replaceOnce(source, 'function nativeHookRelayEventHasLocalWork(registration, event) {',
        'function nativeHookRelayEventHasLocalWork(registration, event) { if (registration[__evaOwnedToolAccounting] && ["pre_tool_use","post_tool_use"].includes(event)) return true;');
      source = replaceOnce(source, 'function nativeHookRelayEventToolMatcher(registration, event) {',
        'function nativeHookRelayEventToolMatcher(registration, event) { if (registration[__evaOwnedToolAccounting] && ["pre_tool_use","post_tool_use"].includes(event)) return undefined;');
      source = replaceOnce(source, 'async function runNativeHookRelayPreToolUse(params) {',
        'async function runNativeHookRelayPreToolUse(params) { try { return await __evaNativePreToolUse(params); } catch(error) { __evaAccounting.cancelNativeTool(params.invocation); throw error; } }\nasync function __evaNativePreToolUse(params) {');
      source = replaceOnce(source, '\ttry {\n\t\tif (params.executionAdmission?.toolNames.includes(toolName)) {',
        '\t__evaAccounting.nativeToolBefore({ ...params.invocation, toolName });\n\ttry {\n\t\tif (params.executionAdmission?.toolNames.includes(toolName)) {');
      source = replaceOnce(source, 'async function runNativeHookRelayPostToolUse(params) {',
        'async function runNativeHookRelayPostToolUse(params) {\n\t__evaAccounting.nativeToolAfter(params.invocation);');
    } else if (name.startsWith('client')) {
      source = replaceOnce(source, 'request(method, params, optionsInput) {', "request(method, params, optionsInput) {\n\t\tparams = __evaAccounting.turnParams(method, params, this);");
      if (accounting.observeTools) {
        source = replaceOnce(source, 'handleNotification(notification) {', 'handleNotification(notification) {\n\t\t__evaAccounting.nativeNotification(this, notification);');
        source = replaceOnce(source, 'async closeAndWait(options) {', 'async closeAndWait(options) {\n\t\tlet readbackError; try { await __evaAccounting.beforeNativeClose(this); } catch(error) { readbackError = error; }\n\t\tconst result = await this.__evaCloseAndWait(options); __evaAccounting.nativeClosed.set(this, result); if (readbackError) throw readbackError; return result;\n\t}\n\tasync __evaCloseAndWait(options) {');
      }
    } else {
      source = replaceOnce(source, 'const rewritten = Buffer.from(JSON.stringify(prepared.body));', 'const normalized = __evaAccounting.prepareBody(prepared.body, bytes);\n\t\tconst rewritten = Buffer.from(JSON.stringify(normalized.body));\n\t\tnormalized.accounting.nativeBodyBytes = rewritten;');
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
