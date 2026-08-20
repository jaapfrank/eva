const HASH = /^[a-f0-9]{64}$/u;
const ID = /^[a-z0-9][a-z0-9._:-]{0,159}$/u;
const DESCRIPTOR_KEYS = [
  'contract', 'participantId', 'version', 'sourceDigest', 'configDigest',
  'adapterDigest', 'lanes', 'tracks',
];
const EVENT_KEYS = ['sequence', 'kind', 'callId', 'tool', 'argsSha256'];
const EVENT_KINDS = new Set(['tool_call', 'tool_result', 'case_complete']);

function canonicalData(value) {
  if (Array.isArray(value)) return value.map(canonicalData);
  if (value && typeof value === 'object') return Object.fromEntries(
    Object.keys(value).sort().map((key) => [key, canonicalData(value[key])]),
  );
  return value;
}

const canonical = (value) => JSON.stringify(canonicalData(value));

function exactKeys(value, keys) {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value)
    && canonical(Object.keys(value).sort()) === canonical([...keys].sort()));
}

function validateDescriptor(descriptor) {
  if (!exactKeys(descriptor, DESCRIPTOR_KEYS)
    || descriptor.contract !== 'LabAdapterDescriptor/v1'
    || !ID.test(String(descriptor.participantId || ''))
    || !/^\d+\.\d+\.\d+$/u.test(String(descriptor.version || ''))
    || ![descriptor.sourceDigest, descriptor.configDigest, descriptor.adapterDigest]
      .every((value) => HASH.test(String(value || '')))
    || !Array.isArray(descriptor.lanes) || !descriptor.lanes.length
    || !descriptor.lanes.every((lane) => ['controlled', 'native'].includes(lane))
    || !Array.isArray(descriptor.tracks) || !descriptor.tracks.length
    || !descriptor.tracks.every((track) => (
      ['sim', 'core_live', 'native_live', 'resident_long'].includes(track)
    ))) throw new TypeError('LAB-adapterdescriptor is ongeldig');
  return Object.freeze({
    ...descriptor,
    lanes: Object.freeze([...descriptor.lanes]),
    tracks: Object.freeze([...descriptor.tracks]),
  });
}

function adapterMethodsValid(adapter) {
  return ['describe', 'prepare', 'beginCase', 'send', 'observe', 'endCase', 'close']
    .every((method) => typeof adapter?.[method] === 'function');
}

function validateEvent(event, expectedSequence) {
  if (!exactKeys(event, EVENT_KEYS) || event.sequence !== expectedSequence
    || !EVENT_KINDS.has(event.kind)) throw new TypeError('LAB-toolreeks bevat een ongeldig event');
  if (event.kind === 'case_complete') {
    if (event.callId != null || event.tool != null || event.argsSha256 != null) {
      throw new TypeError('LAB-toolreeks bevat een ongeldig eindevent');
    }
    return Object.freeze({ ...event });
  }
  if (!ID.test(String(event.callId || '')) || !ID.test(String(event.tool || ''))
    || !HASH.test(String(event.argsSha256 || ''))) {
    throw new TypeError('LAB-toolreeks bevat ongeldige toolbinding');
  }
  return Object.freeze({ ...event });
}

function exactExpectedSequence(events, expected) {
  const calls = events.filter((event) => event.kind === 'tool_call');
  const results = events.filter((event) => event.kind === 'tool_result');
  if (!Array.isArray(expected) || calls.length !== expected.length
    || results.length !== expected.length || events.at(-1)?.kind !== 'case_complete') return false;
  const resultByCall = new Map(results.map((result) => [result.callId, result]));
  if (resultByCall.size !== results.length) return false;
  return calls.every((call, index) => {
    const specification = expected[index];
    const result = resultByCall.get(call.callId);
    return exactKeys(specification, ['tool', 'argsSha256'])
      && call.tool === specification.tool && call.argsSha256 === specification.argsSha256
      && result?.tool === call.tool && result?.argsSha256 === call.argsSha256
      && result.sequence > call.sequence;
  });
}

export async function openLabAdapterSession(adapter, context) {
  if (!adapterMethodsValid(adapter) || !context || typeof context !== 'object') {
    throw new TypeError('LAB-adapter mist de verplichte interface');
  }
  const descriptor = validateDescriptor(await adapter.describe());
  if (descriptor.participantId !== context.participantId) {
    throw new TypeError('LAB-adapter hoort bij een andere participant');
  }
  if (!descriptor.lanes.includes(context.lane)) throw new TypeError('LAB-adapter ondersteunt lane niet');
  if (!descriptor.tracks.includes(context.track)) throw new TypeError('LAB-adapter ondersteunt track niet');
  if (!ID.test(String(context.runId || ''))
    || typeof context.controlledConfigRef !== 'string' || !context.controlledConfigRef.trim()
    || !context.effectCapability || typeof context.effectCapability !== 'object') {
    throw new TypeError('LAB-adapterpreparecontext is ongeldig');
  }
  const prepared = await adapter.prepare(Object.freeze({ ...context, descriptor }));
  if (!prepared || !ID.test(String(prepared.sessionId || ''))) {
    throw new TypeError('LAB-adapter gaf geen geldige sessie');
  }
  let closed = false;
  return Object.freeze({
    descriptor,
    sessionId: prepared.sessionId,
    adapter,
    close: async () => {
      if (closed) return Object.freeze({ accepted: true, alreadyClosed: true });
      const receipt = await adapter.close({ sessionId: prepared.sessionId });
      if (receipt?.accepted !== true) throw new Error('LAB-adaptersessie kon niet sluiten');
      closed = true;
      return Object.freeze({ accepted: true, alreadyClosed: false });
    },
    isClosed: () => closed,
  });
}

export async function runLabToolSequence(session, { caseInstanceId, input, expected }) {
  if (session?.isClosed?.() !== false || !/^lab:[a-f0-9]{64}$/u.test(String(caseInstanceId || ''))
    || !exactKeys(input, ['kind', 'contentSha256']) || input.kind !== 'text'
    || !HASH.test(String(input.contentSha256 || ''))) {
    throw new TypeError('LAB-toolreeks heeft geen geldige sessie of input');
  }
  const caseRef = Object.freeze({ sessionId: session.sessionId, caseInstanceId });
  const begun = await session.adapter.beginCase(caseRef);
  if (begun?.accepted !== true) throw new Error('LAB-toolreeks kon niet starten');
  let result;
  let failure = null;
  try {
    const ingress = await session.adapter.send(Object.freeze({ ...caseRef, input }));
    if (ingress?.accepted !== true || !ID.test(String(ingress.ingressId || ''))) {
      throw new Error('LAB-toolreeks kreeg geen ingressreceipt');
    }
    const rawEvents = await session.adapter.observe(Object.freeze({
      ...caseRef, cursor: 0, ingressId: ingress.ingressId,
    }));
    if (!Array.isArray(rawEvents) || !rawEvents.length) {
      throw new Error('LAB-toolreeks bevat geen events');
    }
    const events = rawEvents.map((event, index) => validateEvent(event, index + 1));
    if (!exactExpectedSequence(events, expected)) {
      throw new Error('LAB-toolreeks wijkt af van de verwachte tools of argumenten');
    }
    result = Object.freeze({
      contract: 'LabToolSequenceResult/v1',
      caseInstanceId,
      passed: true,
      events: Object.freeze(events),
      metrics: Object.freeze({
        toolCalls: expected.length, correctToolCalls: expected.length,
      }),
    });
  } catch (error) { failure = error; }
  const ended = await session.adapter.endCase(caseRef);
  if (failure) throw failure;
  if (ended?.accepted !== true) throw new Error('LAB-toolreeks kon niet afsluiten');
  return result;
}
