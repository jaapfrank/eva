import React from 'react';

const endpoint = '/internal/v1/dashboard/work';
const labels = { admitted: 'Ingepland', call_started: 'Bezig', stopped: 'Gestopt',
  succeeded: 'Resultaat klaar', verified: 'Resultaat gecontroleerd', failed: 'Mislukt',
  needs_help: 'Hulp nodig', ambiguous: 'Uitkomst onzeker', expired: 'Verlopen' };
const lanes = [
  ['active', 'Bezig & ingepland', ['admitted', 'call_started']],
  ['attention', 'Aandacht nodig', ['failed', 'needs_help', 'ambiguous', 'expired']],
  ['done', 'Resultaten', ['succeeded', 'verified']],
  ['stopped', 'Gestopt', ['stopped']],
];
const progressLabels = { pending: 'Nog te doen', working: 'Bezig', done: 'Gedaan volgens Eva', blocked: 'Geblokkeerd', skipped: 'Overgeslagen' };
function validProgress(progress) {
  return progress && progress.contract === 'EvaWorkProgress/v1' && Array.isArray(progress.steps)
    && progress.steps.length <= 12 && typeof progress.handoff === 'string'
    && progress.steps.every(step => typeof step.id === 'string' && typeof step.title === 'string'
      && typeof step.evidence === 'string' && Object.hasOwn(progressLabels, step.state));
}
const resumable = new Set(['stopped', 'failed', 'needs_help', 'ambiguous', 'expired']);
function validWork(work, detail = false) {
  return work && typeof work.work_id === 'string' && Object.hasOwn(labels, work.state)
    && Number.isInteger(work.generation) && typeof work.stop_requested === 'boolean'
    && typeof work.result_available === 'boolean'
    && typeof work[detail ? 'task' : 'task_preview'] === 'string'
    && (!detail || work.result === null || typeof work.result?.output === 'string');
}
function measured(value) {
  return Number.isSafeInteger(value) && value >= 0 ? value.toLocaleString('nl-NL') : 'Niet gemeten';
}
function timestamp(value) {
  const time = typeof value === 'string' ? Date.parse(value) : NaN;
  return Number.isFinite(time) ? new Date(time).toLocaleString('nl-NL') : 'Onbekend';
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
      body: JSON.stringify(operation === 'schedule_cancel' ? { schedule_id: workId } : { work_id: workId }) }),
  });
  if (!response.ok) throw new Error('Werkoverzicht niet beschikbaar. Vernieuw om de opgeslagen toestand op te halen.');
  const data = await response.json();
  if (data.contract !== 'EvaDelegatedWorkOwnerResult/v1' || data.operation !== operation
    || (operation === 'list' ? !Array.isArray(data.jobs) || data.jobs.length > 20
      || !data.jobs.every(work => validWork(work)) || typeof data.truncated !== 'boolean'
      : operation === 'schedule_cancel' ? typeof data.status !== 'string' : !validWork(data.work, true) || (operation !== 'resume' && data.work.work_id !== workId))) {
    throw new Error('Het werkantwoord kon niet worden gelezen. Vernieuw het overzicht.');
  }
  return data;
}

export default function OwnerWork() {
  const [filter, setFilter] = React.useState('');
  const detailRef = React.useRef(null);
  const [page, setPage] = React.useState(null);
  const [detail, setDetail] = React.useState(null);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState('');
  const [notice, setNotice] = React.useState('');
  const commands = React.useRef(() => {});
  React.useEffect(() => {
    if (detail) detailRef.current?.focus({ preventScroll: false });
  }, [detail?.work_id]);
  React.useEffect(() => {
    let mounted = true, running = false, halted = false, pending = null;
    let selected = null, currentDetail = null, revision = 0, timer;
    const controller = new AbortController();
    const fingerprint = work => JSON.stringify([work.work_id, work.state,
      work.generation, work.stop_requested, work.result_available,
      work.delivery?.state, work.delivery?.description, work.usage]);
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
            if (selected && (!currentDetail || ['admitted', 'call_started'].includes(currentDetail.state) || (job
              ? fingerprint(job) !== fingerprint(currentDetail)
              : ['admitted', 'call_started'].includes(currentDetail.state)))) {
              const result = await request('get', selected, controller.signal);
              if (!mounted) return;
              if (version !== revision) continue;
              currentDetail = result.work;
              setDetail(currentDetail);
            }
          } else if (operation === 'schedule_cancel') {
            selected = null; currentDetail = null; setDetail(null);
            setNotice(data.status === 'cancelled' ? 'Geplande start geannuleerd.'
              : data.status === 'stop_requested' ? 'De opdracht was al gestart; stopverzoek opgeslagen.'
              : 'Actuele planning opgehaald.');
            pending = { operation: 'list', version: revision, foreground: true };
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
  const visible = (page?.jobs || []).filter(job => job.task_preview.toLocaleLowerCase('nl-NL').includes(filter.toLocaleLowerCase('nl-NL')));
  function downloadResult() {
    if (!detail?.result_available || !detail.result) return;
    const url = URL.createObjectURL(new Blob([readable(detail.result.output)], { type: 'text/plain;charset=utf-8' }));
    const link = document.createElement('a');
    link.href = url; link.download = 'eva-resultaat.txt'; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return <section className="owner-work" aria-label="Mijn hulpopdrachten">
    <h2>Mijn opdrachten</h2>
    <p>Onderzoek, digitale taken en hulpwerk van Eva. De opgeslagen toestand wordt elke vijf seconden bijgewerkt.</p>
    <div className="work-toolbar"><a href="/internal/v1/dashboard/conversation">Nieuwe opdracht aan Eva →</a><label>Zoek opdracht <input value={filter} onChange={event => setFilter(event.target.value)} type="search" /></label></div>
    <button disabled={busy} onClick={() => refresh()}>Werk vernieuwen</button>
    {error && <p role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    {busy && <p role="status">Werk ophalen…</p>}
    {Array.isArray(page?.planning?.schedules) && page.planning.schedules.length > 0 && <section aria-label="Geplande startmomenten">
      <h3>Later aan het werk</h3>
      <p>Een bewaarde afspraak voor één start of hervatting. Tot dat moment draait hiervoor geen model.</p>
      {page.planning.schedules.map(item => <article className="card" key={item.schedule_id}>
        <h4>{item.task || 'Eerdere opdracht hervatten'}</h4>
        <p>{timestamp(item.run_at)} · {({ scheduled: 'Wacht op startmoment', launching: 'Wordt ingepland', dispatched: 'Overgedragen aan werker', cancelled: 'Geannuleerd', blocked: 'Start geblokkeerd; vraag Eva om bijsturing' })[item.state] || 'Onbekend'}</p>
        {item.can_cancel && <button disabled={busy} onClick={() => act('schedule_cancel', item.schedule_id)}>Annuleren</button>}
        {item.work_id && <button disabled={busy} onClick={() => act('get', item.work_id)}>Opdracht openen</button>}
      </article>)}
      {page.planning.truncated && <p>De twintig meest recente of open afspraken worden getoond.</p>}
    </section>}
    {page && !page.jobs.length && <p>Nog geen hulpopdracht beschikbaar.</p>}
    {page && <div className="work-kanban">{lanes.map(([id, title, states]) => {
      const jobs = visible.filter(job => states.includes(job.state));
      return <section className={`work-lane ${id}`} key={id} aria-label={title}>
        <h3>{title} <span>{jobs.length}</span></h3>
        {!jobs.length && <p className="work-empty">{filter ? 'Geen match' : 'Geen opdrachten'}</p>}
        {jobs.map(job => <article className="card" key={job.work_id}>
          <h4>{job.task_preview}</h4><p>{labels[job.state]} · poging {job.generation}</p>
          {typeof job.delivery?.description === 'string' && <p>{job.delivery.description}</p>}
          {job.stop_requested && <p>Stopverzoek opgeslagen</p>}
          <button disabled={busy} onClick={() => act('get', job.work_id)}>{job.result_available ? 'Resultaat bekijken' : 'Opdracht openen'}</button>
          {id === 'active' && !job.stop_requested && <button disabled={busy} onClick={() => act('stop', job.work_id)}>Stoppen</button>}
        </article>)}
      </section>;
    })}</div>}
    {page?.truncated && <p>Er worden maximaal twintig opdrachten getoond, met lopend werk eerst.</p>}
    {detail && <article ref={detailRef} tabIndex={-1} aria-label="Geopende hulpopdracht" className="card work-detail">
      <h3>Opdracht</h3><p style={{ whiteSpace: 'pre-wrap' }}>{detail.task}</p>
      <p>{labels[detail.state]} · poging {detail.generation}</p>
      <p>Aangemaakt: {timestamp(detail.created_at)}{detail.result?.completed_at && <> · Afgerond: {timestamp(detail.result.completed_at)}</>}</p>
      {typeof detail.parent_work_id === 'string' && detail.parent_work_id !== detail.work_id && <p>
        Deze poging vervolgt een eerdere opdracht. <button disabled={busy} onClick={() => act('get', detail.parent_work_id)}>Vorige poging bekijken</button>
      </p>}
      {(page?.jobs || []).filter(job => job.parent_work_id === detail.work_id).map(job => <p key={job.work_id}>
        Vervolgpoging {job.generation}: {labels[job.state]} <button disabled={busy} onClick={() => act('get', job.work_id)}>Vervolgpoging openen</button>
      </p>)}
      <details><summary>Gemeten verbruik van deze poging</summary>
        <dl className="work-usage"><dt>Modelantwoorden</dt><dd>{measured(detail.usage?.completed_model_responses)}</dd>
          <dt>Toolaanroepen</dt><dd>{measured(detail.usage?.tool_calls)}</dd>
          <dt>Inputtokens</dt><dd>{measured(detail.usage?.input_tokens)}</dd>
          <dt>Outputtokens</dt><dd>{measured(detail.usage?.output_tokens)}</dd>
          <dt>Kosten</dt><dd>{Number.isSafeInteger(detail.usage?.cost_usd_micros) && detail.usage.cost_usd_micros >= 0
            ? new Intl.NumberFormat('nl-NL', { style: 'currency', currency: 'USD', maximumFractionDigits: 6 }).format(detail.usage.cost_usd_micros / 1000000)
            : 'Onbekend'}</dd></dl>
        <p>Dit betreft alleen deze poging, niet eerdere pogingen of het abonnementstegoed. Ontbrekende metingen zijn geen nulverbruik.</p>
      </details>
      {typeof detail.failure_description === 'string' && <p role="status">{detail.failure_description}</p>}
      {typeof detail.delivery?.description === 'string' && <p>{detail.delivery.description}</p>}
      {['admitted', 'call_started'].includes(detail.state) && !detail.stop_requested
        && <button disabled={busy} onClick={() => act('stop', detail.work_id)}>Stop deze hulpopdracht</button>}
      {resumable.has(detail.state) && <>
        <p>Hervatten start een nieuwe begrensde poging en kan opnieuw modelverbruik opleveren. De eerdere poging blijft bewaard.</p>
        <button disabled={busy} onClick={() => act('resume', detail.work_id)}>Hervatten met nieuwe poging</button>
      </>}
      {validProgress(detail.progress) && <section aria-label="Uitvoerplan en voortgang" className="work-progress">
        <h3>Uitvoerplan</h3>
        <p>Bijgewerkt: {timestamp(detail.progress.updated_at)}. Eva’s werknotities; de stappen zijn niet onafhankelijk gecontroleerd.</p>
        <ol>{detail.progress.steps.map(step => <li key={step.id} className={`work-step ${step.state}`}>
          <strong>{step.title}</strong> <span>{progressLabels[step.state]}</span>
          {step.evidence && <p>{step.evidence}</p>}
        </li>)}</ol>
        {detail.progress.handoff && <><h4>Stand en volgende actie</h4><p style={{ whiteSpace: 'pre-wrap' }}>{detail.progress.handoff}</p></>}
        <p>Bij hervatten blijft dit plan beschikbaar. Het plan zelf start geen nieuwe poging.</p>
      </section>}
      <h3>Opgeslagen resultaat</h3>
      {detail.result_available && detail.result && <button onClick={downloadResult}>Resultaat downloaden</button>}
      {detail.result_available && detail.result
        ? <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{readable(detail.result.output)}</pre>
        : <p>Geen afgerond resultaat beschikbaar.</p>}
    </article>}
  </section>;
}
