import {test} from 'node:test';
import assert from 'node:assert/strict';
import {applyMemoryPresentation} from './memory-presentation.mjs';
const board={executionTasks:['CO-004','CO-005'].map(id=>({id,title:id,status:'done',outcome:'Bronbediening'})),milestones:[]};
function project(status) {return {acceptedDelivery:{taskId:'CO-008'},practiceTests:[{id:'GE-02',status,change:'Bronbediening testen'}],capabilities:[{id:'memory',accepted:'yes',outcomes:[{id:'memory-preference',subtitle:'CO-008',accepted:'yes'}]}]};}
test('completed controls do not inherit acceptance from GE-01',()=>{
 const p=project('ready');const assessment=applyMemoryPresentation(p,board);const row=p.capabilities[0];
 assert.equal(row.accepted,'partial');assert.equal(assessment.state,'partial_passed');
 assert.equal(assessment.label,'GE-01 geslaagd · GE-02 open');
 assert.equal(row.outcomes.find(x=>x.subtitle==='CO-008').accepted,'yes');
 for(const id of ['CO-004','CO-005']) {const outcome=row.outcomes.find(x=>x.subtitle===id);assert.equal(outcome.built,'yes');assert.equal(outcome.accepted,'no');}
});
test('only explicit GE-02 acceptance marks the new controls accepted',()=>{
 const p=project('accepted');const assessment=applyMemoryPresentation(p,board);
 assert.equal(p.capabilities[0].accepted,'yes');assert.equal(assessment.label,'GE-01 geslaagd · GE-02 geslaagd');
 for(const id of ['CO-004','CO-005'])assert.equal(p.capabilities[0].outcomes.find(x=>x.subtitle===id).accepted,'yes');
});
