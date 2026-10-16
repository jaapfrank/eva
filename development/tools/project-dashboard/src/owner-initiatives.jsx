import React from 'react';
import OwnerLearning from './owner-learning.jsx';

const endpoint = '/internal/v1/dashboard/initiatives';
const labels = { working: 'Bezig', done: 'Resultaat klaar', stopped: 'Gestopt', attention: 'Aandacht nodig' };
const reviewLabels = {
  scheduled: 'Beoordeling gepland', working: 'Beoordeling bezig', waiting: 'Wacht op volgende stap',
  silence: 'Bewust geen actie', adopted: 'Initiatief opgepakt', not_admitted: 'Geen initiatief gestart',
  abandoned: 'Afgesloten · verbruik onbekend', attention: 'Aandacht nodig', unknown: 'Uitkomst onbekend',
};
const isDate = value => typeof value === 'string' && Number.isFinite(Date.parse(value));
function validReview(review) {
  return review == null || (typeof review === 'object' && !Array.isArray(review)
    && isDate(review.updated_at) && typeof review.phase === 'string'
    && Object.hasOwn(reviewLabels, review.state)
    && ['reason', 'blocker', 'intent_id'].every(key => review[key] === null || typeof review[key] === 'string'));
}
function ReviewStatus({ page }) {
  const review = page.latest_review;
  return <div aria-label="Laatste beoordeling">
    <h3>Laatste beoordeling</h3>
    {review ? <>
      <p><strong>{review.blocker === 'background_budget' ? 'Wacht op achtergrondbudget' : reviewLabels[review.state]}</strong></p>
      <p>Bijgewerkt: <time dateTime={review.updated_at}>{new Date(review.updated_at).toLocaleString('nl-NL')}</time></p>
      {review.reason && <p>{review.reason}</p>}
      {review.blocker && <p>Blokkade: {review.blocker === 'background_budget'
        ? 'Het beschikbare achtergrondbudget laat deze stap nu niet toe.' : review.blocker}</p>}
      {review.state === 'silence' && <p>Eva heeft beoordeeld dat er nu geen passende actie nodig is.</p>}
    </> : <p>{review === null ? 'Er is nog geen opgeslagen beoordeling beschikbaar.'
      : 'De laatste beoordeling is via deze versie nog niet beschikbaar.'}</p>}
    {page.checked_at && <p>Overzicht opgehaald: <time dateTime={page.checked_at}>{new Date(page.checked_at).toLocaleString('nl-NL')}</time></p>}
    <p>Een beoordeling is nog geen bijdrage. Opgepakte initiatieven en hun resultaten staan hieronder.</p>
  </div>;
}

export default function OwnerInitiatives() {
  const [page, setPage] = React.useState(null);
  const [error, setError] = React.useState('');
  const [busy, setBusy] = React.useState(false);
  const [notice, setNotice] = React.useState('');
  const refreshSequence = React.useRef(0);
  const refresh = React.useCallback(async (signal) => {
    const sequence = ++refreshSequence.current;
    setPage(null); if (document.hidden) return;
    try {
    const response = await fetch(endpoint + '/history', { credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal });
    if (!response.ok) throw new Error('Eigen initiatieven zijn tijdelijk niet beschikbaar.');
    const data = await response.json();
    if (data.contract !== 'EvaOwnerInitiativeHistory/v1' || !Array.isArray(data.cards) || data.cards.length > 64
      || !Array.isArray(data.learning?.reflections) || !Array.isArray(data.learning?.components) || !Array.isArray(data.learning?.current_styles)
      || !validReview(data.latest_review) || (data.checked_at !== undefined && !isDate(data.checked_at))) {
      throw new Error('Het initiatiefsoverzicht kon niet worden gelezen.');
    }
    if (sequence !== refreshSequence.current) return;
    setPage(data);
    setError('');
    } catch (e) {
      if (sequence !== refreshSequence.current || e.name === 'AbortError') return;
      setPage(null);
      setError(e.message);
    }
  }, []);
  React.useEffect(() => {
    let controller;
    const load = () => { controller?.abort(); controller = new AbortController();
      refresh(controller.signal).catch(e => { if (e.name !== 'AbortError') setError(e.message); }); };
    const visibility = () => { ++refreshSequence.current; controller?.abort(); setPage(null); setNotice(''); if (!document.hidden) load(); };
    load(); document.addEventListener('visibilitychange', visibility); window.addEventListener('focus', load);
    return () => { ++refreshSequence.current; controller?.abort(); document.removeEventListener('visibilitychange', visibility); window.removeEventListener('focus', load); };
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
    {page && !error && <ReviewStatus page={page} />}
    {page && !error && !page.cards.length && <p>Nog geen eigen initiatief opgepakt.</p>}
    <div className="board">{!error && page?.cards.map(card => <InitiativeCard key={card.intent_id} card={card} busy={busy} stop={stop} refresh={refresh} />)}</div>
    {!error && page?.truncated && <p>De 64 laatst bijgewerkte initiatieven worden getoond.</p>}
    {!error && page?.learning && <OwnerLearning learning={page.learning} />}
  </section>;
}

function InitiativeCard({ card, busy, stop, refresh }) {
  const [feedback, setFeedback] = React.useState(''), [notice, setNotice] = React.useState(''), [sending, setSending] = React.useState(false), [full, setFull] = React.useState(null);
  const controller = React.useRef(null);
  React.useEffect(() => () => controller.current?.abort(), []);
  async function contribution() {
    controller.current?.abort(); const request = new AbortController(); controller.current = request;
    try { const response = await fetch(endpoint + '/' + encodeURIComponent(card.intent_id) + '/artifact', { credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: request.signal });
      if (!response.ok) throw new Error(); const text = await response.text(); if (text.length > 131072) throw new Error();
      if (!request.signal.aborted) setFull(text);
    } catch { if (!request.signal.aborted) setNotice('De volledige bijdrage is nu niet beschikbaar.'); }
  }
  async function submit(event) {
    event.preventDefault(); setSending(true); setNotice('');
    try { const response = await fetch(endpoint + '/feedback', { method: 'POST', credentials: 'same-origin', cache: 'no-store', redirect: 'error', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ intent_id: card.intent_id, feedback: feedback.trim() }) });
      if (!response.ok) throw new Error(); const result = await response.json();
      if (result.status !== 'recorded' || result.intent_id !== card.intent_id) throw new Error();
      await refresh();
    } catch { setNotice('Je feedback is niet bevestigd. Vernieuw eerst voordat je opnieuw verstuurt.'); }
    finally { setSending(false); }
  }
  return <article className="card" aria-label={'Initiatief: ' + card.title}>
    <h3>{card.title}</h3><strong>{labels[card.status] || 'Status onbekend'}</strong>
    <p><strong>Aanleiding en eigen besluit</strong><br />{card.motivation}</p><p>{card.question}</p>
    <small>Opgepakt: {new Date(card.adopted_at).toLocaleString('nl-NL')}</small>
    {card.result_preview && <p style={{ whiteSpace: 'pre-wrap' }}>{card.result_preview}{card.result_truncated ? '…' : ''}</p>}
    {card.result_at && <p>Resultaat bewaard: {new Date(card.result_at).toLocaleString('nl-NL')}</p>}
    {card.work_id && <p><a href={'#/?section=work&work_id=' + encodeURIComponent(card.work_id)}>Uitvoering en resultaat openen</a></p>}
    {card.has_artifact && <button onClick={contribution}>Volledige bijdrage tonen</button>}
    {full !== null && <><pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{full}</pre><button onClick={() => setFull(null)}>Bijdrage verbergen</button></>}
    <h4>Jouw echte feedback</h4><p>{card.feedback || 'Nog geen feedback ontvangen.'}</p><small>{card.feedback_later_use}</small>
    <form onSubmit={submit}><label>Feedback op deze bijdrage<textarea aria-label={'Feedback: ' + card.title} value={feedback} maxLength={800} onChange={e => setFeedback(e.target.value)} /></label>
      <button disabled={sending || !feedback.trim()}>Feedback bewaren</button></form>
    {notice && <p role="status">{notice}</p>}
    {card.can_stop && <button disabled={busy || sending} onClick={() => stop(card.intent_id)}>Stop dit initiatief</button>}
  </article>;
}
