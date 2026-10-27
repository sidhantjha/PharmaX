import { useEffect, useState } from 'react';
import type { HeleneCase, ModelVerification } from './heleneApi';
import { validateVerification, type Verification, type Claim } from './EvidenceVerification';
import { Sheet } from './primitives';

interface Identity {
  original:{source_organization:string;local_sku:string;ndc?:string;manufacturer_code?:string;name:string;volume_ml:number;strength_mg_ml:number};
  canonical_ndc:string|null;manufacturer_code:string|null;classification:string;review_required:boolean;failures:string[];units_per_case:number;reference:string;limitations:string[];
}
interface Operations {
  schema_version:string;readiness:{identities:Identity[];feeds:{source:string;owner:string;last_update:string;records:number}[];errors:string[]};
  inventory:{hospital_id:string;lot_id:string;expiry_date:string;on_hand:number;reserved:number;quality_status:string}[];
  forecasts:{hospital_id:string;total_unmet:number}[];
}
const words=(s:string)=>s.replaceAll('_',' ');
const utc=(s?:string)=>s&&!Number.isNaN(Date.parse(s))?new Date(s).toISOString().replace('T',' ').replace('Z',' UTC'):'Date not recorded';
function ReadableRecord({value,name}:{value:unknown;name:(id?:string)=>string}){
  if(Array.isArray(value))return <>{value.map((v,n)=><article key={n}><ReadableRecord value={v} name={name}/></article>)}</>;
  if(!value||typeof value!=='object')return null;
  const r=value as Record<string,unknown>;
  const fields=['meaning','organization','classification','quantity','unit','status','reason','canonical_ndc','manufacturer_code','units_per_case','local_sku','ndc','name','volume_ml','strength_mg_ml','lot_id','expiry_date','on_hand','reserved','quality_status','total_unmet','initial_usable','total_received','expected_daily_demand','as_of','end_on','source','owner','records','last_update','previous_unmet','revised_unmet','mode','returned_rows','issued_at','observed_at','note'];
  return <>{r.hospital_id!=null&&<h3>{name(String(r.hospital_id))}</h3>}<dl className="trace-record">{fields.filter(k=>r[k]!==undefined&&r[k]!==null).map(k=><div key={k}><dt>{words(k)}</dt><dd>{String(r[k])}</dd></div>)}</dl>{r.original!=null&&<ReadableRecord value={r.original} name={name}/>} {Array.isArray(r.failures)&&r.failures.length>0&&<p>Unresolved: {r.failures.map(String).map(words).join(', ')}</p>}{r.evidence!=null&&<ReadableRecord value={r.evidence} name={name}/>} {r.claims!=null&&<ReadableRecord value={r.claims} name={name}/>} {r.finding!=null&&<ReadableRecord value={r.finding} name={name}/>}</>;
}

export function ProductTraceability({data,status}:{data:HeleneCase;status:ModelVerification|null}){
  const [ops,setOps]=useState<Operations|null>(null),[verification,setVerification]=useState<Verification|null>(null),[errors,setErrors]=useState<string[]>([]);
  const [sourceDetails,setSourceDetails]=useState(false);
  const [inspect,setInspect]=useState<{title:string;description:string;record:unknown}|null>(null);
  const name=(id?:string)=>data.operations.hospitals.find(h=>h.hospital_id===id)?.name.replace(' (fictional)','')??'Source organization';
  useEffect(()=>{const abort=new AbortController();setOps(null);setVerification(null);setErrors([]);setInspect(null);
    const base='/api/v1/helene/cases/'+encodeURIComponent(data.investigation_id);
    async function load(resource:string){try{const r=await fetch(base+'/'+resource,{signal:abort.signal});if(!r.ok)throw Error();const v=await r.json();if(abort.signal.aborted)return;
      if(resource==='verification')setVerification(validateVerification(v));
      else {if(v.schema_version!=='8.1'||!Array.isArray(v.readiness?.identities)||!Array.isArray(v.readiness?.feeds)||!Array.isArray(v.inventory)||!Array.isArray(v.forecasts))throw Error();setOps(v);}
    }catch{if(!abort.signal.aborted)setErrors(e=>[...e,resource+' unavailable. No substitute evidence shown.']);}}
    void load('operations');void load('verification');return()=>abort.abort();
  },[data.investigation_id]);
  const source=(title:string,record:unknown,description:string)=> <button className="trace-source" onClick={()=>setInspect({title,record,description})}>{title} ↗</button>;
  const identities=ops?.readiness.identities??[];
  const matched=identities.filter(i=>!i.review_required&&i.classification==='usable_with_assumptions'&&!!i.canonical_ndc&&!!i.manufacturer_code&&!i.failures.length);
  const first=matched[0];
  const conflict=verification?.findings.find(f=>f.category==='conflict'&&f.claim_ids.some(id=>verification.claims.find(c=>c.claim_id===id)?.meaning==='shipped')&&f.claim_ids.some(id=>verification.claims.find(c=>c.claim_id===id)?.meaning==='accepted_receipt'));
  const linked=conflict?verification!.claims.filter(c=>conflict.claim_ids.includes(c.claim_id)):[];
  const shipment=linked.find(c=>c.meaning==='shipped');
  const requested=verification?.claims.find(c=>c.meaning==='requested'&&(!shipment||c.facility_id===shipment.facility_id&&c.order_id===shipment.order_id&&c.line_id===shipment.line_id&&c.product_id===shipment.product_id));
  const orderClaims=verification?.claims.filter(c=>!!requested&&c.facility_id===requested.facility_id&&c.order_id===requested.order_id&&c.line_id===requested.line_id&&c.product_id===requested.product_id)??[];
  const stages:[string,Claim|undefined][]=[['Requested',requested],['Allocation policy',verification?.claims.find(c=>c.meaning==='allocation_policy')],['Acknowledged',orderClaims.find(c=>c.meaning==='acknowledged')],['Reported shipped',shipment??orderClaims.find(c=>c.meaning==='shipped')],['Accepted receipt',linked.find(c=>c.meaning==='accepted_receipt')??orderClaims.find(c=>c.meaning==='accepted_receipt')]];
  const layer=status?.available?status.data_layer:null;
  return <section className="product-traceability" aria-label="Product traceability">
    <header><div><span className="eyebrow">PRODUCT TRACEABILITY</span><h2>Matching medicines across systems</h2></div><span className="execution-pill">{ops?`${matched.length} matched with assumptions · ${identities.length-matched.length} unresolved`:'Identity mappings pending or unavailable'}</span></header>
    {errors.map(e=><p role="status" key={e}>{e}</p>)}
    {!ops&&!errors.length&&<p role="status">Loading retained product mappings…</p>}
    <ol className="relationship-flow" aria-label="Product to forecast relationship">{['Baxter product','Standardized product ID','Hospital inventory records'].map(label=><li key={label}>{label}</li>)}</ol>
    {first&&<p className="trace-identity"><strong>{first.original.name} · {first.original.volume_ml.toLocaleString()} mL</strong> · Manufacturer code {first.manufacturer_code} · Normalized NDC {first.canonical_ndc} {source('Catalog identity',first,'Catalog-anchored identity; hospital mapping remains a simulated assertion.')} </p>}
    <div className="trace-mappings">{identities.map((i,n)=><div key={n}><span className="mapping-connector" aria-hidden="true">↳</span><strong>{name(i.original.source_organization)}</strong> {source(i.original.local_sku,i,'Original hospital item-master identity and backend resolution.')}<span>{matched.includes(i)?`Matched with assumptions · 1 case = ${i.units_per_case} bags`:'Unresolved · '+(i.failures.map(words).join(', ')||'Missing authoritative mapping')}</span></div>)}</div>
    {identities.length>0&&<p className="trace-note">Case counts are simulated item-master assertions, not verified manufacturer packaging. Missing distributor and GS1 identities remain unverified.</p>}
    <h2 className="supply-tracking-heading">Tracking supply from order to delivery</h2><div className="trace-stages" aria-label="Distinct supply stages">{stages.map(([label,c])=><div key={label}><span>{label}</span>{c?source(`${c.quantity??'Unknown'} ${c.unit==='percent'?'%':c.unit}`,c,`${c.organization} · ${c.classification} · ${utc(c.issued_at)}. ${c.reason}`):<strong>Unconfirmed</strong>}</div>)}</div>
    <p className="trace-note">{requested?name(requested.facility_id)+' order. ':''}Allocation is a policy percentage, not this order’s allocated quantity.</p>
    {conflict?<div className="trace-discrepancy"><strong>{conflict.discrepancy_bags} bags unexplained</strong><span>Shipment and receiving records disagree. Location and disposition are unknown.</span>{source('Shipment versus receipt', {finding:conflict,claims:linked},conflict.reason)}</div>:<p className="trace-note">No matched shipment-versus-receipt conflict in this checkpoint. Reconciliation scenarios are available in Source details.</p>}
    <div className="trace-forecast"><strong>Forecast consequence</strong> {verification?.forecast_changes.filter(c=>c.previous_unmet!==c.revised_unmet).map(c=><span key={c.hospital_id}>{source(`${name(c.hospital_id)}: ${c.previous_unmet} → ${c.revised_unmet} unmet bags`,c,'Saved reconciliation forecast change; only accepted supply admitted.')}</span>)}{source('Canonical forecast',data.forecasts,'Canonical replay includes supported modeled inbound assumptions; an expected arrival is not proof of receipt.')} {ops&&source('Conservative operational forecast',ops.forecasts,'Operational forecast uses conservative inbound assumptions and retained workflow state. Separate from the canonical replay.')}</div>
    <button className="trace-source" onClick={()=>setSourceDetails(true)}>Identity source details ↗</button>
    <Sheet open={sourceDetails} onOpenChange={setSourceDetails} title="Identity source details" description="Retained product and source provenance"><div className="panel-body">
    <details><summary>Lots, source feeds and mapping limitations</summary>{ops?.inventory.map((r,n)=><p key={n}>{name(r.hospital_id)} · {r.on_hand} on hand / {r.reserved} reserved · {r.quality_status} · expires {r.expiry_date} {source('Inventory lot '+(n+1),r,'Retained simulated inventory lot. This does not establish the lot of a separate shipment or receipt.')}</p>)}{ops?.readiness.feeds.map(f=><p key={f.source}>{source(words(f.source)+' source',f,`${f.owner} · ${f.records} records · exported ${utc(f.last_update)}`)}</p>)}{identities.flatMap(i=>i.limitations).filter((v,n,a)=>a.indexOf(v)===n).map(v=><p key={v}>{v}</p>)}{data.unresolved.map((v,n)=><p key={n}>Unresolved: {words(v)}</p>)}</details>
    {layer&&<details><summary>Zetaris query and lineage</summary><pre>{JSON.stringify(layer,null,2)}</pre></details>}</div></Sheet>
    <Sheet open={!!inspect} onOpenChange={open=>{if(!open)setInspect(null);}} title={inspect?.title??'Source evidence'} description="Read-only retained evidence"><div className="panel-body"><p>{inspect?.description}</p><ReadableRecord value={inspect?.record} name={name}/><p>Hospital operations are simulated. Record validation does not authenticate a real transaction.</p>{inspect?.title==='Catalog identity'&&first&&/^https?:\/\//.test(first.reference)&&<a href={first.reference} target="_blank" rel="noreferrer">Manufacturer catalog reference ↗</a>}<details><summary>Original record and technical provenance</summary><pre>{JSON.stringify(inspect?.record,null,2)}</pre></details></div></Sheet>
  </section>;
}
