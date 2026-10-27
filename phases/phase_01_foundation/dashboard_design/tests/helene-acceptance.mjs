/** Local isolated service acceptance. Never dispatches live inference. */
import assert from 'node:assert/strict';
import { writeFile } from 'node:fs/promises';
const origin=process.argv[2]??'http://127.0.0.1:8899';
assert.match(origin,/^http:\/\/127\.0\.0\.1:\d+$/);
async function read(path){const r=await fetch(origin+path);assert.equal(r.status,200,path);return r.json();}
const base='/api/v1/helene/cases';
const listing=await read(base), item=listing.items.find(c=>!c.parent_id&&c.investigation_id.includes('2024-10-01'));
assert.ok(item);const id=item.investigation_id,path=base+'/'+id;
const before=await read(path),ops=await read(path+'/operations'), verification=await read(path+'/verification'),native=await read(path+'/model-verification');
assert.deepEqual(ops.readiness.identities.map(i=>[i.original.local_sku,i.canonical_ndc,i.manufacturer_code,i.units_per_case]),[
  ['IV-NS1L','00338004904','2B1324X',12],['SAL-1000','00338004904','2B1324X',12],['NACL009-L','00338004904','2B1324X',12]]);
assert.equal(verification.scenario,'checkpoint');
assert.equal(native.roles_executed,12);assert.equal(native.findings_validated,11);
assert.notEqual(native.roles.find(r=>r.role_id==='hospital').status,'complete');
const questions=["What happened to Baxter's North Cove facility?",'Which hospitals face the greatest saline shortage?','Why were 800 units reportedly shipped but only 500 received?','What mitigation options were evaluated?','What happens to the forecast if another 120 bags arrive?'];
let conversation_id;const chats=[];
for(const question of questions){const r=await fetch(origin+path+'/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question,conversation_id,mode:'offline'})});assert.equal(r.status,200,question);const d=await r.json();assert.equal(d.model_executed,false);assert.equal(d.mode,'offline-deterministic');conversation_id=d.conversation_id;const turn=d.turns.at(-1);assert.equal(turn.question,question);assert.ok(turn.answer.length>0);assert.ok(turn.citations.length>0);const citations=[];for(const c of turn.citations){assert.ok(c.href.startsWith(path+'/chat-sources/'));await read(c.href);citations.push({label:c.label,status:200});}chats.push({question,answer:turn.answer,citations,model_executed:d.model_executed});}
const history=await read(path+'/chat/'+conversation_id);assert.equal(history.turns.length,5);
assert.deepEqual((await read(path)).forecasts,before.forecasts,'Hypothetical must not mutate canonical forecasts');
const b=listing.items.find(i=>i.reconciliation_scenario==='B');let discrepancy=null;
if(b){const v=await read(base+'/'+b.investigation_id+'/verification');const conflict=v.findings.find(f=>f.category==='conflict'&&f.discrepancy_bags===300);assert.ok(conflict);const linked=v.claims.filter(c=>conflict.claim_ids.includes(c.claim_id));assert.equal(linked.find(c=>c.meaning==='shipped').quantity,800);assert.equal(linked.find(c=>c.meaning==='accepted_receipt').quantity,500);discrepancy={case_id:b.investigation_id,shipped:800,accepted:500,unexplained:300,forecast_changes:v.forecast_changes};}
const result={status:'passed',origin,case_id:id,chat:chats,history_turns:history.turns.length,canonical_unchanged:true,identity_mappings:ops.readiness.identities,canonical:before.forecasts.map(f=>({hospital:f.hospital_id,unmet:f.total_unmet})),conservative:ops.forecasts.map(f=>({hospital:f.hospital_id,unmet:f.total_unmet})),saved_execution:{roles:native.roles_executed,validated:native.findings_validated,rows:native.data_layer.returned_rows},discrepancy,live_inference_requests:0};
if(process.argv[3])await writeFile(process.argv[3],JSON.stringify(result,null,2)+'\n');
console.log(JSON.stringify({status:result.status,questions:chats.length,citations:chats.map(c=>c.citations.length),discrepancy:!!discrepancy,canonical_unchanged:true,live_inference_requests:0}));
