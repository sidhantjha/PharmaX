import { useEffect, useState } from 'react';

export interface Claim {claim_id:string;meaning:string;classification:string;organization:string;record_id:string;document_id:string;originating_claim_id:string;quantity:number|null;unit:string;reason:string;facility_id?:string;order_id?:string;line_id?:string;product_id?:string;shipment_id?:string;issued_at?:string;evidence:{source_url:string;content_sha256:string;note:string}}
export interface Verification {schema_version:'7.1';scenario:string;claims:Claim[];findings:{finding_id:string;category:string;claim_ids:string[];reason:string;discrepancy_bags:number;verification_steps:string[];requires_human_review:boolean;independent_origins:number}[];physical_receipts:Record<string,number>;conditional_forecasts:{quantity:number;assumption:string;forecasts:{hospital_id:string;total_unmet:number}[]}[];forecast_changes:{hospital_id:string;previous_unmet:number;revised_unmet:number}[];requires_human_review:boolean;limitations:string[]}

export function validateVerification(value:unknown):Verification {
  const v=value as Verification;
  if(!v||v.schema_version!=='7.1'||!Array.isArray(v.claims)||!Array.isArray(v.findings)||!Array.isArray(v.limitations)||!Array.isArray(v.conditional_forecasts)||!Array.isArray(v.forecast_changes)||typeof v.requires_human_review!=='boolean')throw Error('Invalid verification response');
  if(!v.physical_receipts||Object.values(v.physical_receipts).some(n=>!Number.isSafeInteger(n)||n<0)||v.claims.some(c=>!c.claim_id||!c.evidence?.content_sha256||typeof c.reason!=='string')||v.conditional_forecasts.some(c=>!Number.isSafeInteger(c.quantity)||c.quantity<0||!Array.isArray(c.forecasts)))throw Error('Invalid supply evidence');
  const ids=new Set(v.claims.map(c=>c.claim_id));
  if(v.findings.some(f=>!['consistent','compatible','conflict','missing','superseded'].includes(f.category)||!f.claim_ids.every(id=>ids.has(id))))throw Error('Invalid evidence references');
  return v;
}

export function EvidenceVerification({caseId,cutoff,saved}:{caseId:string;cutoff?:string;saved:(id:string)=>void}){
  const [value,setValue]=useState<Verification|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  const laterCheckpoint=!!cutoff&&new Date(cutoff).getTime()>new Date('2024-10-02T04:00:00Z').getTime();
  useEffect(()=>{const abort=new AbortController();setValue(null);setError('');
    fetch(`/api/v1/helene/cases/${encodeURIComponent(caseId)}/verification`,{signal:abort.signal})
      .then(async r=>{if(!r.ok)throw Error();return validateVerification(await r.json());})
      .then(v=>{if(!abort.signal.aborted)setValue(v);}).catch(()=>{if(!abort.signal.aborted)setError('Evidence verification unavailable. Retry by reopening this checkpoint.');});
    return()=>abort.abort();},[caseId]);
  async function run(scenario:string){setBusy(true);setError('');try{
    const r=await fetch('/api/v1/helene/reconciliation',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scenario,parent_id:caseId})});
    if(!r.ok)throw Error();const c=await r.json();validateVerification(c.verification);saved(c.investigation_id);
  }catch{setError('Reconciliation could not be saved. No result was substituted.');}finally{setBusy(false);}}
  return <section className="alternative-section" aria-label="Evidence verification"><h3>Evidence verification</h3>
    <p className="operator-note">Actual Baxter and agency documents · Fictional supplier and hospital transactions</p>
    <div className="helene-timeline">{[['A','Request vs allocation'],['B','Shipped vs received'],['C','Missing confirmation']].map(([id,label])=><button key={id} disabled={busy||laterCheckpoint} onClick={()=>run(id)}>Case {id} · {label}</button>)}</div>
    {laterCheckpoint&&<p className="operator-note">These scenarios use October 1 records. Select September 29 or October 1 to create a revision without importing future evidence into its parent.</p>}
    {busy&&<p role="status">Reconciling source records and recalculating supply…</p>}{error&&<p role="alert">{error}</p>}
    {value&&<><p>{value.requires_human_review?'Operator verification required before trusting unresolved supply.':'Evidence checks complete.'} {Object.values(value.physical_receipts).reduce((a,b)=>a+b,0)} bags supported by accepted receiving records.</p>
      <div className="alternative-list">{value.findings.map(f=><details key={f.finding_id}><summary><span>{f.category} · {f.reason}{f.discrepancy_bags>0&&<small>{f.discrepancy_bags} bags require reconciliation</small>}</span></summary>
        <p>{f.verification_steps.join(' ')}</p><small>{f.independent_origins} originating fact(s); source organization alone does not establish independence.</small>
        {value.claims.filter(c=>f.claim_ids.includes(c.claim_id)).map(c=><article className="provenance-record" key={c.claim_id}><strong>{c.organization} · {c.classification}</strong><p>{c.meaning.replaceAll('_',' ')} · {c.quantity??c.reason} {c.quantity===null?'':c.unit}</p><details><summary>Record identity and provenance</summary><code>{c.record_id}</code><small>Origin: {c.originating_claim_id}</small><code>SHA-256 {c.evidence.content_sha256}</code></details><p>{c.reason}</p>{c.classification==='real'&&<a href={c.evidence.source_url} target="_blank" rel="noreferrer">Inspect public document</a>}<small>{c.evidence.note}</small></article>)}
      </details>)}</div>
      <h4>Forecast effect</h4>{value.forecast_changes.map(c=><p key={c.hospital_id}>{c.hospital_id}: {c.previous_unmet} → {c.revised_unmet} calculated unmet bags</p>)}
      {value.conditional_forecasts.map((c,i)=><details key={i}><summary>Conditional supply · {c.quantity} bags</summary><p>{c.assumption}</p>{c.forecasts.map(f=><p key={f.hospital_id}>{f.hospital_id}: {f.total_unmet} unmet bags</p>)}</details>)}
      <p className="operator-note">{value.limitations.join(' ')}</p></>}
  </section>;
}
