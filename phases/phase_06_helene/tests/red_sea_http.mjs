/** Actual read-only HTTP regression through the preserved frontend client. */
import assert from 'node:assert/strict';
import { writeFile } from 'node:fs/promises';
import { createOperatorClient } from '../../phase_01_foundation/dashboard_design/src/operatorApi.ts';
const origin=process.argv[2]||'http://127.0.0.1:5186';
const client=createOperatorClient({baseUrl:origin+'/api/v1'});
await client.health();
const listing=await client.investigations();
const parent=listing.items.find(c=>c.scenario==='red-sea'&&!c.parent_id);
const child=listing.items.find(c=>c.parent_id===parent?.investigation_id);
assert.ok(parent&&child);
const results=[];
for(const [summary,expected] of [[parent,24],[child,30]]){
  const data=await client.workspace(summary.investigation_id);
  const values=Object.fromEntries(data.forecasts.filter(f=>f.forecast.request.hospital_id==='sim-central').map(f=>[f.case,f.forecast.total_unmet]));
  assert.deepEqual(values,{baseline:0,disruption:expected,mitigation:0});
  assert.ok(data.snapshot.mitigation.evaluations.some(e=>e.failures.some(f=>f.code==='DONOR_SHORTAGE')));
  assert.ok(data.actions.every(a=>a.dispatch_available===false));
  results.push({id:summary.investigation_id,unmet:values,previous_option_recheck:data.snapshot.revision?.previous_option_recheck??null});
}
assert.equal(results[1].previous_option_recheck.remaining_unmet,6);
const receipt={status:'passed',classification:'actual frontend client / real loopback HTTP / preserved Red Sea',origin,results};
if(process.argv[3])await writeFile(process.argv[3],JSON.stringify(receipt,null,2)+'\n');
console.log(JSON.stringify(receipt,null,2));
