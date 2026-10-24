import React from 'react';

const digest = value => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);
const phases = ['authorized', 'evaluated_negative', 'evaluated_awaiting_notice', 'adopted'];

function validTrial(trial, state) {
  if (!trial) return true;
  if (!digest(trial.trial_id) || !digest(trial.version_id) || !digest(trial.binding_sha256)
    || trial.version_id !== state.current_version || trial.binding_sha256 !== state.evidence.binding_sha256
    || trial.owner_principal_id !== state.owner_principal_id || !phases.includes(trial.phase)
    || !Number.isSafeInteger(trial.expires_at_epoch) || !Number.isSafeInteger(trial.max_effects)
    || trial.max_effects < 1 || trial.max_effects > 16 || !Number.isSafeInteger(trial.effect_count)
    || trial.effect_count < 0 || trial.effect_count > trial.max_effects) return false;
  if (trial.phase === 'evaluated_awaiting_notice' || trial.phase === 'adopted') {
    const notice = trial.notice, evaluation = trial.evaluation;
    if (!notice || !digest(notice.notice_id) || !digest(notice.nonce)
      || notice.trial_id !== trial.trial_id || notice.version_id !== trial.version_id
      || notice.owner_principal_id !== state.owner_principal_id
      || typeof notice.message !== 'string' || !notice.message.trim() || notice.message.length > 2000
      || evaluation?.working !== true || evaluation?.useful !== true
      || evaluation.owner_principal_id !== state.owner_principal_id || !digest(evaluation.proof_sha256)
      || (trial.phase === 'evaluated_awaiting_notice' && notice.acknowledged_at !== null)) return false;
  }
  return true;
}

export default function OwnerHouseholdProvisionTrial({ page, busy, submit }) {
  const state = page.provision, trial = state.trial;
  const [hours, setHours] = React.useState('24'), [maximum, setMaximum] = React.useState('1');
  const [working, setWorking] = React.useState(''), [useful, setUseful] = React.useState('');
  const [feedback, setFeedback] = React.useState('');
  const [now, setNow] = React.useState(() => Math.floor(Date.now() / 1000));
  React.useEffect(() => { const timer = setInterval(() => setNow(Math.floor(Date.now() / 1000)), 30000); return () => clearInterval(timer); }, []);
  if (!validTrial(trial, state)) return <p role="alert">De proefgegevens zijn niet volledig. Vernieuw het overzicht.</p>;
  const expired = trial && trial.expires_at_epoch <= now;
  const usable = !state.stopped && !page.change_pending && !['rejected', 'retired'].includes(state.status);
  const current = usable && state.status === 'proposed' && trial && !expired;
  const grant = usable && ['proposed', 'paused'].includes(state.status) && state.effect_status === 'clear'
    && (!trial || expired || state.status === 'paused' || trial.phase === 'evaluated_negative');
  const bounds = Number.isInteger(Number(hours)) && Number(hours) >= 1 && Number(hours) <= 168
    && Number.isInteger(Number(maximum)) && Number(maximum) >= 1 && Number(maximum) <= 16;
  return <section aria-label="Proef van de huisvoorziening" style={{ marginTop: 20 }}>
    <h4>Tijdelijk proberen</h4>
    {trial && <p>{trial.phase === 'adopted' ? 'De beoordeelde proef is blijvend aangenomen.'
      : trial.phase === 'evaluated_negative' ? 'De proef is beoordeeld en de voorziening is gepauzeerd.'
      : expired ? 'De toestemming voor deze proef is verlopen.'
      : `Toegestaan tot ${new Date(trial.expires_at_epoch * 1000).toLocaleString('nl-NL')}. Uitvoeringen: ${trial.effect_count} van maximaal ${trial.max_effects}.`}</p>}
    {grant && <form onSubmit={event => { event.preventDefault(); if (bounds) submit('trial_authorize', {
      expires_at_epoch: Math.floor(Date.now() / 1000) + Number(hours) * 3600, max_effects: Number(maximum) }); }}>
      <p>Je staat de huidige versie tijdelijk toe binnen de bestaande afspraken. Blijvend gebruik volgt pas na gecontroleerde werking, je beoordeling en een zichtbare melding. Je kunt de proef tussentijds stoppen.</p>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, marginBottom: 12 }}>
        <label>Duur in uren <input aria-label="Duur in uren" type="number" min="1" max="168" required value={hours} onChange={event => setHours(event.target.value)} style={{ width: 80 }} /></label>
        <label>Maximale uitvoeringen <input aria-label="Maximale uitvoeringen" type="number" min="1" max="16" required value={maximum} onChange={event => setMaximum(event.target.value)} style={{ width: 70 }} /></label>
      </div>
      <button disabled={busy || !bounds}>Proef toestaan</button>
    </form>}
    {current && trial.phase === 'authorized' && <form onSubmit={event => { event.preventDefault(); if (working && useful && feedback.trim()) submit('trial_evaluate', {
      working: working === 'yes', useful: useful === 'yes', feedback: feedback.trim() }); }}>
      <p>Beoordeel wat je zelf hebt gezien. Een positieve beoordeling wordt pas bevestigd als Eva ook de werking van deze proef heeft gecontroleerd.</p>
      <p><label>Werkte de voorziening? <select required aria-label="Werkte de voorziening?" value={working} onChange={event => setWorking(event.target.value)}>
        <option value="">Kies je antwoord</option><option value="yes">Ja</option><option value="no">Nee</option>
      </select></label></p>
      <p><label>Was de voorziening nuttig? <select required aria-label="Was de voorziening nuttig?" value={useful} onChange={event => setUseful(event.target.value)}>
        <option value="">Kies je antwoord</option><option value="yes">Ja</option><option value="no">Nee</option>
      </select></label></p>
      <p><label>Wat heb je ervaren?<textarea aria-label="Wat heb je ervaren?" required maxLength={1000} value={feedback} onChange={event => setFeedback(event.target.value)} style={{ display: 'block', width: '100%', maxWidth: 540, minHeight: 80, boxSizing: 'border-box' }} /></label></p>
      <button disabled={busy || !working || !useful || !feedback.trim()}>Beoordeling vastleggen</button>
    </form>}
    {current && trial.phase === 'evaluated_awaiting_notice' && <div role="status">
      <p>{trial.notice.message}</p>
      <p>Na bevestiging dat je deze melding hebt gelezen wordt de voorziening actief binnen je eerdere toestemming.</p>
      <button disabled={busy || state.effect_status !== 'clear'} onClick={() => submit('trial_acknowledge', {
        notice_id: trial.notice.notice_id, nonce: trial.notice.nonce })}>Melding gelezen</button>
    </div>}
  </section>;
}
