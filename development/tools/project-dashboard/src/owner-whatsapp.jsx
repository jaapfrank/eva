import React from 'react';

const endpoint = '/internal/v1/dashboard/whatsapp';
const states = { disabled: 'Uitgeschakeld', awaiting_qr: 'Scan de QR-code', connecting: 'Verbinden…', connected: 'Verbonden', disconnected: 'Verbinding verbroken', logged_out: 'Opnieuw aanmelden nodig' };

export default function OwnerWhatsApp() {
  const [page, setPage] = React.useState(null), [phone, setPhone] = React.useState('');
  const [audio, setAudio] = React.useState(false), [error, setError] = React.useState(''), [busy, setBusy] = React.useState(false);
  const mounted = React.useRef(false), pending = React.useRef(false), controller = React.useRef(null), dirty = React.useRef(false);
  const load = React.useCallback(async (operation, payload) => {
    if (pending.current || document.hidden) return;
    pending.current = true; setBusy(Boolean(operation));
    const request = new AbortController(); controller.current = request;
    try {
      const response = await fetch(endpoint + (operation ? '/' + operation : ''), {
        credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: request.signal,
        ...(operation ? { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(payload) } : {}),
      });
      if (!response.ok) throw new Error();
      const value = await response.json();
      if (value?.contract !== 'EvaWhatsApp/v1' || !Object.hasOwn(states, value.connection)
          || typeof value.enabled !== 'boolean' || typeof value.audio_enabled !== 'boolean'
          || !(value.contact_phone === null || /^[1-9][0-9]{6,14}$/.test(value.contact_phone))
          || !(value.qr_data_url === null || (typeof value.qr_data_url === 'string'
            && value.qr_data_url.length < 100000 && /^data:image\/png;base64,[A-Za-z0-9+/=]+$/.test(value.qr_data_url)))) throw new Error();
      if (!mounted.current || document.hidden) return;
      setPage(value); setError('');
      if (operation === 'configure') dirty.current = false;
      if (!dirty.current) { setPhone(value.contact_phone || ''); setAudio(value.audio_enabled); }
    } catch {
      if (mounted.current && !request.signal.aborted) { setPage(null); setError('WhatsApp-status niet beschikbaar. Vernieuw eerst; een handeling wordt niet automatisch herhaald.'); }
    } finally { pending.current = false; if (mounted.current) setBusy(false); }
  }, []);
  React.useEffect(() => {
    mounted.current = true; void load();
    const interval = setInterval(() => void load(), 3000);
    const hide = () => { controller.current?.abort(); setPage(null); setPhone(''); dirty.current = false; if (!document.hidden) void load(); };
    document.addEventListener('visibilitychange', hide);
    return () => { mounted.current = false; clearInterval(interval); controller.current?.abort(); document.removeEventListener('visibilitychange', hide); };
  }, [load]);
  return <section aria-labelledby="whatsapp-heading" style={{ border: '1px solid #ccd8d0', borderRadius: 16, padding: 24, margin: '24px 0' }}>
    <h2 id="whatsapp-heading">WhatsApp · Eva’s eigen nummer</h2>
    <p>Bel en chat met dezelfde Eva via haar eigen WhatsApp-account. Hiervoor heeft Eva een apart telefoonnummer nodig dat een verificatie-sms of oproep kan ontvangen.</p>
    {error && <p role="alert">{error}</p>}
    {page && <>
      <p role="status" style={{ color: page.connection === 'connected' ? '#176438' : '#855800', fontWeight: 700 }}>{states[page.connection]}</p>
      <p><strong>Eva’s nummer:</strong> {page.account_phone ? `+${page.account_phone}` : 'Nog niet gekoppeld'}. <strong>Jouw toegestane nummer:</strong> {page.contact_phone ? `+${page.contact_phone}` : 'Nog niet ingesteld'}.</p>
      <p>1. Registreer Eva’s aparte nummer in WhatsApp op een telefoon. 2. Bewaar hieronder jouw eigen nummer als toegestaan contact. 3. Zet live bellen aan en verbind. Scan de code vanuit <strong>Eva’s account</strong>. Een WhatsApp Business Platform-account is voor deze koppeling niet nodig.</p>
      <form onSubmit={event => { event.preventDefault(); void load('configure', { contact_phone: phone.replace(/^\+/, ''), audio_enabled: audio }); }}>
        <label>Jouw WhatsApp-nummer met landcode <input aria-label="WhatsApp-nummer" type="tel" autoComplete="off" value={phone} placeholder="31612345678" onChange={event => { dirty.current = true; setPhone(event.target.value); }} required pattern="\+?[1-9][0-9]{6,14}" /></label>
        <p><label><input type="checkbox" checked={audio} onChange={event => { dirty.current = true; setAudio(event.target.checked); }} /> Live bellen met Eva toestaan</label></p>
        <button disabled={busy} type="submit">Contact en belkeuze bewaren</button>
      </form>
      <p><strong>Bellen:</strong> {page.audio_enabled ? 'toegestaan' : 'uitgeschakeld'} · {page.call_state === 'active' ? 'Gesprek actief' : page.call_state === 'connecting' ? 'Gesprek verbinden…' : page.media_ready ? 'Bestaande Eva-spraakservice beschikbaar' : 'Spraakservice nu niet beschikbaar'}. {page.features?.live_calls !== true && 'Deze adapter ondersteunt nog geen live gesprekken.'}</p>
      <p><button disabled={busy || page.enabled || !page.contact_phone} onClick={() => void load('enable', {})}>WhatsApp verbinden</button>{' '}
        <button disabled={busy || !page.enabled} onClick={() => void load('disable', {})}>Uitschakelen</button>{' '}
        <button disabled={busy} onClick={() => void load('logout', {})}>Afmelden en koppeling wissen</button></p>
      {page.qr_data_url && <div><p>Open WhatsApp met <strong>Eva’s aparte account</strong>: <strong>Instellingen → Gekoppelde apparaten → Apparaat koppelen</strong>. Scan deze code. Scan niet met je persoonlijke account: Eva moet een eigen contact zijn.</p><img alt="WhatsApp-koppelcode" src={page.qr_data_url} width="280" height="280" /></div>}
      {page.connection === 'connected' && <p>Bel Eva’s nummer vanaf jouw ingestelde contact, of stuur haar een bericht. Eva gebruikt haar bestaande gesprek, geheugen en achtergrondwerk. Achtergrondresultaten komen als korte samenvatting terug bij de oorspronkelijke vraag.</p>}
      <p>Deze optionele koppeling gebruikt de opensourcebibliotheken Hypermeow en Meowcaller en is geen officiële Meta Business API. Alleen jouw ingestelde contact wordt verwerkt; geen groepen, video of oude chatgeschiedenis. Uitschakelen beëindigt ook een lopend gesprek. Reeds begonnen achtergrondwerk blijft zichtbaar bij Opdrachten.</p>
    </>}
  </section>;
}
