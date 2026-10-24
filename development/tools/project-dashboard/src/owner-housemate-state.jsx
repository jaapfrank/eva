import React from 'react';
import { validHousemateState } from './owner-housemate-state.mjs';

const outcomes = { open: 'Nog niet bevestigd', confirmed: 'Bevestigde terugkoppeling', refuted: 'Weerlegd', unknown: 'Uitkomst onbekend' };
const date = value => new Date(value).toLocaleString('nl-NL');

export default function OwnerHousemateState({ onSource }) {
  const [state, setState] = React.useState(null), [error, setError] = React.useState('');
  React.useEffect(() => {
    let controller, sequence = 0;
    const load = async () => {
      const current = ++sequence; controller?.abort(); controller = new AbortController();
      const request = controller; setState(null); setError('');
      if (document.hidden) return;
      try {
        const response = await fetch('/internal/v1/dashboard/initiatives/state', {
          credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: request.signal });
        if (!response.ok) throw new Error();
        const value = await response.json();
        if (!validHousemateState(value)) throw new Error();
        if (current === sequence && !request.signal.aborted) setState(value);
      } catch {
        if (current === sequence && !request.signal.aborted) setError('Deze inzichten zijn nu niet beschikbaar. Je projecten en bijdragen blijven afzonderlijk bereikbaar.');
      }
    };
    const visibility = () => { ++sequence; controller?.abort(); setState(null); setError(''); if (!document.hidden) load(); };
    load(); document.addEventListener('visibilitychange', visibility); window.addEventListener('focus', load);
    return () => { ++sequence; controller?.abort(); document.removeEventListener('visibilitychange', visibility); window.removeEventListener('focus', load); };
  }, []);
  if (!state) return <section className="owner-panel" aria-label="Actuele inzichten en eigen ontwikkeling">
    <h3>Wat Eva voorlopig over jou heeft geleerd</h3><p role="status">{error || 'Actuele inzichten ophalen…'}</p>
  </section>;
  return <section className="owner-panel" aria-label="Actuele inzichten en eigen ontwikkeling">
    <h3>Wat Eva voorlopig over jou heeft geleerd</h3>
    <p>Dit heeft Eva voorlopig uit jullie contact afgeleid. Bekijk de bron om iets te corrigeren of te laten vergeten.</p>
    {!state.current_learning.length && <p>In de recente selectie zijn geen actuele leerbronnen beschikbaar. Gewiste of vervallen bronnen worden niet opnieuw getoond.</p>}
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 280px), 1fr))', gap: 12 }}>{state.current_learning.map(item => <article className="card" style={{ minWidth: 0, overflowWrap: 'anywhere' }} key={item.source_id}
      aria-label={item.kind === 'expectation' ? 'Actuele verwachting' : 'Actuele persoonsafleiding'}>
      <strong>{item.kind === 'expectation' ? 'Verwachting' : 'Voorlopige persoonsafleiding'}</strong>
      <p>{item.text}</p><p><strong>{outcomes[item.outcome]}</strong> · {item.uncertainty_reason}</p>
      <small>{item.revised ? 'Actuele herziening' : 'Vastgelegd'}: {date(item.recorded_at)}</small>
      {item.review_at && <p>Beoordelingsmoment in de bron: {date(item.review_at)}</p>}
      <p><button style={{ whiteSpace: 'normal' }} disabled={typeof onSource !== 'function'} onClick={() => onSource(item.source_id)}>Bron bekijken, corrigeren of vergeten</button></p>
    </article>)}</div>
    <small>Maximaal acht recente actuele bronnen; geen compleet profiel. Na corrigeren of vergeten wordt dit overzicht bij opnieuw openen of vernieuwen opnieuw uit de bron opgebouwd.</small>
    <details><summary>Eigen ontwikkeling en gedeelde gedachten</summary>
      <p><strong>Nieuwsgierigheid binnen eigen projecten:</strong> de werkelijk open verkenningsvragen staan bij de maximaal twee projecten hierboven. Open betekent niet dat Eva nu onderzoek uitvoert.</p>
      <p>Persoonlijkheidsontwikkeling: nog geen onderbouwde eigen bijstelling beschikbaar in dit overzicht.</p>
      <p>Gedeelde gedachten: hier is nog geen eigen gedachte met herkomst beschikbaar. Haar bijdragen en jouw feedback staan bij de initiatieven.</p>
    </details>
    <p><small>Bronnen opnieuw gecontroleerd: <time dateTime={state.checked_at}>{date(state.checked_at)}</time></small></p>
  </section>;
}
