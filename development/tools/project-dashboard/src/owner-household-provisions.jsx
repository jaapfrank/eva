import React from 'react';

const endpoint = '/internal/v1/dashboard/household-provisions';
const labels = { proposed: 'Voorgesteld', active: 'Actief', paused: 'Gepauzeerd', rejected: 'Afgewezen', retired: 'Beëindigd' };
const actions = { adopt: 'Voorziening aannemen', pause: 'Pauzeren', resume: 'Hervatten', reject: 'Voorstel afwijzen', retire: 'Voorziening beëindigen', stop: 'Stoppen' };
const digest = value => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);
const identifier = value => typeof value === 'string' && value.trim().length > 0 && value.length <= 160;

function validatePage(value) {
  if (!value || value.contract !== 'EvaOwnerHouseholdProvisions/v1' || !identifier(value.provision_id)
    || typeof value.enrolled_record_missing !== 'boolean' || typeof value.actual_working_proof_available !== 'boolean') throw new Error('Het overzicht van huisvoorzieningen kon niet worden gelezen.');
  const enrollment = value.enrollment, state = value.provision;
  if (enrollment !== null && (!enrollment || enrollment.contract !== 'EvaHouseholdProvisionEnrollment/v1'
    || enrollment.provision_id !== value.provision_id || !digest(enrollment.binding_sha256))) throw new Error('De registratie kon niet worden gelezen.');
  if (state !== null && (!state || state.provision_id !== value.provision_id || !Object.hasOwn(labels, state.status)
    || !Number.isSafeInteger(state.revision) || state.revision < 1 || !digest(state.evidence?.binding_sha256)
    || typeof state.stopped !== 'boolean' || !['clear', 'unknown', 'in_flight', 'stopped'].includes(state.effect_status)
    || !(state.adoption === null || (state.adoption && typeof state.adoption === 'object' && !Array.isArray(state.adoption)))
    || !(state.verification_sha256 === null || digest(state.verification_sha256))
    || (enrollment !== null && enrollment.binding_sha256 !== state.evidence.binding_sha256))) throw new Error('De actuele voorziening kon niet worden gelezen.');
  if (value.enrolled_record_missing !== (enrollment !== null && state === null)) throw new Error('De registratiestatus is onvolledig.');
  return value;
}

export default function OwnerHouseholdProvisions() {
  const [page, setPage] = React.useState(null), [error, setError] = React.useState('');
  const [notice, setNotice] = React.useState(''), [busy, setBusy] = React.useState(false);
  const sequence = React.useRef(0), requests = React.useRef(new Map());
  const refresh = React.useCallback(async signal => {
    const current = ++sequence.current;
    setPage(null);
    if (document.hidden) return;
    try {
      const response = await fetch(endpoint, { credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal });
      if (!response.ok) throw new Error('Huisvoorzieningen zijn momenteel niet beschikbaar.');
      const value = validatePage(await response.json());
      if (current !== sequence.current) return;
      setPage(value); setError('');
    } catch (failure) {
      if (current !== sequence.current || failure.name === 'AbortError') return;
      setPage(null); setError(failure.message);
    }
  }, []);
  React.useEffect(() => {
    let controller;
    const load = () => { controller?.abort(); controller = new AbortController(); refresh(controller.signal); };
    const visibility = () => { ++sequence.current; controller?.abort(); setPage(null); setNotice(''); if (!document.hidden) load(); };
    load(); window.addEventListener('focus', load); document.addEventListener('visibilitychange', visibility);
    return () => { ++sequence.current; controller?.abort(); window.removeEventListener('focus', load); document.removeEventListener('visibilitychange', visibility); };
  }, [refresh]);

  async function submit(action) {
    if (busy || !page) return;
    const state = page.provision;
    const payload = action === 'register' ? {} : { provision_id: state.provision_id, action,
      expected_revision: state.revision, binding_sha256: state.evidence.binding_sha256 };
    const key = action + ':' + page.provision_id + ':' + JSON.stringify(payload);
    if (!requests.current.has(key)) requests.current.set(key, crypto.randomUUID());
    payload.request_id = requests.current.get(key);
    setBusy(true); setNotice(''); setError('');
    try {
      const response = await fetch(endpoint + (action === 'register' ? '/register' : '/command'), {
        method: 'POST', credentials: 'same-origin', cache: 'no-store', redirect: 'error',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      });
      if (!response.ok) throw new Error('De wijziging is niet bevestigd. Vernieuw het overzicht voordat je opnieuw probeert.');
      const result = await response.json();
      const receipt = action === 'register' ? result.receipt : result;
      if ((action === 'register' && result.contract !== 'EvaOwnerHouseholdProvisionRegistration/v1')
        || receipt?.contract !== 'EvaHouseholdProvisionReceipt/v1' || receipt.provision_id !== page.provision_id
        || !Object.hasOwn(labels, receipt.status) || !Number.isSafeInteger(receipt.revision)) throw new Error('De bevestiging kon niet worden gelezen. Vernieuw het overzicht.');
      requests.current.delete(key);
      setNotice((action === 'register' ? 'Het voorstel is geregistreerd.' : 'Vastgelegd: ' + labels[receipt.status].toLowerCase() + '.')
        + (receipt.in_flight_effect_may_complete ? ' Een al aangenomen handeling kan nog aflopen.' : ''));
      await refresh();
    } catch (failure) { setError(failure.message); setPage(null); }
    finally { setBusy(false); }
  }

  const state = page?.provision;
  const terminal = state && ['rejected', 'retired'].includes(state.status);
  const canRun = page?.actual_working_proof_available === true && state?.effect_status === 'clear' && !state.stopped;
  const available = state && page.enrollment !== null && !terminal ? [
    ...(!state.adoption && canRun ? ['adopt'] : []),
    ...(state.status === 'paused' && state.adoption && canRun ? ['resume'] : []),
    ...(state.status !== 'paused' && !state.stopped ? ['pause'] : []),
    ...(!state.stopped ? ['stop'] : []),
    ...(state.status === 'proposed' && !state.adoption ? ['reject'] : []), 'retire',
  ] : [];
  return <section aria-label="Huisvoorzieningen" style={{ marginBlock: 24 }}>
    <h3>Huisvoorzieningen</h3>
    <p>Blijvende voorzieningen die je samen met Eva kunt aannemen, pauzeren en beëindigen.</p>
    <button disabled={busy} onClick={() => refresh()}>Huisvoorzieningen vernieuwen</button>
    {error && <p role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    {!page && !error && <p>Huisvoorzieningen ophalen…</p>}
    {page && !error && <article className="card" aria-label="Huidige huisvoorziening" style={{ marginTop: 12, overflowWrap: 'anywhere' }}>
      {state ? <>
        <p><strong>{page.enrollment === null ? 'Registratie niet gekoppeld' : labels[state.status]}</strong> · revisie {state.revision}</p>
        <p>Door jou aangenomen: {state.adoption ? 'ja' : 'nee'}.</p>
        {state.stopped && <p>Deze voorziening is gestopt en kan niet worden hervat.</p>}
        {!page.actual_working_proof_available && !terminal && <p>De werking is nog niet bevestigd. De voorziening kan daarom nog niet actief worden of hervatten.</p>}
        {state.effect_status === 'in_flight' && <p>Een eerder aangenomen handeling loopt nog.</p>}
        {state.effect_status === 'unknown' && <p>De uitkomst van eerdere handelingen is nog niet bevestigd.</p>}
        {page.enrollment === null && <>
          <p>Bewaarde status: {labels[state.status].toLowerCase()}. Deze registratie is nog niet aan de uitvoering gekoppeld.</p>
          {state.status === 'proposed' && !state.adoption && !state.stopped && page.actual_working_proof_available
            ? <><p>Rond de registratie af om de voorziening te kunnen bedienen. Daarna wacht de uitvoering op jouw keuze om haar aan te nemen.</p>
              <button disabled={busy} onClick={() => submit('register')}>Registratie afronden</button></>
            : state.status === 'proposed' && !state.adoption && !state.stopped
              ? <p>Het voorstel is bewaard. Zodra de werking is bevestigd kun je de registratie afronden en de voorziening aannemen.</p>
            : <p>De koppeling moet worden hersteld voordat deze voorziening kan worden bediend.</p>}
        </>}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>{available.map(action =>
          <button key={action} disabled={busy} onClick={() => submit(action)}>{actions[action]}</button>)}</div>
      </> : page.enrolled_record_missing ? <p role="alert">De gekoppelde registratie ontbreekt. Bediening is tijdelijk niet beschikbaar.</p> : <>
        <p>De bestaande huisvoorziening is nog niet als voorstel geregistreerd.</p>
        <p>Je kunt het voorstel alvast bewaren. Activeren kan pas nadat de werking is gecontroleerd en je de voorziening aanneemt.</p>
        <button disabled={busy} onClick={() => submit('register')}>Als voorstel registreren</button>
      </>}
    </article>}
  </section>;
}
