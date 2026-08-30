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

const dataProvider = {
  async getOne(resource) {
    if (resource === 'status') {
      const payload = await readJson(STATUS);
      return { data: { id: 'runtime', ...payload } };
    }
    if (resource === 'identity') {
      const payload = await readJson(IDENTITY);
      return { data: { id: 'current', ...payload } };
    }
    throw new Error('Onbekende Eva-resource');
  },
  async getList(resource) {
    if (resource !== 'workboard') throw new Error('Onbekende Eva-resource');
    const payload = await readJson(WORKBOARD);
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
  checkAuth: () => readJson(STATUS).then(() => undefined),
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
    <section>
      <h2>Werkbord</h2>
      <p>{cards.length} duurzame {cards.length === 1 ? 'taak' : 'taken'}</p>
      <div className="board">
        {['todo', 'doing', 'attention', 'review', 'done'].map((column) => <article key={column}>
          <h3>{column}</h3>
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
    <Admin
      authProvider={authProvider}
      dashboard={Dashboard}
      dataProvider={dataProvider}
      disableTelemetry
      requireAuth
      store={memoryStore()}
    >
      <Resource name="workboard" />
    </Admin>
  </React.StrictMode>,
);
