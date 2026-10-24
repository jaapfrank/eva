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
    <h3>Eva’s ontwikkeling en jullie contact</h3><p role="status">{error || 'Actuele inzichten ophalen…'}</p>
  </section>;
  return <section className="owner-panel" aria-label="Actuele inzichten en eigen ontwikkeling">
    <h3>Eva’s ontwikkeling en jullie contact</h3>
    <p>Actuele ontwikkeling en inzichten, met de bron erbij. Je kunt die bekijken, corrigeren of laten vergeten.</p>
    <div className="card" aria-label="Eva's actuele eigen ontwikkeling" style={{ borderLeft: '5px solid #287b68', marginBottom: 16 }}>
      <h4>Waar Eva nu op voortbouwt</h4>
      {state.development_attention && <p role="status" style={{ color: '#94620e' }}>De laatste verwerking vraagt aandacht. Alleen opnieuw gecontroleerde bronnen worden getoond.</p>}
      {state.inner_state ? <>
        {state.inner_state.current_curiosity && <p><strong>Huidige nieuwsgierigheid:</strong> {state.inner_state.current_curiosity}</p>}
        <p>{({ own_project_needs_attention: 'Een bestaand eigen project vraagt aandacht.', continuing_own_project: 'Eva bouwt voort op een bestaand eigen project.', adjusting_from_personal_feedback: 'Eva verwerkt feedback op haar manier van helpen.' })[state.inner_state.recent_tone]}</p>
        <p>{state.inner_state.open_threads.length} open eigen {state.inner_state.open_threads.length === 1 ? 'project' : 'projecten'} · {state.inner_state.evidence_count} gecontroleerde {state.inner_state.evidence_count === 1 ? 'bron' : 'bronnen'}</p>
        <small>Laatste onderliggende activiteit: <time dateTime={state.inner_state.last_updated}>{date(state.inner_state.last_updated)}</time>.</small>
      </> : <p>{['unavailable', 'source_changed'].includes(state.development_state)
        ? 'Een actuele ontwikkelingsbron is nu niet beschikbaar of is veranderd. Oude ontwikkeling wordt niet opnieuw getoond.'
        : state.development_state === 'not_configured' ? 'De bron voor eigen ontwikkeling is nog niet aangesloten.'
          : 'Er is nog geen actuele eigen ontwikkeling uit de droomverwerking beschikbaar. Dit betekent niet dat Eva niets doet.'}</p>}
      <p><small>Gebruik de stopknop bij het project. Vernieuwen controleert de actuele toestand en bronnen opnieuw.</small></p>
    </div>
    <h4>Hoe Eva haar eigen omgang bijstelt</h4>
    <p>Voorlopige bijstellingen uit jullie contact, alleen voor jouw privécontext.</p>
    {!state.personality_deltas.length && <p>Er is nog geen actuele brongebonden omgangsbijstelling in deze selectie.</p>}
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 280px), 1fr))', gap: 12 }}>{state.personality_deltas.map(item => <article className="card" key={item.source_id}
      aria-label="Eva's eigen omgangsbijstelling" style={{ minWidth: 0, overflowWrap: 'anywhere', borderLeft: `5px solid ${item.processing_state === 'processed' ? '#287b68' : item.active ? '#bd8a28' : '#7a8183'}` }}>
      <strong>{({ processed: 'Meegenomen in de droomverwerking', awaiting_dream: 'Vastgelegd · wacht op droomverwerking', inactive: 'Niet actief · ingetrokken of herbeoordeling nodig' })[item.processing_state]}</strong>
      <p>{item.text}</p><p>{outcomes[item.outcome]} · {item.uncertainty_reason}</p>
      <small>Versie {item.version} · {item.revised ? 'Actuele herziening' : 'Vastgelegd'}: {date(item.recorded_at)}</small>
      {item.review_at && <p>Herbeoordeling: {date(item.review_at)}</p>}
      <p><button style={{ whiteSpace: 'normal', padding: '8px 12px', borderRadius: 8, border: '1px solid #b8cfc6', background: '#f3f8f4' }} disabled={typeof onSource !== 'function'} onClick={() => onSource(item.source_id)}>Bron bekijken, corrigeren of vergeten</button></p>
    </article>)}</div>
    <h4>Voorlopige inzichten over jou</h4>
    {!state.current_learning.length && <p>In de recente selectie zijn geen actuele leerbronnen beschikbaar. Gewiste of vervallen bronnen worden niet opnieuw getoond.</p>}
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 280px), 1fr))', gap: 12 }}>{state.current_learning.map(item => <article className="card" style={{ minWidth: 0, overflowWrap: 'anywhere' }} key={item.source_id}
      aria-label={item.kind === 'expectation' ? 'Actuele verwachting' : 'Actuele persoonsafleiding'}>
      <strong>{item.kind === 'expectation' ? 'Verwachting' : 'Voorlopige persoonsafleiding'}</strong>
      <p>{item.text}</p><p><strong>{outcomes[item.outcome]}</strong> · {item.uncertainty_reason}</p>
      <small>{item.revised ? 'Actuele herziening' : 'Vastgelegd'}: {date(item.recorded_at)}</small>
      {item.review_at && <p>Beoordelingsmoment in de bron: {date(item.review_at)}</p>}
      <p><button style={{ whiteSpace: 'normal', padding: '8px 12px', borderRadius: 8, border: '1px solid #b8cfc6', background: '#f3f8f4' }} disabled={typeof onSource !== 'function'} onClick={() => onSource(item.source_id)}>Bron bekijken, corrigeren of vergeten</button></p>
    </article>)}</div>
    <small>Recente bronselectie: maximaal acht inzichten en drie bijstellingen. Na corrigeren of vergeten wordt het overzicht opnieuw uit de bron opgebouwd.</small>
    <details><summary>Gedeelde gedachten</summary>
      <p>Hier is nog geen gedeelde gedachte met geldige herkomst vastgelegd. Deze privébronnen worden niet als gezinsbrede ervaring getoond.</p>
    </details>
    <p><small>Bronnen opnieuw gecontroleerd: <time dateTime={state.checked_at}>{date(state.checked_at)}</time></small></p>
  </section>;
}
