import React from 'react';
const stages = { candidate: 'Kandidaat voorgesteld', evaluation: 'Vergelijking uitgevoerd', selection: 'Keuze vastgelegd', review: 'Klaargezet voor onafhankelijke beoordeling', application_request: 'Toepassing aangevraagd', application_status: 'Toepassingsstatus vastgelegd' };
const states = { reference_retained: 'Referentie behouden', tie_reference_retained: 'Gelijkspel · referentie behouden', baseline_retained: 'Referentie behouden', candidate_selected: 'Kandidaat geselecteerd', completed: 'Proces afgerond', queued: 'Ingepland', waiting: 'Wacht op vervolg', rejected: 'Afgewezen', unconfirmed: 'Niet bevestigd' };
const date = value => value ? new Date(value).toLocaleString('nl-NL') : 'Niet vastgelegd';
export default function OwnerLearning({ learning }) {
  return <section className="owner-panel" aria-label="Leerhistorie"><h2>Leren en dromen</h2>
    <p>Reflectie, voorstellen en werkelijk toepassen zijn afzonderlijke stappen. Deze pagina leest bestaande registraties en start geen verbeterproef.</p>
    <h3>Wat Eva over haar omgang met jou heeft afgeleid</h3>
    {learning.current_styles.map((style, i) => <article className="card" key={i}>
      <strong>{style.state === 'available' ? 'Bewaarde communicatiestijl' : style.state === 'absent' ? 'Nog geen bewaarde stijl voor deze ingang' : 'Deze stijl is nu niet bereikbaar'}</strong>
      {style.content && <p style={{ whiteSpace: 'pre-wrap' }}>{style.content}</p>}<small>{style.meaning}</small>
    </article>)}
    <h3>Afgeronde reflecties</h3>
    {!learning.reflections.length && <p>Nog geen eigen afgeronde reflectie geregistreerd.</p>}
    <div className="board">{learning.reflections.map(item => <article className="card" key={item.id}>
      <strong>{item.content_sha256 ? item.changed ? 'Afgeleid geheugen bijgewerkt' : 'Reflectie zonder inhoudswijziging' : 'Geen toepasbaar geheugen bevestigd'}</strong>
      <p>{date(item.at)} · {Number.isInteger(item.source_fact_count) ? item.source_fact_count + ' bronfeiten' : 'Bronaantal onbekend'}</p>
      <p>{item.later_context_at ? 'Later als gesprekscontext aangeboden: ' + date(item.later_context_at) : 'Nog geen later contextgebruik geregistreerd.'}</p>
      <small>{item.meaning}</small>
    </article>)}</div>
    {learning.reflections_truncated && <p>De twintig recentste eigen reflecties worden getoond.</p>}
    <h3>Kandidaten, beoordelingen en toepassingen</h3>
    {!learning.components.length && <p>Geen bij jouw huidige taken toegelaten verbeterregistratie gevonden. Dat bewijst geen toegepaste verbetering.</p>}
    <div className="board">{learning.components.map(item => <article className="card" key={item.id}>
      <strong>{item.applied ? 'Werkelijk geïnstalleerd volgens het ontvangstbewijs' : item.reference_retained ? 'Referentie behouden' : stages[item.stage] || 'Opgeslagen verbeterstap'}</strong>
      <p>{date(item.at)}{item.path ? ' · ' + item.path : ''}</p>
      {item.state && <p>{states[item.state] || item.state}</p>}
      {item.candidate_sha256 && <small>Kandidaat: {item.candidate_sha256.slice(0, 12)}</small>}
      <p><a href={'#/?section=work&work_id=' + encodeURIComponent(item.work_id)}>Bijbehorende opdracht openen</a></p>
      <small>{item.meaning}</small>
    </article>)}</div>
    {learning.components_truncated && <p>Dit is een begrensde selectie van recente eigen werk-/verbeterregistraties.</p>}
  </section>;
}
