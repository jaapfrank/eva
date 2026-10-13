import {test} from 'node:test';
import assert from 'node:assert/strict';
import {applyCurrentCapabilitySummaries} from './project-server.mjs';
import {applyProductCompletion} from './project-completion.mjs';

test('explicit current summary replaces dated narrative but cannot grant completion', () => {
  const historical = {id:'browser', works:'Old result', visibleResult:'Old visible',
    gap:'Old gap',detail:'Old detail',built:'partial',tested:'partial',live:'partial',
    accepted:'no',resultBasis:'11 september',evidence:'dated-proof.json',outcomes:[]};
  const board = {executionTasks:[],milestones:[],audit:{projectDashboard:{capabilities:[{
    id:'browser',currentSummary:{works:'Installed navigation and click',gap:'Owner trial remains',
      detail:'Current scope',checkedAt:'2026-10-03T14:00:00Z',evidencePaths:['current-proof.json'],accepted:'yes',built:'yes',evidence:'fake'}}]}}};
  const project={capabilities:[structuredClone(historical)],practiceTests:[]};
  const original=structuredClone(project);
  const before=structuredClone(board);
  applyCurrentCapabilitySummaries(project,board);
  const row=project.capabilities[0];
  assert.equal(row.works,'Installed navigation and click');
  assert.equal(row.visibleResult,row.works);
  assert.equal(row.gap,'Owner trial remains');
  assert.equal(row.detail,'Current scope');
  for(const key of ['built','tested','live','accepted','outcomes'])
    assert.deepEqual(row[key],historical[key]);
  assert.equal(row.historicalSummary.visibleResult,'Old visible');
  assert.equal(row.historicalSummary.resultBasis,'11 september');
  assert.equal(row.historicalSummary.evidence,'dated-proof.json');
  assert.equal(row.resultBasis,'Actuele werkbordstand · 2026-10-03T14:00:00Z');
  assert.equal(row.evidence,'dated-proof.json · current-proof.json');
  applyProductCompletion(original,board);
  applyProductCompletion(project,board);
  assert.equal(row.product.complete,original.capabilities[0].product.complete);
  assert.deepEqual(row.product.steps.map(s=>s.complete),original.capabilities[0].product.steps.map(s=>s.complete));
  assert.deepEqual(board,before);
});

test('unmarked or malformed summaries keep dated evidence and valid partial fields override only text', () => {
  for(const summary of [undefined,null,[],{works:'',gap:3}]) {
    const project={capabilities:[{id:'installation',works:'Dated',gap:'Open'}]};
    const before=structuredClone(project);
    applyCurrentCapabilitySummaries(project,{audit:{projectDashboard:{capabilities:[{id:'installation',currentSummary:summary}]}}});
    assert.deepEqual(project,before);
  }
  const project={capabilities:[{id:'installation',works:'Dated',gap:'Old'}]};
  applyCurrentCapabilitySummaries(project,{audit:{projectDashboard:{capabilities:[{id:'installation',currentSummary:{gap:'Current'}}]}}});
  assert.equal(project.capabilities[0].works,'Dated');
  assert.equal(project.capabilities[0].gap,'Current');
});


test('current memory summary follows actual memory projection without changing acceptance or counts', () => {
  const board={executionTasks:['CO-004','CO-005'].map(id=>({id,title:id,status:'done',outcome:'Controls'})),
    milestones:[],audit:{projectDashboard:{capabilities:[{id:'memory',currentSummary:{
      works:'M1.2: recent context installed',gap:'Family recognition remains open',
      detail:'Current memory delivery',checkedAt:'2026-10-03T15:00:00Z'}}]}}};
  const project={acceptedDelivery:{taskId:'CO-008'},practiceTests:[{id:'GE-02',status:'accepted'}],
    capabilities:[{id:'memory',accepted:'yes',tested:'yes',outcomes:[],works:'Old',gap:'Old',resultBasis:'11 September'}]};
  applyProductCompletion(project,board);
  const row=project.capabilities[0];
  const before=structuredClone(row);
  assert.match(row.visibleResult,/CO-008\/GE-01/);
  applyCurrentCapabilitySummaries(project,board);
  assert.equal(row.visibleResult,'M1.2: recent context installed');
  assert.equal(row.works,row.visibleResult);
  assert.equal(row.product.delivery.result,row.visibleResult);
  assert.equal(row.product.delivery.followup,'Family recognition remains open');
  assert.equal(row.historicalSummary.visibleResult,before.visibleResult);
  assert.equal(row.accepted,before.accepted);
  assert.deepEqual(row.stageNotes,before.stageNotes);
  assert.deepEqual(row.product,{...before.product,delivery:{...before.product.delivery,
    result:row.visibleResult,followup:row.gap}});
});
