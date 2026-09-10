import { readFile, stat } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';

const boardFile = process.env.EVA_PROJECT_BOARD_FILE || fileURLToPath(new URL('../../design/product/execution-workboard-v1.json', import.meta.url));
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
    const initiative = board.audit?.initiativeDeliveryAU008;
    if (item.id === 'initiative' && initiative) {
      for (const stage of ['built','tested','live']) row[stage] = initiative[stage] === true ? 'yes' : 'partial';
      row.accepted = initiative.ownerAccepted === true ? 'yes' : 'no';
      row.scope = `AU-008 · ${initiative.liveCapabilityCount ?? 'toegelaten'} kosteloze taakroutes`;
      row.currentWork = initiative.currentWork;
      row.stageNotes = { ...row.stageNotes, tested: initiative.testEvidence, live: `Centrale bron: ${initiative.liveImage}; ${initiative.implementationCommit}.`, accepted: 'Dit betreft AU-008. De beperkte waardering van de eerdere AU-007-kaart staat afzonderlijk.' };
    }
    return row;
  });
}

async function applyCapabilityAudit(project, board) {
  try {
    applyReviewedCapabilities(project, board, JSON.parse(await readFile(auditFile, 'utf8')));
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
        const project = structuredClone(board.audit.projectDashboard);
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
          ...(task.id === 'AU-008' && board.audit?.initiativeDeliveryAU008 ? { displayOutcome: board.audit.initiativeDeliveryAU008.currentWork } : {}),
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
