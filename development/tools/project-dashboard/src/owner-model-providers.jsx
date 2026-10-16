import React from 'react';

const endpoint = '/internal/v1/dashboard/model-providers';
const kinds = { anthropic: 'Claude / Anthropic', openai_compatible: 'OpenAI-compatible API' };
const checkLabels = { unchecked: 'Nog niet gecontroleerd', ready: 'Modelcontrole geslaagd', failed: 'Modelcontrole niet geslaagd' };
const initialForm = { kind: 'anthropic', label: '', model: '', endpoint: 'https://api.anthropic.com', workspace_id: '' };
const fieldStyle = { display: 'block', width: '100%', boxSizing: 'border-box', minWidth: 0, padding: '.5rem', font: 'inherit' };
const text = (value, maximum = 1024) => typeof value === 'string' && value.length <= maximum;
function validStatus(value) {
  if (value?.contract !== 'EvaModelProviders/v1' || value.scope !== 'private_text_conversations'
    || !(value.selected_id === null || text(value.selected_id, 160))
    || !text(value.default?.label, 200) || typeof value.default.ready !== 'boolean' || !text(value.default.state, 160)
    || !text(value.meaning, 4000) || !Array.isArray(value.connections) || value.connections.length > 32
    || !Array.isArray(value.adapters) || value.adapters.length > 8) return false;
  // The browser never needs a credential, including an echoed configure input.
  const containsSecret = item => item && typeof item === 'object' && Object.entries(item).some(([key, nested]) =>
    /^(api_key|secret|password|access_token|refresh_token)$/i.test(key) || containsSecret(nested));
  if (containsSecret(value)) return false;
  if (!value.adapters.every(adapter => Object.hasOwn(kinds, adapter.kind)
    && text(adapter.label, 200) && typeof adapter.installed === 'boolean')) return false;
  if (!value.connections.every(connection => text(connection.id, 160) && connection.id.length > 0
    && text(connection.label, 200) && Object.hasOwn(kinds, connection.kind) && text(connection.model, 200)
    && text(connection.endpoint, 1024) && typeof connection.enabled === 'boolean'
    && (connection.checked_at === null || (text(connection.checked_at, 80) && Number.isFinite(Date.parse(connection.checked_at))))
    && Object.hasOwn(checkLabels, connection.check_state) && typeof connection.selected === 'boolean'
    && connection.selected === (connection.id === value.selected_id))) return false;
  return new Set(value.connections.map(connection => connection.id)).size === value.connections.length
    && (value.selected_id === null || value.connections.some(connection => connection.id === value.selected_id && connection.enabled));
}

export default function OwnerModelProviders() {
  const [page, setPage] = React.useState(null), [form, setForm] = React.useState(initialForm);
  const [busy, setBusy] = React.useState(false), [error, setError] = React.useState(''), [notice, setNotice] = React.useState('');
  const keyInput = React.useRef(null), request = React.useRef(null), sequence = React.useRef(0), mounted = React.useRef(false), inFlight = React.useRef(false);
  const clearKey = () => { if (keyInput.current) keyInput.current.value = ''; };
  const send = React.useCallback(async (operation, payload) => {
    request.current?.abort(); const controller = new AbortController(); request.current = controller;
    const current = ++sequence.current;
    setPage(null); setError(''); setNotice(''); setBusy(true); inFlight.current = true;
    try {
      if (document.hidden) return;
      const options = { credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: controller.signal };
      if (operation) {
        options.method = 'POST'; options.headers = { 'content-type': 'application/json' };
        options.body = JSON.stringify(payload);
        if (Object.hasOwn(payload, 'api_key')) payload.api_key = '';
      }
      const response = await fetch(operation ? endpoint + '/' + operation : endpoint, options);
      if (!response.ok) throw new Error();
      const value = await response.json();
      if (!validStatus(value)) throw new Error();
      if (!mounted.current || controller.signal.aborted || current !== sequence.current || document.hidden) return;
      setPage(value);
      if (operation) setNotice(({ configure: 'Verbinding privé bewaard. Er is nog geen modelcontrole of selectie uitgevoerd.',
        check: 'Controle afgerond. Bekijk de opgeslagen uitkomst bij de verbinding.',
        select: 'De keuze voor privétekstgesprekken is opgeslagen.', disable: 'Verbinding uitgeschakeld. Bekijk hierboven welke route nu gekozen is.' })[operation]);
    } catch {
      if (mounted.current && !controller.signal.aborted && current === sequence.current) {
        clearKey(); setForm(initialForm); setPage(null);
        setError('Modelverbindingen zijn nu niet beschikbaar of de handeling is niet bevestigd. Vernieuw eerst; stuur een controle niet automatisch opnieuw.');
      }
    } finally {
      if (mounted.current && current === sequence.current) { setBusy(false); inFlight.current = false; }
    }
  }, []);
  React.useEffect(() => {
    mounted.current = true;
    const visibility = () => {
      ++sequence.current; request.current?.abort(); clearKey(); setForm(initialForm);
      setPage(null); setError(''); setNotice(''); setBusy(false); inFlight.current = false;
      if (!document.hidden) void send();
    };
    const focus = () => { if (!document.hidden && !inFlight.current) void send(); };
    if (!document.hidden) void send();
    document.addEventListener('visibilitychange', visibility); window.addEventListener('focus', focus);
    return () => { mounted.current = false; ++sequence.current; request.current?.abort(); clearKey();
      document.removeEventListener('visibilitychange', visibility); window.removeEventListener('focus', focus); };
  }, [send]);
  const change = event => setForm(current => ({ ...current, [event.target.name]: event.target.value }));
  const configuredAdapter = page?.adapters.find(adapter => adapter.kind === form.kind);
  function configure(event) {
    event.preventDefault();
    const payload = { ...form, label: form.label.trim(), model: form.model.trim(), endpoint: form.endpoint.trim(),
      workspace_id: form.workspace_id.trim(), api_key: keyInput.current?.value || '' };
    clearKey();
    if (!busy) void send('configure', payload);
  }
  return <section className="owner-panel" aria-label="Optionele modelverbindingen">
    <h2>Optionele modelverbindingen</h2>
    <p>Deze keuze geldt voor privétekstgesprekken op deze website en Telegram. Het blijft dezelfde Eva, met hetzelfde geheugen en dezelfde opdrachten.</p>
    <p>Inhoudelijk achtergrondwerk en eigen initiatief behouden de bestaande Astra-route. Spraak heeft haar eigen bestaande verbinding.</p>
    <button type="button" disabled={busy} onClick={() => send()}>Modelstatus vernieuwen</button>
    {busy && <p role="status">Modelverbindingen ophalen…</p>}
    {error && <p role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    {page && <>
      <div aria-label="Gekozen tekstmodel">
        <h3>Gekozen voor privétekstgesprekken</h3>
        <p><strong>{page.selected_id === null ? page.default.label : page.connections.find(connection => connection.id === page.selected_id)?.label}</strong></p>
        <p>Bestaande route: {page.default.label} · {page.default.ready ? 'toegang beschikbaar' : 'toegang nu niet beschikbaar'} ({page.default.state}). Dit is geen nieuwe modelcontrole.</p>
        <button type="button" disabled={busy || page.selected_id === null} onClick={() => send('select', { id: null })}>Gebruik bestaande modelroute</button>
      </div>
      <p>{page.meaning}</p>
      <ul>{page.adapters.map(adapter => <li key={adapter.kind}>{adapter.label}: {adapter.installed ? 'adapter geïnstalleerd' : 'adapter ontbreekt; aansluiten nog niet beschikbaar'}</li>)}</ul>
      {!page.connections.length && <p>Nog geen optionele modelverbinding bewaard. De bestaande route blijft gekozen.</p>}
      <div className="board">{page.connections.map(connection => <article className="card" aria-label={'Modelverbinding: ' + connection.label} key={connection.id} style={{ overflowWrap: 'anywhere' }}>
        <h3>{connection.label}</h3><p>{kinds[connection.kind]} · {connection.model}</p><p>{connection.endpoint}</p>
        <p><strong>{connection.selected ? 'Gekozen' : connection.enabled ? 'Beschikbaar om te kiezen' : 'Uitgeschakeld'}</strong> · {checkLabels[connection.check_state]}</p>
        {connection.checked_at && <p>Laatste controle: {new Date(connection.checked_at).toLocaleString('nl-NL')}. Dit is een opgeslagen meting.</p>}
        <p>Controleren stuurt één klein modelverzoek, inclusief gereedschapscontrole. Dit kan providerverbruik en kosten veroorzaken. Er worden hierbij geen persoonlijke gesprekken of hulpmiddelen uitgevoerd.</p>
        <div className="memory-actions">
          <button type="button" disabled={busy || !connection.enabled || !page.adapters.some(adapter => adapter.kind === connection.kind && adapter.installed)} onClick={() => send('check', { id: connection.id, allow_provider_request: true })}>Controleer met één modelantwoord</button>
          <button type="button" disabled={busy || connection.selected || !connection.enabled || connection.check_state !== 'ready' || !page.adapters.some(adapter => adapter.kind === connection.kind && adapter.installed)} onClick={() => send('select', { id: connection.id })}>Kies voor privétekstgesprekken</button>
          <button type="button" disabled={busy || !connection.enabled} onClick={() => send('disable', { id: connection.id })}>Verbinding uitschakelen</button>
        </div>
      </article>)}</div>
      <form onSubmit={configure} style={{ maxWidth: 680 }} aria-label="Modelverbinding toevoegen">
        <h3>Modelverbinding toevoegen</h3>
        <p>Gebruik een eigen API-sleutel. Een Claude-abonnement wordt hier niet als API-aanmelding gebruikt. Bewaren start geen modelverzoek.</p>
        <label>Provider<select aria-label="Provider" name="kind" value={form.kind} disabled={busy} style={fieldStyle} onChange={event => {
          clearKey(); setForm({ ...initialForm, kind: event.target.value, endpoint: event.target.value === 'anthropic' ? 'https://api.anthropic.com' : '' });
        }}>{Object.entries(kinds).map(([kind, label]) => <option key={kind} value={kind}>{label}</option>)}</select></label>
        <label>Naam voor deze verbinding<input name="label" value={form.label} onChange={change} required maxLength={80} disabled={busy} style={fieldStyle} autoComplete="off" /></label>
        <label>Model-ID<input name="model" value={form.model} onChange={change} required maxLength={160} disabled={busy} style={fieldStyle} autoComplete="off" spellCheck={false} /></label>
        <label>API-adres<input name="endpoint" type="url" value={form.endpoint} onChange={change} required maxLength={500} disabled={busy || form.kind === 'anthropic'} style={fieldStyle} autoComplete="off" spellCheck={false} /></label>
        {form.kind === 'openai_compatible' && <p>Kies bewust het HTTPS-adres van jouw provider, inclusief het API-pad, bijvoorbeeld /v1. Privégesprekscontext wordt naar de gekozen provider gestuurd.</p>}
        {form.kind === 'anthropic' && <label>Workspace-ID, indien vereist<input name="workspace_id" value={form.workspace_id} onChange={change} maxLength={160} disabled={busy} style={fieldStyle} autoComplete="off" spellCheck={false} /></label>}
        <label>API-sleutel<input ref={keyInput} name="api_key" type="password" required minLength={16} maxLength={8192} disabled={busy} style={fieldStyle} autoComplete="new-password" spellCheck={false} /></label>
        <p>De sleutel wordt na versturen uit dit veld gewist en wordt niet teruggetoond. Uitschakelen stopt nieuwe aanvragen via deze verbinding; een al gestarte aanvraag kan nog afronden.</p>
        {!configuredAdapter?.installed && <p>Deze adapter is nog niet geïnstalleerd. Er wordt geen werkende verbinding voorgesteld.</p>}
        <button type="submit" disabled={busy || !configuredAdapter?.installed}>Verbinding privé bewaren</button>
      </form>
    </>}
  </section>;
}
