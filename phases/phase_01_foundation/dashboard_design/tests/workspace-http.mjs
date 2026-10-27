/** Read-only acceptance against real saved cases via the exact frontend client. */
import assert from 'node:assert/strict';
import { writeFile } from 'node:fs/promises';
import { createOperatorClient } from '../src/operatorApi.ts';
const origin=process.argv[2]||'http://127.0.0.1:5174';
const requests=[];
const client=createOperatorClient({baseUrl:origin+'/api/v1',fetcher:async(url,init)=>{
  const response=await fetch(url,init);requests.push({method:init?.method||'GET',path:new URL(url).pathname,status:response.status});return response;
}});
await client.health();const list=await client.investigations();
const original=list.items.find(c=>c.scenario==='red-sea'&&!c.parent_id);
const revised=list.items.find(c=>c.parent_id===original?.investigation_id);
assert.ok(original&&revised);
const cases=[];
for(const [summary,unmet,quantity] of [[original,24,24],[revised,30,36]]){
  const data=await client.workspace(summary.investigation_id), c=data.snapshot;
  const values=Object.fromEntries(data.forecasts.filter(f=>f.forecast.request.hospital_id===c.mitigation.request.hospital_id).map(f=>[f.case,f.forecast.total_unmet]));
  assert.deepEqual(values,{baseline:0,disruption:unmet,mitigation:0});
  const selected=c.mitigation.evaluations.find(e=>e.candidate.candidate_id===c.mitigation.recommendation_id);
  assert.equal(selected.candidate.quantity,quantity);assert.equal(selected.feasible,true);assert.equal(selected.remaining_unmet,0);
  assert.equal(c.hospitals.length,3);assert.ok(c.hospitals.every(h=>h.state==='IL'&&h.country_iso==='USA'));
  assert.ok(c.evidence.some(e=>e.kind==='observed'&&e.source==='marad'));
  assert.ok(c.mitigation.evaluations.some(e=>e.failures.some(f=>f.code==='DONOR_SHORTAGE')));
  assert.ok(c.mitigation.evaluations.some(e=>e.failures.some(f=>f.code==='PRODUCT_MISMATCH')));
  assert.ok(c.mitigation.memory_ids.length>0);
  assert.ok(c.decision_ledger.some(r=>r.memory_ids.length||r.memory_refs.length));
  assert.ok(data.actions.every(a=>a.investigation_id===summary.investigation_id&&!a.dispatch_available));
  const clinical=c.clinical_impact.filter(i=>i.hospital_id===c.mitigation.request.hospital_id);
  if(!summary.parent_id)assert.deepEqual(clinical.filter(i=>i.case!=='baseline').map(i=>i.exposed_sessions),[12,0]);
  if(summary.parent_id){assert.equal(c.revision.parent_id,original.investigation_id);assert.equal(c.revision.previous_option_recheck.remaining_unmet,6);}
  cases.push({id:c.investigation_id,parent_id:c.parent_id,unmet_vials:values,selected:c.mitigation.recommendation_id,quantity,clinical:clinical.map(i=>({case:i.case,sessions:i.exposed_sessions})),hospitals:c.hospitals.map(h=>({id:h.hospital_id,name:h.name})),agent_records:c.agents.length,ledger_records:c.decision_ledger.length,evidence_records:c.evidence.length});
}
const resources=[];
for(const summary of [original,revised]){
  for(const section of ['forecast','mitigations','evidence','agents','decision-ledger','execution-graph','clinical-impact','actions','revision','forecast-deltas']){
    let offset=0,total=0,returned=0;
    do{
      const path=`/api/v1/investigations/${summary.investigation_id}/${section}?limit=100&offset=${offset}`;
      const response=await fetch(origin+path);requests.push({method:'GET',path,status:response.status});assert.equal(response.status,200);
      const body=await response.json();assert.equal(body.investigation_id,summary.investigation_id);assert.equal(body.availability,'available');
      total=body.total;returned+=body.items.length;if(returned<total)assert.ok(body.items.length>0);offset=returned;
    }while(returned<total);
    resources.push({id:summary.investigation_id,resource:section,total,returned});
  }
  for(const section of ['hospitals','medicines','disruptions','map/layers']){
    const path=`/api/v1/${section}?investigation_id=${summary.investigation_id}&limit=100`;
    const response=await fetch(origin+path);requests.push({method:'GET',path,status:response.status});assert.equal(response.status,200);
    const body=await response.json();assert.equal(body.investigation_id,summary.investigation_id);
    resources.push({id:summary.investigation_id,resource:section,total:body.total,availability:body.availability});
  }
}
const directHealth=await fetch('http://127.0.0.1:8767/api/v1/health');assert.equal(directHealth.status,200);
const result={checked_at:new Date().toISOString(),origin,direct_backend_health_status:directHealth.status,requests,cases,resources,live_inference_requests:0,review_mutations:0,status:'passed'};
if(process.argv[3])await writeFile(process.argv[3],JSON.stringify(result,null,2)+'\n');
console.log(JSON.stringify(result,null,2));
