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
const stages = [['built', 'Gebouwd'], ['tested', 'Getest'], ['live', 'Live bewezen'], ['accepted', 'Geaccepteerd']];
const names = { yes: 'Bewezen', partial: 'Gedeeltelijk', no: 'Nog niet', unknown: 'Onbekend' };
const time = (value) => new Date(value).toLocaleTimeString('nl-NL', { hour: '2-digit', minute: '2-digit' });

function Stage({ state, label }) {
  return <span className={`proof-dot ${state}`} title={`${label}: ${names[state]}`} aria-label={`${label}: ${names[state]}`}>
    {state === 'yes' ? '✓' : state === 'partial' ? '◐' : state === 'unknown' ? '?' : '–'}
  </span>;
}

function PracticeTests({ tests = [], browserAccess, stale, ownerBrowserUpdate }) {
  if (!tests.length) return null;
  const labels = { ready: 'Klaar om te testen', accepted: 'Door jou bevestigd', working: 'In ontwikkeling — nog niet testen' };
  return <section className="practice-section" aria-label="Jouw praktijktests">
    <div className="section-title"><div><h2>Jouw praktijktests</h2><p>Geef hier in de chat het testnummer en je ervaring door. Ik werk de uitslag bij.</p></div></div>
    <div className="practice-grid">{tests.map((test) => <article className={`practice-card ${test.id === 'BR-01' ? 'access-check' : test.status}`} key={test.id}>
      <span className="task-id">{test.id} · {test.updatedOn}</span><span className="task-status">{test.id === 'BR-01' ? (ownerBrowserUpdate?.viewerTrial === 'failed' ? 'Login gelukt · beeldproef mislukt' : stale ? 'Toegangscontrole niet actueel' : browserAccess?.state === 'login_required' ? 'Aanmelden nodig — jouw proef nog open' : 'Toegang nog niet bevestigd') : labels[test.status] || 'Status onbekend'}</span>
      <h3>{test.title}</h3><p>{test.change}</p>
      {test.id === 'BR-01' ? <>
        {ownerBrowserUpdate?.viewerTrial === 'failed' ? <><p>Jouw aanmelding is gelukt. Na overnemen bleef het beeld zwart met een WebSocket-fout; na teruggeven verdween het beeld. De eerdere technische proef bewijst geen werkende huidige kijkverbinding.</p><p><b>Gewenste route:</b> jouw bestaande Google Chrome en een door jou beschikbaar gesteld tabblad, met zichtbaar werken en overnemen. Die koppeling is nog niet geleverd.</p><p>We onderzoeken de officiële Playwright-extensie. De aparte browser is aanvullend en vervangt jouw werkbrowser niet.</p></> : <p>De eerdere technische browserproef is geslaagd. Dat bevestigt nog niet dat jij nu kunt meekijken of overnemen.</p>}
        {!stale && browserAccess?.state === 'login_required' ? <><a className="owner-dashboard-link" href={browserAccess.url} target="_blank" rel="noreferrer">Open eigenaarsdashboard ↗</a>{!ownerBrowserUpdate?.loginAccepted && <p>Meld je aan als <b>eva-owner</b> met het privé lokaal overgedragen wachtwoord.</p>}</> : <p>De juiste dashboardtoegang kan momenteel niet worden bevestigd. Wacht met deze proef.</p>}
        <small>Bereikbaarheid gecontroleerd om {browserAccess?.checkedAt ? time(browserAccess.checkedAt) : '—'}. Dit controleert geen beeldverbinding of gebruikersacceptatie.</small>
      </> : <>{test.status === 'ready' && <><h4>Zo test je</h4><ol>{test.steps.map((step) => <li key={step}>{step}</li>)}</ol><p><b>Geslaagd als:</b> {test.expected}</p><p><b>Bericht hier:</b> {test.report}</p></>}{test.result && <p className="practice-result"><b>Uitslag:</b> {test.result}</p>}</>}
    </article>)}</div>
  </section>;
}

const userOutcomeStatus = {
  'answer-stream': 'Zichtbare streamervaring nog te beoordelen',
  conversation: 'Privégesprekken werken; continuïteit nog te bewijzen',
  memory: 'Voorkeurfunctie afgerond en geaccepteerd',
  initiative: 'Eerste zelfstandige bijdrage ontbreekt',
  tools: 'Documenttaak bewezen; zelfstandig toolgebruik nog open',
  voice: 'Natuurlijke gespreksproef nog open',
  projects: 'Stopproef mislukt; herstel loopt',
  browser: 'Koppeling met Eva ontbreekt',
  installation: 'Beheerde wissel bewezen; zelf installeren nog open',
};

function CapabilityMatrix({ capabilities, onEvidence }) {
  const [expanded, setExpanded] = useState({});
  return <>
    <p className="matrix-mobile-hint">Veeg de tabel opzij voor alle kolommen. Tik op een onderdeel voor de details.</p>
    <div className="matrix-scroll" role="region" aria-label="Statusmatrix, horizontaal scrollbaar" tabIndex={0}>
      <table className="compact-matrix">
        <caption className="visually-hidden">Negen hoofdonderdelen met de gebruikersuitkomst voorop. Technische proefresultaten staan in de details.</caption>
        <thead><tr><th scope="col">Onderdeel · gebruikersuitkomst</th><th scope="col">Wat nu werkt</th><th scope="col">Wat nog nodig is</th></tr></thead>
        {capabilities.map(category => <tbody key={category.id}>
          <tr className="matrix-category">
            <th scope="row"><button className="matrix-toggle" aria-expanded={Boolean(expanded[category.id])} aria-controls={`details-${category.id}`} onClick={() => setExpanded(value => ({ ...value, [category.id]: !value[category.id] }))}><span aria-hidden="true">{expanded[category.id] ? '−' : '+'}</span><span>{category.title}</span></button><p className={`matrix-user-outcome ${category.id === 'memory' && category.accepted === 'yes' ? 'completed' : ''}`}>{category.id === 'memory' && category.accepted !== 'yes' ? 'Voorkeurfunctie nog te beoordelen' : category.userOutcomeStatus || userOutcomeStatus[category.id] || 'Gebruikersuitkomst nog te beoordelen'}</p></th>
            <td className="matrix-current"><p className="matrix-result">{category.visibleResult || category.works}</p>{category.historyNote && <p className="matrix-history">{category.historyNote}</p>}</td>
            <td className="matrix-gap"><span>{category.gap}</span>{category.links?.map(link => <a className="matrix-result-link" href={link.url} target="_blank" rel="noreferrer" key={link.url}>{link.title} ↗</a>)}</td>
          </tr>
          <tr id={`details-${category.id}`} hidden={!expanded[category.id]} className="matrix-details"><td colSpan={3}>
            <div className="matrix-detail-intro"><p className="matrix-scope"><b>Technische oplevering:</b> {category.scope}</p><p className="matrix-technical">{stages.map(([key, label]) => `${label}: ${category.stageLabels?.[key] || names[category[key]]}`).join(' · ')}</p><p>{category.resultBasis || 'Volgens werkbord'}</p><p><b>Wat werkt:</b> {category.works || category.subtitle}</p><p><b>Open:</b> {category.gap}</p>{category.tryIt && <p><b>Probeer:</b> {category.tryIt}</p>}<button onClick={() => onEvidence(category.id)}>Bewijs en grenzen</button></div>
            {!!category.outcomes?.length && <table className="matrix-outcomes"><caption>Concrete deelresultaten binnen {category.title}</caption><thead><tr><th scope="col">Deelresultaat</th>{stages.map(([key,label]) => <th scope="col" key={key}>{label}</th>)}<th scope="col">Open werk</th></tr></thead><tbody>{category.outcomes.map(item => <tr key={item.id}><th scope="row"><button onClick={() => onEvidence(item.id)}>{item.title}</button><small>{item.works}</small></th>{stages.map(([key,label]) => <td key={key}><Stage state={item[key]} label={label}/></td>)}<td className="matrix-gap">{item.gap}</td></tr>)}</tbody></table>}
          </td></tr>
        </tbody>)}
      </table>
    </div>
    <p className="outcome-help">Klap een rij open voor de technische oplevering, gebouwd/getest/live/acceptatie en eerder behaalde deelresultaten.</p>
  </>;
}

function Roadmap({ phases, tasks, searching }) {
  return <div className="project-roadmap" aria-label="Route naar een complete Eva">{phases.map((phase, index) => {
    const cards = tasks.filter((task) => phase.taskIds.includes(task.id));
    if (!cards.length) return null;
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
  const { project, active, milestones, queued, completed, runtime, browserAccess, updatedAt, readAt, revision } = query.data;
  const capability = project.capabilities.flatMap(item => [item, ...(item.outcomes || [])]).find((item) => item.id === selected);
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
        <section className="focus-grid">{project.acceptedDelivery && <article className="accepted-delivery"><p className="eyebrow">✓ AFGEROND EN DOOR JOU GEACCEPTEERD</p><h2>{project.acceptedDelivery.title}</h2><p>{project.acceptedDelivery.result}</p><small>{project.acceptedDelivery.review}</small></article>}
          <article className="project-focus"><div className="focus-kicker"><span className="pulse-dot"/> {active.displayKicker || 'HUIDIGE OPDRACHT'} <span>{active.id}</span></div><h2>{active.displayTitle || active.title}</h2><p>{active.displayOutcome || active.outcome}</p>{active.resultArtifact && <p className="active-artifact"><a href={active.resultArtifact.url} target="_blank" rel="noreferrer">Open resultaat: {active.resultArtifact.title} ↗</a><small>{active.resultArtifact.status}</small></p>}{active.progress && <dl className="focus-progress"><dt>Werkt</dt><dd>{active.progress.works}</dd><dt>Herstel</dt><dd>{active.progress.repair} {active.progress.next}</dd><dt>Nog open</dt><dd>{active.progress.open}</dd></dl>}<div className="focus-footer"><span>{active.displayStatus || `Kaart: ${active.status === 'in_progress' ? 'in uitvoering' : active.status}`}</span><button onClick={() => setTab('werk')}>Bekijk werk →</button></div></article>
          <article className="project-next"><p className="eyebrow">EERST DUIDELIJK, DAN VERDER</p>{project.nextOutcomes.map((item, index) => <div className="next-outcome" key={item.title}><span>{String(index + 1).padStart(2, '0')}</span><div><strong>{item.title}</strong><small>{item.detail}</small></div></div>)}</article>
        </section>
        <section className="capability-section" aria-label="Mogelijkheden en open werk"><div className="section-title"><div><h2>Wat werkt en wat nog mist</h2><p>De gebruikersuitkomst staat voorop. Per onderdeel zie je wat nu werkt en wat nog nodig is; een geslaagde technische proef betekent niet dat de gebruikersuitkomst af is.</p><p className="audit-caption">Live bewezen = uitgevoerd op de vermelde datum. Huidige bereikbaarheid is apart gecontroleerd; een latere kernversie is niet automatisch opnieuw beproefd.</p>{project.capabilityAudit?.unavailable && <p role="alert">Feitencontrole niet beschikbaar; onderstaande statussen komen uit het werkbord.</p>}</div></div>
          <CapabilityMatrix capabilities={project.capabilities} onEvidence={setSelected}/>
        </section>
        <PracticeTests tests={project.practiceTests} browserAccess={browserAccess} stale={Boolean(query.error)} ownerBrowserUpdate={project.capabilityAudit?.ownerBrowserUpdate}/>
        <section className="project-bottom"><article><p className="eyebrow">INSTALLATIECONTROLE</p><h3>{query.error ? 'Niet actueel' : runtime.state === 'ready' ? 'Kern bereikbaar' : runtime.state === 'not_ready' ? 'Kern vraagt aandacht' : 'Kern niet bereikbaar'}</h3><p>{project.runtimeLabel} · controle om {time(runtime.checkedAt)}<br/>Bereikbaar betekent niet dat Eva als product af is.</p></article><article><p className="eyebrow">WAT NU EERLIJK OPENSTAAT</p><ul>{project.openRisks.map((risk) => <li key={risk}>{risk}</li>)}</ul></article></section>
      </>}
      {tab === 'werk' && <section className="project-work"><article className="work-active"><span className="eyebrow">NU · {active.id}</span><h2>{active.displayTitle || active.title}</h2><p>{active.displayOutcome || active.outcome}</p>{active.resultArtifact && <p className="active-artifact"><a href={active.resultArtifact.url} target="_blank" rel="noreferrer">Open resultaat: {active.resultArtifact.title} ↗</a><small>{active.resultArtifact.status}</small></p>}{active.progress && <dl className="focus-progress"><dt>Werkt</dt><dd>{active.progress.works}</dd><dt>Herstel</dt><dd>{active.progress.repair} {active.progress.next}</dd><dt>Nog open</dt><dd>{active.progress.open}</dd></dl>}{!active.displayStatus && <><h3>Klaar wanneer</h3><ul>{active.doneWhen.map((item) => <li key={item}>{item}</li>)}</ul></>}{active.displayStatus && <p>{active.displayStatus}</p>}</article><div className="section-title"><h2>Komt daarna</h2><input aria-label="Zoek een taak" placeholder="Zoek een onderdeel…" value={filter} onChange={(event) => setFilter(event.target.value)}/></div><p className="priority-decision">{project.priorityDecision}</p><Roadmap phases={project.roadmap} tasks={visibleTasks} searching={Boolean(filter)}/>{filter && !visibleTasks.length && <p role="status">Geen taken gevonden.</p>}<details className="past-work"><summary>{completed.length} afgeronde technische kaarten — geen productpercentage</summary>{completed.map((task) => <p key={task.id}>✓ {task.id} · {task.title}</p>)}</details></section>}
      {tab === 'keuzes' && <><div className="project-warning">Bronnen bekeken op {project.competitorCheckedAt}. Gedocumenteerde functies zijn nog geen gelijke praktijktest. De rootlicenties zijn gecontroleerd; hergebruik vereist nog controle per bestand, dependency en integratie.</div><section className="competitor-grid">{project.competitors.map((item) => <article key={item.name}><div className="competitor-top"><span className="competitor-monogram">{item.name[0]}</span><span className="task-status">{item.version}</span></div><h2>{item.name}</h2><span className="eyebrow">WAAR WE VAN KUNNEN LEREN</span><p>{item.strength}</p><span className="eyebrow">NOG TE TOETSEN</span><p>{item.question}</p><a href={item.source} target="_blank" rel="noreferrer">Officiële releasebron ↗</a><hr/><span className="eyebrow">EERSTE HERGEBRUIKPROEF · {item.taskId}</span><p>{item.reuse}</p><p>{item.trial}</p><a href={item.licenseSource} target="_blank" rel="noreferrer">{item.license} ↗</a></article>)}</section><section className="module-section"><h2>Eén modulaire Eva</h2><p>Eva houdt identiteit, personen en bevoegdheden. Algemene techniek komt uit onderhouden open source.</p><div className="module-grid">{project.modules.map((item) => <article key={item.name}><span className="eyebrow">{item.name}</span><h3>{item.candidate}</h3><span className="module-state">{item.state}</span><p>{item.next}</p></article>)}</div></section></>}
      <footer className="project-footer"><span>Werklijst gewijzigd: {new Date(updatedAt).toLocaleString('nl-NL')} · versie {revision.slice(0, 8)}</span><span>Geen extra modelcalls voor dit dashboard</span></footer>
    </main>
    {capability && <div className="detail-backdrop" onClick={() => setSelected(null)}><section className="capability-detail" role="dialog" aria-modal="true" aria-label={capability.title} onClick={(event) => event.stopPropagation()}><button className="detail-close" autoFocus onClick={() => setSelected(null)}>Sluiten ×</button><p className="eyebrow">BEWIJS EN GRENZEN</p><h2>{capability.title}</h2><p>{capability.detail}</p><dl>{stages.map(([key, label]) => <div key={key}><dt>{label}</dt><dd><Stage state={capability[key]} label={label}/><span>{capability.stageLabels?.[key] || names[capability[key]]}{capability.stageNotes?.[key] && <small>{capability.stageNotes[key]}</small>}</span></dd></div>)}</dl><h3>Wat werkt</h3><p>{capability.works || capability.detail}</p>{capability.tryIt && <><h3>Zelf proberen</h3><p>{capability.tryIt}</p></>}<h3>Resterend werk</h3><p>{capability.gap}</p>{capability.evidence && <small>Basis: {capability.evidence}</small>}</section></div>}
  </div>;
}
