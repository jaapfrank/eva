import React from 'react';
import './owner-browser.css';

const BASE = '/internal/v1/dashboard/browser';
const LABELS = {
  stopped: 'Uit', starting: 'Browser starten', eva: 'Eva bedient de browser',
  taking_over: 'Overnemen', owner: 'Jij bedient de browser',
  resuming: 'Teruggeven aan Eva', stopping: 'Browser stoppen',
  recovery_required: 'Herstel nodig na een onderbreking',
};

function checked(value) {
  if (value?.contract !== 'EvaBrowserSession/v1' || !Object.hasOwn(LABELS, value.state)
    || !Number.isSafeInteger(value.revision) || value.revision < 0
    || value.owner_control !== (value.state === 'owner')
    || value.eva_control !== (value.state === 'eva')
    || value.viewer_path !== (value.owner_control ? `${BASE}/view/` : null)) {
    throw new Error('De browserstatus is niet beschikbaar.');
  }
  return value;
}

export default function OwnerBrowser() {
  const [state, setState] = React.useState(null);
  const [error, setError] = React.useState('');
  const [busy, setBusy] = React.useState(false);
  const serial = React.useRef(0);
  const pending = React.useRef(false);

  const read = React.useCallback(async () => {
    if (pending.current || document.hidden) return;
    const id = ++serial.current;
    try {
      const response = await fetch(`${BASE}/status`, {
        credentials: 'same-origin', cache: 'no-store', redirect: 'error',
        signal: AbortSignal.timeout(15000),
      });
      if (!response.ok) throw new Error('De browser is momenteel niet beschikbaar.');
      const value = checked(await response.json());
      if (id === serial.current) { setState(value); setError(''); }
    } catch (failure) {
      if (id === serial.current) { setState(null); setError(failure.message); }
    }
  }, []);

  React.useEffect(() => {
    read();
    const timer = setInterval(read, 15000);
    window.addEventListener('focus', read);
    document.addEventListener('visibilitychange', read);
    return () => {
      serial.current += 1;
      clearInterval(timer);
      window.removeEventListener('focus', read);
      document.removeEventListener('visibilitychange', read);
    };
  }, [read]);

  async function control(action) {
    if (!state || pending.current) return;
    const revision = state.revision;
    pending.current = true;
    ++serial.current;
    setBusy(true); setError('');
    // Remove the interactive frame before asking Eva to resume. The server also
    // closes every upstream connection before restarting browser tool access.
    if (action === 'resume' || action === 'stop') setState(null);
    try {
      const response = await fetch(`${BASE}/control`, {
        method: 'POST', credentials: 'same-origin', cache: 'no-store', redirect: 'error',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action, revision }), signal: AbortSignal.timeout(120000),
      });
      if (!response.ok) throw new Error(response.status === 409
        ? 'De browserstatus is veranderd. Vernieuw de status en probeer opnieuw.'
        : 'De bediening is niet afgerond. Vernieuw de status.');
      setState(checked(await response.json()));
    } catch (failure) { setState(null); setError(failure.message); }
    finally { pending.current = false; setBusy(false); }
  }

  return <section className="owner-browser" aria-labelledby="browser-heading">
    <div className="browser-heading"><div><h2 id="browser-heading">Browser</h2>
      <p>Laat Eva websites gebruiken. Neem de sessie over om zelf te werken of in te loggen.</p>
    </div><button disabled={busy} onClick={read}>Vernieuwen</button></div>
    <p role="status">{busy ? 'Bediening uitvoeren…' : state ? LABELS[state.state] : 'Browserstatus ophalen'}</p>
    {error && <p role="alert">{error}</p>}
    <div className="browser-actions">
      <button disabled={busy || !state || state.state !== 'stopped'} onClick={() => control('start')}>Start browser</button>
      <button disabled={busy || !state?.browser_ready || !['eva', 'recovery_required'].includes(state?.state)} onClick={() => control('takeover')}>Neem over</button>
      <button disabled={busy || state?.state !== 'owner'} onClick={() => control('resume')}>Geef terug aan Eva</button>
      <button disabled={busy || !state || state.state === 'stopped'} onClick={() => control('stop')}>Stop browser</button>
    </div>
    {state?.owner_control && <>
      <p>Jij hebt de bediening. Eva wacht tot je de browser teruggeeft.</p>
      <iframe title="Eva’s browser — jij hebt de bediening" src={state.viewer_path}
        allow="clipboard-read; clipboard-write; fullscreen" referrerPolicy="no-referrer" />
    </>}
  </section>;
}
