import { useCallback, useEffect, useState } from 'react';
import { Sheet } from './primitives';

type Proposal={kind:'transfer'|'replenishment';recipient:string;donor:string|null;product_id:string;lot_id:string;quantity:number;units_per_case:number;expected_on:string;actor:string};
type Workflow={id:string;revision:number;state:string;quantity_bags:number;received:number;dispatched:number;proposal:Proposal;next_allowed:string[];documents:unknown[];audit:unknown[]};
type Operations={schema_version:'8.1';readiness:{classification:string;supports_forecast:boolean;errors:string[];identities:{original:{local_sku:string;ndc:string;source_organization:string};classification:string;failures:string[]}[];feeds:{source:string;owner:string;last_update:string;records:number;sha256:string}[];excluded_lots:unknown[];limitations:string[]};inventory:{hospital_id:string;lot_id:string;on_hand:number;reserved:number;expiry_date:string;quality_status:string}[];recommendations:Proposal[];supplier_requests:Proposal[];actions:Workflow[];forecasts:{hospital_id:string;total_unmet:number}[];policy:{assessment:string;required_review:string};limitations:string[]};

function validate(v:Operations):Operations{
  if(v?.schema_version!=='8.1'||!Array.isArray(v.actions)||!Array.isArray(v.recommendations)||!Array.isArray(v.inventory)||!v.readiness||!Array.isArray(v.forecasts))throw Error('Invalid operational response');
  return v;
}

export function OperationalReview({caseId}:{caseId:string}){
  const [data,setData]=useState<Operations|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  const [drawer,setDrawer]=useState(false);
  const [actor,setActor]=useState(''),[role,setRole]=useState('operator'),[attested,setAttested]=useState(false);
  const [note,setNote]=useState(''),[policy,setPolicy]=useState(''),[receiptId,setReceiptId]=useState(''),[received,setReceived]=useState(''),[expiry,setExpiry]=useState(''),[quality,setQuality]=useState('released');
  const base=`/api/v1/helene/cases/${encodeURIComponent(caseId)}/operations`;
  const load=useCallback(async(signal?:AbortSignal)=>{const r=await fetch(base,{signal});if(!r.ok)throw Error();const v=validate(await r.json());if(!signal?.aborted)setData(v);},[base]);
  useEffect(()=>{const abort=new AbortController();setData(null);setError('');load(abort.signal).catch(()=>{if(!abort.signal.aborted)setError('Operational records unavailable. Check retained source files and data readiness.');});return()=>abort.abort();},[load]);
  async function post(path:string,body:unknown){setBusy(true);setError('');try{const r=await fetch(base+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});if(!r.ok){const e=await r.json();throw Error(e.error??'Request rejected');}await load();setAttested(false);}catch(e){setError(e instanceof Error?e.message:'Operational request failed');}finally{setBusy(false);}}
  function advance(a:Workflow,state:string){const body:Record<string,unknown>={expected_revision:a.revision,state,actor,role,attested,note};
    if(state==='AUTHORIZED')body.policy_reference=policy;
    if(state==='SIMULATED_RECEIPT')Object.assign(body,{receipt_id:receiptId,received_quantity:Number(received),lot_id:a.proposal.lot_id,expires_on:expiry,quality});
    return post(`/actions/${encodeURIComponent(a.id)}/transition`,body);
  }
  return <section className="alternative-section" aria-label="Operational review"><h3>Product, data and action review</h3>
    <p className="operator-note">Fictional health system · Human-reviewed local simulation · No real dispatch or purchase order</p>
    {error&&<p role="alert">{error}</p>}{busy&&<p role="status">Saving validated operational revision…</p>}
    {data&&<><details><summary>Product identity · {data.readiness.identities.length} source mappings</summary>{data.readiness.identities.map((i,n)=><p key={n}>{i.original.source_organization} · {i.original.local_sku} · {i.original.ndc} · {i.classification} {i.failures.join(', ')}</p>)}<pre>{JSON.stringify(data.readiness.excluded_lots,null,2)}</pre></details>
      <details><summary>Lot and expiry status</summary>{data.inventory.map((r,i)=><p key={i}>{r.hospital_id} · {r.lot_id} · {r.on_hand} on hand / {r.reserved} reserved · expires {r.expiry_date} · {r.quality_status}</p>)}</details><details><summary>Data readiness · {data.readiness.classification}</summary><p>{data.readiness.supports_forecast?'Input supports a conditional simulation forecast.':'Input cannot support a forecast.'}</p>{data.readiness.feeds.map(f=><article className="provenance-record" key={f.source}><strong>{f.source} · {f.records} records</strong><p>{f.owner} · exported {f.last_update}</p><code>{f.sha256}</code></article>)}<p>{data.readiness.errors.join(' ')}</p><p>{data.readiness.limitations.join(' ')}</p></details>
      <h4>Conservative operational forecast</h4>{data.forecasts.map(f=><p key={f.hospital_id}>{f.hospital_id}: {f.total_unmet} unmet bags</p>)}
      <p>{data.policy.assessment} {data.policy.required_review}</p>
      <button className="pill-button" onClick={()=>setDrawer(true)}>Review action</button><Sheet open={drawer} onOpenChange={setDrawer} title="Review action" description="Human-reviewed local simulation · No external dispatch"><div className="panel-body">
      <label>Reviewer name <input aria-label="Operational reviewer" value={actor} onChange={e=>setActor(e.target.value)}/></label>
      <label>Review role <select aria-label="Operational role" value={role} onChange={e=>setRole(e.target.value)}><option value="operator">Operator</option><option value="pharmacist">Pharmacist</option><option value="receiving">Receiving</option></select></label>
      <label>Review / discrepancy note <input aria-label="Operational note" value={note} onChange={e=>setNote(e.target.value)}/></label>
      <label>Reviewed pharmacy policy reference <input aria-label="Pharmacy policy reference" value={policy} onChange={e=>setPolicy(e.target.value)}/></label>
      <label><input type="checkbox" checked={attested} onChange={e=>setAttested(e.target.checked)}/>I attest to this explicit simulation step within the selected role.</label>
      <p className="operator-note">Names and roles are local demonstration attestations; live authenticated pharmacy credentials are unverified.</p>
      <h4>Validated transfer proposals</h4>{data.recommendations.length===0&&<p>No currently feasible donor transfer.</p>}{data.recommendations.map((p,i)=><p key={i}>{p.quantity} bags · {p.donor} → {p.recipient} · {p.lot_id} <button disabled={busy||!actor.trim()||role!=='operator'} onClick={()=>post('/actions',{...p,actor})}>Reserve and propose</button></p>)}
      <h4>Supplier replenishment inquiry</h4>{data.supplier_requests.map((p,i)=><p key={i}>{p.recipient} · {p.quantity} bags requested · fill unconfirmed <button disabled={busy||!actor.trim()||role!=='operator'} onClick={()=>post('/actions',{...p,actor})}>Prepare inquiry for review</button></p>)}<h4>Saved human-review workflows</h4>{data.actions.map(a=><article className="provenance-record" key={a.id}><strong>{a.proposal.kind} · {a.state.replaceAll('_',' ')}</strong><p>{a.quantity_bags} bags · lot {a.proposal.lot_id} · {a.received} received / {a.dispatched} simulated dispatched</p>
        {a.next_allowed.includes('SIMULATED_RECEIPT')&&<details><summary>Simulated receiving record</summary><label>Unique receipt ID <input aria-label="Receipt ID" value={receiptId} onChange={e=>setReceiptId(e.target.value)}/></label><label>Received bags <input aria-label="Received bags" type="number" min="0" value={received} onChange={e=>setReceived(e.target.value)}/></label><label>Lot expiration <input aria-label="Receipt expiration" type="date" value={expiry} onChange={e=>setExpiry(e.target.value)}/></label><select aria-label="Receipt quality" value={quality} onChange={e=>setQuality(e.target.value)}><option value="released">Released</option><option value="quarantined">Quarantined</option><option value="recalled">Recalled</option></select></details>}
        <div className="helene-timeline">{a.next_allowed.map(s=><button key={s} disabled={busy||!actor.trim()||!note.trim()||!attested} onClick={()=>advance(a,s)}>{s.replaceAll('_',' ')}</button>)}</div>
        <details><summary>Source-linked request drafts and immutable audit</summary><pre>{JSON.stringify({documents:a.documents,audit:a.audit},null,2)}</pre></details>
      </article>)}<p className="operator-note">{data.limitations.join(' ')}</p></div></Sheet></>}
  </section>;
}
