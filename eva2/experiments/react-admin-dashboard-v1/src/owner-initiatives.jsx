import React from 'react';

const endpoint = '/internal/v1/dashboard/initiatives';
const labels = { working: 'Bezig', done: 'Resultaat klaar', stopped: 'Gestopt', attention: 'Aandacht nodig' };

export default function OwnerInitiatives() {
  const [page, setPage] = React.useState(null);
  const [error, setError] = React.useState('');
  const [busy, setBusy] = React.useState(false);
  const [notice, setNotice] = React.useState('');
  const refresh = React.useCallback(async (signal) => {
    const response = await fetch(endpoint, { credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal });
    if (!response.ok) throw new Error('Eigen initiatieven zijn tijdelijk niet beschikbaar.');
    const data = await response.json();
    if (data.contract !== 'EvaOwnerInitiatives/v1' || !Array.isArray(data.cards) || data.cards.length > 64) {
      throw new Error('Het initiatiefsoverzicht kon niet worden gelezen.');
    }
    setPage(data);
    setError('');
  }, []);
  React.useEffect(() => {
    const controller = new AbortController();
    refresh(controller.signal).catch(e => { if (e.name !== 'AbortError') setError(e.message); });
    return () => controller.abort();
  }, [refresh]);
  async function stop(intent_id) {
    setBusy(true);
    setNotice('');
    try {
      const response = await fetch(endpoint + '/stop', { method: 'POST', credentials: 'same-origin',
        redirect: 'error', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ intent_id }) });
      if (!response.ok) throw new Error('Stoppen is niet bevestigd. Vernieuw het overzicht en probeer opnieuw.');
      const result = await response.json();
      if (result.intent_id !== intent_id || !['stopped', 'already_completed'].includes(result.status)) throw new Error('Stoppen is niet bevestigd.');
      setNotice(result.status === 'stopped'
        ? 'Gestopt. Eva begint geen volgende stap voor dit initiatief. Een al lopende berekening kan nog aflopen.'
        : 'Dit initiatief was al afgerond. Het resultaat blijft beschikbaar.');
      await refresh();
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }
  return <section aria-label="Eigen initiatieven">
    <h2>Eigen initiatieven</h2>
    <p>Wat Eva zelf heeft opgepakt, waarom, en wat het oplevert.</p>
    <button disabled={busy} onClick={() => refresh().catch(e => setError(e.message))}>Vernieuwen</button>
    {error && <p role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    {!page && !error && <p>Initiatieven ophalen…</p>}
    {page && !page.cards.length && <p>Nog geen eigen initiatief opgepakt.</p>}
    <div className="board">{page?.cards.map(card => <article className="card" key={card.intent_id}>
      <h3>{card.title}</h3><strong>{labels[card.status] || 'Status onbekend'}</strong>
      <p>{card.motivation}</p><p>{card.question}</p>
      {card.has_artifact && <p><a href={endpoint + '/' + encodeURIComponent(card.intent_id) + '/artifact'}>Gemaakt bestand downloaden</a></p>}
      {card.can_stop && <button disabled={busy || Boolean(error)} onClick={() => stop(card.intent_id)}>Stop dit initiatief</button>}
    </article>)}</div>
    {page?.truncated && <p>De 64 laatst bijgewerkte initiatieven worden getoond.</p>}
  </section>;
}
