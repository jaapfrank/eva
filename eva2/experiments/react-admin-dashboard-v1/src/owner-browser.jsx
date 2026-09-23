import React, { useEffect, useRef, useState } from 'react';
import './owner-browser.css';

const PERSONAL_CHROME = 'http://127.0.0.1:44718';

export default function OwnerBrowser() {
  const localDashboard = ['localhost', '127.0.0.1', '[::1]'].includes(window.location.hostname);

  return <><ManagedBrowser /><section className="owner-browser" aria-labelledby="browser-heading">
    <div className="browser-heading"><h2 id="browser-heading">Persoonlijke Chrome</h2></div>
    <p>Deel zelf één bestaand Chrome-tabblad met Eva. In de browserbediening kun je het tabblad
      delen, overnemen, teruggeven en de verbinding intrekken.</p>
    {localDashboard ? <>
      <p>Open dit op de computer waarop Eva’s persoonlijke Chrome draait.
        De knop opent de lokale bediening op deze computer in een nieuw tabblad.</p>
      <a className="browser-control-link" href={PERSONAL_CHROME} target="_blank" rel="noopener noreferrer">
        Open Chrome-bediening
      </a>
    </> : <p>Open het dashboard op de computer waarop Eva’s persoonlijke Chrome draait,
      via het lokale dashboardadres. Op een telefoon, via Home Assistant of op een andere
      computer verwijst een lokale browserlink naar dat apparaat, niet naar Eva’s computer.</p>}
    <p>De actuele verbinding en bediening staan op die pagina. Kies bij delen of teruggeven
      zelf een tabblad in de Playwright-extensie. Na herstel of een onderbreking moet je
      opnieuw expliciet een tabblad kiezen.</p>
    <p>Met ‘Neem over’ stopt Eva’s browseractie. Wacht op de bevestiging voordat je zelf
      verdergaat. Eerdere klikken worden niet teruggedraaid. Als de bedieningspagina
      niet bereikbaar is, kun je de tabdeling intrekken met ‘Disconnect’ in de extensie.</p>
  </section></>;
}


const BROWSER_PATH = '/internal/v1/dashboard/browser';
const states = {
  stopped: 'Uit', eva: 'Eva heeft de bediening', owner: 'Jij hebt de bediening',
  recovery_required: 'Verbinding herstellen', starting: 'Starten',
  taking_over: 'Overnemen', resuming: 'Teruggeven', stopping: 'Stoppen',
};

function ManagedBrowser() {
  const [snapshot, setSnapshot] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [viewer, setViewer] = useState(false);
  const inFlight = useRef(false);
  const alive = useRef(true);
  const generation = useRef(0);
  async function readStatus() {
    const response = await fetch(BROWSER_PATH + '/status', {cache: 'no-store'});
    const data = await response.json();
    if (!response.ok || data.contract !== 'EvaBrowserSession/v1'
        || !Number.isSafeInteger(data.revision) || data.revision < 0
        || !Object.hasOwn(states, data.state)) throw new Error('unavailable');
    return data;
  }
  useEffect(() => {
    alive.current = true;
    generation.current++;
    let timer;
    const refresh = async () => {
      if (!inFlight.current) {
        const observedGeneration = generation.current;
        try {
          const data = await readStatus();
          if (alive.current && !inFlight.current && observedGeneration === generation.current) {
            setSnapshot(data); setError('');
            if (data.state !== 'owner') setViewer(false);
          }
        } catch {
          if (alive.current && !inFlight.current && observedGeneration === generation.current) {
            setSnapshot(null); setViewer(false);
            setError('Browserstatus niet bereikbaar. Er wordt niets automatisch opnieuw gestart.');
          }
        }
      }
      if (alive.current) timer = setTimeout(refresh, 5000);
    };
    refresh();
    return () => { alive.current = false; generation.current++; clearTimeout(timer); };
  }, []);
  async function control(action) {
    if (!snapshot || inFlight.current) return;
    generation.current++;
    inFlight.current = true; setBusy(true); setViewer(false); setError('');
    try {
      const response = await fetch(BROWSER_PATH + '/control', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({action, revision: snapshot.revision}),
      });
      if (!response.ok) throw new Error('unconfirmed');
      // Read actual state after the action, rather than assuming it completed.
      const data = await readStatus();
      if (alive.current) setSnapshot(data);
    } catch {
      if (alive.current) {
        setSnapshot(null);
        setError('Bediening niet bevestigd. De actuele status wordt opnieuw opgehaald; de actie wordt niet herhaald.');
      }
    } finally {
      inFlight.current = false;
      if (alive.current) setBusy(false);
    }
  }
  const state = snapshot?.state;
  return <section className="owner-browser" aria-labelledby="managed-browser-heading">
    <div className="browser-heading"><h2 id="managed-browser-heading">Eva’s taakbrowser</h2>
      <span role="status">{busy ? 'Bediening verwerken…' : states[state] || 'Status ophalen…'}</span></div>
    <p>Deze browser gebruikt Eva voor digitale opdrachten. Het bestaande profiel blijft bewaard.
      Neem de bediening over om mee te kijken of zelf verder te gaan.</p>
    {error && <p role="alert">{error}</p>}
    <div className="browser-actions">
      {state === 'stopped' && <button disabled={busy} onClick={() => control('start')}>Start voor Eva</button>}
      {state === 'eva' && <button disabled={busy} onClick={() => control('takeover')}>Neem over</button>}
      {state === 'recovery_required' && <button disabled={busy} onClick={() => control('recover')}>Herstel en neem over</button>}
      {state === 'owner' && <>
        <button disabled={busy} onClick={() => setViewer(value => !value)}>{viewer ? 'Verberg browser' : 'Bekijk en bedien browser'}</button>
        <button disabled={busy} onClick={() => control('resume')}>Geef terug aan Eva</button>
      </>}
      {['eva', 'owner', 'recovery_required'].includes(state) && <button disabled={busy} onClick={() => control('stop')}>Stop browser</button>}
    </div>
    {state === 'recovery_required' && <p>Herstel stopt de automatisering en opent het bewaarde profiel onder jouw bediening.
      Eerdere klikken worden niet herhaald. Controleer de pagina en geef daarna de browser terug aan Eva.</p>}
    {state === 'owner' && <p>Eva bedient deze browser nu niet. Teruggeven maakt browserwerk weer mogelijk.
      Een gestopte opdracht hervat je afzonderlijk op het takenbord; de browserknop start geen nieuwe opdracht.</p>}
    {viewer && state === 'owner' && !busy && <iframe title="Eva’s taakbrowser" src={BROWSER_PATH + '/view/'} />}
    <p>Overnemen of stoppen draait eerdere handelingen niet terug. Na teruggeven moet Eva de pagina opnieuw bekijken.</p>
  </section>;
}
