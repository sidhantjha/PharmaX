import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react';

import { ArrowLeft, ArrowUpRight, Crosshair, FileText, Map, MessageCircle, Minus, Moon, Plus, Sun } from 'lucide-react';

import { BrandMark } from './BrandMark';

import { Sheet } from './primitives';

import { heleneApi, type HeleneCase, type Summary, type Action, type ModelVerification } from './heleneApi';

import type { GlobeSceneHandle, GlobeSignal, GeoPath } from './GlobeScene';


import { InvestigationWorkspace } from './InvestigationWorkspace';

import { HeleneChat } from './HeleneChat';

import './operatorExperience.css';

import './helene.css';

import './desktopWorkspace.css';

const GlobeScene=lazy(()=>import('./GlobeScene').then(m=>({default:m.GlobeScene})));

const focus:[number,number]=[-81.99,35.91];

type Page='overview'|'investigation'|'chat';

function route(){const p=new URLSearchParams(location.hash.slice(1));return {page:(['investigation','chat'].includes(p.get('page')??'')?p.get('page'):'overview') as Page,tab:p.get('tab')??'situation',id:p.get('case')??''};}

export function ScenarioExperience(){
  return <HeleneExperience/>;
}

export function HeleneExperience(){

  const [theme,setTheme]=useState<'dark'|'light'>(()=>localStorage.getItem('pharmax-theme')==='dark'?'dark':'light');

  const [summaries,setSummaries]=useState<Summary[]>([]),[id,setId]=useState(route().id);

  const [data,setData]=useState<HeleneCase|null>(null),[error,setError]=useState(''),[loading,setLoading]=useState(true);

  const [view,setView]=useState(route),[facility,setFacility]=useState<string|null>(null);

  const [review,setReview]=useState<Action|null>(null),[reload,setReload]=useState(0);

  const [modelStatus,setModelStatus]=useState<ModelVerification|null>(null),[modelError,setModelError]=useState('');

  const globe=useRef<GlobeSceneHandle>(null);

  function navigate(page:Page,tab='situation',caseId=id){window.scrollTo({top:0});const p=new URLSearchParams({page,tab,case:caseId});location.hash=p.toString();setView({page,tab,id:caseId});setFacility(null);}

  useEffect(()=>{const changed=()=>{const next=route();setView(next);if(next.id)setId(next.id);setFacility(null);};window.addEventListener('hashchange',changed);return()=>window.removeEventListener('hashchange',changed);},[]);

  useEffect(()=>{localStorage.setItem('pharmax-theme',theme);document.documentElement.dataset.theme=theme;},[theme]);

  useEffect(()=>{let active=true;setError('');setLoading(true);heleneApi.list().then(items=>{if(!active)return;const sorted=[...items].sort((a,b)=>b.checkpoint.cutoff.localeCompare(a.checkpoint.cutoff));setSummaries(sorted);const requested=route().id;const chosen=sorted.find(s=>s.investigation_id===requested)??sorted.find(s=>s.investigation_id.includes('2024-10-01')&&!s.reconciliation_scenario)??sorted[0];setId(chosen?.investigation_id??'');if(!chosen){setLoading(false);setError('No saved Helene investigation. Run the documented offline replay command.');}}).catch(()=>{if(active){setError('Helene API unavailable. No sample results are substituted.');setLoading(false);}});return()=>{active=false;};},[reload]);

  useEffect(()=>{if(!id)return;const abort=new AbortController();setLoading(true);setData(null);setError('');setFacility(null);setReview(null);setModelStatus(null);setModelError('');heleneApi.get(id,abort.signal).then(c=>{if(!abort.signal.aborted)setData(c);}).catch(()=>{if(!abort.signal.aborted)setError('Saved checkpoint could not be validated or loaded.');}).finally(()=>{if(!abort.signal.aborted)setLoading(false);});heleneApi.modelVerification(id,abort.signal).then(v=>{if(!abort.signal.aborted)setModelStatus(v);}).catch(()=>{if(!abort.signal.aborted)setModelError('Saved native receipts unavailable. No model success is assumed.');});return()=>abort.abort();},[id,reload]);

  const signals=useMemo<GlobeSignal[]>(()=>data?[

    {id:'north-cove',title:'Baxter North Cove',location:'Marion, North Carolina',impact:'Manufacturer disruption',time:'Historical source',kind:'risk',point:focus},

    ...data.map_nodes.map(n=>({id:n.id,title:n.title.replace(/ \(fictional\)/,''),point:n.point,location:'Illustrative NC facility',impact:`${data.forecasts.find(f=>f.hospital_id===n.id)?.total_unmet??'Unavailable'} unmet bags`,time:'Saved checkpoint',kind:'update' as const,hospital:true}))]:[],[data]);

  const paths=useMemo<GeoPath[]>(()=>data?data.map_nodes.map(n=>({id:'helene-'+n.id,kind:'dependency',coords:[focus,n.point]})):[],[data]);

  const before=data?.forecasts.reduce((s,f)=>s+f.total_unmet,0),after=data?.mitigated.reduce((s,f)=>s+f.total_unmet,0);

  const page=view.page;

  return <div className={`app-shell operator-experience helene-experience desktop-experience theme-${theme} page-${page}`}>

    <aside className="rail"><button className="brand icon-button" aria-label="PharmaX overview" onClick={()=>navigate('overview')}><BrandMark/></button><nav aria-label="Main navigation">

      <button className={`rail-item ${page==='overview'?'active':''}`} onClick={()=>navigate('overview')}><Map/><span>Overview</span></button>

      <button className={`rail-item ${page==='investigation'?'active':''}`} onClick={()=>navigate('investigation')}><FileText/><span>Investigation</span></button>



      <button className={`rail-item ${page==='chat'?'active':''}`} onClick={()=>navigate('chat')}><MessageCircle/><span>Chat</span></button>

    </nav></aside>

    <div className="workspace"><header className="topbar"><div className="wordmark"><BrandMark/><span className="brand-name">PharmaX</span></div><button className="icon-button" aria-label={`Switch to ${theme==='dark'?'light':'dark'} mode`} onClick={()=>setTheme(t=>t==='dark'?'light':'dark')}>{theme==='dark'?<Sun size={16}/>:<Moon size={16}/>}</button></header>

    <main><div className="overview-scene" aria-hidden={page!=='overview'} inert={page!=='overview'}>

      <section className="overview-bar"><div><div className="eyebrow">PHARMACEUTICAL SUPPLY</div><h1>North Cove supply exposure</h1></div><div className="overview-stats"><button onClick={()=>navigate('investigation','forecast')}><span>Projected unmet · bags</span><strong className="amber">{loading?'—':before??'—'}</strong></button><button onClick={()=>navigate('investigation','mitigation')}><span>After mitigation · bags</span><strong>{loading?'—':after??'—'}</strong></button></div></section>

      <section className="map-section" aria-label="Hurricane Helene supply network"><div className="map-toolbar"><span className="execution-pill">0.9% sodium chloride · 1,000 mL bags</span></div><div className="map-canvas">

      <Suspense fallback={<div className="globe-loading" role="status">Preparing geographic context…</div>}><GlobeScene ref={globe} signals={signals} paths={paths} routes alternate={false} alerts theme={theme} selectedId={facility??'north-cove'} onSelect={s=>{if(s.hospital)setFacility(s.id);else navigate('investigation');}} focusPoint={focus} active={page==='overview'}/></Suspense>

      <div className="map-legend"><span><i className="legend-line"/>Supply dependency</span><span><i className="legend-risk"/>Manufacturer</span><span><i className="legend-update"/>Hospital</span></div>

      <aside className="alerts-overlay operator-attention"><div className="alerts-heading"><span>Hurricane Helene</span><span className="execution-pill">01 OCT 2024</span></div>

      {loading?<p role="status" className="network-state">Loading saved checkpoint…</p>:error?<div className="network-state" role="alert">{error}<button onClick={()=>setReload(v=>v+1)}>Retry connection</button></div>:data&&<button className="alert-row alert-risk alert-priority" onClick={()=>navigate('investigation')}><div className="alert-meta"><span>BAXTER NORTH COVE</span></div><h3>A disruption.<br/>A network-wide consequence.</h3><p>{before} bags projected unmet across the modeled network.</p><div className="alert-bottom"><span>14-day horizon</span><strong>{after} after mitigation</strong></div><span className="attention-action">Examine investigation <ArrowUpRight size={16}/></span></button>}

      </aside><div className="map-bottom-left"><div className="map-controls"><button aria-label="Zoom in" onClick={()=>globe.current?.zoomIn()}><Plus size={16}/></button><button aria-label="Zoom out" onClick={()=>globe.current?.zoomOut()}><Minus size={16}/></button><button aria-label="Reset map" onClick={()=>globe.current?.reset()}><Crosshair size={16}/></button></div></div><div className="map-attribution">Natural Earth · Illustrative hospital locations</div>

      </div></section>

    </div>

    {page!=='overview'&&<div className="full-page-content"><div className="page-navigation"><button className="pill-button back-button" onClick={()=>navigate(page==='chat'?'investigation':'overview')}><ArrowLeft size={15}/>{page==='chat'?'Back to investigation':'Back to overview'}</button><nav className="checkpoint-pills" aria-label="Historical checkpoints">{summaries.map(s=><button key={s.investigation_id} aria-pressed={s.investigation_id===id} onClick={()=>{setId(s.investigation_id);navigate(page,view.tab,s.investigation_id);}}>{s.investigation_id.slice(7,17)}{s.reconciliation_scenario?` · ${s.reconciliation_scenario}`:''}</button>)}</nav></div>{loading?<p role="status" className="soft-panel">Loading investigation…</p>:error?<p role="alert" className="soft-panel">{error}<button className="pill-button" onClick={()=>setReload(v=>v+1)}>Retry</button></p>:data&&(page==='chat'?<HeleneChat key={id} data={data}/>:<InvestigationWorkspace data={data} status={modelStatus} statusError={modelError} tab={view.tab} onTab={tab=>navigate('investigation',tab)} onFacility={setFacility} onReview={setReview} onSaved={next=>{setId(next);navigate('investigation','evidence',next);heleneApi.list().then(setSummaries).catch(()=>{});}}/>)}</div>}

    </main></div>

    {page!=='chat'&&<button className="circular-chat" aria-label="Ask PharmaX" onClick={()=>navigate('chat')}><MessageCircle size={22}/></button>}

    <Sheet open={!!facility&&!!data} onOpenChange={open=>{if(!open)setFacility(null);}} title="Hospital forecast" description="Canonical saved calculation"><div className="panel-body">{data&&facility&&<Facility data={data} id={facility}/>}</div></Sheet>

    <Sheet open={!!review} onOpenChange={open=>{if(!open)setReview(null);}} title="Review action" description="Internal proposal · No shipment or order dispatched"><div className="panel-body">{review&&data&&<Review data={data} action={review} caseId={data.investigation_id} saved={a=>{setReview(a);setData({...data,actions:data.actions.map(v=>v.action_id===a.action_id?a:v)});}}/>}</div></Sheet>

  </div>;

}

function Facility({data,id}:{data:HeleneCase;id:string}){

  const h=data.operations.hospitals.find(h=>h.hospital_id===id),f=data.forecasts.find(f=>f.hospital_id===id),after=data.mitigated.find(f=>f.hospital_id===id);

  return <article className="facility-card"><span className="execution-pill">Simulated NC facility</span><h3>{h?.name}</h3><p>0.9% sodium chloride · 1000 mL · exact bag presentation</p><dl className="facility-facts"><div><dt>Initial usable</dt><dd>{f?.initial_usable} bags</dd></div><div><dt>Expected daily demand</dt><dd>{f?.expected_daily_demand} bags</dd></div><div><dt>Supported modeled receipts</dt><dd>{f?.total_received} bags</dd></div><div><dt>Unmet / after mitigation</dt><dd>{f?.total_unmet} / {after?.total_unmet} bags</dd></div><div><dt>First unmet demand</dt><dd>{f?.projected_stockout_date??'None within horizon'}</dd></div></dl><h4>Canonical inventory lots</h4>{data.operations.inventory.filter(r=>r.hospital_id===id).map(r=><p key={r.lot_id}>{r.lot_id} · {r.on_hand} on hand / {r.reserved} reserved · expires {r.expiry_date}</p>)}<h4>Orders and acknowledgments</h4>{data.operations.purchase_orders.filter(p=>p.hospital_id===id).map(p=><p key={p.purchase_order_id}>{p.purchase_order_id} · {p.quantity} ordered bags · {data.operations.acknowledgments.find(a=>a.purchase_order_id===p.purchase_order_id)?.quantity??'Missing'} acknowledged · {p.account_id}</p>)}<code>Calculation {f?.analysis_id}</code><p className="operator-note">Forecast {f?.as_of}–{f?.end_on}; reservations remain protected. An expected order is never counted as a shipment.</p></article>;

}

function Review({data,action,caseId,saved}:{data:HeleneCase;action:Action;caseId:string;saved:(a:Action)=>void}){

  const [reviewer,setReviewer]=useState(''),[attested,setAttested]=useState(false),[error,setError]=useState('');

  const retained=action as Action & {candidate_id?:string;required_authorizations?:string[]};
  const proposal=data.candidates.find(c=>c.candidate_id===retained.candidate_id);
  const facilityName=(id?:string)=>data.operations.hospitals.find(h=>h.hospital_id===id)?.name.replace(' (fictional)','')??'Not recorded';
  return <form className="internal-action-review" onSubmit={async e=>{e.preventDefault();setError('');try{saved(await heleneApi.review(caseId,action,reviewer));}catch{setError('Review failed or version changed. Reopen the case before retrying.');}}}><section className="review-selected"><span className="eyebrow">SELECTED PROPOSAL · SIMULATION</span><h3>{action.quantity} bags · {proposal?.kind.replaceAll('_',' ')??'Internal proposal'}</h3><dl><div><dt>From</dt><dd>{facilityName(proposal?.source_id)}</dd></div><div><dt>To</dt><dd>{facilityName(proposal?.hospital_id)}</dd></div><div><dt>Status</dt><dd>{action.status.replaceAll('_',' ')} · revision {action.revision}</dd></div></dl></section><section className="review-requirements"><h3>Review requirements</h3><ul>{[...(retained.required_authorizations??[]),...action.outstanding_verification].map((r,i)=><li key={i}>{r}</li>)}</ul></section><h3>Reviewer confirmation</h3><label>Reviewer<input value={reviewer} onChange={e=>setReviewer(e.target.value)} required/></label><label className="review-attestation"><input type="checkbox" checked={attested} onChange={e=>setAttested(e.target.checked)}/>I reviewed simulation assumptions and required organizational checks.</label><button className="primary-button" disabled={!attested||!reviewer.trim()||action.status==='APPROVED'}>{action.status==='DRAFT'?'Mark ready for review':'Approve internal draft'}</button>{error&&<p role="alert">{error}</p>}<p className="review-safety">No external dispatch is available.</p><details><summary>Original proposal text</summary><p>{action.draft}</p></details></form>;

}
