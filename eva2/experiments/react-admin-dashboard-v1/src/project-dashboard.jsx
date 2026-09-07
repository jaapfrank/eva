import React, { useState } from 'react';
import { useGetOne } from 'react-admin';
import './project-dashboard.css';

export const projectProvider = {
  async getOne() {
    const response = await fetch('/project-state', { cache: 'no-store' });
    if (!response.ok) throw new Error('Projectbron niet bereikbaar');
    const data = await response.json();
    if (data.contract !== 'EvaProjectOverview/v1') throw new Error('Onbekende projectbron');
    return { data };
  },
};

export function ProjectLayout({ children }) { return <div className="project-app">{children}</div>; }
const stages = [['built', 'Gebouwd'], ['tested', 'Getest'], ['live', 'In gebruik'], ['accepted', 'Geaccepteerd']];
const names = { yes: 'Bewezen', partial: 'Gedeeltelijk', no: 'Nog niet', unknown: 'Onbekend' };
const time = (value) => new Date(value).toLocaleTimeString('nl-NL', { hour: '2-digit', minute: '2-digit' });

function Stage({ state, label }) {
  return <span className={`proof-dot ${state}`} title={`${label}: ${names[state]}`} aria-label={`${label}: ${names[state]}`}>
    {state === 'yes' ? '✓' : state === 'partial' ? '◐' : state === 'unknown' ? '?' : '–'}
  </span>;
}

function Roadmap({ phases, tasks, searching }) {
  return <div className="project-roadmap" aria-label="Route naar een complete Eva">{phases.map((phase, index) => {
    const cards = tasks.filter((task) => phase.taskIds.includes(task.id));
    if (searching && !cards.length) return null;
    return <details className="roadmap-phase" key={phase.id} open={searching || undefined}>
      <summary><span className="roadmap-number">{index + 1}</span><span><strong>{phase.title}</strong><small>{phase.outcome}</small></span><span className="roadmap-expand" aria-hidden="true">+</span></summary>
      <p className="roadmap-acceptance"><b>Bruikbaar wanneer:</b> {phase.acceptance}</p>
      <div className="task-grid">{cards.map((task) => <article key={task.id}><span className="task-id">{task.id} · {task.area}</span><h3>{task.title}</h3><p>{task.outcome}</p><span className="task-status">Nog niet af</span></article>)}</div>
    </details>;
  })}</div>;
}

export default function ProjectDashboard() {
  const [tab, setTab] = useState('overzicht');
  const [selected, setSelected] = useState(null);
  const [filter, setFilter] = useState('');
  const query = useGetOne('project', { id: 'project' }, { refetchInterval: 5000, retry: false });
  if (!query.data) return <div className="project-loading"><h1>Eva, in één oogopslag.</h1><p role={query.error ? 'alert' : undefined}>{query.error ? 'De projectbron is niet bereikbaar. Er wordt geen groene status verzonnen.' : 'Actuele projectbron ophalen…'}</p><button onClick={() => query.refetch()}>Opnieuw ophalen</button></div>;
  const { project, active, milestones, queued, completed, runtime, updatedAt, readAt, revision } = query.data;
  const capability = project.capabilities.find((item) => item.id === selected);
  const visibleTasks = [...queued, ...milestones].filter((task) => `${task.id} ${task.title} ${task.area}`.toLowerCase().includes(filter.toLowerCase()));
  return <div className="project-frame">
    <aside className="project-sidebar">
      <a className="project-brand" href="#"><span className="eva-mark">e</span><span>eva<span className="brand-sub">PROJECTOVERZICHT</span></span></a>
      <nav aria-label="Projectnavigatie">{[['overzicht', '◫', 'Overzicht'], ['werk', '☷', 'Werk & volgorde'], ['keuzes', '◇', 'Concurrenten & keuzes']].map(([id, icon, title]) => <button key={id} aria-current={tab === id ? 'page' : undefined} onClick={() => { setTab(id); setSelected(null); }}><span aria-hidden="true">{icon}</span>{title}</button>)}</nav>
      <div className="sidebar-goal"><span className="eyebrow">DE AMBITIE</span><p>Een warme huisgenoot die zelf leert én handelt.</p><small>Beter dan de concurrenten.<br/>Pas als we het bewijzen.</small></div>
      <div className="sidebar-source"><span className="source-led"/> Eén gedeelde werklijst<br/><small>Zelfde bron voor jou en Codex</small></div>
    </aside>
    <main className="project-main">
      <header className="project-topbar"><span>Eva 2 <span className="slash">/</span> {tab === 'overzicht' ? 'Projectoverzicht' : tab === 'werk' ? 'Werk & volgorde' : 'Concurrenten & keuzes'}</span><span className="project-sync">{query.error ? '⚠ Synchronisatie onderbroken' : `Bron gelezen om ${time(readAt)}`}</span></header>
      {query.error && <div className="project-warning" role="alert">Verbinding met de projectbron verloren. Onderstaande gegevens zijn van de vorige controle.</div>}
      <section className="project-heading"><div><p className="eyebrow">VAN AMBITIE NAAR DAGELIJKS GEBRUIK</p><h1>{tab === 'overzicht' ? 'Waar staat Eva écht?' : tab === 'werk' ? 'Eén richting. Zichtbaar werk.' : 'Leren van de besten.'}</h1><p>{tab === 'overzicht' ? 'Wat werkt, wat ontbreekt en wat je als volgende krijgt.' : tab === 'werk' ? 'Dit is de werklijst waaruit ik werk. Een technische kaart is geen af product.' : 'Actuele bronnen, open keuzes en bewijs. Geen verzonnen voorsprong.'}</p></div><span className="phase-tag">In ontwikkeling</span></section>
      {tab === 'overzicht' && <>
        <section className="project-summary" aria-label="Samenvatting">
          {project.summary.map((item) => <article key={item.label}><span className="stat-label">{item.label}</span><strong>{item.value}</strong><span className="stat-foot">{item.detail}</span></article>)}
        </section>
        <section className="focus-grid">
          <article className="project-focus"><div className="focus-kicker"><span className="pulse-dot"/> HUIDIGE OPDRACHT <span>{active.id}</span></div><h2>{active.title}</h2><p>{active.outcome}</p><div className="focus-footer"><span>Kaart: {active.status === 'in_progress' ? 'in uitvoering' : active.status}</span><button onClick={() => setTab('werk')}>Bekijk werk →</button></div></article>
          <article className="project-next"><p className="eyebrow">EERST DUIDELIJK, DAN VERDER</p>{project.nextOutcomes.map((item, index) => <div className="next-outcome" key={item.title}><span>{String(index + 1).padStart(2, '0')}</span><div><strong>{item.title}</strong><small>{item.detail}</small></div></div>)}</article>
        </section>
        <section className="capability-section"><div className="section-title"><div><h2>Van code naar iets wat jij kunt gebruiken</h2><p>Een groen vinkje geldt alleen voor die stap. Geen misleidend totaalpercentage.</p></div><span className="section-note">Klik op een onderdeel voor de grens</span></div>
          <div className="capability-table"><div className="capability-row table-head"><span>Onderdeel</span>{stages.map(([key, label]) => <span key={key}>{label}</span>)}<span>Wat ontbreekt</span></div>
            {project.capabilities.map((item) => <button className="capability-row" key={item.id} onClick={() => setSelected(item.id)}><span className="capability-name"><span className="capability-icon">{item.icon}</span><span><strong>{item.title}</strong><small>{item.subtitle}</small></span></span>{stages.map(([key, label]) => <Stage key={key} state={item[key]} label={label}/>)}<span className="capability-gap">{item.gap}<b>↗</b></span></button>)}
          </div><div className="proof-legend"><span><Stage state="yes" label="Bewezen"/> Bewezen</span><span><Stage state="partial" label="Gedeeltelijk"/> Gedeeltelijk</span><span><Stage state="no" label="Nog niet"/> Nog niet</span><span><Stage state="unknown" label="Onbekend"/> Onbekend</span></div>
        </section>
        <section className="project-bottom"><article><p className="eyebrow">INSTALLATIECONTROLE</p><h3>{query.error ? 'Niet actueel' : runtime.state === 'ready' ? 'Kern bereikbaar' : runtime.state === 'not_ready' ? 'Kern vraagt aandacht' : 'Kern niet bereikbaar'}</h3><p>{project.runtimeLabel} · controle om {time(runtime.checkedAt)}<br/>Bereikbaar betekent niet dat Eva als product af is.</p></article><article><p className="eyebrow">WAT NU EERLIJK OPENSTAAT</p><ul>{project.openRisks.map((risk) => <li key={risk}>{risk}</li>)}</ul></article></section>
      </>}
      {tab === 'werk' && <section className="project-work"><article className="work-active"><span className="eyebrow">NU · {active.id}</span><h2>{active.title}</h2><p>{active.outcome}</p><h3>Klaar wanneer</h3><ul>{active.doneWhen.map((item) => <li key={item}>{item}</li>)}</ul></article><div className="section-title"><h2>Komt daarna</h2><input aria-label="Zoek een taak" placeholder="Zoek een onderdeel…" value={filter} onChange={(event) => setFilter(event.target.value)}/></div><p className="priority-decision">{project.priorityDecision}</p><Roadmap phases={project.roadmap} tasks={visibleTasks} searching={Boolean(filter)}/>{filter && !visibleTasks.length && <p role="status">Geen taken gevonden.</p>}<details className="past-work"><summary>{completed.length} afgeronde technische kaarten — geen productpercentage</summary>{completed.map((task) => <p key={task.id}>✓ {task.id} · {task.title}</p>)}</details></section>}
      {tab === 'keuzes' && <><div className="project-warning">Bronnen bekeken op {project.competitorCheckedAt}. Gedocumenteerde functies zijn nog geen gelijke praktijktest. De rootlicenties zijn gecontroleerd; hergebruik vereist nog controle per bestand, dependency en integratie.</div><section className="competitor-grid">{project.competitors.map((item) => <article key={item.name}><div className="competitor-top"><span className="competitor-monogram">{item.name[0]}</span><span className="task-status">{item.version}</span></div><h2>{item.name}</h2><span className="eyebrow">WAAR WE VAN KUNNEN LEREN</span><p>{item.strength}</p><span className="eyebrow">NOG TE TOETSEN</span><p>{item.question}</p><a href={item.source} target="_blank" rel="noreferrer">Officiële releasebron ↗</a><hr/><span className="eyebrow">EERSTE HERGEBRUIKPROEF · {item.taskId}</span><p>{item.reuse}</p><p>{item.trial}</p><a href={item.licenseSource} target="_blank" rel="noreferrer">{item.license} ↗</a></article>)}</section><section className="module-section"><h2>Eén modulaire Eva</h2><p>Eva houdt identiteit, personen en bevoegdheden. Algemene techniek komt uit onderhouden open source.</p><div className="module-grid">{project.modules.map((item) => <article key={item.name}><span className="eyebrow">{item.name}</span><h3>{item.candidate}</h3><span className="module-state">{item.state}</span><p>{item.next}</p></article>)}</div></section></>}
      <footer className="project-footer"><span>Werklijst gewijzigd: {new Date(updatedAt).toLocaleString('nl-NL')} · versie {revision.slice(0, 8)}</span><span>Geen extra modelcalls voor dit dashboard</span></footer>
    </main>
    {capability && <div className="detail-backdrop" onClick={() => setSelected(null)}><section className="capability-detail" role="dialog" aria-modal="true" aria-label={capability.title} onClick={(event) => event.stopPropagation()}><button className="detail-close" autoFocus onClick={() => setSelected(null)}>Sluiten ×</button><p className="eyebrow">BEWIJS EN GRENZEN</p><h2>{capability.title}</h2><p>{capability.detail}</p><dl>{stages.map(([key, label]) => <div key={key}><dt>{label}</dt><dd><Stage state={capability[key]} label={label}/>{names[capability[key]]}</dd></div>)}</dl><h3>Volgende gebruikersuitkomst</h3><p>{capability.gap}</p><small>Basis: {capability.evidence}</small></section></div>}
  </div>;
}
