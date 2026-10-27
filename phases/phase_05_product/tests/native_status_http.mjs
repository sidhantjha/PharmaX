/** Actual frontend client / real loopback status API; zero inference. */
import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
import {heleneApi} from '../../phase_01_foundation/dashboard_design/src/heleneApi.ts';
const origin=process.argv[2];
assert.equal(new URL(origin).hostname,'127.0.0.1');
const originalFetch=globalThis.fetch;
globalThis.fetch=(url,options)=>originalFetch(new URL(String(url),origin),options);
const list=await heleneApi.list();
let found;
for(const c of list){
  const value=await heleneApi.modelVerification(c.investigation_id);
  if(value.available){found=value;break;}
}
assert.ok(found);
assert.equal(found.roles_executed,12);
assert.equal(found.findings_validated,11);
assert.equal(found.model_requests,27);
assert.equal(found.roles.find(r=>r.role_id==='hospital').status,'failed');
assert.equal(found.data_layer.mode,'live-zetaris');
assert.equal(found.model,'nvidia/nemotron-3.5-lightning-30b-a3b');
const report={classification:'Actual frontend client and saved status API over loopback HTTP; zero inference',
  origin,roles_executed:found.roles_executed,findings_validated:found.findings_validated,
  model_requests:found.model_requests,status:found.status,model:found.model,data_layer:found.data_layer};
if(process.argv[3])await writeFile(process.argv[3],JSON.stringify(report,null,2)+'\n');
console.log(JSON.stringify(report,null,2));
