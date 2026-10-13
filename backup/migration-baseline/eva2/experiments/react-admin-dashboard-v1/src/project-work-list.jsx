import React, { useState } from 'react';

function taskProgressLabel(task) {
  if (task.status === 'completed') return 'Technisch afgerond';
  if (task.status === 'blocked') return task.ownerActionRequired
    ? 'Jouw handeling nodig' : 'Technische blokkade · Codex';
  if (task.assignment?.activeWorkers?.length) return 'Codex werkt eraan';
  if (task.ownerInstructions?.availableNow === true) return task.ownerInstructions.purpose === 'access'
    ? 'Jouw handeling nodig' : 'Door jou te proberen';
  if (task.ownerTrial?.status === 'not_accepted') return 'Feedback nog open';
  if (task.status === 'rejected') return 'Afgesloten zonder invoering';
  if (task.ownerInstructions?.availableNow === false &&
      (task.kind === 'gebruikersproef' || task.owner === 'Jaap')) return 'Later door jou te beoordelen';
  return 'Codex · nog af te maken';
}

function TaskGuidance({ task }) {
  const trial = task.ownerInstructions || task.ownerTrial;
  const steps = trial?.steps || [];
  const canOpenLink = !trial?.localComputerOnly || (typeof window !== 'undefined' &&
    ['localhost', '127.0.0.1', '[::1]', '::1'].includes(window.location.hostname));
  return <div className="task-guidance">
    {task.nextAction && <p><b>Volgende stap:</b> {task.nextAction}</p>}
    {task.blocker && <p className="checklist-blocker"><b>Wat houdt dit tegen:</b> {task.blocker}</p>}
    {trial?.availableNow === false && <p><b>Voor jou:</b> {trial.readyWhen || 'Nu niets nodig; Codex moet eerst het resterende technische werk afronden.'}</p>}
    {steps.length > 0 && <div><b>{trial?.availableNow === false ? 'Daarna kun je dit doen:' : 'Jouw concrete stappen:'}</b>
      {trial.url && canOpenLink && <p><a href={trial.url} target="_blank" rel="noopener noreferrer">{trial.linkLabel || 'Open de bediening'}</a></p>}
      {trial.url && !canOpenLink && <p>Open dit op de computer met Eva's Chrome: {trial.url}</p>}
      <ol>{steps.map((step, index) => <li key={index}>{step}</li>)}</ol>
      {trial.expected && <p><b>Verwacht resultaat:</b> {trial.expected}</p>}
      {trial.reportBack && <p><b>Meld hier terug:</b> {trial.reportBack}</p>}
    </div>}
    {!steps.length && !task.ownerActionRequired && trial?.availableNow !== false &&
      <p><b>Voor jou:</b> Geen handeling nodig voor deze bouwtaak.</p>}
  </div>;
}

export function SmallTaskChecklist({ project, stale = false }) {
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState('all');
  const tasks = (project.deliveryPlan?.implementationSlices || []).flatMap(slice =>
    (slice.remainingTasks || []).map(task => ({ ...task, section: slice.outcome })));
  const finished = tasks.filter(task => task.status === 'completed').length;
  const visible = tasks.filter(task => {
    const done = task.status === 'completed';
    return (filter === 'all' || (filter === 'done') === done) &&
      `${task.id} ${task.title} ${task.section} ${task.doneWhen}`.toLocaleLowerCase('nl')
        .includes(query.toLocaleLowerCase('nl'));
  });
  return <section id="small-tasks" className="small-checklist" aria-label="Alle kleine taken">
    <header><div><span className="eyebrow">CONCREET RESTEREND WERK</span><h2>Alle kleine taken</h2>
      <p>Groen = technisch afgerond. Rood = nog niet afgerond. Klik een taak voor de volgende stap en wat jij eventueel kunt doen.</p></div>
      <div className="checklist-totals" aria-label="Stand van kleine taken">
        <span className="checklist-total-done">✓ <b>{finished}</b> afgerond</span>
        <span className="checklist-total-open">● <b>{tasks.length - finished}</b> nog open</span>
      </div></header>
    {stale && <p role="alert">Verbinding onderbroken: hieronder staat de laatst ontvangen takenlijst.</p>}
    <div className="checklist-controls"><label>Zoek een kleine taak<input type="search" value={query}
      onChange={event => setQuery(event.target.value)} placeholder="Taaknummer, speakers, automatisering…" /></label>
      <div className="checklist-filters" role="group" aria-label="Filter kleine taken">
        {[['all', 'Alles'], ['open', 'Rood · nog open'], ['done', 'Groen · afgerond']].map(([value, title]) =>
          <button key={value} type="button" aria-pressed={filter === value}
            onClick={() => setFilter(value)}>{title}</button>)}
      </div></div>
    <p className="checklist-note">{tasks.length} bestaande kleine taken. De grote productonderdelen en jouw acceptatie staan daar los van.
      De lijst wordt iedere vijf seconden uit het werkbord bijgewerkt; alleen “Codex werkt eraan” betekent dat er een uitvoerder is geregistreerd. Een open gebruiksproef blokkeert onafhankelijk bouwen niet.</p>
    <div className="checklist-rows">{visible.map(task => {
      const done = task.status === 'completed';
      return <details key={task.id} className={`checklist-task ${done ? 'is-done' : 'is-open'}`}
        data-checklist-task-id={task.id} data-checklist-state={done ? 'done' : 'open'}>
        <summary><span className="checklist-mark" aria-hidden="true">{done ? '✓' : '●'}</span>
          <span className="checklist-task-heading"><strong>{task.id} · {task.title}</strong><small>{task.section}</small></span>
          <span className="checklist-status">{taskProgressLabel(task)}</span></summary>
        <div className="checklist-task-detail"><p><b>Klaar wanneer:</b> {task.doneWhen}</p>
          <p><b>Verantwoordelijke:</b> {task.owner || 'Niet vastgelegd'}</p>
          {!done && <TaskGuidance task={task} />}
        </div></details>;
    })}</div>
    {!visible.length && <p role="status">Geen kleine taken in dit filter.</p>}
  </section>;
}

export default function ProjectWorkList({ project }) {
  const [query, setQuery] = useState('');
  const [mode, setMode] = useState('open');
  const slices = project.deliveryPlan?.implementationSlices || [];
  const groups = [...project.capabilities, { id: 'project-wide', title: 'Vergelijking en eindoplevering' }];
  const matches = (slice) => `${slice.id} ${slice.outcome} ${(slice.remainingTasks || []).map(task => `${task.id} ${task.title} ${task.doneWhen}`).join(' ')}`.toLocaleLowerCase('nl').includes(query.toLocaleLowerCase('nl'));
  const smallTasks = slices.flatMap(slice => slice.remainingTasks || []);
  const hasOpenActions = slice => slice.status !== 'completed' ||
    (slice.remainingTasks || []).some(task => task.status !== 'completed');
  const completed = slices.filter(slice => slice.status === 'completed');
  const open = slices.filter(hasOpenActions);
  const visible = slices.filter(slice => matches(slice) && (mode === 'all' ||
    (mode === 'done' ? slice.status === 'completed' : hasOpenActions(slice))));
  return <section className="concrete-work" aria-label="Concrete taken per onderdeel">
    <header><h2>Alle taken per onderdeel</h2><p><b>{open.length} opdrachten met open acties</b> · {completed.length} technisch afgerond, inclusief de basiskeuze; daar kan nog een beoordeling bij openstaan. Dit zijn de bestaande 24 hoofdopdrachten. Het resterende werk is opgesplitst in <b>{smallTasks.filter(task => task.status !== 'completed').length} kleine taken</b>; geen extra scope of productpercentage.</p></header>
    <div className="work-list-controls"><label>Zoek taak of onderwerp<input type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="Bijvoorbeeld speakers, geheugen of T2" /></label><fieldset><legend>Toon</legend>{[['open', 'Open'], ['done', 'Afgerond'], ['all', 'Alles']].map(([value, label]) => <button key={value} aria-pressed={mode === value} onClick={() => setMode(value)}>{label}</button>)}</fieldset></div>
    <p className="work-list-help">Iedere kleine taak heeft een eigen nummer, status en eindvoorwaarde. De status komt uit dezelfde werklijst als die van Codex. Een open opdracht kan al werkende delen bevatten. Gepland of eerder gestart betekent niet dat er nu een agent aan werkt.</p>
    <div className="work-jump-links">{groups.map(group => <a key={group.id} href={`#work-group-${group.id}`}>{group.title}</a>)}</div>
    {groups.map(group => {
      const all = slices.filter(slice => slice.dashboardComponent === group.id);
      const shown = all.filter(slice => visible.includes(slice));
      if (query && !shown.length) return null;
      return <section className="work-component" id={`work-group-${group.id}`} key={group.id}>
        <header><h3>{group.title}</h3><span>{all.filter(hasOpenActions).length} met open acties · {all.filter(slice => slice.status === 'completed').length} technisch afgerond</span></header>
        {!shown.length && <p className="work-empty">{mode === 'open' && !all.some(slice => slice.status !== 'completed') ? 'Geen open bouwopdracht binnen deze scope.' : 'Geen opdrachten in dit filter.'}{group.product?.complete && ' De eerder geaccepteerde scope blijft klaar.'}</p>}
        {shown.map(slice => {
          const done = slice.status === 'completed';
          const openChild = (slice.remainingTasks || []).some(task => task.status !== 'completed');
          const dependencies = (slice.dependsOn || []).filter(id => slices.some(item => item.id === id && item.status !== 'completed'));
          return <article className={`concrete-task ${done ? 'done' : 'open'}`} key={slice.id} data-slice-id={slice.id}>
            <div className="concrete-task-title"><span className="work-task-id">{slice.id}</span><h4>{slice.outcome}</h4><span className="work-state">{done ? `✓ Technisch afgerond${openChild ? ' · actie open' : ''}` : slice.status === 'planned' ? '○ Gepland' : '◐ Nog af te maken'}</span></div>
            {(!done || openChild) && <div className="remaining-actions">{slice.remainingTasks?.length ? slice.remainingTasks.map(task => <article className={`small-task ${task.status}`} key={task.id} data-small-task-id={task.id}>
              <div><span className="small-task-state">{taskProgressLabel(task)}</span><strong>{task.id} · {task.title}</strong></div>
              <p><b>Klaar wanneer:</b> {task.doneWhen}</p><small>Verantwoordelijke: {task.owner} · {task.kind}</small>
              {task.status !== 'completed' && <details className="task-next-action"><summary>Volgende stap en jouw instructies</summary><TaskGuidance task={task} /></details>}
            </article>) : <p>De concrete resterende acties zijn nog niet vastgelegd. Deze opdracht is niet als afgerond geregistreerd.</p>}</div>}
            {done && <p>Technische afronding is geregistreerd; jouw acceptatie en de complete productfunctie worden afzonderlijk bijgehouden.</p>}
            {!done && dependencies.length > 0 && <p className="task-dependencies">Samenhang met nog open opdrachten: {dependencies.join(', ')}. Onafhankelijk werk kan doorgaan.</p>}
            <details><summary>Vastgelegde afronding en bewijs</summary><p>{slice.completion?.remaining || slice.remaining || slice.nextAction || slice.endCondition || slice.baselineDecision?.nextAction || 'Zie de vastgelegde opdrachtspecificatie.'}</p><small>Bewijs: {slice.latestEvidence || slice.evidence || slice.baselineDecision?.evidencePaths?.join(' · ') || 'Geen afzonderlijk bewijs gekoppeld.'}</small><p>Bron: bestaande werkbordopdracht {slice.id}. Bouw, installatie, praktijkbewijs en acceptatie zijn afzonderlijke beoordelingen.</p></details>
          </article>;
        })}
        {group.product?.steps?.length > 0 && <details className="original-scope"><summary>Oorspronkelijke deelstappen en eindvoorwaarden ({group.product.steps.filter(step => !step.evidenceOnly).length})</summary>{group.product.steps.filter(step => !step.evidenceOnly).map(step => <article key={step.id}><h4>{step.complete ? '✓' : '○'} {step.id} · {step.title}</h4><p>{step.status}</p><ul>{(step.criteria || []).map((criterion, index) => <li key={index}>{criterion}</li>)}</ul></article>)}<p>Dit zijn de onderliggende bestaande criteria, geen extra opdrachten naast de 24 bouwopdrachten.</p></details>}
      </section>;
    })}
    {query && !visible.length && <p role="status">Geen taken gevonden voor deze zoekopdracht.</p>}
  </section>;
}
