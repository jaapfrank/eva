import {applyMemoryPresentation} from './memory-presentation.mjs';
import {optionalOptions} from './scope-decisions.mjs';
// Presentation of the existing workboard and product scope; never changes task state.
const groups = {
  'answer-stream': ['Antwoordvorming en volledig antwoord via Telegram', ['CO-001', 'CO-007']],
  conversation: ['Gewone gesprekken, eigenheid en gezinscontinuïteit', ['RT-001', 'CO-007', 'UP-001', 'CP-000', 'EV-001', 'EV-002', 'EV-003', 'CP-001', 'CP-002', 'CP-003']],
  memory: ['Gekozen geheugenfuncties: onthouden, bijwerken, inzien en vergeten', ['CO-006', 'CO-008', 'CO-004', 'CO-005']],
  initiative: ['Zelf kiezen, uitvoeren en gewaardeerd bijdragen', ['AU-007', 'AU-008', 'AU-005']],
  tools: ['Onbekende diensten en combinatietaken afmaken', ['TL-001', 'TL-002', 'AU-001', 'TL-003']],
  voice: ['Doorlopend gesprek op laptop, telefoon en thuis', ['VB-001', 'VB-002']],
  projects: ['Hulpwerk, leren en meerdaagse projecten', ['AU-002', 'UX-002', 'AU-004', 'AU-006', 'AU-003']],
  browser: ['Bestaande Chrome bedienen, overnemen en herstellen', ['VB-003', 'VB-004']],
  installation: ['Installeren, verhuizen, bijwerken en herstellen', ['GOV-001', 'GOV-002', 'GOV-003', 'UX-000', 'UX-001', 'RB-001', 'RB-002', 'RB-003', 'RB-004', 'RB-005', 'RB-006', 'RR-001', 'RR-002', 'IN-001', 'IN-002', 'IN-003', 'FN-001', 'RB-007', 'RB-008', 'RB-009', 'RB-010', 'FN-002', 'FN-003']],
};
const scopeSource = 'eva2/PROJECTDOEL.md · audit.projectDashboard.capabilities';
const requirements = {
  'answer-stream': ['Zichtbare Telegramstream door Jaap bevestigen'],
  conversation: ['Privégesprek: warmte, eigen oordeel en samenhang door Jaap beoordelen', 'Gezinsgebruik, langdurige gesprekscontinuïteit en persoonsgrenzen aantonen'],
  memory: ['Langetermijn- en gezinskwaliteit van het automatische geheugen aantonen'],
  initiative: ['Nut, timing en hinder van herhaalde bijdragen door het gezin beoordelen'],
  tools: ['Onbekende dienst zonder ontwikkelhulp aansluiten en opdracht afmaken', 'Persoonlijke aanmeldingen en bredere combinatietaken bewijzen'],
  voice: ['Fysiek laptopgesprek, echo en antwoordvertraging beoordelen', 'Telefoonroute installeren en in spraak/chat beproeven', 'Bestaande thuisapparaten aansluiten met werkende terugweg', 'Doorlopend gesprek en geheugen tijdens lang werk behouden', 'Verbinding beëindigen en na uitval herstellen', 'Andere toegelaten personen met eigen context en geweigerde acties beproeven'],
  projects: ['Telegram: starten, later stoppen, hervatten en resultaat terugvragen'],
  browser: ['Gewone opdracht via Eva voert een omkeerbare taak uit in het expliciet gedeelde bestaande Chrome-tabblad', 'Jaap blijft meekijken in hetzelfde Chrome-tabblad terwijl Eva bedient', 'Overnemen blokkeert nieuwe acties en stopt een reeds lopende Eva-actie', 'Alleen expliciet teruggeven hervat met een nieuwe paginawaarneming; oude acties blijven ongeldig', 'Intrekken of wegvallen van de verbinding blokkeert Eva zichtbaar', 'Jaap beoordeelt de werkende Chrome-route; acceptatie blijft afzonderlijk'],
  installation: [],
};
const scopeTrials = {'answer-stream-scope-0': 'TG-01', 'conversation-scope-0': 'GS-01', 'conversation-scope-1': 'GS-02'};
const optional = new Set(Object.keys(optionalOptions));
const stateLabel = status => ({done:'Klaar binnen kaartscope', in_progress:'In uitvoering', paused:'Gepauzeerd', blocked:'Geblokkeerd', queued:'Open', review:'In review', ready_for_review:'In review'}[status] || status || 'Open');

export function aggregateCompletion(steps) {
  const required = steps.filter(step => step.required);
  return { complete: required.length > 0 && required.every(step => step.complete), done: required.filter(step => step.complete).length, total: required.length };
}

export function applyProductCompletion(project, board) {
  const memoryAcceptance = applyMemoryPresentation(project, board);
  const tasks = [...board.executionTasks, ...board.milestones];
  const included = new Set();
  for (const row of project.capabilities) {
    const [scope, ids] = groups[row.id];
    let steps = ids.map(id => {
      const task = tasks.find(item => item.id === id);
      if (!task) return { id, title:id, required:!optional.has(id), complete:false, status:'Geen bronbewijs', detail:'Kaart ontbreekt in actuele bron.', criteria:[] };
      included.add(id);
      const outcome = row.outcomes?.find(item => item.subtitle?.includes(id));
      const accepted = outcome?.accepted === 'yes';
      return { id, title:task.title, required:!optional.has(id), complete:task.status === 'done', status:stateLabel(task.status || 'queued'), detail:task.outcome, criteria:task.doneWhen || [], accepted, source:'execution-workboard-v1.json',
        ...(id === 'AU-002' ? { title:'Hulpagent via dashboardchat bedienen', detail:`${row.visibleResult} ${row.gap}` } : {}),
        ...(id === 'AU-008' ? { detail:`${row.visibleResult} ${row.gap}`,
          ...(task.status === 'review' && board.audit?.initiativeDeliveryAU008?.technicalComplete === true
            ? {complete:true,status:'Technisch afgerond · jouw beoordeling open'} : {}) } : {}),
      };
    });
    const memoryInventory = row.id === 'memory' && board.audit?.memoryComponentDecision20260911;
    if (memoryInventory?.requiredEvidenceInventory) {
      for (const item of memoryInventory.requiredEvidenceInventory) steps.push({id:item.id,title:item.outcome,required:false,evidenceOnly:true,complete:item.id==='memory-automatic-use' && project.acceptedDelivery!=null,status:'Bestaand bewijs en afbakening',detail:item.assessment,source:item.evidence.join(' · '),criteria:[]});
      steps.push({id:'memory-selected-delivery',title:'Bewijs voor de gekozen geheugenoplevering afronden',required:true,complete:memoryInventory.status==='completed',status:memoryInventory.status==='completed' ? 'Afgerond' : 'Bewijsafbakening in voorbereiding',detail:memoryInventory.next,source:'audit.memoryComponentDecision20260911',criteria:[]});
    }
    for (const [index, title] of (memoryInventory?.requiredEvidenceInventory ? [] : requirements[row.id]).entries()) {
      const id = `${row.id}-scope-${index}`;
      const trial = project.practiceTests?.find(item => scopeTrials[id] && item.id === scopeTrials[id]);
      const accepted = trial?.status === 'accepted' && trial.ownerAccepted === true;
      steps.push({id, title, required:true, complete:accepted, accepted,
        status:accepted ? 'Door Jaap geaccepteerd' : 'Nog niet bewezen',
        detail:accepted ? trial.result : row.gap,
        source:accepted ? trial.acceptanceEvidence : scopeSource, criteria:[]});
    }
    if (row.id === 'voice') for (const test of project.practiceTests.filter(item => item.id.startsWith('SP-'))) {
      steps.push({id:test.id,title:test.title,required:true,complete:test.status === 'accepted',accepted:test.status === 'accepted',status:test.status === 'accepted' ? 'Door Jaap geslaagd' : 'Open',detail:test.result || test.change,source:'audit.projectDashboard.practiceTests',criteria:test.steps || []});
    }
    if (row.id === 'voice' && row.firstPartProof) steps.push({id:'voice-first-part',title:row.firstPartProof.title,required:true,complete:true,status:'Beperkte technische proef geslaagd',accepted:false,detail:row.firstPartProof.detail,source:row.firstPartProof.source,criteria:[]});
    // Keep narrow accepted results explicit even when a broader card has other requirements.
    if (row.id === 'memory') {
      const preference = steps.find(step => step.id === 'CO-008');
      if (preference) preference.accepted = project.acceptedDelivery != null;
    }
    const decisionArchive = [];
    steps = steps.filter(step => {
      if (!optional.has(step.id) && !board.audit?.optionalScopeDecisions?.[step.id]) return true;
      const record = board.audit?.optionalScopeDecisions?.[step.id];
      const decision = ['included','excluded'].includes(record?.decision) ? record.decision : 'undecided';
      step.decision = decision;
      step.required = decision !== 'excluded';
      step.complete = decision === 'included' && step.complete;
      step.status = decision === 'undecided' ? 'Keuze nodig · nog geen bouwopdracht' : decision === 'included' ? `Opgenomen · ${step.status}` : 'Afgewezen';
      if (decision === 'excluded') { decisionArchive.push(step); return false; }
      return true;
    });
    const failed = row.id === 'projects' && (board.audit?.au002Delivery?.telegramUserTrial?.status === 'failed' || /Telegram.*mislukt|Telegramproef|geweigerd/i.test(`${row.userOutcomeStatus} ${row.visibleResult}`))
      || row.id === 'browser' && project.capabilityAudit?.ownerBrowserUpdate?.viewerTrial === 'failed'
      || row.id === 'voice' && project.practiceTests.some(test => test.id === 'SP-04' && /niet opgelost/.test(test.result || ''));
    const acceptedSteps = steps.filter(step => step.accepted);
    row.product = {
      scope, steps, decisionArchive, undecided:steps.filter(step=>step.decision==='undecided').length, ...aggregateCompletion(steps),
      tested:{state:row.tested === 'yes' ? 'partial_passed' : 'unknown',label:row.tested === 'yes' ? 'Deelproeven geslaagd' : 'Onvolledig',detail:'Technische deelproeven zijn bewijs voor hun eigen scope; geen volledige eindtest.'},
      jaap:{state:failed ? 'failed' : acceptedSteps.length ? 'partial_passed' : row.accepted === 'partial' ? 'partial' : 'unknown',label:failed ? (row.id === 'projects' ? 'Telegram mislukt' : row.id === 'voice' ? 'Wakeword mislukt' : 'Kijkproef mislukt') : acceptedSteps.length ? 'Deelproef geslaagd' : row.accepted === 'partial' ? 'Beperkt beoordeeld' : 'Ongetest / geen bewijs',detail:failed ? `${row.id === 'projects' ? 'Telegramproef 11 sep 09:40: hulpagent geweigerd. Dashboard-HTTP-proef blijft bewezen.' : row.id === 'browser' ? 'Kijkverbinding mislukte; losse Chrome-proef wel gelukt.' : 'Onterechte wakeword-activering nog niet opgelost; SP-01 en SP-02 blijven geaccepteerd.'}` : acceptedSteps.map(step => step.id).join(', ') || row.gap},
      comparison:{state:'unknown',label:row.id === 'conversation' ? 'Beperkt · onbekend' : 'Niet vergeleken',detail:row.id === 'conversation' ? 'EV-002: één casus, Eva en Hermes voldoen aan dezelfde drie criteria; twee deelnemers geblokkeerd. Geen representatieve rangorde. Brononderzoek UP-001 bewijst geen concurrentiewinst.' : 'Geen representatieve vergelijking met uitkomst beter, even of slechter vastgelegd. CP-001/002/003 blijven aparte werkkaarten.'},
    };
    if (!failed && row.product.complete && row.accepted === 'yes') {
      row.product.jaap = {state:'passed', label:'Geaccepteerd', detail:row.gap};
    }
  }
  if (memoryAcceptance) project.capabilities.find(row => row.id === 'memory').product.jaap = memoryAcceptance;
  const feedback = board.audit?.voiceLaptopOwnerFeedback20260911;
  if (feedback?.laptopInterruptionAccepted === true) {
    const voice = project.capabilities.find(row => row.id === 'voice');
    voice.product.jaap = {state:'partial_passed',label:'Onderbreken geslaagd',detail:`Alleen laptoponderbreken fysiek geaccepteerd. ${feedback.remaining} SP-01/SP-02 blijven geaccepteerd; onterechte wakewords blijven apart open.`};
  }
  project.completionSource = {unmappedTaskIds:tasks.filter(task => !included.has(task.id)).map(task => task.id), source:'execution-workboard-v1.json + PROJECTDOEL.md', note:'Teller: afgeronde vereiste stappen, geen productpercentage. Onbesliste opties blokkeren; opgenomen opties moeten af; afgewezen opties tellen niet mee. Kaart klaar is geen algemene gebruikersacceptatie.'};
}
