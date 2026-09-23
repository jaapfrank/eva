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
  const commands = React.useRef(() => {});
  React.useEffect(() => {
    let mounted = true, running = false, halted = false, pending = null;
    let selected = null, currentDetail = null, revision = 0, timer;
    const controller = new AbortController();
    const fingerprint = work => JSON.stringify([work.work_id, work.state,
      work.generation, work.stop_requested, work.result_available,
      work.delivery?.state, work.delivery?.description]);
    function clearPrivate() {
      selected = null; currentDetail = null; pending = null;
      setPage(null); setDetail(null); setNotice('');
    }
    function schedule() {
      clearTimeout(timer);
      if (mounted && !halted && !document.hidden) {
        timer = setTimeout(() => enqueue('list'), 5000);
      }
    }
    async function drain() {
      if (running || !pending || !mounted) return;
      running = true;
      clearTimeout(timer);
      while (pending && mounted) {
        const { operation, workId, version, foreground } = pending;
        pending = null;
        if (foreground) setBusy(true);
        setError('');
        try {
          const data = await request(operation, workId, controller.signal);
          if (!mounted) return;
          // Even superseded requests must enforce access failures in catch.
          if (version !== revision) continue;
          if (operation === 'list') {
            setPage(data);
            const job = data.jobs.find(item => item.work_id === selected);
            if (selected && (!currentDetail || (job
              ? fingerprint(job) !== fingerprint(currentDetail)
              : ['admitted', 'call_started'].includes(currentDetail.state)))) {
              const result = await request('get', selected, controller.signal);
              if (!mounted) return;
              if (version !== revision) continue;
              currentDetail = result.work;
              setDetail(currentDetail);
            }
          } else {
            currentDetail = data.work;
            selected = data.work.work_id;
            setDetail(currentDetail);
            if (operation === 'stop') setNotice(data.work.stop_requested
              ? 'Stopverzoek opgeslagen. Een lopende berekening kan nog aflopen; het resultaat wordt niet geleverd.'
              : data.work.state === 'stopped' ? 'Gestopt voordat een modelcall begon.' : 'Deze taak was al afgerond.');
            if (operation === 'resume') setNotice(data.status === 'not_resumable'
              ? 'Geen nieuwe poging ingepland: de taak is nog actief of al geslaagd.'
              : `${data.created ? 'Nieuwe poging ingepland.' : 'Bestaande vervolgpoging geopend.'} De vorige poging blijft bewaard.`);
            // Read back mutations without optimistic list changes.
            if (operation === 'stop' || operation === 'resume') {
              pending = { operation: 'list', version: revision, foreground: true };
            }
          }
        } catch (e) {
          if (!mounted) return;
          // Fail closed for all failures, including malformed or out-of-scope data.
          halted = true;
          ++revision;
          clearPrivate();
          setError(e.message);
        }
      }
      running = false;
      if (mounted) { setBusy(false); schedule(); }
    }
    function enqueue(operation, workId, foreground = false) {
      if (!mounted || (!foreground && (halted || running))) return;
      if (foreground) {
        halted = false;
        ++revision;
        if (operation !== 'list') {
          selected = workId; currentDetail = null;
          setDetail(null); setNotice('');
        }
      }
      pending = { operation, workId, version: revision, foreground };
      void drain();
    }
    function visibilityChanged() {
      clearTimeout(timer);
      if (!document.hidden) enqueue('list');
    }
    commands.current = enqueue;
    document.addEventListener('visibilitychange', visibilityChanged);
    if (!document.hidden) enqueue('list', undefined, true);
    return () => {
      mounted = false;
      clearTimeout(timer);
      controller.abort();
      document.removeEventListener('visibilitychange', visibilityChanged);
    };
  }, []);
  const refresh = () => commands.current('list', undefined, true);
  const act = (operation, workId) => commands.current(operation, workId, true);
  return <section aria-label="Mijn hulpopdrachten">
    <h2>Mijn hulpopdrachten</h2>
    <p>Opdrachten aan Eva's onderzoeker, met hun opgeslagen status en resultaat. Dit overzicht wordt automatisch bijgewerkt zolang dit tabblad zichtbaar is.</p>
    <button disabled={busy} onClick={() => refresh()}>Werk vernieuwen</button>
    {error && <p role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    {busy && <p role="status">Werk ophalen…</p>}
    {page && !page.jobs.length && <p>Nog geen hulpopdracht beschikbaar.</p>}
    <div className="board">{page?.jobs.map(job => <article className="card" key={job.work_id}>
      <h3>{job.task_preview}</h3><p>{labels[job.state]} · poging {job.generation}</p>
      {typeof job.delivery?.description === 'string' && <p>{job.delivery.description}</p>}
      {job.stop_requested && <p>Stopverzoek opgeslagen</p>}
      <button onClick={() => act('get', job.work_id)}>Opdracht openen</button>
    </article>)}</div>
    {page?.truncated && <p>Er worden maximaal twintig opdrachten getoond, met lopend werk eerst.</p>}
    {detail && <article aria-label="Geopende hulpopdracht" className="card">
      <h3>Opdracht</h3><p style={{ whiteSpace: 'pre-wrap' }}>{detail.task}</p>
      <p>{labels[detail.state]} · poging {detail.generation}</p>
      {typeof detail.failure_description === 'string' && <p role="status">{detail.failure_description}</p>}
      {typeof detail.delivery?.description === 'string' && <p>{detail.delivery.description}</p>}
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
