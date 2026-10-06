import React, { useState } from 'react';
import { useGetOne } from 'react-admin';
import './project-dashboard.css';
import ProjectWorkList, { SmallTaskChecklist } from './project-work-list.jsx';
import { projectFreshness } from '../project-freshness.mjs';

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
  const labels = { awaiting_result: 'Uitgevoerd — uitslag nog open', ready: 'Technisch beschikbaar', accepted: 'Door jou bevestigd', working: 'In ontwikkeling — nog niet testen' };
  const requested = tests.filter(test => test.ownerAction === 'requested' && test.trialLink && test.status === 'ready');
  return <section className="practice-section" aria-label="Jouw praktijktests">
    <div className="section-title"><div><h2>Praktijkervaring en resultaten</h2><p>Eerdere proeven blijven bewaard. Alleen een expliciete nieuwe proefvraag verschijnt als actie; technisch beschikbaar betekent niet dat je opnieuw moet testen.</p></div></div>
    <div className="owner-trial-summary" aria-label="Actuele proefvragen">
      <h3>Actuele proefvragen</h3>
      {stale ? <p>De bron is niet actueel. Vernieuw het overzicht voordat je een proef kiest.</p> : requested.length ? <ul>{requested.map(test => <li key={test.id}>
        <a href={test.trialLink.url}><b>{test.id} · {test.title}</b></a><p>{test.requestReason || test.expected}</p>
      </li>)}</ul>
      : <p>Je hoeft nu geen eerdere proeven te herhalen. We werken verder aan de gemelde verbeterpunten.</p>}
    </div>
    <details className="practice-history"><summary>Eerdere proeven, resultaten en bewaarde instructies ({tests.length})</summary>
    <div className="practice-grid">{tests.map((test) => <article className={`practice-card ${test.id === 'BR-01' ? 'access-check' : test.status}`} key={test.id} id={'practice-' + test.id}>
      <span className="task-id">{test.id} · {test.updatedOn}</span><span className="task-status">{test.id === 'BR-01' ? (ownerBrowserUpdate?.normalEvaDenialVerified ? (ownerBrowserUpdate.normalEvaIsolatedVerified ? 'Technisch getest · jouw Chrome-proef open' : 'Chrome gereed voor tabdeling') : ownerBrowserUpdate?.viewerTrial === 'failed' ? 'Login gelukt · beeldproef mislukt' : stale ? 'Toegangscontrole niet actueel' : browserAccess?.state === 'login_required' ? 'Aanmelden nodig — jouw proef nog open' : 'Toegang nog niet bevestigd') : labels[test.status] || 'Status onbekend'}</span>
      <h3>{test.title}</h3><p>{test.change}</p>
      {test.trialLink && !test.trialLink.url.startsWith('#') && test.id !== 'BR-01' && <a href={test.trialLink.url}>{test.trialLink.label || 'Open bewaard resultaat of proefroute'}</a>}
      {test.id === 'BR-01' && ownerBrowserUpdate?.normalEvaDenialVerified ? <>
        <p>{ownerBrowserUpdate.bridgeProgress}</p>
        <a className="owner-dashboard-link" href={ownerBrowserUpdate.chromeControlUrl} target="_blank" rel="noreferrer">Open Chrome-bediening ↗</a>
        <p>{ownerBrowserUpdate.trialInstructions || 'Klik Deel een bestaand tabblad en kies in Chrome Allow & select bij één gewenst bestaand tabblad. Meld daarna hier Gedeeld.'}</p>{ownerBrowserUpdate.chromeTrialUrl && <a href={ownerBrowserUpdate.chromeTrialUrl} target="_blank" rel="noreferrer">Open Chrome-proefpagina ↗</a>}
        <small>Geen aanmelding nodig. De echte bedienings- en overnameproef en jouw beoordeling staan nog open.</small>
      </> : test.id === 'BR-01' ? <>
        {ownerBrowserUpdate?.viewerTrial === 'failed' ? <><p>Jouw aanmelding is gelukt. Na overnemen bleef het beeld zwart met een WebSocket-fout; na teruggeven verdween het beeld. De eerdere technische proef bewijst geen werkende huidige kijkverbinding.</p><p><b>Gewenste route:</b> jouw bestaande Google Chrome en een door jou beschikbaar gesteld tabblad, met zichtbaar werken en overnemen. Die koppeling is nog niet geleverd.</p><p>De losse proef met de officiële Playwright-extensie in jouw Chrome is geslaagd, inclusief opnieuw verbinden na tabselectie. De koppeling met Eva en exclusieve overname zijn nog niet bewezen.</p>{ownerBrowserUpdate.bridgeProgress && <p>{ownerBrowserUpdate.bridgeProgress}</p>}</> : <p>De eerdere technische browserproef is geslaagd. Dat bevestigt nog niet dat jij nu kunt meekijken of overnemen.</p>}
        {!stale && browserAccess?.state === 'login_required' ? <><a className="owner-dashboard-link" href={browserAccess.url} target="_blank" rel="noreferrer">Open eigenaarsdashboard ↗</a>{!ownerBrowserUpdate?.loginAccepted && <p>Meld je aan als <b>eva-owner</b> met het privé lokaal overgedragen wachtwoord.</p>}</> : <p>De juiste dashboardtoegang kan momenteel niet worden bevestigd. Wacht met deze proef.</p>}
        <small>Bereikbaarheid gecontroleerd om {browserAccess?.checkedAt ? time(browserAccess.checkedAt) : '—'}. Dit controleert geen beeldverbinding of gebruikersacceptatie.</small>
      </> : <>{test.status === 'ready' && <details><summary>{test.ownerAction === 'requested' ? 'Instructies voor deze proefvraag' : 'Bewaarde proefinstructies — geen nieuwe testvraag'}</summary><ol>{test.steps.map((step) => <li key={step}>{step}</li>)}</ol><p><b>Verwacht resultaat:</b> {test.expected}</p></details>}{test.result && <p className="practice-result"><b>Uitslag:</b> {test.result}</p>}</>}
    </article>)}</div>
    </details>
  </section>;
}

function CompletionMark({ complete }) {
  return <span className={`completion-mark ${complete ? 'done' : 'open'}`}><span aria-hidden="true">{complete ? '✓' : '✕'}</span> {complete ? 'KLAAR' : 'NIET KLAAR'}</span>;
}
function TestResult({ result }) {
  return <span className={`test-result ${result.state}`} title={result.detail}><span aria-hidden="true">{result.state === 'failed' ? '✕' : ['passed', 'partial_passed'].includes(result.state) ? '✓' : '—'}</span> {result.label}</span>;
}
function ScopeChoices({ category, data, refresh, token, setToken }) {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const options = category.product.steps.filter(step => step.decision);
  const archive = category.product.decisionArchive || [];
  if (!options.length && !archive.length) return null;
  const save = async (optionId, decision) => {
    if (!data?.localOwner && !token) { setMessage('Vul je bestaande eigenaar-dashboardtoken in om je keuze op te slaan.'); return; }
    setBusy(true);setMessage('');
    try {
      const response = await fetch('/scope-decisions', {method:'POST',headers:{'Content-Type':'application/json',...(!data?.localOwner ? {Authorization:`Bearer ${token}`} : {})},body:JSON.stringify({optionId,decision,revision:data.revision,requestId:crypto.randomUUID()})});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Opslaan mislukt.');
      await refresh();setMessage('Besluit opgeslagen. Codex verwerkt dit apart; het is geen bewijs dat de functie klaar of geaccepteerd is.');
    } catch (error) { setMessage(error.message);await refresh(); }
    finally { setBusy(false); }
  };
  const controls = (step, restored=false) => <div className="scope-buttons">{restored ? <button disabled={busy} onClick={() => save(step.id,'undecided')}>Keuze heropenen</button> : <><button disabled={busy || step.decision==='included'} onClick={() => save(step.id,'included')}>Opnemen</button><button disabled={busy} onClick={() => save(step.id,'excluded')}>Afwijzen</button></>}</div>;
  return <section className="scope-choices" aria-label="Keuzes voor dit onderdeel">
    <h3>Jouw keuzes</h3><p>Onbeslist houdt dit onderdeel open. Opnemen maakt de optie vereist; afwijzen haalt haar uit de actieve lijst. Dit verandert geen test- of acceptatiebewijs.</p><p>Codex controleert opgeslagen keuzes ongeveer elke 10 minuten zolang de app en computer aan staan. Je keuze wordt bij de volgende controle opgepakt.</p>
    {!data?.localOwner && <label>Eigenaarsaanmelding voor opslaan<input type="password" autoComplete="off" value={token} onChange={event=>setToken(event.target.value)} placeholder="Bestaand eigenaar-dashboardtoken"/></label>}
    {message && <p role="status">{message}</p>}
    {options.map(step => { const info=data.options[step.id];const event=[...data.events].reverse().find(item=>item.optionId===step.id);return <article key={step.id} data-option-id={step.id}><h4>{step.id} · {info?.title || step.title}</h4><p>{info?.explanation || step.detail}</p><p><b>Nut:</b> {info?.benefit || 'Zie de omschrijving.'}</p><p><b>Keuze:</b> {step.decision==='included' ? 'Opgenomen — moet worden afgerond' : 'Onbeslist — eerst jouw keuze'}{event && <> · verwerking: {event.processing.status}</>}</p>{controls(step)}</article>;})}
    <details className="scope-history"><summary>Besluitgeschiedenis en afgewezen opties</summary>{archive.map(step=><article key={step.id}><h4>{step.id} · {data.options[step.id]?.title || step.title}</h4><p>Afgewezen — buiten actieve lijst en teller.</p>{controls(step,true)}</article>)}<ul>{data.events.filter(event=>[...options,...archive].some(step=>step.id===event.optionId)).map(event=><li key={event.id}>{event.optionId} · {{included:'Opgenomen',excluded:'Afgewezen',undecided:'Heropend'}[event.decision]} · {new Date(event.createdAt).toLocaleString('nl-NL')} · {event.processing.status}<small>Besluit {event.id}</small></li>)}</ul></details>
  </section>;
}
function CapabilityMatrix({ capabilities, onEvidence, scopeDecisions, refresh }) {
  const [expanded, setExpanded] = useState({});
  const [ownerToken, setOwnerToken] = useState('');
  return <>
    <p className="matrix-mobile-hint">Veeg opzij voor het resterende werk. Tik op een onderdeel voor tests en bewijs.</p>
    <div className="matrix-scroll" role="region" aria-label="Statusmatrix, horizontaal scrollbaar" tabIndex={0}>
      <table className="compact-matrix completion-matrix">
        <caption className="visually-hidden">Negen volledige onderdelen, vereiste deelstappen en afzonderlijke technische, persoonlijke en concurrentietests.</caption>
        <thead><tr><th scope="col">Onderdeel</th><th scope="col">Al opgeleverd</th><th scope="col">Nog af te maken</th><th scope="col">Beoordeling</th></tr></thead>
        {capabilities.map(category => <tbody key={category.id}>
          <tr className="matrix-category">
            <th scope="row"><button className="matrix-toggle" aria-expanded={Boolean(expanded[category.id])} aria-controls={`details-${category.id}`} onClick={() => setExpanded(value => ({ ...value, [category.id]: !value[category.id] }))}><span aria-hidden="true">{expanded[category.id] ? '−' : '+'}</span><span>{category.title}</span></button><small className="completion-scope">{category.product.scope}</small><small className="step-count">{category.product.undecided > 0 && `${category.product.undecided} keuzes open · `}Resultaten en vervolgstappen uitklappen</small></th>
            <td><p>{category.product.delivery.result}</p><small>{category.product.delivery.scope}</small></td>
            <td className="remaining-work"><strong>{category.product.complete ? 'Gekozen onderdeel afgerond' : 'Onderdeel nog niet afgerond'}</strong><ul>{category.product.remaining.map(item => <li key={item}>{item}</li>)}</ul></td>
            <td><small>Technisch</small><TestResult result={category.product.tested}/><small>Door jou</small><TestResult result={category.product.jaap}/></td>
          </tr>
          <tr id={`details-${category.id}`} hidden={!expanded[category.id]} className="matrix-details"><td colSpan={4}>
            <div className="matrix-detail-intro"><p><b>Volledige ambitie:</b> {category.product.complete ? 'Alle huidige vereiste stappen afgerond.' : 'Vervolgwerk en bewijs staan nog open; afgeronde opleveringen blijven behouden.'} {category.product.done}/{category.product.total} vereiste stappen afgerond. Dit is geen productpercentage.</p><p><b>Werkt binnen bewezen scope:</b> {category.visibleResult}</p><p><b>Nog nodig:</b> {category.gap}</p>{category.historyNote && <p>{category.historyNote}</p>}<p><b>Jaaps proef:</b> {category.product.jaap.detail}</p><p><b>Concurrentie:</b> {category.product.comparison.detail}</p><p className="matrix-technical">{category.scope} · {stages.map(([key,label]) => `${label}: ${category.stageLabels?.[key] || names[category[key]]}`).join(' · ')}</p><p>{category.resultBasis}</p><button onClick={() => onEvidence(category.id)}>Bewijs en grenzen</button>{category.links?.map(link => <a className="matrix-result-link" href={link.url} target="_blank" rel="noreferrer" key={link.url}>{link.title} ↗</a>)}</div>
            <ScopeChoices category={category} data={scopeDecisions} refresh={refresh} token={ownerToken} setToken={setOwnerToken}/>
            <ul className="completion-steps">{category.product.steps.filter(step => !step.evidenceOnly).map(step => <li key={step.id} data-step-id={step.id}><details><summary><span className={`step-state ${step.complete ? 'done' : 'open'}`}>{step.complete ? '✓' : '✕'}</span><span className="step-title">{step.id} · {step.title}<small>{step.status}{!step.required && (step.evidenceOnly ? ' · bewijsgrens, geen extra optie' : ' · telt niet mee')}{step.accepted && ' · door Jaap geaccepteerd'}</small></span></summary><p>{step.detail}</p>{!!step.criteria?.length && <ul>{step.criteria.map(criterion => <li key={criterion}>{criterion}</li>)}</ul>}<small>Bron: {step.source}</small></details></li>)}</ul>
            {category.product.steps.some(step => step.evidenceOnly) && <details className="completion-evidence-notes">
              <summary>Bewijs en beperkingen bij deze oplevering</summary>
              <p>Dit zijn toelichtingen bij het bestaande bewijs, geen extra afvinktaken of mislukte tests. Beperkt technisch bewijs betekent niet dat alle langetermijn- en gezinspraktijk is beoordeeld.</p>
              <ul>{category.product.steps.filter(step => step.evidenceOnly).map(step => <li key={step.id} data-evidence-id={step.id}>
                <strong>{step.title}</strong><p>{step.detail}</p><small>Bron: {step.source}</small>
              </li>)}</ul>
            </details>}
          </td></tr>
        </tbody>)}
      </table>
    </div>
    <p className="outcome-help">Een afgeronde oplevering blijft zichtbaar binnen haar bewezen scope. Vervolgwensen, gezinsproeven en vergelijkingen staan apart bij de volledige ambitie; zij maken eerder geleverd werk niet ongedaan. Technisch bewijs is geen eigenaaracceptatie.</p>
  </>;
}

function Roadmap({ phases, tasks, searching }) {
  return <div className="project-roadmap" aria-label="Route naar een complete Eva">{phases.map((phase, index) => {
    const cards = tasks.filter((task) => phase.taskIds.includes(task.id));
    if (!cards.length && !phase.keepVisible) return null;
    return <details className="roadmap-phase" key={phase.id} open={searching || undefined}>
      <summary><span className="roadmap-number">{index + 1}</span><span><strong>{phase.title}</strong><small>{phase.outcome}</small></span><span className="roadmap-expand" aria-hidden="true">+</span></summary>
      <p className="roadmap-acceptance"><b>Bruikbaar wanneer:</b> {phase.acceptance}</p>
      <div className="task-grid">{cards.map((task) => <article key={task.id}><span className="task-id">{task.id} · {task.area}</span><h3>{task.title}</h3><p>{task.outcome}</p><span className="task-status">Nog niet af</span></article>)}</div>
    </details>;
  })}</div>;
}

const componentIcons = { 'answer-stream':'↗', conversation:'☏', memory:'◈', initiative:'✦', tools:'⚒', voice:'♪', projects:'▤', browser:'▣', installation:'⟳' };
function DeliveryOverview({ project, runtime, stale, onWork, onHistory, onComparison }) {
  const overview = project.deliveryOverview;
  if (!overview) return <p role="alert">Het actuele opleveroverzicht ontbreekt.</p>;
  const latest = overview.deliveries.find(item => ['installed', 'accepted'].includes(item.state));
  const freshness = projectFreshness(overview.reviewedAt, Date.now());
  const complete = project.capabilities.filter(item => item.product.complete);
  const unfinished = project.capabilities.length - complete.length;
  const runtimeKnown = !stale && Number.isFinite(Date.parse(runtime?.checkedAt));
  const runtimeReady = runtimeKnown && runtime.state === 'ready';
  return <div className="delivery-overview control-room owner-overview">
    <section className="owner-summary" aria-label="Project en installatie">
      <div className="owner-product-status"><span className="summary-kicker">PROJECT</span><h2>Eva is nog in ontwikkeling</h2>
        <div className="scope-counts"><span><b>{complete.length}</b> afgebakende functies klaar</span><span><b>{unfinished}</b> nog af te maken</span></div>
        <p>{complete.length ? `Afgebakend klaar: ${complete.map(item => item.title).join(' · ')}.` : 'Nog geen volledig onderdeel afgerond.'}</p>
      </div>
      <div className={`owner-runtime-status ${runtimeReady ? 'reachable' : 'attention'}`}><span className="summary-kicker">INSTALLATIE · LOS VAN PROJECTVOORTGANG</span>
        <h2><span aria-hidden="true">{runtimeReady ? '●' : '○'}</span> {runtimeReady ? 'Eva-kern bereikbaar' : runtimeKnown && runtime.state === 'not_ready' ? 'Eva-kern vraagt aandacht' : runtimeKnown ? 'Eva-kern niet bereikbaar' : 'Bereikbaarheid onbekend'}</h2>
        <p>{runtimeKnown ? `Controle: ${new Date(runtime.checkedAt).toLocaleString('nl-NL')}` : 'Geen actuele bereikbaarheidscontrole.'}</p><small>Dit bewijst geen werkend gesprek of afgerond product.</small>
      </div>
    </section>
    <div className={`board-freshness ${stale ? 'outdated' : freshness}`} role={stale || freshness !== 'current' ? 'status' : undefined}>
      <strong>{stale ? 'Verbinding verloren' : freshness === 'current' ? 'Voortgang handmatig bijgewerkt' : 'Let op: voortgang niet actueel bevestigd'}</strong>
      <span>{Number.isFinite(Date.parse(overview.reviewedAt)) ? `Inhoud gecontroleerd: ${new Date(overview.reviewedAt).toLocaleString('nl-NL')}` : 'Geen inhoudelijke controletijd beschikbaar'} · opgeslagen voortgang, geen live agentstatus</span>
      {(stale || freshness !== 'current') && <span>Dit is de laatst vastgelegde stand; huidig werk kan inmiddels veranderd zijn.</span>}
    </div>
    <section className="delivery-kanban" aria-label="Projectwerkbord">
      <article className="board-lane delivered"><h2><span aria-hidden="true">✓</span> Laatst opgeleverd</h2>{latest ? <><small>{latest.date} · {latest.state === 'accepted' ? 'door jou geaccepteerd' : 'geïnstalleerd'}</small><h3>{latest.title}</h3><details><summary>Resultaat, open punten en bewijs</summary><p>{latest.result}</p><p>{latest.remaining}</p><small>{latest.source}</small></details></> : <p>Geen geïnstalleerde oplevering vastgelegd.</p>}</article>
      <article className="board-lane doing"><h2><span aria-hidden="true">●</span> Laatst gemeld werk</h2><small>{stale || freshness !== 'current' ? 'Huidige uitvoering niet bevestigd' : overview.now.state}</small><h3>{overview.now.title}</h3><details><summary>Werk en eindvoorwaarde</summary><p>{overview.now.detail}</p><p><b>Klaar wanneer:</b> {overview.now.doneWhen}</p></details></article>
      <article className="board-lane upcoming"><h2><span aria-hidden="true">→</span> Hierna</h2><small>{overview.next.state}</small><h3>{overview.next.title}</h3><details><summary>Volgende stap</summary><p>{overview.next.detail}</p><button className="board-link" onClick={onWork}>Hele uitvoervolgorde →</button></details></article>
      <article className="board-lane waiting"><h2><span aria-hidden="true">◷</span> Van jou nodig</h2><small>Blokkeert ander werk niet</small><h3>{overview.ownerAction.title}</h3><p className="lane-summary">{overview.ownerAction.steps[0]}</p><details className="owner-next-action"><summary>Zo doe je dat</summary><ol>{overview.ownerAction.steps.map(step => <li key={step}>{step}</li>)}</ol><p>{overview.ownerAction.expected}</p>{overview.ownerAction.url && <a href={overview.ownerAction.url}>{overview.ownerAction.linkLabel || 'Open de proef'} →</a>}</details></article>
    </section>
    <section className="task-list-entry" aria-label="Resterende concrete taken"><div><h2>Welke taken moeten nog af?</h2><p>De 9 grote onderdelen zijn de einduitkomst. Bekijk daarnaast alle 24 bouwopdrachten en hun concrete resterende acties.</p></div><button onClick={onWork}>Bekijk alle open taken per onderdeel →</button></section>
    <section className="product-map" aria-label="Status per onderdeel"><div className="overview-section-title"><h2>Wat kun je gebruiken — wat ontbreekt?</h2><span>Groen = genoemde scope klaar · amber = nog werk open</span></div>
      <div className="component-list">{project.capabilities.map(item => <details className={`component-row ${item.product.complete ? 'complete' : 'unfinished'}`} key={item.id}>
        <summary><span aria-hidden="true" className="tile-icon">{componentIcons[item.id]}</span><strong>{item.title}</strong><span className="component-state">{item.product.complete ? '✓ Klaar binnen scope' : '◷ Nog af te maken'}</span><span className="component-visible-scope">{item.ownerSummary || item.product.scope}</span><span aria-hidden="true">⌄</span></summary>
        <div className="component-detail"><p><b>Omvang:</b> {item.product.scope}</p><p><b>Al geleverd:</b> {item.product.delivery.result}</p>{item.product.remaining.length > 0 && <ul>{item.product.remaining.map(text => <li key={text}>{text}</li>)}</ul>}<p><b>Technisch:</b> {item.product.tested.label}<br/><b>Door jou:</b> {item.product.jaap.label}</p></div>
      </details>)}</div>
    </section>
    <section className="comparison-entry"><h2>Eva2 tegenover Eva1 en concurrenten</h2><p>{project.comparisonResults?.followup?.headline || project.comparisonResults?.headline || "Nog geen volledige vergelijking."}</p><button className="board-link" onClick={onComparison}>Bekijk resultaten, verliezen en verbruik →</button></section>
    <div className="overview-shortcuts"><button className="board-link" onClick={onWork}>Werk & volgorde →</button><span>Klik op een onderdeel voor de volledige scope.</span><button className="board-link" onClick={onHistory}>Opleveringen & testuitslagen →</button></div>
  </div>;
}

const comparisonLabels = { observed: 'Waargenomen voordeel', failed: 'Niet gehaald', incomplete: 'Niet afgerond', unknown: 'Onbekend' };
const comparisonFamilies = { conversation_memory: 'Gesprek & geheugen', cross_domain_actions: 'Diensten combineren', initiative: 'Initiatief', browser_computer: 'Browser & computer', learning_projects: 'Leren & projecten', voice_presence: 'Spraak · geparkeerd' };
const numberNl = (value) => Number.isFinite(value) ? value.toLocaleString('nl-NL') : 'Onbekend';
function ComparisonState({ value }) {
  return <span className={'comparison-state ' + value.state}><span aria-hidden="true">{value.state === 'observed' ? '✓' : value.state === 'failed' ? '×' : value.state === 'incomplete' ? '◷' : '?'}</span> {value.label || comparisonLabels[value.state]}</span>;
}
function ComparisonFollowup({ result }) {
  if (!result) return null;
  return <section aria-label="Nieuwste aparte vergelijking">
    <h3>Nieuwste ronde · geen geldig kwaliteitspaar</h3>
    <p className="comparison-verdict">{result.headline}</p><p>{result.scope}</p>
    <div className="comparison-table-wrap"><table><caption>Uitvoering en kwaliteit zijn afzonderlijke beoordelingen</caption><thead><tr><th>Systeem</th><th>Formele kwaliteit</th><th>Native antwoorden</th><th>Wat is vastgesteld?</th></tr></thead><tbody>{result.rows.map(row => <tr key={row.name}><th scope="row">{row.name}</th><td><ComparisonState value={row.quality}/></td><td>{row.nativeReplies}</td><td>{row.execution}</td></tr>)}</tbody></table></div>
    <p className="comparison-usage"><b>{result.usage}</b><br/>{result.humanHelpNote}</p>
    <p><b>Volgende stap voor Codex:</b> {result.nextAction}</p><p>{result.remaining}</p>
    <details><summary>Meetgrenzen, configuratie en bewijs van deze ronde</summary><p>{result.timingNote}</p><ul>{result.configurationNotes.map(note => <li key={note}>{note}</li>)}</ul><div className="comparison-table-wrap"><table><caption>Transportmetingen; geen snelheidsscore</caption><thead><tr><th>Systeem</th><th>Transport per verzoek (ms)</th><th>Modelpogingen geregistreerd</th><th>Tools waargenomen</th><th>Volledige tokens in / uit</th></tr></thead><tbody>{result.rows.map(row => <tr key={row.name}><th scope="row">{row.name}</th><td>{row.transportMs.map(numberNl).join(' / ')}</td><td>{numberNl(row.providerAttemptsRegistered)}</td><td>{numberNl(row.observedToolDispatches)}</td><td>{numberNl(row.completeInputTokens)} / {numberNl(row.completeOutputTokens)}</td></tr>)}</tbody></table></div><p>{result.resources}</p><p>{result.automaticReviewNote}</p><ul>{result.evidenceBindings.map(source => <li key={source.path}><code>{source.path}</code></li>)}</ul></details>
  </section>;
}
function ComparisonResults({ results }) {
  if (!results) return <p role="status">Geen actuele eindvergelijking gepubliceerd.</p>;
  const total = results.runtimeTotals;
  const historicalUsage = (value) => {
    if (!value) return 'Onbekend';
    if (Number.isFinite(value.inputTokens) && Number.isFinite(value.outputTokens)) return numberNl(value.inputTokens) + ' / ' + numberNl(value.outputTokens);
    const lower = value.lowerBound;
    return lower && Number.isFinite(lower.input_tokens) ? '≥ ' + numberNl(lower.input_tokens) + ' / ' + numberNl(lower.output_tokens) + '; totaal onbekend' : 'Onbekend';
  };
  return <section className="comparison-results" aria-label="Gepubliceerde eindvergelijking">
    <header><p className="eyebrow">P2.2 · RESULTATEN · {new Date(results.publishedAt).toLocaleDateString('nl-NL')}</p><h2>Waar is Eva2 beter — en waar nog niet?</h2><p>{results.followup?.headline || results.headline}</p><strong className="comparison-verdict">Algemene voorsprong: niet bewezen · volledige vergelijking nog open</strong></header>
    <p className="comparison-legend">Groen = genoemde waarneming · rood = genoemde voorwaarde niet gehaald · amber = geen afgerond resultaat · grijs = onbekend. Dit zijn geen productpercentages.</p>
    <div className="comparison-outcomes">{results.housemateOutcomes.map(row => <article key={row.name} className={row.state}><h3>{row.name}</h3><p>{row.result}</p></article>)}</div>
    <ComparisonFollowup result={results.followup}/>
    <details open={!results.followup}><summary>Eerdere aparte ronde van 6 oktober · resultaten behouden</summary><p>{results.scope}</p>
    <div className="comparison-table-wrap"><table><caption>Resultaatkwaliteit per gekozen geval</caption><thead><tr><th>Geval</th><th>Eva1</th><th>Eva2</th><th>Betekenis</th></tr></thead><tbody>{results.latestRows.map(row => <tr key={row.case}><th scope="row">{row.case}</th><td><ComparisonState value={row.eva1}/></td><td><ComparisonState value={row.eva2}/></td><td>{row.meaning}</td></tr>)}</tbody></table></div>
    <h3>Snelheid, zelfstandigheid en verbruik</h3>
    <div className="comparison-table-wrap"><table><caption>Alle vijf gestarte native pogingen; mislukte pogingen inbegrepen</caption><thead><tr><th>Geval / systeem</th><th>Eerste inhoud per beurt</th><th>Totale observatie incl. geheugen</th><th>Ongeplande hulp</th><th>Modelpogingen</th><th>Toolaanroepen</th><th>Tokens in / uit</th></tr></thead><tbody>{results.latestMetrics.map(row => <tr key={row.case}><th scope="row">{row.case.replace('eva1-bottie:', 'Eva1 · ').replace('eva2:', 'Eva2 · ').replace(':epoch1', '')}</th><td>{row.firstContentSeconds ? row.firstContentSeconds.map(numberNl).join(' / ') + ' s' : 'Onbekend'}</td><td>{Number.isFinite(row.sampleSecondsIncludingMemory) ? numberNl(row.sampleSecondsIncludingMemory) + ' s' : 'Onbekend'}</td><td>{numberNl(row.unplannedHumanHelp)}</td><td>{numberNl(row.providerAttempts)}</td><td>{numberNl(row.toolDispatches)}</td><td>{numberNl(row.inputTokens)} / {numberNl(row.outputTokens)}</td></tr>)}</tbody></table></div>
    <p className="comparison-usage"><b>Deze ronde totaal: {numberNl(total.providerAttempts)} modelpogingen · {numberNl(total.inputTokens)} invoer · {numberNl(total.outputTokens)} uitvoer · {numberNl(total.nativeToolDispatches)} toolaanroepen.</b><br/>Creditkosten en Codex-ontwikkelverbruik: onbekend. Sneller eerste tekst tonen is geen bewezen snellere taakafronding. Nul hulp bij mislukking bewijst geen zelfstandige oplevering.</p></details>
    <div className="comparison-competitors">{results.unavailableParticipants.map(row => <article key={row.name}><h3>{row.name} <ComparisonState value={row}/></h3><p>{row.reason}</p></article>)}</div>
    <details><summary>Alle eerdere uitkomsten behouden · 12 geheugengevallen en 2 combinatietaken</summary><p>Oorspronkelijke vaste kandidaat uit de eerdere ronde. Geen score voor de gehele gewijzigde installatie; menselijke eindbeoordeling ontbreekt. Alle gevallen staan hieronder, ook verliezen en onbekenden.</p><div className="comparison-table-wrap"><table><caption>Eerdere resultaten; tokens per systeem en geval, geen totaal over alle weken</caption><thead><tr><th>Geval</th><th>Eva1</th><th>Eva2</th><th>Eva1 tokens in / uit</th><th>Eva2 tokens in / uit</th></tr></thead><tbody>{results.history.map(row => <tr key={row.caseId}><th scope="row">{row.caseId}</th>{['eva1','eva2'].map(id => <td key={id}><ComparisonState value={{state: row.participants[id]?.objective === true ? 'observed' : row.participants[id]?.objective === false ? 'failed' : 'unknown', label: row.participants[id]?.quality || 'Onbekend'}}/></td>)}<td>{historicalUsage(row.participants.eva1?.usage)}</td><td>{historicalUsage(row.participants.eva2?.usage)}</td></tr>)}</tbody></table></div>{results.historicalWork.map(row => <p key={row.case}><b>{row.case}:</b> {row.result}</p>)}</details>
    <details><summary>Eerdere tijd, hulp en toolgebruik · alle gemeten waarden</summary><p>Meetgrenzen verschillen per rapport. Veldnamen noemen de oorspronkelijke grens; ms zijn uitsluitend naar seconden omgerekend. Geen nieuwe meting of relatieve snelheidswinst. Ontbrekende hulpmeting blijft onbekend.</p>{results.history.map(row => <details key={row.caseId}><summary>{row.caseId}</summary><div className="comparison-history-metrics">{['eva1','eva2'].map(id => { const metric=row.participants[id]; return <article key={id}><h4>{id === 'eva1' ? 'Eva1' : 'Eva2'}</h4><p><b>Ongeplande hulp:</b> {metric?.humanHelpObservations && Object.keys(metric.humanHelpObservations).length ? JSON.stringify(metric.humanHelpObservations) : 'Onbekend'}</p><p><b>Toolgebruik:</b> {metric?.toolObservations && Object.keys(metric.toolObservations).length ? JSON.stringify(metric.toolObservations) : 'Onbekend'}</p><ul>{metric?.timeObservations?.length ? metric.timeObservations.map(time => <li key={time.boundary}><code>{time.boundary}</code>: {Array.isArray(time.seconds) ? time.seconds.map(numberNl).join(' / ') : numberNl(time.seconds)} s</li>) : <li>Tijd niet afzonderlijk vastgesteld.</li>}</ul></article>; })}</div></details>)}<div className="comparison-table-wrap"><table><caption>Eerdere combinatietaken · Eva2 · niet afgerond</caption><thead><tr><th>Geval</th><th>Modelpogingen</th><th>Toolaanroepen</th><th>Tokens in / uit</th><th>Observatie incl. geheugen</th><th>Ongeplande hulp</th></tr></thead><tbody>{results.historicalWork.map(row => <tr key={row.case}><th scope="row">{row.case}</th><td>{numberNl(row.usage.providerAttempts)}</td><td>{numberNl(row.usage.nativeToolDispatches)}</td><td>{numberNl(row.usage.inputTokens)} / {numberNl(row.usage.outputTokens)}</td><td>{numberNl(row.usage.sampleSeconds)} s</td><td>{numberNl(row.unplannedHumanHelp)}</td></tr>)}</tbody></table></div></details>
    <details><summary>Wat ontbreekt aan de volledige vergelijking?</summary><ul>{results.familyCoverage.map(row => <li key={row.family}><b>{comparisonFamilies[row.family] || row.family}:</b> {row.remainingBasis} Oorspronkelijk {row.originalCases} gevallen × {row.epochs} epochs.</li>)}</ul><p>Twee echte beoordelaars, zeven dagen gezinspraktijk en Jaaps eindoordeel blijven afzonderlijk. Geen nieuwe criteria of stilzwijgende acceptatie.</p></details>
    <details><summary>Meetgrenzen en bewijsbronnen</summary><ul>{results.measurementLimits.map(note => <li key={note}>{note}</li>)}</ul><p>{results.admissionClarification.finding}</p><p>Bronnen zijn onveranderde meetrapporten, gekoppeld aan hun SHA256. Afgeschermde vragen, antwoorden en beoordelingsdata worden hier niet getoond.</p><ul>{results.evidenceBindings.map(source => <li key={source.path}><code>{source.path}</code></li>)}</ul></details>
  </section>;
}

function DeliveryRecord({ item, technical = false }) {
  const grouped = Array.isArray(item.children) && item.children.length > 0;
  const label = grouped ? 'Onvoltooid · uitgesteld' : technical
    ? ({ installed: 'Geïnstalleerde deelstap', tested: 'Deelproef geslaagd', built: 'Deelstap gebouwd', accepted: 'Geaccepteerd' })[item.state]
    : ({ installed: 'Geïnstalleerd', tested: 'Getest · niet geïnstalleerd', built: 'Gebouwd · niet geïnstalleerd', accepted: 'Geaccepteerd' })[item.state];
  return <details>
    <summary><span className={`overview-badge ${item.state}`}>{label || 'Onbekend'}</span> {item.title} · {item.date}</summary>
    <p>{item.result}</p><p><b>Nog open:</b> {item.remaining}</p>
    {grouped && <section aria-label="Technische tussenstappen"><h3>{item.children.length} technische tussenstappen — geen afzonderlijke opleveringen</h3>
      {item.children.map((child, index) => <DeliveryRecord key={`${child.source}-${index}`} item={child} technical />)}
    </section>}
    <small>{item.source}</small>
  </details>;
}

function DeliveryHistory({ project }) {
  return <section className="delivery-history"><h2>Opleveringen en onvoltooid werk</h2>{project.deliveryOverview?.deliveries.map((item, index) => <DeliveryRecord key={`${item.source}-${index}`} item={item} />)}</section>;
}

export default function ProjectDashboard() {
  const [tab, setTab] = useState('overzicht');
  const [selected, setSelected] = useState(null);
  const [filter, setFilter] = useState('');
  const query = useGetOne('project', { id: 'project' }, { refetchInterval: 5000, retry: false });
  if (!query.data) return <div className="project-loading"><h1>Eva, in één oogopslag.</h1><p role={query.error ? 'alert' : undefined}>{query.error ? 'De projectbron is niet bereikbaar. Er wordt geen groene status verzonnen.' : 'Actuele projectbron ophalen…'}</p><button onClick={() => query.refetch()}>Opnieuw ophalen</button></div>;
  const { project, active: storedActive, milestones, queued, completed, runtime, browserAccess, updatedAt, readAt, revision } = query.data;
  const recordedWork = project.deliveryOverview?.now;
  const workFreshness = projectFreshness(project.deliveryOverview?.reviewedAt, Date.now());
  const workCurrent = !query.error && workFreshness === 'current';
  const currentStep = project.deliveryPlan?.steps.find(step => step.number === project.deliveryPlan.currentStep);
  const active = currentStep ? {id:`STAP ${currentStep.number}`, title:currentStep.title,
    outcome:project.deliveryPlan.nextAction, status:'in_progress', doneWhen:[currentStep.doneWhen]} : storedActive;
  const roadmap = project.deliveryPlan ? project.deliveryPlan.steps.map(step => ({
    id:`delivery-${step.number}`,title:step.title,outcome:step.preserve,
    acceptance:step.doneWhen,taskIds:step.existingCards,keepVisible:true,
  })) : project.roadmap;
  const capability = project.capabilities.flatMap(item => [item, ...(item.outcomes || [])]).find((item) => item.id === selected);
  const visibleTasks = [...queued, ...milestones].filter((task) => `${task.id} ${task.title} ${task.area}`.toLowerCase().includes(filter.toLowerCase()));
  return <div className="project-frame">
    <aside className="project-sidebar">
      <a className="project-brand" href="#"><span className="eva-mark">e</span><span>eva<span className="brand-sub">PROJECTOVERZICHT</span></span></a>
      <nav aria-label="Projectnavigatie">{[['overzicht', '◫', 'Overzicht'], ['werk', '☷', 'Taken per onderdeel'], ['details', '▤', 'Details & historie'], ['keuzes', '◇', 'Concurrenten & keuzes']].map(([id, icon, title]) => <button key={id} aria-current={tab === id ? 'page' : undefined} onClick={() => { setTab(id); setSelected(null); }}><span aria-hidden="true">{icon}</span>{title}</button>)}</nav>
      <div className="sidebar-goal"><span className="eyebrow">DE AMBITIE</span><p>Een warme huisgenoot die zelf leert én handelt.</p><small>Beter dan de concurrenten.<br/>Pas als we het bewijzen.</small></div>
      <div className="sidebar-source"><span className="source-led"/> Eén gedeelde werklijst<br/><small>Zelfde bron voor jou en Codex</small></div>
    </aside>
    <main className="project-main">
      <nav className="eva-entry-links" aria-label="Eva gebruiken">
        <div><strong>Projectoverzicht</strong></div>
        <a href="http://localhost:44711/internal/v1/dashboard">Eva’s taken en status <span aria-hidden="true">↗</span></a>
        <a href="http://localhost:44716/">Praten met Eva <span aria-hidden="true">↗</span></a>
      </nav>
      {tab !== 'overzicht' && <header className="project-topbar"><span>Eva 2 <span className="slash">/</span> {tab === 'overzicht' ? 'Projectoverzicht' : tab === 'werk' ? 'Werk & volgorde' : tab === 'details' ? 'Details & historie' : 'Concurrenten & keuzes'}</span><span className="project-sync">{query.error ? '⚠ Synchronisatie onderbroken' : 'Werklijst verbonden'}</span></header>}
      {query.error && <div className="project-warning" role="alert">Verbinding met de projectbron verloren. Onderstaande gegevens zijn van de vorige controle.</div>}
      <section className="project-heading"><div><p className="eyebrow">VAN AMBITIE NAAR DAGELIJKS GEBRUIK</p><h1>{tab === 'overzicht' ? 'Waar staat Eva écht?' : tab === 'werk' ? 'Dit moet er nog af.' : tab === 'details' ? 'Resultaten en bewijs' : 'Leren van de besten.'}</h1><p>{tab === 'overzicht' ? null : tab === 'werk' ? 'De afgesproken volgorde naar een complete Eva.' : tab === 'details' ? 'Eerdere opleveringen, praktijktests en technische onderbouwing.' : 'Actuele bronnen, open keuzes en bewijs. Geen verzonnen voorsprong.'}</p></div>{tab !== 'overzicht' && <span className="phase-tag">In ontwikkeling</span>}</section>
      {tab === 'overzicht' && <>
        <DeliveryOverview project={project} runtime={runtime} stale={Boolean(query.error)} onWork={() => setTab('werk')} onHistory={() => setTab('details')} onComparison={() => setTab('keuzes')}/>
        <SmallTaskChecklist project={project} stale={Boolean(query.error)}/>
      </>}
      {tab === 'details' && <>
        <DeliveryHistory project={project}/>
        <details className="overview-details"><summary>Alle deelstappen, beoordelingen en bewijzen</summary>
          <CapabilityMatrix capabilities={project.capabilities} onEvidence={setSelected} scopeDecisions={project.scopeDecisions} refresh={() => query.refetch()}/>
        </details>
        <details className="overview-details"><summary>Eerdere praktijktests en uitslagen</summary><PracticeTests tests={project.practiceTests} browserAccess={browserAccess} stale={Boolean(query.error)} ownerBrowserUpdate={project.capabilityAudit?.ownerBrowserUpdate}/></details>
        <details className="overview-details"><summary>Installatiecontrole en overige open punten</summary><section className="project-bottom"><article><p className="eyebrow">INSTALLATIECONTROLE</p><h3>{query.error ? 'Niet actueel' : runtime.state === 'ready' ? 'Kern bereikbaar' : runtime.state === 'not_ready' ? 'Kern vraagt aandacht' : 'Kern niet bereikbaar'}</h3><p>{project.runtimeLabel} · controle om {time(runtime.checkedAt)}<br/>Bereikbaar betekent niet dat Eva als product af is.</p></article><article><p className="eyebrow">WAT NU EERLIJK OPENSTAAT</p><ul>{project.openRisks.map((risk) => <li key={risk}>{risk}</li>)}</ul></article></section></details>
      </>}
      {tab === 'werk' && <section className="project-work"><article className="work-active"><span className="eyebrow">LAATST VASTGELEGD WERK</span><p role={!workCurrent ? "status" : undefined}>{workCurrent ? recordedWork?.state || "Uitvoering niet bevestigd" : "Huidige uitvoering niet bevestigd — controleer de opgeslagen voortgang."}</p><h2>{project.deliveryOverview?.now.title || active.displayTitle || active.title}</h2><p>{project.deliveryOverview?.now.detail || active.displayOutcome || active.outcome}</p><details><summary>Eindvoorwaarden en technische voortgang</summary>{!recordedWork && active.resultArtifact && <p className="active-artifact"><a href={active.resultArtifact.url} target="_blank" rel="noreferrer">Open resultaat: {active.resultArtifact.title} ↗</a><small>{active.resultArtifact.status}</small></p>}{!recordedWork && active.progress && <dl className="focus-progress"><dt>Werkt</dt><dd>{active.progress.works}</dd><dt>Herstel</dt><dd>{active.progress.repair} {active.progress.next}</dd><dt>Nog open</dt><dd>{active.progress.open}</dd></dl>}{recordedWork ? <><h3>Eindvoorwaarde van dit werk</h3><p>{recordedWork.doneWhen}</p></> : !active.displayStatus ? <><h3>Klaar wanneer</h3><ul>{active.doneWhen.map((item) => <li key={item}>{item}</li>)}</ul></> : <p>{active.displayStatus}</p>}</details></article><article className="work-next"><span className="overview-label">EERSTVOLGENDE</span><h2>{project.deliveryOverview?.next.title}</h2><p>{project.deliveryOverview?.next.detail}</p></article><SmallTaskChecklist project={project} stale={Boolean(query.error)}/><details className="overview-details"><summary>Hoofdopdrachten, volgorde en bewijs</summary><ProjectWorkList project={project}/></details><details className="past-work"><summary>{completed.length} afgeronde technische kaarten — geen productpercentage</summary>{completed.map((task) => <p key={task.id}>✓ {task.id} · {task.title}</p>)}</details></section>}
      {tab === 'keuzes' && <><ComparisonResults results={project.comparisonResults}/><div className="project-warning">Onderstaand eerder brononderzoek: Bronnen bekeken op {project.competitorCheckedAt}. Gedocumenteerde functies zijn nog geen gelijke praktijktest. De rootlicenties zijn gecontroleerd; hergebruik vereist nog controle per bestand, dependency en integratie.</div><section className="competitor-grid">{project.competitors.map((item) => <article key={item.name}><div className="competitor-top"><span className="competitor-monogram">{item.name[0]}</span><span className="task-status">{item.version}</span></div><h2>{item.name}</h2><span className="eyebrow">WAAR WE VAN KUNNEN LEREN</span><p>{item.strength}</p><span className="eyebrow">NOG TE TOETSEN</span><p>{item.question}</p><a href={item.source} target="_blank" rel="noreferrer">Officiële releasebron ↗</a><hr/><span className="eyebrow">EERSTE HERGEBRUIKPROEF · {item.taskId}</span><p>{item.reuse}</p><p>{item.trial}</p><a href={item.licenseSource} target="_blank" rel="noreferrer">{item.license} ↗</a></article>)}</section><section className="module-section"><h2>Eén modulaire Eva</h2><p>Eva houdt identiteit, personen en bevoegdheden. Algemene techniek komt uit onderhouden open source.</p><div className="module-grid">{project.modules.map((item) => <article key={item.name}><span className="eyebrow">{item.name}</span><h3>{item.candidate}</h3><span className="module-state">{item.state}</span><p>{item.next}</p></article>)}</div></section></>}
      <footer className="project-footer"><span>Werklijst gewijzigd: {new Date(updatedAt).toLocaleString('nl-NL')} · versie {revision.slice(0, 8)}</span><span>Geen extra modelcalls voor dit dashboard</span></footer>
    </main>
    {capability && <div className="detail-backdrop" onClick={() => setSelected(null)}><section className="capability-detail" role="dialog" aria-modal="true" aria-label={capability.title} onClick={(event) => event.stopPropagation()}><button className="detail-close" autoFocus onClick={() => setSelected(null)}>Sluiten ×</button><p className="eyebrow">BEWIJS EN GRENZEN</p><h2>{capability.title}</h2><p>{capability.detail}</p><dl>{stages.map(([key, label]) => <div key={key}><dt>{label}</dt><dd><Stage state={capability[key]} label={label}/><span>{capability.stageLabels?.[key] || names[capability[key]]}{capability.stageNotes?.[key] && <small>{capability.stageNotes[key]}</small>}</span></dd></div>)}</dl><h3>Wat werkt</h3><p>{capability.works || capability.detail}</p>{capability.tryIt && <><h3>Zelf proberen</h3><p>{capability.tryIt}</p></>}<h3>Resterend werk</h3><p>{capability.gap}</p>{capability.evidence && <small>Basis: {capability.evidence}</small>}</section></div>}
  </div>;
}
