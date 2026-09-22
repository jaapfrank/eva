import React from 'react';

const endpoint = '/internal/v1/dashboard/work';
const labels = { admitted: 'Ingepland', call_started: 'Bezig', stopped: 'Gestopt',
  succeeded: 'Resultaat klaar', verified: 'Resultaat gecontroleerd', failed: 'Mislukt',
  needs_help: 'Hulp nodig', ambiguous: 'Uitkomst onzeker', expired: 'Verlopen' };
const resumable = new Set(['stopped', 'failed', 'needs_help', 'ambiguous', 'expired']);
function validWork(work, detail = false) {
  return work && typeof work.work_id === 'string' && Object.hasOwn(labels, work.state)
    && Number.isInteger(work.generation) && typeof work.stop_requested === 'boolean'
    && typeof work.result_available === 'boolean'
    && typeof work[detail ? 'task' : 'task_preview'] === 'string'
    && (!detail || work.result === null || typeof work.result?.output === 'string');
}
function readable(output) {
  try {
    const value = JSON.parse(output);
    if (Object.keys(value).sort().join(',') === 'findings,summary'
      && typeof value.summary === 'string' && Array.isArray(value.findings)
      && value.findings.every(item => typeof item === 'string')) {
      return value.summary + '\n\n' + value.findings.map(item => '- ' + item).join('\n');
    }
  } catch { /* Unknown output remains visible verbatim as text. */ }
  return output;
}
async function request(operation, workId, signal) {
  const response = await fetch(operation === 'list' ? endpoint : `${endpoint}/${operation}`, {
    method: operation === 'list' ? 'GET' : 'POST', credentials: 'same-origin',
    cache: 'no-store', redirect: 'error', signal,
    ...(operation === 'list' ? {} : { headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ work_id: workId }) }),
  });
  if (!response.ok) throw new Error('Werkoverzicht niet beschikbaar. Vernieuw om de opgeslagen toestand op te halen.');
  const data = await response.json();
  if (data.contract !== 'EvaDelegatedWorkOwnerResult/v1' || data.operation !== operation
    || (operation === 'list' ? !Array.isArray(data.jobs) || data.jobs.length > 20
      || !data.jobs.every(work => validWork(work)) || typeof data.truncated !== 'boolean'
      : !validWork(data.work, true) || (operation !== 'resume' && data.work.work_id !== workId))) {
    throw new Error('Het werkantwoord kon niet worden gelezen. Vernieuw het overzicht.');
  }
  return data;
}

export default function OwnerWork() {
  const [page, setPage] = React.useState(null);
  const [detail, setDetail] = React.useState(null);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState('');
  const [notice, setNotice] = React.useState('');
  const sequence = React.useRef(0);
  const refresh = React.useCallback(async (signal) => {
    const id = ++sequence.current;
    setPage(null); setDetail(null); setBusy(true); setError('');
    try {
      const data = await request('list', undefined, signal);
      if (sequence.current === id) setPage(data);
    } catch (e) { if (sequence.current === id && e.name !== 'AbortError') setError(e.message); }
    finally { if (sequence.current === id) setBusy(false); }
  }, []);
  React.useEffect(() => {
    const controller = new AbortController();
    refresh(controller.signal);
    return () => { ++sequence.current; controller.abort(); };
  }, [refresh]);
  async function act(operation, workId) {
    const id = ++sequence.current;
    setBusy(true); setError(''); setNotice(''); setDetail(null);
    try {
      const data = await request(operation, workId);
      // Reload after mutations: the list is never updated optimistically.
      const updated = ['stop', 'resume'].includes(operation) ? await request('list') : null;
      if (sequence.current !== id) return;
      if (updated) setPage(updated);
      setDetail(data.work);
      if (operation === 'stop') setNotice(data.work.stop_requested
        ? 'Stopverzoek opgeslagen. Een lopende berekening kan nog aflopen; het resultaat wordt niet geleverd.'
        : 'Deze taak was al afgerond.');
      if (operation === 'resume') setNotice(data.status === 'not_resumable'
        ? 'Geen nieuwe poging ingepland: de taak is nog actief of al geslaagd.'
        : `${data.created ? 'Nieuwe poging ingepland.' : 'Bestaande vervolg poging geopend.'} De vorige poging blijft bewaard.`);
    } catch (e) {
      if (sequence.current !== id) return;
      setPage(null); setDetail(null); setError(e.message);
    } finally { if (sequence.current === id) setBusy(false); }
  }
  return <section aria-label="Mijn hulpopdrachten">
    <h2>Mijn hulpopdrachten</h2>
    <p>Opdrachten aan Eva's onderzoeker, met hun opgeslagen status en resultaat.</p>
    <button disabled={busy} onClick={() => refresh()}>Werk vernieuwen</button>
    {error && <p role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    {busy && <p role="status">Werk ophalen…</p>}
    {page && !page.jobs.length && <p>Nog geen hulpopdracht beschikbaar.</p>}
    <div className="board">{page?.jobs.map(job => <article className="card" key={job.work_id}>
      <h3>{job.task_preview}</h3><p>{labels[job.state]} · poging {job.generation}</p>
      {job.stop_requested && <p>Stopverzoek opgeslagen</p>}
      <button disabled={busy} onClick={() => act('get', job.work_id)}>Opdracht openen</button>
    </article>)}</div>
    {page?.truncated && <p>De twintig meest recente opdrachten worden getoond.</p>}
    {detail && <article aria-label="Geopende hulpopdracht" className="card">
      <h3>Opdracht</h3><p style={{ whiteSpace: 'pre-wrap' }}>{detail.task}</p>
      <p>{labels[detail.state]} · poging {detail.generation}</p>
      {['admitted', 'call_started'].includes(detail.state) && !detail.stop_requested
        && <button disabled={busy} onClick={() => act('stop', detail.work_id)}>Stop deze hulpopdracht</button>}
      {resumable.has(detail.state) && <>
        <p>Hervatten start een nieuwe begrensde poging en kan opnieuw modelverbruik opleveren. De eerdere poging blijft bewaard.</p>
        <button disabled={busy} onClick={() => act('resume', detail.work_id)}>Hervatten met nieuwe poging</button>
      </>}
      <h3>Opgeslagen resultaat</h3>
      {detail.result_available && detail.result
        ? <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{readable(detail.result.output)}</pre>
        : <p>Geen afgerond resultaat beschikbaar.</p>}
    </article>}
  </section>;
}
