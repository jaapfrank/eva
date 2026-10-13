import {test} from 'node:test';
import assert from 'node:assert/strict';
import {applyProductCompletion} from './project-completion.mjs';

test('owner review preserves explicit technical completion without accepting the result', () => {
  for (const technicalComplete of [false, true]) {
    const board = {executionTasks:[{id:'AU-008',status:'review',title:'Eigen bijdrage',outcome:'Resultaat'}],
      milestones:[],audit:{initiativeDeliveryAU008:{technicalComplete}}};
    const project = {capabilities:[{id:'initiative',accepted:'no',tested:'yes',outcomes:[],
      visibleResult:'Bestaand resultaat',gap:'Jouw oordeel open'}],practiceTests:[]};
    applyProductCompletion(project,board);
    const row=project.capabilities[0];
    const step=row.product.steps.find(item=>item.id==='AU-008');
    assert.equal(step.complete,technicalComplete);
    assert.equal(step.accepted,false);
    assert.notEqual(row.product.jaap.state,'passed');
    assert.equal(board.executionTasks[0].status,'review');
    if(technicalComplete) assert.equal(step.status,'Technisch afgerond · jouw beoordeling open');
  }
});

test('explicit accepted Telegram trial closes its scope without accepting conversation scope', () => {
  for (const ownerAccepted of [false, true]) {
    const board = {executionTasks:['CO-001','CO-007'].map(id => ({id,status:'done',title:id,outcome:'Built'})), milestones:[],audit:{}};
    const project = {capabilities:[{id:'answer-stream',accepted:'yes',tested:'yes',outcomes:[],gap:'Trial'}],
      practiceTests:[{id:'TG-01',status:'accepted',ownerAccepted,result:'Streaming works',acceptanceEvidence:'owner-report.json'}]};
    applyProductCompletion(project,board);
    const product = project.capabilities[0].product;
    assert.equal(product.complete,ownerAccepted);
    assert.equal(product.jaap.state === 'passed',ownerAccepted);
    assert.equal(product.steps.find(s => s.id === 'CO-007').accepted,false);
  }
});

test('private conversation acceptance does not accept family use or product comparisons', () => {
  for (const ownerAccepted of [false, true]) {
    const board = {executionTasks:[],milestones:[],audit:{}};
    const project = {capabilities:[{id:'conversation',accepted:'partial',tested:'yes',outcomes:[],gap:'Family use remains open'}],
      practiceTests:[{id:'GS-01',status:'accepted',ownerAccepted,result:'Private dialogue works',acceptanceEvidence:'owner-report.json'}]};
    applyProductCompletion(project,board);
    const product=project.capabilities[0].product;
    assert.equal(product.steps.find(s=>s.id==='conversation-scope-0').complete,ownerAccepted);
    assert.equal(product.steps.find(s=>s.id==='conversation-scope-1').complete,false);
    assert.equal(product.steps.find(s=>s.id==='CP-001').complete,false);
    assert.equal(product.complete,false);
    assert.notEqual(product.jaap.state,'passed');
  }
});
