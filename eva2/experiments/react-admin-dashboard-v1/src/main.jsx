import React from 'react';
import { createRoot } from 'react-dom/client';
import {
  Admin,
  Resource,
  memoryStore,
  useGetList,
  useGetOne,
} from 'react-admin';
import './style.css';
import OwnerInitiatives from './owner-initiatives.jsx';
import OwnerBrowser from './owner-browser.jsx';
import ProjectDashboard, { ProjectLayout, projectProvider } from './project-dashboard.jsx';

const STATUS = '/internal/v1/dashboard/status';
const IDENTITY = '/internal/v1/dashboard/identity';
const WORKBOARD = '/internal/v1/dashboard/workboard';

async function readJson(url) {
  const response = await fetch(url, {
    credentials: 'same-origin',
    headers: { Accept: 'application/json' },
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
      || !nullableInteger(usage.input_tokens, 1000000)
      || !nullableInteger(usage.output_tokens, 1000000)
      || !nullableInteger(usage.total_tokens, 2000000)
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

function Dashboard() {
  const status = useGetOne('status', { id: 'runtime' });
  const identity = useGetOne('identity', { id: 'current' });
  const work = useGetList('workboard', {
    pagination: { page: 1, perPage: 64 },
  });
  if (status.isPending || identity.isPending || work.isPending) {
    return <main><p>Eva ophalen…</p></main>;
  }
  if (status.error || identity.error || work.error) {
    return <main><h1>Eva</h1><p role="alert">De actuele beheerstatus is niet beschikbaar.</p></main>;
  }
  const cards = work.data || [];
  return <main>
    <header>
      <div><p className="eyebrow">Eigenaarsoverzicht</p><h1>{identity.data.identity.display_name}</h1></div>
      <span className={status.data.runtime.ready ? 'ready' : 'attention'}>
        {status.data.runtime.ready ? 'Gereed' : 'Aandacht nodig'}
      </span>
    </header>
    <OwnerInitiatives />
    <OwnerBrowser />
    <section>
      <h2>Werkbord</h2>
      <p>{cards.length} duurzame {cards.length === 1 ? 'taak' : 'taken'}</p>
      <div className="board">
        {Object.entries({ todo: 'Te doen', doing: 'Bezig', attention: 'Aandacht', review: 'Controleren', done: 'Klaar' }).map(([column, label]) => <article key={column}>
          <h3>{label}</h3>
          {cards.filter((card) => card.column === column).map((card) => <div className="card" key={card.id}>
            <strong>{card.title}</strong><p>{card.summary}</p>
            <small>{card.worker_health} · {card.result_status}</small>
          </div>)}
        </article>)}
      </div>
    </section>
  </main>;
}

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    {import.meta.env.MODE === 'project' ? <Admin
      dashboard={ProjectDashboard} layout={ProjectLayout} dataProvider={projectProvider}
      disableTelemetry store={memoryStore()} loginPage={false}
    ><Resource name="project" /></Admin> :
    <Admin
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
