import React from 'react';
const endpoint = '/internal/v1/dashboard/memory-view';
const kinds = { source: 'Broninformatie', inference: 'Afleiding van Eva', legacy: 'Oud · niet bevestigd' };
export default function OwnerMemoryView({ onSource, revision }) {
  const [page, setPage] = React.useState(null), [query, setQuery] = React.useState(''), [error, setError] = React.useState('');
  const request = React.useRef(null);
  const visibleQuery = React.useRef('');
  const load = React.useCallback(async text => {
    visibleQuery.current = text || '';
    request.current?.abort(); const controller = new AbortController(); request.current = controller;
    setPage(null); setError('');
    if (document.hidden) return;
    try {
      const response = await fetch(text ? endpoint + '/search' : endpoint, { credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: controller.signal,
        ...(text ? { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ text }) } : {}) });
      if (!response.ok) throw new Error();
      const value = await response.json();
      if (value.contract !== 'EvaOwnerMemoryView/v1' || value.complete_directory !== false
        || typeof value.owner?.display_name !== 'string' || !Array.isArray(value.mentioned_names) || value.mentioned_names.length > 32
        || !value.mentioned_names.every(n => typeof n === 'string' && n.length <= 120)
        || !Array.isArray(value.memories) || value.memories.length > 12
        || !value.memories.every(m => typeof m.id === 'string' && typeof m.text === 'string' && typeof m.source_id === 'string' && Object.hasOwn(kinds, m.kind))) throw new Error();
      if (!controller.signal.aborted) setPage(value);
    } catch { if (!controller.signal.aborted) setError('Dit geheugenoverzicht is nu niet beschikbaar. Je eigen bronlijst hieronder blijft afzonderlijk bereikbaar.'); }
  }, []);
  React.useEffect(() => {
    let disposed = false, authorityRequest;
    // Recheck authority periodically without repeating semantic recall each tick.
    const checkAuthority = async () => {
      authorityRequest?.abort(); const controller = new AbortController(); authorityRequest = controller;
      if (document.hidden) return;
      try {
        const response = await fetch('/internal/v1/dashboard/memory-sources?limit=1', {
          credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: controller.signal });
        if (!response.ok) throw new Error();
      } catch {
        if (!disposed && !controller.signal.aborted) {
          request.current?.abort(); setPage(null);
          setError('Dit geheugenoverzicht is nu niet beschikbaar. Je eigen bronlijst hieronder blijft afzonderlijk bereikbaar.');
        }
      }
    };
    const visibility = () => {
      request.current?.abort(); authorityRequest?.abort(); setPage(null); setError('');
      if (!document.hidden) load(visibleQuery.current);
    };
    load(); const interval = setInterval(checkAuthority, 30000);
    document.addEventListener('visibilitychange', visibility); window.addEventListener('focus', checkAuthority);
    return () => {
      disposed = true; request.current?.abort(); authorityRequest?.abort(); clearInterval(interval);
      document.removeEventListener('visibilitychange', visibility); window.removeEventListener('focus', checkAuthority);
    };
  }, [load]);
  React.useEffect(() => { if (revision) load(query.trim()); }, [revision]);
  return <section className="owner-panel" aria-label="Personen en herinneringen">
    <h2>Personen en herinneringen</h2><p>Compacte kennis die Eva uit jouw gesprekken selecteert en samenvoegt: mensen, voorkeuren, afspraken en wat later relevant is. Een nieuwe uitspraak kan dit bijwerken. Dit zijn geen volledige gesprekken; je ziet geen privégeheugen van gezinsleden.</p>
    <form className="memory-search" onSubmit={event => { event.preventDefault(); load(query.trim()); }}>
      <label htmlFor="memory-person-query">Zoek een persoon of onderwerp</label>
      <input id="memory-person-query" value={query} maxLength={200} onChange={event => setQuery(event.target.value)} placeholder="Een naam, je gezin of een voorkeur" />
      <button type="submit">Zoeken</button><button type="button" onClick={() => { setQuery(''); load(); }}>Overzicht vernieuwen</button>
    </form>
    {error && <p role="alert">{error}</p>}
    {!page && !error && <p role="status">Personen en herinneringen ophalen…</p>}
    {page && <><p><strong>{page.owner.display_name}</strong> · jouw privégeheugen</p>
      <div className="memory-name-list" aria-label="Namen in jouw herinneringen">{page.mentioned_names.map(name => <button key={name} onClick={() => { setQuery(name); load(name); }}>{name}</button>)}</div>
      <p>Gevonden namen kunnen personen of onderwerpen zijn. Dit is een relevante selectie uit jouw geheugen, geen complete personenlijst.</p>
      {!page.memories.length && <p>Geen herinnering met een momenteel toegankelijke bron gevonden. Probeer een naam of een concreter onderwerp.</p>}
      {page.memories.map(item => <article className="memory-view-card" key={item.id}><strong className={`memory-kind ${item.kind}`}>{kinds[item.kind]}</strong>
        <p>{item.text}</p><small>Bron: {new Date(item.source_at).toLocaleString('nl-NL')}</small>
        <button onClick={() => onSource(item.source_id)}>Bron bekijken en aanpassen</button>
      </article>)}
      <small>Broninformatie blijft onderscheiden van wat Eva eruit afleidt. Alleen de oorspronkelijke bron kan toegang tot bekijken, corrigeren of vergeten geven.</small>
    </>}
  </section>;
}
