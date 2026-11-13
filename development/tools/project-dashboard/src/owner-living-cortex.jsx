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

  const selectProvider = async (providerId) => {
    if (busy) return;
    setBusy(true);
    setNotice('');
    try {
      const res = await fetch('/internal/v1/dashboard/cortex/subscription', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider_id: providerId, auto_fallback: true }),
      });
      if (res.ok) {
        const json = await res.json();
        setData(json);
        setNotice(`Primair abonnements-LLM gewijzigd naar ${providerId} (zonder API-sleutel).`);
      }
    } catch {
      setNotice('Wisselen van abonnements-LLM kon nu niet worden bevestigd.');
    } finally {
      setBusy(false);
    }
  };

  const triggerStimulus = async (kind, mode) => {
    if (busy) return;
    setBusy(true);
    try {
      const payload = { kind, intensity: 0.75 };
      if (kind === 'resident_feedback') {
        payload.actual_receptivity = mode === 'busy' ? 0.22 : 0.90;
        payload.observed_mood = mode === 'busy' ? 'kortaf / druk bezig' : 'open & ontspannen';
        payload.context_hypothesis = mode === 'busy'
          ? 'Druk bezig — Eva houdt automatisch afstand (Active Inference bijstelling)'
          : 'Ruimte voor proactief meedenken en warme interactie';
      }
      const res = await fetch('/internal/v1/dashboard/cortex/stimulus', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (res.ok) {
        setData(await res.json());
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
      aria-label="Eva Levende Bewustzijnskern en Abonnements-LLM Cockpit"
      style={{
        background: 'linear-gradient(145deg, #0b1120 0%, #111827 65%, #1e1b4b 100%)',
        color: '#f1f5f9',
        border: '1px solid rgba(56, 189, 248, 0.35)',
        borderRadius: 18,
        padding: 22,
        marginBottom: 20,
        boxShadow: '0 16px 40px rgba(0, 0, 0, 0.28)',
      }}
    >
      <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', alignItems: 'center', gap: 12, marginBottom: 16 }}>
        <div>
          <span style={{ fontSize: 12, textTransform: 'uppercase', letterSpacing: '0.08em', color: '#38bdf8', fontWeight: 700 }}>
            Living Entity Cortex · Pydantic v2 Rust + C/SIMD Kern
          </span>
          <h2 style={{ margin: '4px 0 0', color: '#fff', fontSize: '1.3rem' }}>
            Eva’s Innerlijke Belevingswereld, Gezinsbeeld & Abonnements-LLM’s
          </h2>
        </div>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
          <a
            href="/internal/v1/dashboard/cortex"
            style={{
              padding: '9px 15px',
              borderRadius: 10,
              background: 'linear-gradient(135deg, #0284c7, #7c3aed)',
              color: '#fff',
              textDecoration: 'none',
              fontWeight: 600,
              fontSize: 14,
            }}
          >
            🌌 Open Volledige Sci-Fi Cortex →
          </a>
        </div>
      </div>

      {!data ? (
        <p style={{ color: '#cbd5e1', margin: 0 }}>
          Open de volledige Sci-Fi Cortex voor realtime bio-homeostase, Theory-of-Mind gezinskaartjes en 1-klik abonnements-LLM selectie (Gemini Argon, Claude Pro/Max, OpenAI Codex zonder API-keys).
        </p>
      ) : (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 340px), 1fr))', gap: 16, marginBottom: 18 }}>
            <div style={{ background: 'rgba(30, 41, 59, 0.65)', border: '1px solid rgba(56, 189, 248, 0.25)', borderRadius: 14, padding: 16 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                <strong style={{ color: '#7dd3fc' }}>🧠 Hoe Eva zich nu voelt</strong>
                <span style={{ background: 'rgba(16, 185, 129, 0.2)', color: '#6ee7b7', padding: '2px 10px', borderRadius: 999, fontSize: 12, fontWeight: 700 }}>
                  {MOOD_LABELS[data.pulse.mood] || data.pulse.mood}
                </span>
              </div>
              <p style={{ fontStyle: 'italic', color: '#f8fafc', margin: '0 0 8px' }}>
                “{data.pulse.inner_monologue_nl}”
              </p>
              <p style={{ fontSize: 13, color: '#cbd5e1', margin: '0 0 12px' }}>
                <strong>Plan / intentie:</strong> {data.pulse.planned_intent_nl}
              </p>
              <div style={{ display: 'grid', gap: 8 }}>
                {DRIVES.map((d) => {
                  const pct = Math.round((data.pulse.drives[d.key] || 0) * 100);
                  return (
                    <div key={d.key}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, color: '#e2e8f0', marginBottom: 3 }}>
                        <span>{d.label}</span>
                        <strong>{pct}%</strong>
                      </div>
                      <div style={{ height: 6, background: 'rgba(15, 23, 42, 0.9)', borderRadius: 999, overflow: 'hidden' }}>
                        <div style={{ width: `${pct}%`, height: '100%', background: d.color, borderRadius: 999 }} />
                      </div>
                    </div>
                  );
                })}
              </div>
              <div style={{ marginTop: 12, display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                <button type="button" disabled={busy} onClick={() => triggerStimulus('conversation_turn')} style={{ fontSize: 12, padding: '5px 10px', borderRadius: 8, border: '1px solid #38bdf8', background: 'rgba(56, 189, 248, 0.15)', color: '#fff', cursor: 'pointer' }}>
                  ✨ Warm contact
                </button>
                <button type="button" disabled={busy} onClick={() => triggerStimulus('resident_feedback', 'busy')} style={{ fontSize: 12, padding: '5px 10px', borderRadius: 8, border: '1px solid #f59e0b', background: 'rgba(245, 158, 11, 0.15)', color: '#fff', cursor: 'pointer' }}>
                  ⚡ Druk/Kortaf (Prediction Error)
                </button>
              </div>
            </div>

            <div style={{ background: 'rgba(30, 41, 59, 0.65)', border: '1px solid rgba(16, 185, 129, 0.28)', borderRadius: 14, padding: 16 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                <strong style={{ color: '#6ee7b7' }}>⚡ 1-Klik Abonnements-LLM Kiezer</strong>
                <span style={{ fontSize: 12, color: '#94a3b8' }}>Geen API-sleutels nodig</span>
              </div>
              {notice && <p role="status" style={{ color: '#6ee7b7', fontSize: 13, margin: '0 0 8px' }}>{notice}</p>}
              <div style={{ display: 'grid', gap: 10 }}>
                {data.subscriptions.providers.map((prov) => (
                  <div
                    key={prov.id}
                    style={{
                      padding: 11,
                      borderRadius: 10,
                      border: prov.selected ? '1px solid #10b981' : '1px solid rgba(148, 163, 184, 0.2)',
                      background: prov.selected ? 'rgba(16, 185, 129, 0.12)' : 'rgba(15, 23, 42, 0.55)',
                    }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
                      <strong style={{ fontSize: 14, color: '#fff' }}>{prov.name}</strong>
                      <button
                        type="button"
                        disabled={busy || prov.selected}
                        onClick={() => selectProvider(prov.id)}
                        style={{
                          fontSize: 12,
                          padding: '5px 11px',
                          borderRadius: 8,
                          border: 'none',
                          background: prov.selected ? '#059669' : '#0284c7',
                          color: '#fff',
                          fontWeight: 600,
                          cursor: prov.selected ? 'default' : 'pointer',
                        }}
                      >
                        {prov.selected ? '✓ Actief' : 'Kies Abonnement'}
                      </button>
                    </div>
                    <div style={{ fontSize: 12, color: '#94a3b8', marginTop: 4 }}>
                      {prov.subscription_plan} · {prov.status_detail_nl}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 280px), 1fr))', gap: 12 }}>
            {(data.pulse.residents || []).map((r) => (
              <div key={r.principal_id} style={{ background: 'rgba(30, 41, 59, 0.5)', border: '1px solid rgba(148, 163, 184, 0.2)', borderRadius: 12, padding: 12 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <strong style={{ color: '#fff', fontSize: 14 }}>👁️ Hoe Eva {r.display_name} ziet</strong>
                  <span style={{ fontSize: 12, color: '#7dd3fc' }}>{r.perceived_mood}</span>
                </div>
                <p style={{ fontSize: 12, color: '#cbd5e1', margin: '6px 0' }}>{r.current_context_hypothesis}</p>
                <div style={{ fontSize: 12, color: '#94a3b8' }}>
                  Energie: <strong style={{ color: '#38bdf8' }}>{Math.round(r.perceived_energy * 100)}%</strong> · Ontvankelijkheid: <strong style={{ color: '#10b981' }}>{Math.round(r.social_receptivity * 100)}%</strong> · Prediction Error: <strong style={{ color: '#f59e0b' }}>{Math.round((r.last_prediction_error || 0) * 100)}%</strong>
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </section>
  );
}
