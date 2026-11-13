import React from 'react';

const MOOD_LABELS = {
  serene_observant: 'Sereen & Observerend',
  warm_engaged: 'Warm & Betrokken',
  deep_focus: 'Diepe Focus',
  curious_exploring: 'Nieuwsgierig & Onderzoekend',
  protective_attentive: 'Alert & Beschermend',
  resting_dreaming: 'Rustend & Dromend',
};

const DRIVES = [
  { key: 'energy', label: '⚡ Circadiaanse Energie', color: '#38bdf8' },
  { key: 'social_warmth', label: '💛 Sociale Warmte & Verbinding', color: '#f59e0b' },
  { key: 'curiosity', label: '🔭 Nieuwsgierigheid (AIDE²)', color: '#a855f7' },
  { key: 'household_harmony', label: '🏡 Huishoudelijke Harmonie', color: '#10b981' },
  { key: 'anticipation', label: '🎯 Voorspellende Aandacht', color: '#ec4899' },
];

export default function OwnerLivingCortex() {
  const [data, setData] = React.useState(null);
  const [notice, setNotice] = React.useState('');
  const [busy, setBusy] = React.useState(false);
  const [kind, setKind] = React.useState('gemini');
  const [model, setModel] = React.useState('gemini-2.5-pro');
  const [endpoint, setEndpoint] = React.useState('https://generativelanguage.googleapis.com/v1beta/openai');
  const keyRef = React.useRef(null);

  const load = React.useCallback(async () => {
    try {
      const res = await fetch('/internal/v1/dashboard/cortex/state', {
        credentials: 'same-origin',
        cache: 'no-store',
        redirect: 'error',
      });
      if (!res.ok) return;
      const json = await res.json();
      if (json?.contract === 'EvaLivingCortexState/v1') {
        setData(json);
      }
    } catch {
      // Gracefully ignore in isolated static test harnesses
    }
  }, []);

  React.useEffect(() => {
    void load();
  }, [load]);

  const handleKindChange = (nextKind) => {
    setKind(nextKind);
    if (nextKind === 'gemini') {
      setModel('gemini-2.5-pro');
      setEndpoint('https://generativelanguage.googleapis.com/v1beta/openai');
    } else if (nextKind === 'anthropic') {
      setModel('claude-sonnet-4-5');
      setEndpoint('https://api.anthropic.com');
    } else {
      setModel('gpt-4.1');
      setEndpoint('https://openrouter.ai/api/v1');
    }
  };

  const configureCloud = async (event) => {
    event.preventDefault();
    if (busy) return;
    const apiKey = (keyRef.current?.value || '').trim();
    if (keyRef.current) keyRef.current.value = '';
    if (!apiKey) {
      setNotice('Vul eerst een geldige API-sleutel in om de verbinding live te verifiëren.');
      return;
    }
    setBusy(true);
    setNotice('Verbinding versleuteld opslaan en live testen…');
    const labels = {
      gemini: 'Google Gemini Cloud',
      anthropic: 'Anthropic Claude Cloud',
      openai_compatible: 'OpenAI-compatibel',
    };
    try {
      const res = await fetch('/internal/v1/dashboard/cortex/cloud-provider', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          action: 'configure_and_activate',
          kind,
          label: labels[kind] || kind,
          model: model.trim(),
          endpoint: endpoint.trim(),
          api_key: apiKey,
          connection_id: '',
        }),
      });
      if (res.ok) {
        const json = await res.json();
        setData(json);
        setNotice(
          json?.cloud_providers?.selected_id
            ? `✓ ${labels[kind]} (${model.trim()}) geverifieerd en actief met Auto-Failover!`
            : 'Verbinding bewaard, maar live verificatie slaagde niet (controleer sleutel of modelnaam).'
        );
      } else {
        setNotice('Kon verbinding niet opslaan (controleer HTTPS-adres en velden).');
      }
    } catch {
      setNotice('Verbinding kon nu niet worden bevestigd.');
    } finally {
      setBusy(false);
    }
  };

  const manageConnection = async (action, connectionId) => {
    if (busy) return;
    setBusy(true);
    try {
      const res = await fetch('/internal/v1/dashboard/cortex/cloud-provider', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          action,
          connection_id: connectionId,
          kind: 'gemini',
          label: 'Cloud',
          model: 'gemini-2.5-pro',
          endpoint: '',
          api_key: '',
        }),
      });
      if (res.ok) {
        setData(await res.json());
        setNotice('Multi-Brein Cloud instelling bijgewerkt.');
      }
    } catch {
      // ignore
    } finally {
      setBusy(false);
    }
  };

  return (
    <section
      className="owner-panel"
      aria-label="Eva 3D Cockpit en Multi-Brein Cloud Beheer"
      style={{
        background: 'linear-gradient(145deg, #0b111e 0%, #101829 65%, #162238 100%)',
        color: '#f8fafc',
        border: '1px solid rgba(59, 130, 246, 0.32)',
        borderRadius: 16,
        padding: 'clamp(14px, 3vw, 22px)',
        marginBottom: 20,
        minWidth: 0,
        maxWidth: '100%',
        boxSizing: 'border-box',
        overflowWrap: 'anywhere',
        boxShadow: '0 16px 40px rgba(0, 0, 0, 0.28)',
      }}
    >
      <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', alignItems: 'center', gap: 14, marginBottom: 16, minWidth: 0 }}>
        <div style={{ minWidth: 0 }}>
          <span style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.12em', color: '#60a5fa', fontWeight: 700 }}>
            ✦ Eva AI Assistent · 3D Aanwezigheid, JEPA Wereldmodel &amp; Multi-Brein Cloud
          </span>
          <h2 style={{ margin: '4px 0 0', color: '#fff', fontSize: '1.35rem' }}>
            Van context naar actie.
          </h2>
        </div>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', minWidth: 0 }}>
          <a
            href="/internal/v1/dashboard/cortex"
            style={{
              display: 'inline-block',
              maxWidth: '100%',
              boxSizing: 'border-box',
              padding: '10px 16px',
              borderRadius: 12,
              background: 'linear-gradient(135deg, #9c4a35, #7f3927)',
              border: '1px solid rgba(251, 146, 60, 0.35)',
              color: '#fff',
              textDecoration: 'none',
              fontWeight: 700,
              fontSize: 14,
            }}
          >
            ↗ Open Eva 3D Dashboard (Live 3D Model &amp; Gesprek)
          </a>
        </div>
      </div>

      {!data ? (
        <p style={{ color: '#cbd5e1', margin: 0 }}>
          Open het 3D Eva Dashboard voor haar interactieve 3D-model, live gesprek, JEPA Wereldmodel, Titans O(1) Synaptisch Geheugen en Multi-Brein Cloud Failover (0% lokale GPU nodig).
        </p>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 300px), 1fr))', gap: 16, minWidth: 0 }}>
          <div style={{ background: 'rgba(19, 28, 46, 0.85)', border: '1px solid rgba(148, 163, 184, 0.16)', borderRadius: 14, padding: 14, minWidth: 0, boxSizing: 'border-box' }}>
            <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', alignItems: 'center', gap: 6, marginBottom: 8 }}>
              <strong style={{ color: '#7dd3fc' }}>🧠 Eva’s Stemming, Wereldmodel &amp; Synapsen</strong>
              <span style={{ background: 'rgba(16, 185, 129, 0.18)', color: '#6ee7b7', padding: '2px 10px', borderRadius: 999, fontSize: 12, fontWeight: 700 }}>
                {MOOD_LABELS[data.pulse.mood] || data.pulse.mood}
              </span>
            </div>
            <p style={{ fontStyle: 'italic', color: '#f8fafc', margin: '0 0 8px', fontSize: 14 }}>
              “{data.pulse.inner_monologue_nl}”
            </p>
            <p style={{ fontSize: 13, color: '#94a3b8', margin: '0 0 12px' }}>
              <strong>JEPA Traject:</strong> {data.pulse.world_model?.selected_action_id || '--'} ·{' '}
              <strong>Titans ‖M‖_F:</strong> {data.pulse.synaptic_memory?.frobenius_norm ?? '--'}
            </p>
            <div style={{ display: 'grid', gap: 7 }}>
              {DRIVES.map((d) => {
                const pct = Math.round((data.pulse.drives[d.key] || 0) * 100);
                return (
                  <div key={d.key}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: '#e2e8f0', marginBottom: 2, gap: 6 }}>
                      <span>{d.label}</span>
                      <strong>{pct}%</strong>
                    </div>
                    <div style={{ height: 6, background: '#0b111e', borderRadius: 999, overflow: 'hidden' }}>
                      <div style={{ width: `${pct}%`, height: '100%', background: d.color, borderRadius: 999 }} />
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          <div style={{ background: 'rgba(19, 28, 46, 0.85)', border: '1px solid rgba(59, 130, 246, 0.25)', borderRadius: 14, padding: 14, minWidth: 0, boxSizing: 'border-box' }}>
            <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', alignItems: 'center', gap: 6, marginBottom: 6 }}>
              <strong style={{ color: '#60a5fa' }}>☁️ Multi-Brein Cloud &amp; Optioneel Lokaal LLM</strong>
              <span style={{ fontSize: 11, color: '#6ee7b7', fontWeight: 600 }}>0% Lokale GPU Vereist</span>
            </div>
            {notice && <p role="status" style={{ color: '#6ee7b7', fontSize: 12, margin: '0 0 8px' }}>{notice}</p>}

            <div style={{ display: 'grid', gap: 8, marginBottom: 10, minWidth: 0 }}>
              <div style={{ padding: 10, borderRadius: 10, border: data.cloud_providers?.default_codex?.selected ? '1px solid #10b981' : '1px solid rgba(148,163,184,0.2)', background: '#0b111e', minWidth: 0 }}>
                <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
                  <strong style={{ fontSize: 13 }}>{data.cloud_providers?.default_codex?.label || 'OpenAI ChatGPT / Codex (OAuth)'}</strong>
                  {!data.cloud_providers?.default_codex?.selected && (
                    <button type="button" disabled={busy} onClick={() => manageConnection('use_default_codex', '')} style={{ fontSize: 11, padding: '4px 9px', borderRadius: 7, border: 'none', background: '#2563eb', color: '#fff', cursor: 'pointer' }}>
                      Maak Primair
                    </button>
                  )}
                </div>
                <div style={{ fontSize: 11, color: '#94a3b8', marginTop: 3 }}>{data.cloud_providers?.default_codex?.detail_nl}</div>
              </div>

              {(data.cloud_providers?.connections || []).map((conn) => (
                <div key={conn.id} style={{ padding: 10, borderRadius: 10, border: conn.selected ? '1px solid #10b981' : '1px solid rgba(148,163,184,0.2)', background: '#0b111e', minWidth: 0 }}>
                  <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
                    <strong style={{ fontSize: 13 }}>{conn.label} ({conn.model})</strong>
                    <span style={{ fontSize: 11, color: conn.check_state === 'ready' ? '#6ee7b7' : '#fcd34d' }}>
                      {conn.selected ? '✓ Primair' : conn.check_state}
                    </span>
                  </div>
                </div>
              ))}
            </div>

            <form onSubmit={configureCloud} style={{ display: 'grid', gap: 7, minWidth: 0 }}>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 140px), 1fr))', gap: 6, minWidth: 0 }}>
                <select value={kind} onChange={(e) => handleKindChange(e.target.value)} disabled={busy} style={{ minWidth: 0, width: '100%', boxSizing: 'border-box', padding: '6px 8px', borderRadius: 7, background: '#0b111e', color: '#fff', border: '1px solid rgba(148,163,184,0.3)', fontSize: 12 }}>
                  <option value="gemini">Google Gemini Cloud</option>
                  <option value="anthropic">Anthropic Claude Cloud</option>
                  <option value="openai_compatible">OpenAI-compatibel / Lokaal</option>
                </select>
                <input value={model} onChange={(e) => setModel(e.target.value)} disabled={busy} placeholder="Model" style={{ minWidth: 0, width: '100%', boxSizing: 'border-box', padding: '6px 8px', borderRadius: 7, background: '#0b111e', color: '#fff', border: '1px solid rgba(148,163,184,0.3)', fontSize: 12 }} />
              </div>
              <input value={endpoint} onChange={(e) => setEndpoint(e.target.value)} disabled={busy} placeholder="HTTPS Endpoint" style={{ minWidth: 0, width: '100%', boxSizing: 'border-box', padding: '6px 8px', borderRadius: 7, background: '#0b111e', color: '#fff', border: '1px solid rgba(148,163,184,0.3)', fontSize: 12 }} />
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, minWidth: 0 }}>
                <input ref={keyRef} type="password" disabled={busy} placeholder="Plak Gemini / Claude API-key…" autoComplete="new-password" style={{ flex: '1 1 160px', minWidth: 0, boxSizing: 'border-box', padding: '6px 8px', borderRadius: 7, background: '#0b111e', color: '#fff', border: '1px solid rgba(148,163,184,0.3)', fontSize: 12 }} />
                <button type="submit" disabled={busy} style={{ padding: '6px 12px', borderRadius: 7, border: 'none', background: '#2563eb', color: '#fff', fontWeight: 600, fontSize: 12, cursor: 'pointer' }}>
                  Activeer
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </section>
  );
}
