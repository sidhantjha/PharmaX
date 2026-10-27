export interface Forecast {hospital_id:string;quantity_unit:'bag';initial_usable:number;total_received:number;total_unmet:number;expected_daily_demand:number;projected_stockout_date:string|null;analysis_id:string;as_of:string;end_on:string;points:{day:string;unmet:number;closing_usable:number}[]}
export interface Action {action_id:string;quantity:number;quantity_unit:'bag';draft:string;status:string;revision:number;dispatch_available:false;outstanding_verification:string[]}
export interface Evidence {evidence_id:string;kind:string;source:string;source_url:string;observed_at:string;content_sha256:string;note:string}
export interface HeleneCase {
  schema_version:'6.1';investigation_id:string;parent_id:string|null;dataset_version:string;
  checkpoint:{cutoff:string;mode:string;limitations:string[]};
  admission:{sources:{revision_id:string;issue_date:string;evidence:Evidence}[];allocations:{notice_id:string;source_revision_id:string;allocation_percent:number;kind:string;effective_on:string;page:number}[]};
  operations:{hospitals:{hospital_id:string;name:string}[];inventory:{hospital_id:string;lot_id:string;on_hand:number;reserved:number;expiry_date:string}[];purchase_orders:{hospital_id:string;purchase_order_id:string;quantity:number;account_id:string}[];acknowledgments:{purchase_order_id:string;quantity:number;status:string}[]};
  forecasts:Forecast[];mitigated:Forecast[];selected:string[];actions:Action[];evidence:Evidence[];unresolved:string[];
  map_nodes:{id:string;title:string;point:[number,number]}[];
  candidates:{candidate_id:string;kind:string;source_id?:string;hospital_id:string;quantity:number;remaining_unmet:number;feasible:boolean;failures:string[];donor_unmet?:number}[];
  decisions:{agent_id:string;tool:string;execution:string;result:unknown}[];memory:unknown;reconciliation:unknown[];
}
export interface Summary {investigation_id:string;parent_id:string|null;checkpoint:{cutoff:string};reconciliation_scenario?:string|null}
export interface NativeRole {request_records?:{number:number;started_at:string;completed_at?:string;http_status?:number}[];completed_at?:string;zetaris_trace_id?:string;role_id:string;role_name?:string;status:string;tool_selected?:string;tool_input?:unknown;tool_result_reference?:string;tool_result_sha256?:string;predecessor_roles?:string[];memory_ids?:string[];structured_finding?:unknown;model_receipts?:unknown[];validation_review?:string;error_code?:string}
export interface NativeTask {task_id:string;agent_id:string;dependency_ids:string[];started_at?:string;completed_at?:string;status:string}
export interface ModelVerification {schema_version:'model-verification-1';available:boolean;mode:'unavailable'|'nightshift-live-model';run_id:string|null;roles_executed:number;findings_validated:number;model_requests:number;model:string|null;status:'complete'|'partial'|null;roles:NativeRole[];data_layer:{mode?:string;trace_id?:string;returned_rows?:number;source_tables?:unknown;query?:string};hmem:{engine?:string;writes?:unknown[];result?:{memories?:unknown[]}};graph:NativeTask[];budget?:unknown;limitations?:string[]}
export function validateModelVerification(value:unknown):ModelVerification {
  const v=value as ModelVerification;
  if(!v||v.schema_version!=='model-verification-1'||typeof v.available!=='boolean'||!Array.isArray(v.roles)||
    !Number.isInteger(v.roles_executed)||!Number.isInteger(v.findings_validated)||v.findings_validated>v.roles_executed||v.roles_executed>12||v.findings_validated<0||
    (v.available&&(v.mode!=='nightshift-live-model'||!v.run_id||v.roles.length!==12))||
    (!v.available&&(v.mode!=='unavailable'||v.roles.length!==0||v.model_requests!==0)))throw Error('MODEL_VERIFICATION_INVALID');
  return v;
}
export function validateHelene(value:unknown):HeleneCase {
  const c=value as HeleneCase;
  if(!c||c.schema_version!=='6.1'||!c.investigation_id?.startsWith('helene-')||
    !['strict-historical','reconstructed-historical-simulation'].includes(c.checkpoint?.mode)||
    c.operations?.hospitals?.length!==3||c.forecasts?.length!==3||c.mitigated?.length!==3||c.map_nodes?.length!==3||
    !Array.isArray(c.admission?.sources)||!Array.isArray(c.actions)||!Array.isArray(c.evidence)||!Array.isArray(c.candidates))throw Error('HELENE_SCHEMA_INVALID');
  const ids=new Set(c.operations.hospitals.map(h=>h.hospital_id));
  if(ids.size!==3||[...ids].some(id=>!['sim-nc-central','sim-nc-north','sim-nc-south'].includes(id)))throw Error('HELENE_IDENTITY_INVALID');
  for(const f of [...c.forecasts,...c.mitigated])if(f.quantity_unit!=='bag'||!ids.has(f.hospital_id)||f.points?.length!==14||
    [f.initial_usable,f.total_received,f.total_unmet,f.expected_daily_demand].some(n=>!Number.isSafeInteger(n)||n<0))throw Error('HELENE_FORECAST_INVALID');
  if(c.actions.some(a=>a.dispatch_available!==false||a.quantity_unit!=='bag'))throw Error('HELENE_ACTION_INVALID');
  return c;
}
async function read(url:string,signal?:AbortSignal){const r=await fetch(url,{signal});if(!r.ok)throw Error(`HELENE_HTTP_${r.status}`);return r.json();}
let listing:Promise<Summary[]>|null=null;
export const heleneApi={
  async modelVerification(id:string,signal?:AbortSignal){return validateModelVerification(await read('/api/v1/helene/cases/'+encodeURIComponent(id)+'/model-verification',signal));},
  async list(signal?:AbortSignal):Promise<Summary[]>{if(signal){const v=await read('/api/v1/helene/cases',signal);if(v.schema_version!=='6.1'||!Array.isArray(v.items))throw Error('HELENE_LIST_INVALID');return v.items;}if(!listing)listing=read('/api/v1/helene/cases').then(v=>{if(v.schema_version!=='6.1'||!Array.isArray(v.items))throw Error('HELENE_LIST_INVALID');return v.items;}).finally(()=>{listing=null;});return listing;},
  async get(id:string,signal?:AbortSignal){return validateHelene(await read('/api/v1/helene/cases/'+encodeURIComponent(id),signal));},
  async review(id:string,a:Action,reviewer:string):Promise<Action>{const r=await fetch(`/api/v1/helene/cases/${encodeURIComponent(id)}/actions/${encodeURIComponent(a.action_id)}/review`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({expected_revision:a.revision,status:a.status==='DRAFT'?'READY_FOR_REVIEW':'APPROVED',reviewer,attested:true})});if(!r.ok)throw Error('REVIEW_FAILED');const updated=await r.json();if(updated.dispatch_available!==false||updated.quantity_unit!=='bag')throw Error('REVIEW_SCHEMA_INVALID');return updated;}
};
