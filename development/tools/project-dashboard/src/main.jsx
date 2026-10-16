import React from 'react';
import { createRoot } from 'react-dom/client';
import {
  Admin,
  Resource,
  memoryStore,
  useGetOne,
} from 'react-admin';
import './style.css';
import OwnerInitiatives from './owner-initiatives.jsx';
import OwnerBrowser from './owner-browser.jsx';
import OwnerWork from './owner-work.jsx';
import OwnerHealth from './owner-health.jsx';
import OwnerModelProviders from './owner-model-providers.jsx';
import OwnerWhatsApp from './owner-whatsapp.jsx';
import OwnerMemoryView from './owner-memory-view.jsx';
import { ownerSections, ownerSection, ownerSectionHref } from './owner-navigation.mjs';
import ProjectDashboard, { ProjectLayout, projectProvider } from './project-dashboard.jsx';

const STATUS = '/internal/v1/dashboard/status';
const IDENTITY = '/internal/v1/dashboard/identity';
const WORKBOARD = '/internal/v1/dashboard/workboard';
const MEMORY_SOURCES = '/internal/v1/dashboard/memory-sources';
const MEMORY_VIEW = '/internal/v1/dashboard/memory-view';
const MEMORY_PAGE_SIZE = 20;

async function readJson(url, { signal } = {}) {
  const response = await fetch(url, {
    credentials: 'same-origin',
    headers: { Accept: 'application/json' },
    cache: 'no-store', redirect: 'error', signal,
  });
  if (!response.ok) {
    const error = new Error('Eva-dashboard niet beschikbaar');
    error.status = response.status;
    throw error;
  }
  return response.json();
}

function exactObject(value, keys, contract) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`${contract} is ongeldig`);
  }
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
    throw new Error(`${contract} heeft onbekende velden`);
  }
  return value;
}

function statusPayload(value) {
  const payload = exactObject(value, ['contract', 'read_only', 'runtime'], 'status');
  const runtime = exactObject(payload.runtime, ['ready', 'state'], 'runtime');
  if (payload.contract !== 'EvaDashboardStatus/v1' || payload.read_only !== true
    || typeof runtime.ready !== 'boolean'
    || runtime.state !== (runtime.ready ? 'ready' : 'not_ready')) {
    throw new Error('statuscontract wijkt af');
  }
  return payload;
}

function identityPayload(value) {
  const payload = exactObject(value, ['contract', 'read_only', 'identity'], 'identiteit');
  const identity = exactObject(payload.identity, [
    'display_name', 'identity_id', 'language', 'pack_sha256', 'pack_version',
    'state', 'temperament', 'voice_style',
  ], 'identity pack');
  exactObject(identity.voice_style, ['maximum_paragraphs', 'name', 'qualities'], 'stemstijl');
  if (payload.contract !== 'EvaDashboardIdentity/v1' || payload.read_only !== true
    || identity.state !== 'current' || !identity.display_name
    || !Array.isArray(identity.temperament) || !Array.isArray(identity.voice_style.qualities)) {
    throw new Error('identiteitscontract wijkt af');
  }
  return payload;
}

function workboardPayload(value) {
  const payload = exactObject(value, [
    'cards', 'contract', 'message', 'read_only', 'schema_version', 'status', 'truncated',
  ], 'werkbord');
  if (payload.contract !== 'EvaWorkBoard/v1' || payload.schema_version !== 1
    || payload.read_only !== true || !['ready', 'unavailable'].includes(payload.status)
    || !Array.isArray(payload.cards) || payload.cards.length > 64
    || typeof payload.truncated !== 'boolean' || typeof payload.message !== 'string'
    || payload.message.length > 240
    || (payload.status === 'unavailable' && (payload.cards.length || payload.truncated))) {
    throw new Error('werkbordcontract wijkt af');
  }
  payload.cards.forEach((card) => {
    exactObject(card, [
      'attempt', 'changed', 'column', 'contract', 'deadline', 'duration_seconds',
      'maximum_attempts', 'overdue', 'reason', 'result_status', 'schema_version',
      'summary', 'title', 'usage', 'worker_health', 'worker_name', 'worker_role',
    ], 'werkbordkaart');
    const usage = exactObject(card.usage, [
      'contract', 'cost_usd_micros', 'input_tokens', 'measurement', 'output_tokens',
      'schema_version', 'total_tokens',
    ], 'werkbordgebruik');
    const bounded = (text, maximum) => typeof text === 'string' && text.length > 0 && text.length <= maximum;
    const integer = (number, minimum, maximum) => Number.isInteger(number) && number >= minimum && number <= maximum;
    const nullableInteger = (number, maximum) => number === null || integer(number, 0, maximum);
    const tokenValues = [usage.input_tokens, usage.output_tokens, usage.total_tokens];
    const allTokens = tokenValues.every((number) => number !== null);
    const noTokens = tokenValues.every((number) => number === null);
    const resultColumns = {
      not_received: ['todo', 'doing'], awaiting_review: ['review'], approved: ['done'],
      failed: ['attention'], ambiguous: ['attention'], stopped: ['done'],
    };
    if (card.contract !== 'EvaWorkBoardCard/v1' || card.schema_version !== 1
      || !bounded(card.title, 120) || !bounded(card.summary, 240)
      || !bounded(card.worker_name, 32) || !bounded(card.worker_role, 80)
      || !bounded(card.reason, 240) || !integer(card.attempt, 0, 3)
      || !integer(card.maximum_attempts, 1, 3) || card.attempt > card.maximum_attempts
      || !integer(card.duration_seconds, 0, 31536000) || typeof card.overdue !== 'boolean'
      || typeof card.deadline !== 'string' || Number.isNaN(Date.parse(card.deadline))
      || !['waiting', 'active', 'contact_lost', 'finished', 'stopped'].includes(card.worker_health)
      || !['just_now', 'minutes_ago', 'hours_ago', 'days_ago', 'unknown'].includes(card.changed)
      || !resultColumns[card.result_status]?.includes(card.column)
      || usage.contract !== 'EvaWorkBoardUsage/v1' || usage.schema_version !== 1
      || !['measured', 'partial', 'unmeasured'].includes(usage.measurement)
      || !nullableInteger(usage.input_tokens, 3200000)
      || !nullableInteger(usage.output_tokens, 1000000)
      || !nullableInteger(usage.total_tokens, 3300000)
      || !nullableInteger(usage.cost_usd_micros, 1000000000)
      || (usage.measurement === 'unmeasured' && (!noTokens || usage.cost_usd_micros !== null))
      || (usage.measurement === 'measured' && (!allTokens || usage.cost_usd_micros === null))
      || (usage.measurement === 'partial' && noTokens && usage.cost_usd_micros === null)
      || (!allTokens && !noTokens)
      || (allTokens && usage.total_tokens !== usage.input_tokens + usage.output_tokens)) {
      throw new Error('werkbordkaart wijkt af');
    }
  });
  return payload;
}

const dataProvider = {
  async getOne(resource) {
    if (resource === 'status') {
      const payload = statusPayload(await readJson(STATUS));
      return { data: { id: 'runtime', ...payload } };
    }
    if (resource === 'identity') {
      const payload = identityPayload(await readJson(IDENTITY));
      return { data: { id: 'current', ...payload } };
    }
    throw new Error('Onbekende Eva-resource');
  },
  async getList(resource) {
    if (resource !== 'workboard') throw new Error('Onbekende Eva-resource');
    const payload = workboardPayload(await readJson(WORKBOARD));
    const data = payload.cards.map((card, index) => ({
      id: `${card.column}:${index}:${card.title}`,
      ...card,
    }));
    return { data, total: data.length };
  },
  create: () => Promise.reject(new Error('Alleen-lezen')),
  delete: () => Promise.reject(new Error('Alleen-lezen')),
  deleteMany: () => Promise.reject(new Error('Alleen-lezen')),
  getMany: () => Promise.reject(new Error('Niet beschikbaar')),
  getManyReference: () => Promise.reject(new Error('Niet beschikbaar')),
  update: () => Promise.reject(new Error('Alleen-lezen')),
  updateMany: () => Promise.reject(new Error('Alleen-lezen')),
};

const authProvider = {
  login: () => Promise.reject(new Error('Gebruik de lokale eigenaarstoegang')),
  logout: () => Promise.resolve(),
  checkAuth: () => readJson(STATUS).then(statusPayload).then(() => undefined),
  checkError: (error) => (
    error?.status === 401 ? Promise.reject() : Promise.resolve()
  ),
  getPermissions: () => Promise.resolve(['owner-read']),
  getIdentity: () => Promise.resolve({ id: 'owner', fullName: 'Eigenaar' }),
};

function memorySourcesPayload(value) {
  const payload = exactObject(value, ['contract', 'meaning', 'read_only', 'sources', 'next_cursor'], 'geheugenbronnen');
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
  const timestamp = value => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(value)
    && Number.isFinite(Date.parse(value)) && new Date(value).toISOString() === value.replace('Z', '.000Z');
  if (payload.contract !== 'EvaOwnerMemorySources/v1' || payload.read_only !== true
    || typeof payload.meaning !== 'string' || payload.meaning.length > 240
    || !Array.isArray(payload.sources) || payload.sources.length > MEMORY_PAGE_SIZE
    || (payload.next_cursor !== null && (typeof payload.next_cursor !== 'string'
      || payload.next_cursor.length !== 57 || !timestamp(payload.next_cursor.slice(0, 20))
      || payload.next_cursor[20] !== '|' || !uuid.test(payload.next_cursor.slice(21))))) {
    throw new Error('Geheugencontract wijkt af');
  }
  const ids = new Set();
  for (const source of payload.sources) {
    exactObject(source, ['source_id', 'occurred_at', 'excerpt', 'truncated', 'retention_state'], 'geheugenbron');
    if (typeof source.source_id !== 'string' || !uuid.test(source.source_id) || ids.has(source.source_id)
      || !timestamp(source.occurred_at) || typeof source.excerpt !== 'string'
      || !source.excerpt || [...source.excerpt].length > 2048 || typeof source.truncated !== 'boolean'
      || !['retained', 'pending', 'needs_help'].includes(source.retention_state)) throw new Error('Geheugenbron wijkt af');
    ids.add(source.source_id);
  }
  if (payload.next_cursor !== null && !payload.sources.length) throw new Error('Lege vervolgpagina');
  return payload;
}

function MemorySourceControl({ sourceId, generation, onClose, onChanged, onSelectSource }) {
  const [detail, setDetail] = React.useState(null);
  const [text, setText] = React.useState('');
  const [mode, setMode] = React.useState('view');
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState('');
  React.useEffect(() => {
    const controller = new AbortController();
    setDetail(null); setError('');
    readJson(`${MEMORY_VIEW}/sources/${sourceId}`, { signal: controller.signal }).then(value => {
      exactObject(value, ['contract', 'source_id', 'content', 'occurred_at', 'retention_state', 'origin', 'kind', 'trust', 'learning', 'current_learning', 'current_source_id', 'can_forget', 'erasure_scope'], 'geheugenbron');
      if (value.contract !== 'EvaOwnerMemorySourceView/v1' || value.source_id !== sourceId
        || typeof value.content !== 'string' || !value.content || value.content.length > 1048576
        || !['retained', 'pending', 'needs_help'].includes(value.retention_state)
        || typeof value.origin !== 'string' || value.origin.length > 80) throw new Error();
      if (!controller.signal.aborted) setDetail(value);
    }).catch(() => { if (!controller.signal.aborted) setError('Deze bron is niet beschikbaar. Sluit dit venster en vernieuw de lijst.'); });
    return () => controller.abort();
  }, [sourceId, generation]);
  const change = async action => {
    if (busy) return;
    setBusy(true); setError('');
    try {
      const response = await fetch(`${MEMORY_VIEW}/sources/${sourceId}/${action}`, {
        method: 'POST', credentials: 'same-origin', cache: 'no-store', redirect: 'error',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify(action === 'forget' ? { confirm: true } : { text }),
        signal: AbortSignal.timeout(60000),
      });
      if (!response.ok) throw new Error();
      const value = await response.json();
      exactObject(value, ['contract', 'source_id', 'state'], 'geheugenwijziging');
      if (value.contract !== 'EvaOwnerMemoryChange/v1' || value.source_id !== sourceId
        || !(action === 'forget' ? ['erased', 'erasure_requested'] : ['correction_queued']).includes(value.state)) throw new Error();
      onChanged({ erased: detail?.erasure_scope === 'memory_document' ? 'Deze geheugenbron of afleiding is vergeten. Het oorspronkelijke gesprek blijft bewaard.' : 'Deze bron en het daarvan afgeleide geheugen zijn gewist.',
        erasure_requested: 'Wissen is aangevraagd. De bron verdwijnt uit deze lijst zodra de verwerking klaar is.',
        correction_queued: 'Je correctie is ontvangen. De nieuwe bron staat in de lijst; Verwerkt verschijnt zodra Eva haar geheugen heeft bijgewerkt.' }[value.state]);
    } catch {
      setError('De wijziging kon niet worden bevestigd. Sluit dit venster en vernieuw eerst de lijst voordat je opnieuw probeert.');
    } finally { setBusy(false); }
  };
  return <section className="memory-source-control" aria-label="Geheugenbron bekijken en aanpassen">
    <div className="memory-toolbar"><h3>Geselecteerde bron</h3><button disabled={busy} onClick={onClose}>Sluiten</button></div>
    {error && <p role="alert">{error}</p>}
    {!detail && !error && <p role="status">Volledige bron ophalen…</p>}
    {detail && <>
      <p>{detail.origin} · {new Date(detail.occurred_at).toLocaleString('nl-NL')}</p>
      <p className={`memory-kind ${detail.kind}`}>{detail.kind === 'inference' ? 'Afleiding van Eva · geen vastgesteld feit' : detail.kind === 'legacy' ? 'Oude bron · niet bevestigd' : 'Broninformatie · wat is gezegd of waargenomen'}</p>
      <div className="memory-full-content">{detail.content}</div>
      {detail.learning && <div className="memory-learning"><p>Soort: {detail.learning.kind === 'expectation' ? 'Verwachting' : 'Persoonsaanname'} · beoordeling: {({open:'Open',confirmed:'Bevestigd',refuted:'Weerlegd',unknown:'Onbekend'})[detail.learning.outcome]}</p>
        <p>Onzekerheid: {detail.learning.uncertainty_reason}</p>
        {detail.current_learning && <><h4>Actuele herziening</h4><p>{detail.current_learning.interpretation}</p>
          <p>Een bevestigde beoordeling van een aanname maakt haar nog geen onafhankelijk vastgesteld feit.</p></>}
        {!detail.current_learning && <p>Een actuele herziening is nog niet beschikbaar; vernieuw na verwerking. Deze historische bron is niet automatisch actueel.</p>}
      </div>}
      {detail.current_source_id && detail.current_source_id !== sourceId && <button onClick={() => onSelectSource(detail.current_source_id)}>Open actuele herziening</button>}
      {mode === 'view' && <div className="memory-actions">
        <button disabled={detail.learning && detail.current_source_id !== sourceId} onClick={() => setMode('correct')}>Informatie corrigeren</button>
        <button disabled={!detail.can_forget} onClick={() => setMode('forget')}>Deze bron vergeten</button>
      </div>}
      {mode === 'correct' && <form onSubmit={event => { event.preventDefault(); change('correct'); }}>
        <label htmlFor="memory-correction">Wat moet Eva voortaan onthouden?</label>
        <textarea id="memory-correction" value={text} maxLength={detail.learning ? 1600 : 12000} rows={4} required disabled={busy}
          onChange={event => setText(event.target.value)} placeholder="Bijvoorbeeld: mijn favoriete kleur is nu groen." />
        <p>Schrijf de nieuwe informatie volledig op. Eva verwerkt dit als actuele correctie. De eerdere uitspraak blijft als historische bron zichtbaar; met Vergeten kun je die bron wissen.</p>
        <button type="submit" disabled={busy || !text.trim()}>Correctie doorgeven</button>
        <button type="button" disabled={busy} onClick={() => setMode('view')}>Annuleren</button>
      </form>}
      {mode === 'forget' && <div>
        <p>{detail.erasure_scope === 'memory_document' ? 'Deze geselecteerde geheugenbron of afleiding en het daarop steunende geheugen worden vergeten. Het oorspronkelijke gesprek blijft bewaard.' : 'Deze gespreksbron en het daarvan afgeleide geheugen worden gewist. Andere gesprekken waarin je hetzelfde vertelde blijven bestaan. Dit verwijdert geen berichten uit Telegram zelf.'}</p>
        <button disabled={busy} onClick={() => change('forget')}>Bevestigen: deze bron vergeten</button>
        <button disabled={busy} onClick={() => setMode('view')}>Annuleren</button>
      </div>}
      {detail.retention_state !== 'retained' && <p>Wissen is beschikbaar zodra deze bron verwerkt is.</p>}
      {busy && <p role="status">Je wijziging wordt verwerkt…</p>}
    </>}
  </section>;
}

function MemorySources({ selectedSource, onMemoryChanged }) {
  // Keep only the visible page in component memory, never in a persistent store
  // or the shared query cache. Changing page/access first clears old content.
  const [selected, setSelected] = React.useState(null);
  React.useEffect(() => { if (selectedSource) setSelected(selectedSource.id); }, [selectedSource]);
  const [generation, revalidateDetail] = React.useReducer(value => value + 1, 0);
  const [notice, setNotice] = React.useState('');
  const [cursors, setCursors] = React.useState([null]);
  const [revision, refresh] = React.useReducer(value => value + 1, 0);
  const [result, setResult] = React.useState({ state: 'loading', page: null });
  const cursor = cursors[cursors.length - 1];
  React.useEffect(() => {
    let controller;
    let disposed = false;
    const load = async () => {
      controller?.abort();
      revalidateDetail();
      setResult({ state: 'loading', page: null });
      if (document.hidden) { setSelected(null); return; }
      const request = new AbortController();
      controller = request;
      const timeout = setTimeout(() => request.abort(), 10000);
      try {
        const query = new URLSearchParams({ limit: String(MEMORY_PAGE_SIZE) });
        if (cursor !== null) query.set('before', cursor);
        const page = memorySourcesPayload(await readJson(`${MEMORY_SOURCES}?${query}`, { signal: request.signal }));
        if (page.next_cursor !== null && cursors.includes(page.next_cursor)) throw new Error('Herhaalde cursor');
        if (!disposed && controller === request && !request.signal.aborted) setResult({ state: 'ready', page });
      } catch (error) {
        if (!disposed && controller === request) setResult({ state: [401, 403].includes(error.status) ? 'denied' : 'error', page: null });
      } finally { clearTimeout(timeout); }
    };
    load();
    const interval = setInterval(load, 30000);
    window.addEventListener('focus', load);
    document.addEventListener('visibilitychange', load);
    return () => {
      disposed = true; controller?.abort(); clearInterval(interval);
      window.removeEventListener('focus', load);
      document.removeEventListener('visibilitychange', load);
    };
  }, [cursor, cursors, revision]);
  React.useEffect(() => { if (['denied', 'error'].includes(result.state)) setSelected(null); }, [result.state]);
  const changePage = next => { setSelected(null); setResult({ state: 'loading', page: null }); setCursors(next); };
  const page = result.page;
  const labels = { retained: 'Verwerkt', pending: 'Wacht op verwerking', needs_help: 'Hulp nodig' };
  return <section className="memory-sources" aria-labelledby="memory-heading">
    <div className="memory-toolbar"><div><h2 id="memory-heading">Jouw geheugenbronnen</h2>
      <p>Eigen gespreksbronnen voor het geheugen — niet alle afgeleide herinneringen. Alleen jij kunt deze bronnen hier bekijken.</p></div>
      <button onClick={() => { setResult({ state: 'loading', page: null }); refresh(); }}>Vernieuwen</button></div>
    {notice && <p role="status">{notice}</p>}
    {selected && <MemorySourceControl key={selected} sourceId={selected} generation={generation} onClose={() => setSelected(null)} onSelectSource={setSelected}
      onChanged={message => { setNotice(message); setSelected(null); refresh(); onMemoryChanged?.(); }} />}
    {result.state === 'loading' && <p role="status">Geheugenbronnen ophalen…</p>}
    {result.state === 'denied' && <p role="alert">Je eigenaarstoegang is niet meer geldig. De bronnen zijn verborgen.</p>}
    {result.state === 'error' && <p role="alert">Geheugenbronnen zijn nu niet beschikbaar. Probeer opnieuw; eerdere inhoud is verborgen.</p>}
    {page && <>
      {!page.sources.length && <p role="status">Nog geen zichtbare geheugenbronnen.</p>}
      <ol className="memory-list">{page.sources.map(source => <li key={source.source_id}>
        <div className="memory-meta"><time dateTime={source.occurred_at}>{new Date(source.occurred_at).toLocaleString('nl-NL')}</time>
          <span>{labels[source.retention_state]}</span></div>
        <p className="memory-excerpt">{source.excerpt}</p>
        {source.truncated && <small>Fragment — deze bron is langer dan hier getoond.</small>}
        <button onClick={() => { setNotice(''); setSelected(source.source_id); }}>Bekijken en aanpassen</button>
      </li>)}</ol>
      <nav className="memory-pagination" aria-label="Geheugenpagina's">
        <button disabled={cursors.length === 1} onClick={() => changePage(cursors.slice(0, -1))}>Vorige</button>
        <span>Pagina {cursors.length}</span>
        <button disabled={page.next_cursor === null} onClick={() => changePage([...cursors, page.next_cursor])}>Volgende</button>
      </nav>
    </>}
    <small>Eva onderhoudt haar geheugen zelf. Gebruik deze bediening als je iets wilt controleren, corrigeren of bewust vergeten.</small>
  </section>;
}

function PeopleOverview() {
  const [state, setState] = React.useState({ data: null, error: false });
  React.useEffect(() => {
    const controller = new AbortController();
    readJson('/internal/v1/dashboard/people-memory', { signal: controller.signal }).then(data => {
      const labels = {
        dynamic_account: { extra_account_linked: 'Extra account gekoppeld', no_extra_account: 'Geen extra account gekoppeld', unavailable: 'Niet beschikbaar' },
        sharing: { private_only: 'Alleen privé', group_active: 'Gedeelde groep actief', expires_soon: 'Groepsdeling verloopt binnenkort', unavailable: 'Niet beschikbaar' },
        memory: { no_signals: 'Nog geen bronnen', up_to_date: 'Bijgewerkt', processing: 'Wordt verwerkt', retrying: 'Verwerking wordt hervat', needs_help: 'Hulp nodig', unavailable: 'Niet beschikbaar' },
      };
      if (data.contract !== 'EvaPeopleMemoryOverview/v1' || data.read_only !== true
        || data.contains_raw_content !== false || !Object.hasOwn(labels.dynamic_account, data.people?.dynamic_account)
        || !Object.hasOwn(labels.sharing, data.sharing?.state) || !Object.hasOwn(labels.memory, data.memory?.state)) throw new Error();
      if (!controller.signal.aborted) setState({ data: {
        account: labels.dynamic_account[data.people.dynamic_account], sharing: labels.sharing[data.sharing.state], memory: labels.memory[data.memory.state],
      }, error: false });
    }).catch(() => { if (!controller.signal.aborted) setState({ data: null, error: true }); });
    return () => controller.abort();
  }, []);
  return <section className="owner-panel" aria-label="Personen en geheugentoegang">
    <h2>Personen en geheugentoegang</h2>
    {state.error && <p role="alert">Dit personenoverzicht is nu niet beschikbaar. Je geheugenbronnen worden afzonderlijk opgehaald.</p>}
    {!state.data && !state.error && <p role="status">Personenoverzicht ophalen…</p>}
    {state.data && <dl className="owner-facts"><dt>Accounts</dt><dd>{state.data.account}</dd><dt>Delen</dt><dd>{state.data.sharing}</dd><dt>Geheugenverwerking</dt><dd>{state.data.memory}</dd></dl>}
    <p>Dit bestaande overzicht toont toegangs- en verwerkingsstatus. Namen, persoonsprofielen en afgeleide herinneringen worden hier nog niet getoond.</p>
  </section>;
}

function Dashboard() {
  const status = useGetOne('status', { id: 'runtime' });
  const identity = useGetOne('identity', { id: 'current' });
  const [selected, setSelected] = React.useState(() => ownerSection(window.location.hash));
  const workIdFromHash = () => { const id = new URLSearchParams(window.location.hash.split('?')[1] || '').get('work_id'); return id && id.length <= 240 ? id : null; };
  const [selectedWork, setSelectedWork] = React.useState(workIdFromHash);
  const [selectedSource, setSelectedSource] = React.useState(null);
  const [memoryRevision, memoryChanged] = React.useReducer(n => n + 1, 0);
  React.useEffect(() => {
    const change = () => { setSelected(ownerSection(window.location.hash)); setSelectedWork(workIdFromHash()); };
    window.addEventListener('hashchange', change);
    return () => window.removeEventListener('hashchange', change);
  }, []);
  const name = identity.data?.identity.display_name || 'Eva';
  return <div className="owner-shell">
    <aside className="owner-sidebar">
      <a className="owner-brand" href={ownerSectionHref('overview')} aria-label="Eva overzicht"><span>e</span><strong>{name}<small>Jouw huisgenoot</small></strong></a>
      <nav aria-label="Eva beheer">{ownerSections.map(item => <a key={item.id} href={ownerSectionHref(item.id)}
        aria-current={selected === item.id ? 'page' : undefined}><span aria-hidden="true">{item.icon}</span>{item.label}</a>)}</nav>
      <p>Dit is Eva’s productbediening. Het aparte projectdashboard toont de bouwvoortgang.</p>
    </aside>
    <main className="owner-dashboard" id="owner-content">
      <header><div><p className="eyebrow">Dagelijks Eva-gebruik</p><h1>{ownerSections.find(item => item.id === selected).label}</h1></div>
        <span className={status.data?.runtime.ready ? 'ready' : 'attention'}>{status.error ? 'Status niet beschikbaar' : status.isPending ? 'Status ophalen…' : status.data?.runtime.ready ? 'Kern bereikbaar' : 'Kern vraagt aandacht'}</span></header>
      {identity.error && <p role="alert">Eva’s actuele identiteit kon niet worden opgehaald. De andere secties blijven afzonderlijk beschikbaar.</p>}
      {selected === 'overview' && <>
        <p className="owner-intro">Volg wat {name} doet en beheer wat ze van jou onthoudt.</p>
        <div className="owner-shortcuts">{ownerSections.filter(item => item.id !== 'overview').map(item => <a key={item.id} href={ownerSectionHref(item.id)}>
          <span className="owner-shortcut-icon" aria-hidden="true">{item.icon}</span><h2>{item.label}</h2><p>{item.description}</p><strong>Openen →</strong></a>)}</div>
      </>}
      {selected === 'conversation' && <section className="owner-panel" aria-label="Gesprek met Eva">
        <h2>Praat met {name}</h2><p>Open je bestaande privégesprek. Je kunt daar schrijven, antwoorden volgen en een opdracht stoppen. Geheugen en opdrachten gebruiken dezelfde kern.</p>
        <a className="owner-primary" href="/internal/v1/dashboard/conversation">Open gesprek →</a>
        <p>Met ‘Terug naar cockpit’ kom je weer bij dit beheer. Telegram blijft dezelfde Eva.</p>
      </section>}
      {selected === 'memory' && <><OwnerMemoryView revision={memoryRevision} onSource={id => setSelectedSource({ id, revision: Date.now() })} /><MemorySources selectedSource={selectedSource} onMemoryChanged={memoryChanged} /></>}
      {selected === 'work' && <OwnerWork key={selectedWork || 'all'} initialWorkId={selectedWork} />}
      {selected === 'initiative' && <OwnerInitiatives />}
      {selected === 'connections' && <><OwnerModelProviders /><OwnerWhatsApp /><OwnerHealth /><section className="owner-panel"><h2>Optionele verbindingen</h2>
        <p>Modelproviders en WhatsApp beheer je hierboven. Iedere verbinding blijft optioneel.</p>
      </section><OwnerBrowser /></>}
    </main>
  </div>;
}

function OwnerLayout({ children }) {
  // React-admin keeps authentication/data access; product navigation owns layout.
  return <>{children}</>;
}

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    {import.meta.env.MODE === 'project' ? <Admin
      dashboard={ProjectDashboard} layout={ProjectLayout} dataProvider={projectProvider}
      disableTelemetry store={memoryStore()} loginPage={false}
    ><Resource name="project" /></Admin> :
    <Admin
      layout={OwnerLayout}
      authProvider={authProvider}
      dashboard={Dashboard}
      dataProvider={dataProvider}
      disableTelemetry
      requireAuth
      store={memoryStore()}
    >
      <Resource name="workboard" />
    </Admin>}
  </React.StrictMode>,
);
