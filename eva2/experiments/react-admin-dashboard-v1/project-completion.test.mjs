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
