import React from 'react';

const labels = { ready: 'Controle geslaagd', attention: 'Aandacht nodig', unknown: 'Niet gemeten', not_configured: 'Niet ingesteld' };
export default function OwnerHealth() {
  const [data, setData] = React.useState(null);
  const [error, setError] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const [revision, setRevision] = React.useState(0);
  React.useEffect(() => {
    const controller = new AbortController();
    setBusy(true); setError(false);
    fetch('/internal/v1/dashboard/components', { credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: controller.signal })
      .then(async response => {
        if (!response.ok) throw new Error('Unavailable');
        const value = await response.json();
        if (value.contract !== 'EvaComponentObservations/v1' || !Array.isArray(value.components)
          || value.components.length > 32 || !Number.isFinite(Date.parse(value.checked_at))
          || !value.components.every(item => typeof item.id === 'string' && typeof item.label === 'string'
            && typeof item.detail === 'string' && Object.hasOwn(labels, item.state)
            && (item.observed_at === null || Number.isFinite(Date.parse(item.observed_at))))) throw new Error('Invalid observations');
        if (!controller.signal.aborted) setData(value);
      }).catch(() => { if (!controller.signal.aborted) { setData(null); setError(true); } })
      .finally(() => { if (!controller.signal.aborted) setBusy(false); });
    return () => controller.abort();
  }, [revision]);
  return <section aria-label="Verbindingen en onderdelen">
    <h2>Verbindingen en onderdelen</h2>
    <p>Een geslaagde controle geldt alleen voor de beschreven meting. Niet gemeten betekent niet dat een onderdeel kapot is.</p>
    <button disabled={busy} onClick={() => setRevision(value => value + 1)}>Controleer opnieuw</button>
    {busy && <p role="status">Status ophalen…</p>}
    {error && <p role="alert">Actuele status niet beschikbaar. Eerdere controles worden niet als actueel getoond.</p>}
    {data && <><p>Momentopname: {new Date(data.checked_at).toLocaleString('nl-NL')}. Vernieuw voor een nieuwe controle.</p>
      <div className="component-health">{data.components.map(item => <article className={`card component-${item.state}`} key={item.id}>
        <h3>{item.label}</h3><strong>{labels[item.state]}</strong><p>{item.detail}</p>
        <small>{item.observed_at ? `Gemeten: ${new Date(item.observed_at).toLocaleString('nl-NL')}` : 'Geen actuele gezondheidsmeting'}</small>
      </article>)}</div></>}
  </section>;
}
