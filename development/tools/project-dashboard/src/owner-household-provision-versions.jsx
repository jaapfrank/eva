import React from 'react';

const endpoint = '/internal/v1/dashboard/household-provisions';
const digest = value => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);
const date = value => new Date(value).toLocaleString('nl-NL');
const events = {
  HOUSEHOLD_PROVISION_REGISTERED: 'Voorstel bewaard',
  HOUSEHOLD_PROVISION_ADOPT: 'Voorziening aangenomen',
  HOUSEHOLD_PROVISION_PAUSE: 'Gepauzeerd', HOUSEHOLD_PROVISION_STOP: 'Gestopt',
  HOUSEHOLD_PROVISION_RESUME: 'Hervat', HOUSEHOLD_PROVISION_RETIRE: 'Beëindigd',
  HOUSEHOLD_PROVISION_VERSION_STAGED: 'Nieuwe versie klaargezet',
  HOUSEHOLD_PROVISION_VERSION_CHANGE_PREPARED: 'Wijziging voorbereid',
  HOUSEHOLD_PROVISION_VERSION_CHANGED: 'Versie gewijzigd',
  HOUSEHOLD_PROVISION_VERSION_RESTORED: 'Vorige versie teruggezet',
  HOUSEHOLD_PROVISION_CHANGE_PREPARED: 'Wijziging voorbereid',
  HOUSEHOLD_PROVISION_CHANGE_VERIFIED: 'Versie bevestigd',
};

function validate(choices, history, page) {
  if (choices?.contract !== 'EvaHouseholdProvisionChoices/v1' || !Array.isArray(choices.choices)
    || choices.choices.length > 8 || choices.choices.some(value => !digest(value.choice_id)
      || value.state !== 'reviewed_saved' || typeof value.label !== 'string' || !value.label.trim()
      || value.label.length > 240 || typeof value.reference_id !== 'string')) throw new Error('De beschikbare versies konden niet worden gelezen.');
  if (history?.contract !== 'EvaHouseholdProvisionHistory/v1' || history.provision_id !== page.provision_id
    || history.revision !== page.provision.revision || !Array.isArray(history.entries) || history.entries.length > 32
    || !Number.isSafeInteger(history.earlier_entries) || history.earlier_entries < 0
    || history.entries.some(value => !Number.isSafeInteger(value.revision) || value.revision < 1
      || value.revision > history.revision || typeof value.event !== 'string'
      || !digest(value.version_id) || !Number.isFinite(Date.parse(value.at)))) throw new Error('De versiegeschiedenis is gewijzigd of niet beschikbaar. Vernieuw het overzicht.');
  const state = page.provision;
  if (state.versions !== undefined && (!state.versions || Array.isArray(state.versions)
    || !digest(state.current_version) || !Object.hasOwn(state.versions, state.current_version)
    || !(state.previous_version === null || (digest(state.previous_version) && Object.hasOwn(state.versions, state.previous_version)))
    || Object.keys(state.versions).length > 16 || Object.entries(state.versions).some(([key, value]) => !digest(key)
      || !value || !digest(value.evidence?.binding_sha256) || typeof value.evidence.reference_id !== 'string'
      || !Number.isFinite(Date.parse(value.recorded_at))))) throw new Error('De bewaarde versies konden niet worden gelezen.');
  return { choices: choices.choices, history };
}

export default function OwnerHouseholdProvisionVersions({ page, busy, submit }) {
  const [data, setData] = React.useState(null), [error, setError] = React.useState('');
  React.useEffect(() => {
    const controller = new AbortController(); let current = true;
    setData(null); setError('');
    if (document.hidden) return () => controller.abort();
    async function read(path) {
      const response = await fetch(path, { credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: controller.signal });
      if (!response.ok) throw new Error('Versies zijn momenteel niet beschikbaar.');
      return response.json();
    }
    Promise.all([read(endpoint + '/versions'), read(endpoint + '/' + encodeURIComponent(page.provision_id) + '/history')])
      .then(([choices, history]) => { const value = validate(choices, history, page); if (current) setData(value); })
      .catch(failure => { if (current && failure.name !== 'AbortError') { setData(null); setError(failure.message); } });
    return () => { current = false; controller.abort(); };
  }, [page]);
  const state = page.provision, pending = page.change_pending;
  const editable = !pending && !state.stopped && !['rejected', 'retired'].includes(state.status);
  const versions = data ? Object.entries(state.versions || {}).sort((a, b) => a[1].recorded_at.localeCompare(b[1].recorded_at)) : [];
  return <section aria-label="Versies van de huisvoorziening" style={{ marginTop: 20 }}>
    <h4>Versies en wijzigingen</h4>
    {pending && <div role="status">
      <p>De wijziging is nog niet volledig afgerond. De voorziening blijft tijdens het afronden gepauzeerd{state.stopped ? ' en gestopt' : ''}.</p>
      <button disabled={busy} onClick={() => submit(pending.undo ? 'version_undo' : 'version_change', pending.to_version)}>Wijziging afronden</button>
    </div>}
    {error && <p role="alert">{error}</p>}
    {!data && !error && <p>Versies ophalen…</p>}
    {data && <>
      {editable && data.choices.map(choice => <div key={choice.choice_id} style={{ marginBlock: 12 }}>
        <p>{choice.label}</p>
        <button disabled={busy} onClick={() => submit('version_stage', choice.choice_id)}>Wijziging klaarzetten: {choice.label}</button>
      </div>)}
      {editable && data.choices.length === 0 && <p>Er staan geen nieuwe beoordeelde wijzigingen klaar.</p>}
      {versions.length > 0 && <ul>{versions.map(([id, version], index) => <li key={id}>
        <span>{id === state.current_version ? 'Huidige versie' : id === state.previous_version ? 'Vorige versie' : 'Bewaarde versie ' + (index + 1)} · {date(version.recorded_at)}</span>
        {editable && id !== state.current_version && id !== state.previous_version
          && <button disabled={busy} onClick={() => submit('version_change', id)} style={{ marginLeft: 8 }}>Deze versie gebruiken</button>}
      </li>)}</ul>}
      {editable && state.previous_version && <button disabled={busy} onClick={() => submit('version_undo')}>Vorige versie terugzetten</button>}
      <details style={{ marginTop: 12 }}>
        <summary>Recente wijzigingen</summary>
        {data.history.entries.length === 0 ? <p>Er zijn nog geen wijzigingen.</p>
          : <ol>{[...data.history.entries].reverse().map(entry => <li key={entry.revision}>{events[entry.event] || 'Status bijgewerkt'} · {date(entry.at)}</li>)}</ol>}
        {data.history.earlier_entries > 0 && <p>Eerdere wijzigingen blijven bewaard.</p>}
      </details>
    </>}
  </section>;
}
