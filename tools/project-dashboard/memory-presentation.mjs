// GE-01 concerns the earlier automatic-memory outcome; GE-02 separately
// evaluates the newly selected source controls. Technical completion is not consent.
export function applyMemoryPresentation(project, board) {
  const row = project.capabilities.find(item => item.id === 'memory');
  const trial = project.practiceTests?.find(item => item.id === 'GE-02');
  if (!row || !trial) return null;
  const priorAccepted = Boolean(project.acceptedDelivery);
  const controlsAccepted = trial.status === 'accepted';
  const performed = trial.status === 'awaiting_result';
  const tasks = [...board.executionTasks, ...board.milestones];
  const controls = ['CO-004', 'CO-005'].map(id => tasks.find(task => task.id === id)).filter(Boolean);
  const complete = controls.length === 2 && controls.every(task => task.status === 'done');
  const label = `${priorAccepted ? 'GE-01 geslaagd' : 'GE-01 open'} · ${controlsAccepted ? 'GE-02 geslaagd' : performed ? 'GE-02 uitgevoerd · uitslag open' : 'GE-02 open'}`;
  const detail = `${priorAccepted ? 'GE-01 bevestigt alleen de eerdere CO-008-geheugenproef.' : 'GE-01 is niet als geaccepteerd vastgelegd.'} ${controlsAccepted ? 'GE-02 is afzonderlijk door Jaap geaccepteerd.' : 'De nieuwe broninzage, correctie en vergeetbediening wachten op Jaaps GE-02-beoordeling.'}`;
  row.accepted = priorAccepted && controlsAccepted ? 'yes' : priorAccepted || controlsAccepted ? 'partial' : 'no';
  row.scope = 'Automatisch geheugen en gekozen bronbediening · CO-008, CO-004, CO-005';
  row.visibleResult = `${priorAccepted ? 'CO-008/GE-01 blijft geaccepteerd.' : 'Eerdere geheugenproef apart vastgelegd.'} ${complete ? `CO-004 en CO-005 zijn technisch afgerond${controlsAccepted ? ' en door Jaap geaccepteerd (GE-02)' : ''}: bronnen bekijken, corrigeren en bewust vergeten.` : 'De gekozen bronbediening wordt technisch afgerond.'}`;
  row.works = row.visibleResult;
  row.gapKind = controlsAccepted ? 'Afzonderlijk beoordeeld' : 'Jouw beoordeling';
  row.gap = controlsAccepted ? detail : performed ? 'GE-02 is door Jaap uitgevoerd; alleen de expliciete uitslag ontbreekt. De proef hoeft niet opnieuw.' : 'GE-02 staat klaar; Jaaps beoordeling van de nieuwe bediening is nog open. Technisch klaar betekent niet door Jaap geaccepteerd.';
  row.detail = `${row.visibleResult} ${row.gap}`;
  row.stageLabels = {...row.stageLabels, accepted:label};
  row.stageNotes = {...row.stageNotes, accepted:detail};
  row.outcomes = [ ...(row.outcomes || []).filter(item => !['memory-inspect', 'memory-controls'].includes(item.id)),
    ...controls.map(task => ({id:task.id === 'CO-004' ? 'memory-inspect' : 'memory-controls', title:task.title,
      subtitle:task.id, built:task.status === 'done' ? 'yes' : 'partial', tested:task.status === 'done' ? 'yes' : 'no',
      live:complete ? 'yes' : 'no', accepted:controlsAccepted ? 'yes' : 'no', detail:task.outcome,
      gap:controlsAccepted ? 'GE-02 afzonderlijk geaccepteerd.' : 'GE-02: menselijke beoordeling nog open.', tryIt:trial.change})) ];
  return {state:priorAccepted && controlsAccepted ? 'passed' : priorAccepted || controlsAccepted ? 'partial_passed' : 'unknown', label, detail};
}
