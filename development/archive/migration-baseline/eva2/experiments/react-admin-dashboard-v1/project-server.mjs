import { readFile, stat } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { applyProductCompletion, hasLaterTelegramRepair } from './project-completion.mjs';
import { readDecisions, decisionMiddleware, optionalOptions, authorizeOwner } from './scope-decisions.mjs';

const boardFile = process.env.EVA_PROJECT_BOARD_FILE || fileURLToPath(new URL('../../design/product/execution-workboard-v1.json', import.meta.url));
const decisionsFile = process.env.EVA_PROJECT_DECISIONS_FILE || fileURLToPath(new URL('../../design/product/optional-scope-decisions.json', import.meta.url));
const auditFile = fileURLToPath(new URL('./capability-audit.json', import.meta.url));

export function acceptedMemoryDelivery(board) {
  const memory = board.audit?.memoryGoal20260910;
  if (memory?.ownerAccepted !== true || memory.userTrial?.accepted !== true) return null;
  const task = board.executionTasks.find(item => item.id === 'CO-008');
  return { taskId: 'CO-008', title: 'Geheugenresultaat afgerond',
    result: 'Voorkeur onthouden, wijzigen en gebruiken — door jou geaccepteerd (GE-01).',
    reviewOpen: task?.status !== 'done',
    review: task?.status === 'done' ? 'De technische kaart is ook afgesloten.' : memory.next,
    evidence: memory.ownerAcceptanceEvidence };
}

// Only explicitly maintained summary text supersedes the dated audit narrative.
// Evidence, technical/owner states and completion remain in their existing paths.
export function applyCurrentCapabilitySummaries(project, board) {
  const current = board.audit?.projectDashboard?.capabilities || [];
  for (const row of project.capabilities) {
    const summary = current.find(item => item.id === row.id)?.currentSummary;
    if (!summary || typeof summary !== 'object' || Array.isArray(summary)) continue;
    const fields = ['works', 'gap', 'detail'].filter(key =>
      typeof summary[key] === 'string' && summary[key].trim());
    if (!fields.length) continue;
    row.historicalSummary ??= Object.fromEntries(
      ['works', 'visibleResult', 'gap', 'detail', 'resultBasis', 'evidence'].map(key => [key, row[key]]));
    for (const key of fields) row[key] = summary[key];
    if (fields.includes('works')) row.visibleResult = summary.works;
    if (row.product?.delivery) {
      if (fields.includes('works')) row.product.delivery.result = row.visibleResult;
      if (fields.includes('gap')) row.product.delivery.followup = row.gap;
    }
    if (typeof summary.checkedAt === 'string' && /^\d{4}-\d{2}-\d{2}T/.test(summary.checkedAt)
        && Number.isFinite(Date.parse(summary.checkedAt))) {
      row.resultBasis = `Actuele werkbordstand · ${summary.checkedAt}`;
    }
    if (Array.isArray(summary.evidencePaths)) {
      const paths = summary.evidencePaths.filter(path => typeof path === 'string' && path.trim());
      row.evidence = [...new Set([...(row.evidence ? row.evidence.split(' · ') : []), ...paths])].join(' · ');
    }
  }
}

export function applyReviewedCapabilities(project, board, audit) {
  if (audit.contract !== 'EvaCapabilityAudit/v2') throw new Error('scoped audit required');
  project.capabilityAudit = { checkedAt: audit.checkedAt, meaning: audit.meaning, ownerBrowserUpdate: audit.ownerBrowserUpdate };
  project.capabilities = project.capabilities.map(item => {
    const finding = audit.findings.find(value => value.id === item.id);
    if (!finding?.scope || !finding.sources?.length) throw new Error('missing scoped evidence');
    // A changed broad ambition does not revoke dated evidence for a named delivery.
    const row = { ...item, ...finding, ...finding.states, works: finding.visibleResult,
      detail: finding.intendedOutcome, evidence: finding.sources.map(source => source.path).join(' · '),
      outcomes: item.outcomes?.map(outcome => ({ ...outcome,
        ...(outcome.id === 'connector' ? { tested: 'partial', works: 'GitMCP-resultaat geleverd na ontwikkelreparaties en handmatige hervattingen.', gap: 'Een nieuwe dienst in één ononderbroken gewone opdracht aansluiten en gebruiken.' } : {}),
      })),
    };
    const connector = board.audit?.tl003UnassistedPreparation20260912;
    if (connector?.technicalComplete === true && connector.independentReview === 'GO') {
      row.outcomes = row.outcomes?.map(outcome => outcome.id !== 'connector' ? outcome : {
        ...outcome, tested:'yes', works:'Een nieuwe CPython-endpoint bij GitMCP in één gewone beurt aangesloten en gebruikt; uitleg opgeslagen en teruggelezen.',
        gap:'Onbekende aanbieders/protocollen en eigenaaracceptatie blijven open.',
      });
    }
    const initiative = board.audit?.initiativeDeliveryAU008;
    if (item.id === 'initiative' && initiative) {
      for (const stage of ['built','tested','live']) row[stage] = initiative[stage] === true ? 'yes' : 'partial';
      row.accepted = initiative.ownerAccepted === true ? 'yes' : 'no';
      row.scope = `AU-008 · ${initiative.liveCapabilityCount ?? 'toegelaten'} kosteloze taakroutes`;
      row.currentWork = initiative.currentWork;
      row.stageNotes = { ...row.stageNotes, built: 'Toegelaten taakroutes gebouwd; actuele telling staat bij de technische oplevering.', tested: initiative.testEvidence, live: `Centrale bron: ${initiative.liveImage}; ${initiative.implementationCommit}.`, accepted: 'Dit betreft AU-008. De beperkte waardering van de eerdere AU-007-kaart staat afzonderlijk.' };
      const research = board.audit?.au008GeneralResearch20260910;
      if (research?.status === 'live') {
        row.scope = 'AU-008 · kosteloze taakroutes en publiek onderzoek';
        row.visibleResult = 'Algemeen publiek onderzoek is live naast de bestaande werkmap- en huisleesroutes.';
        row.works = row.visibleResult;
        row.gap = research.remaining;
        row.resultBasis = `Live bron ${research.sourceCommit.slice(0,7)} · 10 sep`;
        row.accepted = research.ownerAccepted === true ? 'yes' : 'no';
        row.stageNotes = { ...row.stageNotes, tested: research.technicalChecks, live: `Centrale onderzoeksroute: ${research.image}; ${research.sourceCommit}.` };
        row.evidence += ' · ' + research.evidencePath;
      }
      const privateReview = board.audit?.au008PrivateReview20260910;
      if (privateReview?.live === true) {
        const budget = privateReview.budgetAtReadback;
        row.scope = 'AU-008 · onderzoek en private beoordeling';
        row.visibleResult = 'Algemeen publiek onderzoek en private beoordeling op rustige momenten zijn live.';
        row.works = row.visibleResult;
        row.gap = `Eerste bruikbare eigen bijdrage en jouw beoordeling blijven open. Budgetmeting van 10 sep: ${budget?.personUsed ?? '?'} / ${budget?.personLimit ?? '?'}; dit is geen actuele budgetcontrole.`;
        row.resultBasis = `Live bron ${privateReview.sourceCommit.slice(0,7)} · 10 sep`;
        row.accepted = privateReview.ownerAccepted === true ? 'yes' : 'no';
        row.stageNotes = { ...row.stageNotes, live: `Centrale private beoordelingsroute: ${privateReview.image}; ${privateReview.sourceCommit}.`, accepted: privateReview.remaining };
        row.evidence += ' · ' + privateReview.evidencePath;
      }
      const observation = finding.latestObservation;
      if (observation) {
        row.userOutcomeStatus = observation.userOutcomeStatus;
        row.visibleResult = observation.visibleResult;
        row.works = row.visibleResult;
        row.gap = observation.remaining;
        row.resultBasis = `Praktijkcontrole · ${observation.checkedAt}`;
        row.stageNotes = { ...row.stageNotes, live: observation.evidenceSummary, accepted: 'Jouw beoordeling van deze bijdrage is nog open.' };
        if (observation.evidencePath) row.evidence += ' · ' + observation.evidencePath;
      }
      row.stageLabels = { ...row.stageLabels, built: `${initiative.liveCapabilityCount ?? 'Toegelaten'} routes`, accepted: row.accepted === 'yes' ? 'Geaccepteerd' : 'Niet beoordeeld' };
      row.currentWork = `${row.visibleResult} ${row.gap}`;
      row.detail = row.currentWork;
      row.intendedOutcome = row.currentWork;
    }
    const laptopFeedback = board.audit?.voiceLaptopOwnerFeedback20260911;
    if (item.id === 'voice' && laptopFeedback?.laptopInterruptionAccepted === true) {
      row.visibleResult = 'Jaap heeft fysiek bevestigd: laptoponderbreken werkt. De verhaalinhoud vindt hij leuk.';
      row.works = row.visibleResult;
      row.gap = laptopFeedback.remaining;
      row.detail = `${row.visibleResult} ${row.gap}`;
      row.stageNotes = { ...row.stageNotes, accepted: 'Alleen laptoponderbreken door Jaap geaccepteerd; volledige stemervaring blijft open.' };
      row.historyNote = 'SP-01 en SP-02 blijven geaccepteerd. Telefoonpaneel en VoicePE zijn niet als geïnstalleerd of geaccepteerd bevestigd.';
      row.stageLabels = { ...row.stageLabels, accepted: 'Volledige ervaring open' };
      row.resultBasis = 'Fysieke Jaapfeedback · 11 september';
    }
    const delegatedWork = board.audit?.au002Delivery;
    if (item.id === 'projects' && delegatedWork) {
      row.userOutcomeStatus = delegatedWork.userOutcomeStatus || (delegatedWork.providerRepairCandidateImage
        ? 'Reparatiekandidaat gereed; live gebruikersroute nog open'
        : 'Herstel loopt; live gebruikersroute nog open');
      row.visibleResult = delegatedWork.currentWork || delegatedWork.pendingSourceChange || delegatedWork.result || row.visibleResult;
      row.works = row.visibleResult;
      row.gap = delegatedWork.remaining || delegatedWork.next || row.gap;
      row.historyNote = hasLaterTelegramRepair(delegatedWork)
        ? `Historie: de eigenaarproef van ${delegatedWork.telegramUserTrial.date} mislukte. Latere technische reparatie: ${delegatedWork.evidencePath}. De oude fout is bewaard; eigenaaracceptatie is niet verondersteld.`
        : `Eerdere proef: ${finding.visibleResult}`;
      row.scope = hasLaterTelegramRepair(delegatedWork)
        ? 'AU-002 · hulpagent via Telegrambrug; technisch bewezen, eigenaaroordeel open'
        : 'AU-002 · hulpagent; bewijs geldt voor de vermelde gebruikersroute';
      row.detail = [row.visibleResult, delegatedWork.lastActualResult, row.gap].filter(Boolean).join(' ');
      row.intendedOutcome = row.detail;
      row.gapKind = delegatedWork.live === true ? 'Gebruikersbeoordeling en vervolg' : 'Gebruikersroute nog open';
      // Images and preparation checks never imply a proven live owner lifecycle.
      for (const stage of ['built', 'tested', 'live']) {
        if (typeof delegatedWork[stage] === 'boolean') row[stage] = delegatedWork[stage] ? 'yes' : 'no';
      }
      row.accepted = delegatedWork.ownerAccepted === true ? 'yes' : 'no';
      row.stageLabels = { ...row.stageLabels,
        tested: delegatedWork.tested === undefined ? 'Levenscyclus nog open' : (row.tested === 'yes' ? 'Getest' : 'Nog niet bewezen'),
        live: row.live === 'yes' ? 'Gebruikersroute bewezen' : 'Gebruikersroute nog open',
        accepted: row.accepted === 'yes' ? 'Geaccepteerd' : 'Nog open' };
      row.stageNotes = { ...row.stageNotes, tested: delegatedWork.sourceChecks || row.stageNotes.tested,
        live: delegatedWork.lastActualResult || row.stageNotes.live };
      row.resultBasis = `Centrale AU-002-audit${delegatedWork.completedAt || delegatedWork.preparationRecordedAt ? ` · ${delegatedWork.completedAt || delegatedWork.preparationRecordedAt}` : ''}`;
      for (const path of [delegatedWork.providerEvidencePath, delegatedWork.evidencePath, delegatedWork.reviewPath]) {
        if (path) row.evidence += ' · ' + path;
      }
    }
    row.states = Object.fromEntries(['built', 'tested', 'live', 'accepted'].map(stage => [stage, row[stage]]));
    return row;
  });
}

export function applyVoiceTurnEvidence(project, evidence) {
  const proof = evidence.browserProof;
  if (proof?.passed !== true || !Number.isFinite(proof.speechEndToAudioMs) || !Number.isFinite(proof.speechEndToFinalMs)) return;
  const row = project.capabilities.find(item => item.id === 'voice');
  if (!row) return;
  const first = (proof.speechEndToAudioMs / 1000).toLocaleString('nl-NL', {maximumFractionDigits:3});
  const final = (proof.speechEndToFinalMs / 1000).toLocaleString('nl-NL', {maximumFractionDigits:3});
  const result = `Bij expliciet verzoek spreekt Eva een eerste zin terwijl echt onderzoek doorgaat. Browserproef: eerste audio na ${first} s; eindantwoord na ${final} s.`;
  row.visibleResult += ` ${result}`;
  row.works = row.visibleResult;
  row.gap = `De beginstilte van ${first} s is te lang. Snelle betekenisvolle reactie, algemeen spontaan vervolg tijdens werk en natuurlijke stem/uitspraak blijven open. Fenna is nog niet door Jaap beoordeeld; telefoon en VoicePE niet opgeleverd.`;
  row.detail = `${row.visibleResult} ${row.gap} ${evidence.limits?.join(' ') || ''}`;
  row.resultBasis = `Browserdeelproef 11 september · kern ${evidence.initialBuild?.images?.core?.image?.slice(7,14) || 'volgens bewijs'}, media ${evidence.paragraphFix?.image?.slice(7,14) || 'volgens bewijs'}`;
  row.stageNotes = { ...row.stageNotes, live:result, tested:'Echte browser en kern, synthetische spraakinvoer; geen nieuwe fysieke luisterproef.' };
  row.evidence += ' · eva2/evidence/product/voice-natural-turn-candidate-2026-09-11.json';
  row.firstPartProof = {title:'Eerste zin tijdens expliciet gevraagd onderzoek', detail:result, source:'voice-natural-turn-candidate-2026-09-11.json'};
}

async function applyCapabilityAudit(project, board) {
  try {
    applyReviewedCapabilities(project, board, JSON.parse(await readFile(auditFile, 'utf8')));
    try { applyVoiceTurnEvidence(project, JSON.parse(await readFile(new URL('../../evidence/product/voice-natural-turn-candidate-2026-09-11.json', import.meta.url), 'utf8'))); } catch { /* Existing dated voice proof remains if this supplementary evidence is unavailable. */ }
    const lifecycle = board.audit?.voiceClientLifecycle20260912;
    const voice = project.capabilities.find(item => item.id === 'voice');
    if (voice && lifecycle?.live === true) {
      voice.works += ' ' + lifecycle.scope;
      voice.gap = lifecycle.remaining + ' Natuurlijke Voice PE-toegang blijft afzonderlijk werk.';
      voice.detail = voice.works + ' ' + voice.gap;
      voice.stageNotes.live += ' Spraaksessiecorrectie 12 september technisch live; fysieke proef open.';
      voice.evidence += ' · ' + lifecycle.evidencePath;
    }
  } catch { project.capabilityAudit = { unavailable: true }; }
}

// The configured delivery route takes precedence over historical workboard ports.
// Read only the non-secret route drop-in; never load runtime credentials here.
async function runtimePort(project) {
  if (!process.env.EVA_PROJECT_ROUTE_FILE) return project.runtimePort;
  const route = await readFile(process.env.EVA_PROJECT_ROUTE_FILE, 'utf8');
  const matches = [...route.matchAll(/\bEVA2_CANARY_URL=http:\/\/127\.0\.0\.1:(44710|44711)(?=[\s"']|$)/g)];
  if (matches.length !== 1) throw new Error('delivery route unavailable');
  return Number(matches[0][1]);
}

// One read-only projection of the same file used by the development workboard.
// No credentials, chat content, filesystem browsing or task mutation endpoint.
export function projectServer() {
  const configure = (server) => {
    // Public, synthetic trial artifacts only; request paths never become filesystem paths.
    server.middlewares.use('/results/au004-procedure', async (request, response) => {
      response.setHeader('Cache-Control', 'no-store');
      response.setHeader('X-Content-Type-Options', 'nosniff');
      if (request.method !== 'GET') { response.writeHead(405, {Allow:'GET'}); response.end(); return; }
      const artifacts = new Map([
        ['/', 'Resultaat-procedureleerproef.md'],
        ['/procedure', 'au004-srt-webvtt-procedure-20260912-attempt3.md'],
        ['/A.vtt', 'au004-subtitles-A-20260912-attempt3.vtt'],
        ['/B.vtt', 'au004-subtitles-B-20260912-attempt3.vtt'],
      ]);
      const file = artifacts.get((request.url || '/').split('?')[0]);
      if (!file) { response.writeHead(404); response.end('Resultaat niet gevonden.'); return; }
      try {
        const text = await readFile(new URL('../../evidence/product/au004-attempt3/' + file, import.meta.url), 'utf8');
        if (file.endsWith('.vtt')) {
          response.setHeader('Content-Type', 'text/vtt; charset=utf-8');
          response.end(text); return;
        }
        response.setHeader('Content-Type', 'text/html; charset=utf-8');
        const escaped = text.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
        response.end('<!doctype html><html lang="nl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Eva — procedureleerproef</title><style>body{margin:24px auto;padding:0 20px;max-width:850px;background:#f5f7f2;color:#24372d;font:17px/1.65 system-ui}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}a{color:#254f34}nav{display:flex;flex-wrap:wrap;gap:16px}</style><main><nav><a href="/">Dashboard</a><a href="/results/au004-procedure/">Resultaat</a><a href="/results/au004-procedure/procedure">Eva’s procedure</a><a href="/results/au004-procedure/A.vtt">Ondertitels A</a><a href="/results/au004-procedure/B.vtt">Ondertitels B</a></nav><pre>' + escaped + '</pre></main></html>');
      } catch { response.writeHead(503); response.end('Resultaat tijdelijk niet beschikbaar.'); }
    });
    server.middlewares.use('/results/tl003-contextmanager', async (request, response) => {
      response.setHeader('Cache-Control', 'no-store');
      response.setHeader('Content-Type', 'text/html; charset=utf-8');
      response.setHeader('X-Content-Type-Options', 'nosniff');
      if (request.method !== 'GET') { response.writeHead(405, {Allow:'GET'}); response.end(); return; }
      try {
        const source = await readFile(new URL('../../design/product/TL-003-python-contextmanager-result-2026-09-12.md', import.meta.url), 'utf8');
        const escaped = source.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
        response.end('<!doctype html><html lang="nl"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Eva — Python-uitleg</title><style>body{margin:24px auto;padding:0 20px;max-width:850px;background:#f5f7f2;color:#24372d;font:17px/1.65 system-ui}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}a{color:#254f34}</style><main><a href="/">Terug naar het dashboard</a><p>TL-03 · Lees Eva’s resultaat en geef je oordeel in de chat. Het codevoorbeeld is niet uitgevoerd.</p><pre>' + escaped + '</pre></main></html>');
      } catch { response.writeHead(503); response.end('Resultaat tijdelijk niet beschikbaar.'); }
    });
    // Serve this public trial from its single maintained document, without copying it.
    server.middlewares.use('/owner-trials/voice', async (request, response) => {
      response.setHeader('Cache-Control', 'no-store');
      response.setHeader('Content-Type', 'text/plain; charset=utf-8');
      response.setHeader('X-Content-Type-Options', 'nosniff');
      if (request.method !== 'GET') { response.writeHead(405, {Allow:'GET'}); response.end(); return; }
      try { response.end(await readFile(new URL('../../design/product/VOICE-owner-trial-2026-09-12.md', import.meta.url), 'utf8')); }
      catch { response.writeHead(503); response.end('Gebruikersproef tijdelijk niet beschikbaar.'); }
    });
    server.middlewares.use('/scope-decisions', decisionMiddleware({path:decisionsFile, localOwner:process.env.EVA_PROJECT_LOCAL_OWNER==='1', authorize:async authorization=>{
      const board=JSON.parse(await readFile(boardFile,'utf8'));
      const port=await runtimePort(board.audit.projectDashboard);
      const url=`http://127.0.0.1:${port}/internal/v1/dashboard/status`;
      return authorizeOwner(url,authorization);
    }}));
    server.middlewares.use('/project-state', async (request, response) => {
      response.setHeader('Cache-Control', 'no-store');
      response.setHeader('Content-Type', 'application/json; charset=utf-8');
      response.setHeader('X-Content-Type-Options', 'nosniff');
      if (request.method !== 'GET') {
        response.writeHead(405, { Allow: 'GET' }); response.end('{}'); return;
      }
      if (request.headers.origin && request.headers.origin !== `http://${request.headers.host}`) {
        response.writeHead(403); response.end('{}'); return;
      }
      try {
        const raw = await readFile(boardFile);
        const board = JSON.parse(raw);
        const metadata = await stat(boardFile);
        const decisions = await readDecisions(decisionsFile);
        board.audit.optionalScopeDecisions = decisions.data.decisions;
        const project = structuredClone(board.audit.projectDashboard);
        project.scopeDecisions = {localOwner:process.env.EVA_PROJECT_LOCAL_OWNER==='1',revision:decisions.revision,events:decisions.data.events,options:optionalOptions};
        if (!project || !Array.isArray(project.capabilities)) throw new Error('project state missing');
        await applyCapabilityAudit(project, board);
        const primary = board.executionTasks.find((task) => task.id === board.activeTaskId);
        if (!primary) throw new Error('active task missing');
        let runtime = { state: 'unknown', checkedAt: new Date().toISOString() };
        let browserAccess = { state: 'unavailable', checkedAt: runtime.checkedAt, url: null };
        try {
          const port = await runtimePort(project);
          if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error('invalid runtime port');
          runtime.port = port;
          const check = await fetch(`http://127.0.0.1:${port}/readyz`, { signal: AbortSignal.timeout(1500), redirect: 'error' });
          const value = await check.json();
          runtime.state = check.ok && value.ready === true ? 'ready' : 'not_ready';
          if (runtime.state === 'ready') {
            const access = await fetch(`http://127.0.0.1:${port}/internal/v1/dashboard`, { signal: AbortSignal.timeout(1500), redirect: 'error' });
            // A 401 is a working login boundary, not a successful user/browser test.
            if (access.status === 401 && access.headers.get('www-authenticate')?.startsWith('Basic realm="Eva cockpit"')) {
              browserAccess = { state: 'login_required', checkedAt: new Date().toISOString(), url: `http://localhost:${port}/internal/v1/dashboard` };
            }
            await access.body?.cancel();
          }
        } catch { /* Unreachable never becomes a cached green result. */ }
        project.runtimeLabel = runtime.port ? `Geconfigureerde route · poort ${runtime.port}` : 'Route niet vastgesteld';
        // Current access supersedes the stored BR-01 invitation. Historical build
        // and test evidence, task states and human acceptance remain untouched.
        project.practiceTests = project.practiceTests?.map(test => test.id !== 'BR-01' ? test : {
          ...test, status: project.capabilityAudit?.ownerBrowserUpdate?.viewerTrial === 'failed' ? 'failed' : browserAccess.state, steps: [],
          result: 'Oude aparte viewer: beeldverbinding mislukt. Het bestaande Chrome-tabblad is inmiddels in een losse proef bediend en herverbonden; integratie met Eva en exclusieve overname staan open.',
        });
        project.nextOutcomes = project.nextOutcomes?.map(item => item.title !== 'Bestaande resultaten proberen' ? item : {
          ...item, detail: 'Geaccepteerde proeven blijven geaccepteerd. Nieuwe laptopspraak: login en browseraudio werken; fysieke beoordeling staat open.',
        });
        project.acceptedDelivery = acceptedMemoryDelivery(board);
        const limitedCardAppreciation = board.audit?.initiativeDeliveryAU007?.status === 'done_limited_card_appreciation'
          && board.audit.initiativeDeliveryAU007.ownerAccepted === true;
        if (limitedCardAppreciation) {
          project.capabilities = project.capabilities.map(item => item.id !== 'initiative' ? item : {
            ...item,
            outcomes: item.outcomes?.map(outcome => outcome.id !== 'initiative-private-result' ? outcome : {
              ...outcome, title: 'Beoordelingskaart voor huisautomatisering', accepted: 'yes',
              works: 'Jij vindt deze concrete kaart “op zich wel bruikbaar”.',
              tryIt: 'De kaart is al beoordeeld. De waardering geldt uitsluitend voor dit document.',
              gap: 'Afgerond binnen deze beperkte kaartscope; bredere initiatiefkwaliteit blijft apart.',
              detail: 'AU-007 heeft technische GO en beperkte waardering van de kaart. Geen algemene acceptatie van eigen initiatief.',
            }),
          });
          project.practiceTests = project.practiceTests?.map(test => test.id !== 'IN-01' ? test : {
            ...test, status: 'accepted', steps: [], result: 'Kaart beoordeeld: “op zich wel bruikbaar”. Dit geldt alleen voor deze kaart; geen algemene acceptatie van eigen initiatief.',
          });
        }
        if (project.acceptedDelivery && !project.acceptedDelivery.reviewOpen) {
          project.capabilities = project.capabilities.map(item => item.id !== 'memory' ? item : {
            ...item, gapKind: 'Vervolgambities', gap: 'CO-008 is afgerond. Bredere langetermijn- en gezinskwaliteit blijven vervolgwerk.',
            stageNotes: { ...item.stageNotes, accepted: 'GE-01 is door Jaap geaccepteerd; de onafhankelijke review heeft GO gegeven en CO-008 is afgesloten. Dit is geen volledige gezinsacceptatie.' },
            outcomes: item.outcomes?.map(outcome => !['memory-source', 'memory-preference'].includes(outcome.id) ? outcome : {
              ...outcome, gap: outcome.id === 'memory-preference' ? 'Afgerond, inclusief onafhankelijke technische review.' : 'CO-006 is technisch afgerond; de bijbehorende CO-008-gespreksproef is ook afgerond.',
              tryIt: 'GE-01 is geaccepteerd en CO-008 is afgesloten. Gewoon blijven gebruiken via Telegram.',
            }),
          });
        }
        if (project.acceptedDelivery) project.nextOutcomes = project.nextOutcomes?.map(item => item.title !== 'Geheugen afronden' ? item : {
          title: project.acceptedDelivery.reviewOpen ? 'Alleen technische afronding resteert' : 'Geheugenresultaat afgerond',
          detail: project.acceptedDelivery.review,
        });
        // The matrix is the only maintained result summary; avoid repeated claims.
        project.summary = [];
        const card = (task) => ({ id: task.id, title: task.title, outcome: task.outcome,
          status: task.status, doneWhen: task.doneWhen, area: task.area,
          ...(task.id === 'AU-008' && board.audit?.initiativeDeliveryAU008 ? {
            displayOutcome: project.capabilities.find(item => item.id === 'initiative')?.currentWork || board.audit.initiativeDeliveryAU008.currentWork,
            ...(task.status === 'review' && board.audit.initiativeDeliveryAU008.technicalComplete === true ? {
              displayKicker:'ALLEEN JOUW BEOORDELING', displayTitle:'Eva’s eigen bijdrage ligt klaar',
              displayStatus:'Technisch afgerond en live · IN-02 open',
            } : {}),
          } : {}),
          ...(task.id === 'AU-007' && board.audit?.initiativeDeliveryAU007?.ownerContextRepair ? {
            progress: {
              works: 'Een echte huiswaarneming leidde tot een eigen keuze; stoppen via de eigenaarroute werkte. De eerdere keuze herhaalde een al afgeronde opdracht.',
              repair: 'De koppeling met kennis uit gewone Telegramgesprekken is hersteld en technisch getest.',
              next: 'De beoordelingskaart is uitgewerkt en teruggelezen; eerdere proefmislukkingen blijven in het bewijs staan.',
              open: limitedCardAppreciation ? 'Kaart afgerond met beperkte waardering; bredere initiatiefkwaliteit blijft apart.' : 'Jouw beoordeling of de kaart bruikbaar is. AU-007 is nog niet door jou geaccepteerd.',
            },
            resultArtifact: { title: 'Beoordelingskaart voor gezinsvriendelijke automatiseringen', url: '/results/au007-beoordelingskaart.html', status: limitedCardAppreciation ? 'Kaart beoordeeld: op zich wel bruikbaar' : 'Document beschikbaar · jouw nutbeoordeling staat open' },
          } : {}),
          ...(task.id === project.acceptedDelivery?.taskId && project.acceptedDelivery.reviewOpen ? {
            displayTitle: 'Technische review van CO-008', displayOutcome: project.acceptedDelivery.review,
            displayKicker: 'REVIEW EN ADMINISTRATIEVE AFRONDING', displayStatus: 'Gebruikersresultaat afgerond · review nog open',
          } : {}) });
        applyProductCompletion(project, board);
        applyCurrentCapabilitySummaries(project, board);
        response.end(JSON.stringify({
          id: 'project', contract: 'EvaProjectOverview/v1',
          revision: createHash('sha256').update(raw).digest('hex'),
          updatedAt: metadata.mtime.toISOString(), readAt: new Date().toISOString(),
          project, runtime, browserAccess, active: card(primary),
          queued: board.executionTasks.filter((task) => task.status === 'queued').map(card),
          milestones: board.milestones.map(card),
          completed: board.executionTasks.filter((task) => task.status === 'done').map(card),
        }));
      } catch {
        response.writeHead(503); response.end(JSON.stringify({ error: 'Projectbron niet beschikbaar' }));
      }
    });
  };
  return { name: 'eva-project-source', configureServer: configure, configurePreviewServer: configure };
}
