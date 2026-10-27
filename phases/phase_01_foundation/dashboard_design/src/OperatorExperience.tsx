import { lazy, Suspense, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowUpRight, Bell, Box, Crosshair, FileText, Map, Minus, Moon, Plus, ShieldCheck, Sun } from 'lucide-react';
import { BrandMark } from './BrandMark';
import { Dropdown, Sheet } from './primitives';
import { createOperatorClient, type CaseSummary, type OperatorClient } from './operatorApi';
import { affected, disruptionPoint, facilityForecast, illustrativeIllinois, labelMedicine, recommended, type WorkspaceData } from './operatorWorkspace';
import { InvestigationWorkspace, FacilityCard, ActionReviewForm } from './OperatorViews';
import type { GeoPath, GlobeSceneHandle, GlobeSignal } from './GlobeScene';
import type { ActionDraft } from './operatorWorkspace';
import './operatorExperience.css';

const GlobeScene=lazy(()=>import('./GlobeScene').then(m=>({default:m.GlobeScene})));
const api=createOperatorClient();
export function investigationRoot(c: CaseSummary, cases: CaseSummary[]): string {
  const seen=new Set<string>(); let current=c;
  while(current.parent_id && !seen.has(current.investigation_id)) {
    seen.add(current.investigation_id); const parent=cases.find(p=>p.investigation_id===current.parent_id);
    if(!parent)return current.parent_id; current=parent;
  }
  return current.investigation_id;
}
export function OperatorExperience({client=api}:{client?:OperatorClient}) {
  const [theme,setTheme]=useState<'dark'|'light'>(()=>localStorage.getItem('pharmax-theme')==='light'?'light':'dark');
  const [cases,setCases]=useState<CaseSummary[]>([]), [id,setId]=useState<string|null>(null);
  const [cache,setCache]=useState<Record<string,WorkspaceData>>({});
  const [loading,setLoading]=useState(true), [error,setError]=useState<string|null>(null), [version,refresh]=useState(0);
  const [detail,setDetail]=useState(false), [facility,setFacility]=useState<string|null>(null);
  const [panel,setPanel]=useState<'investigations'|'facilities'|'evidence'|'chat'|null>(null);
  const [review,setReview]=useState<ActionDraft|null>(null), [alerts,setAlerts]=useState(true);
  const [medicine,setMedicine]=useState<string|null>(null);
  const globe=useRef<GlobeSceneHandle>(null), main=useRef<HTMLElement>(null);
  const data=id?cache[id]:undefined;
  useEffect(()=>{localStorage.setItem('pharmax-theme',theme);document.documentElement.dataset.theme=theme;},[theme]);
  useEffect(()=>{
    const request=new AbortController();setLoading(true);setError(null);setCache({});
    (async()=>{
      await client.health(request.signal);
      const first=await client.investigations(0,request.signal);const items=[...first.items];
      if(first.total>200)throw new Error('CASE_LIST_LIMIT');
      while(items.length<first.total){const next=await client.investigations(items.length,request.signal);if(!next.items.length)throw new Error('INCOMPLETE_CASE_LIST');items.push(...next.items);}
      if(!request.signal.aborted){setCases(items);setId(current=>items.some(c=>c.investigation_id===current)?current:items[0]?.investigation_id??null);}
    })().catch(()=>{if(!request.signal.aborted)setError('The operator API is unavailable or returned incompatible data. No demo data has been substituted.');})
      .finally(()=>{if(!request.signal.aborted)setLoading(false);});
    return()=>request.abort();
  },[client,version]);
  useEffect(()=>{
    if(!id || !cases.length)return;
    if(cache[id]){setLoading(false);setError(null);return;}
    const request=new AbortController();setLoading(true);setError(null);
    const root=investigationRoot(cases.find(c=>c.investigation_id===id)!,cases);
    const family=cases.filter(c=>investigationRoot(c,cases)===root);
    Promise.all(family.map(c=>client.workspace(c.investigation_id,request.signal)))
      .then(results=>{if(!request.signal.aborted)setCache(Object.fromEntries(results.map(d=>[d.snapshot.investigation_id,d])));})
      .catch(()=>{if(!request.signal.aborted)setError('Saved investigation details could not be validated or loaded. Retry the connection; no estimated values are shown.');})
      .finally(()=>{if(!request.signal.aborted)setLoading(false);});
    return()=>request.abort();
  },[client,id,cases]);
  const groups=useMemo(()=>cases.filter((c,i,all)=>all.findIndex(other=>investigationRoot(other,all)===investigationRoot(c,all))===i),[cases]);
  const family=data?cases.filter(c=>investigationRoot(c,cases)===investigationRoot(data.snapshot,cases)).sort((a,b)=>a.created_at.localeCompare(b.created_at)):[];
  const activeMedicine=medicine??data?.snapshot.mitigation.request.product_id;
  const matches=!!data && activeMedicine===data.snapshot.mitigation.request.product_id;
  const signals=useMemo<GlobeSignal[]>(()=>{
    if(!data)return[];const point=disruptionPoint(data);
    const result:GlobeSignal[]=point?[{id:'advisory',title:'Red Sea · advisory context',location:'Curated advisory area',impact:`${facilityForecast(data,data.snapshot.mitigation.request.hospital_id,'disruption')?.total_unmet??'—'} modeled unmet vials`,time:'Retained evidence',kind:'risk',point,routeId:'modeled-dependency'}]:[];
    for(const h of data.snapshot.hospitals){const position=illustrativeIllinois[h.hospital_id];if(!position)continue;
      const forecast=facilityForecast(data,h.hospital_id,'disruption');
      result.push({id:h.hospital_id,title:h.name,location:'Illustrative Illinois position',impact:`${forecast?.total_unmet??'—'} unmet vials`,time:'Simulation',kind:(forecast?.total_unmet??0)>0?'risk':'update',point:position,hospital:true});
    }return result;
  },[data]);
  const paths=useMemo<GeoPath[]>(()=>{
    const legs=data?.snapshot.intelligence?.dependency.legs;if(!legs?.length)return[];
    const centers=legs.map(l=>[(l.geography.west+l.geography.east)/2,(l.geography.south+l.geography.north)/2]);
    return [{id:'modeled-dependency',kind:'shipment',coords:centers},...legs.filter(l=>l.leg_id==='red-sea'||l.leg_id==='aden').map(l=>({id:l.leg_id,kind:'risk',coords:[[l.geography.east,l.geography.south],[l.geography.west,l.geography.north]]}))];
  },[data]);
  function focus(signal:GlobeSignal){globe.current?.focus(signal);}
  function openInvestigation(){setDetail(true);setFacility(null);setPanel(null);if(main.current)main.current.scrollTop=0;const risk=signals.find(s=>s.id==='advisory');if(risk)window.setTimeout(()=>focus(risk),100);}
  function openFacility(hospitalId:string){setFacility(hospitalId);setPanel(null);const signal=signals.find(s=>s.id===hospitalId);if(signal)focus(signal);}
  function overview(){setDetail(false);setFacility(null);setPanel(null);window.setTimeout(()=>globe.current?.reset(),100);}
  function selectRevision(next:string){setId(next);setFacility(null);setReview(null);if(main.current)main.current.scrollTop=0;}
  const target=data?.snapshot.mitigation.request.hospital_id;
  const forecast=data&&target?facilityForecast(data,target,'disruption'):undefined;
  return <div className={`app-shell operator-experience theme-${theme} ${detail?'risk-mode':''}`}>
    <aside className="rail"><a className="brand" href="#" aria-label="PharmaX overview" onClick={e=>{e.preventDefault();overview();}}><BrandMark/></a>
      <nav aria-label="Main navigation">
        <button className={`rail-item ${!detail&&!panel?'active':''}`} onClick={overview}><Map/><span>Overview</span></button>
        <button className={`rail-item ${detail||panel==='investigations'?'active':''}`} onClick={()=>setPanel('investigations')}><FileText/><span>Investigations</span></button>
        <button className="rail-item" onClick={()=>{setPanel('facilities');globe.current?.focusPoint([-89,40],1.1);}}><Box/><span>Hospitals</span></button>
        <button className="rail-item" onClick={()=>setPanel('evidence')}><ShieldCheck/><span>Evidence</span></button>
      </nav>
    </aside>
    <div className="workspace"><header className="topbar"><div className="wordmark" aria-label="PharmaX"><BrandMark/><span className="brand-name">PharmaX</span></div><div className="top-actions">
      <button className="icon-button theme-toggle" aria-label={`Switch to ${theme==='dark'?'light':'dark'} mode`} onClick={()=>setTheme(t=>t==='dark'?'light':'dark')}>{theme==='dark'?<Sun size={16}/>:<Moon size={16}/>}</button>
      <button className="icon-button" aria-label="Toggle attention list" aria-pressed={alerts} onClick={()=>setAlerts(!alerts)}><Bell size={16}/></button>
    </div></header>
    <main ref={main}>
      {!detail&&<section className="overview-bar"><div><div className="eyebrow">PHARMACEUTICAL SUPPLY</div><h1>Supply overview</h1></div><div className="overview-stats">
        <button onClick={()=>setPanel('investigations')}><span>Investigations</span><strong>{!loading&&!error?groups.length:'—'}</strong></button>
        <button onClick={()=>setPanel('facilities')}><span>Facility shortfalls</span><strong className="amber">{data&&!loading&&!error?affected(data).length:'—'}</strong></button>
        <button onClick={()=>setPanel('facilities')}><span>Affected medicines</span><strong>{data&&!loading&&!error?new Set(data.forecasts.filter(f=>f.case==='disruption'&&f.forecast.total_unmet>0).map(f=>f.forecast.request.product_id)).size:'—'}</strong></button>
      </div></section>}
      <section className="map-section" aria-label="Pharmaceutical supply network"><div className="map-toolbar">{data&&<Dropdown label={activeMedicine===data.snapshot.mitigation.request.product_id?`Carboplatin · ${labelMedicine(data).split(' /')[0]}`:'Other modeled presentation'} items={data.snapshot.medicines.map(m=>({label:m.presentation.split(';')[0],action:()=>setMedicine(m.product_id)}))}/>}</div>
        <div className="map-canvas"><Suspense fallback={<div className="globe-loading" role="status">Rendering map…</div>}><GlobeScene ref={globe} signals={error?[]:signals} paths={error?[]:paths} routes alternate={false} alerts={true} theme={theme} selectedId={facility??(detail?'advisory':undefined)} selectedRouteId={detail?'modeled-dependency':undefined} onSelect={s=>s.hospital?openFacility(s.id):openInvestigation()}/></Suspense>
          <div className="map-legend" aria-label="Map legend"><span><i className="legend-line"/>Modeled dependency</span><span><i className="legend-risk"/>Advisory context</span><span><i className="legend-update"/>Simulated hospital</span></div>
          <div className="map-context"><span className="crosshair-label">SIMULATED US NETWORK · ILLUSTRATIVE POSITIONS</span></div>
          {!detail&&alerts&&<aside className="alerts-overlay operator-attention" aria-label="Priority pharmaceutical investigations"><div className="alerts-heading"><span>Needs attention <b>{!loading&&!error?groups.length:'—'}</b></span><span className="execution-pill">Saved simulation</span></div>
            {loading?<p className="network-state" role="status">Loading operator data…</p>:error?<div className="network-state" role="alert"><p>{error}</p><button className="secondary-button" onClick={()=>refresh(v=>v+1)}>Retry connection</button></div>:!data?<p className="network-state">No saved investigations. Create an explicit replay with your operator.</p>:matches?<button className="alert-row alert-risk alert-priority" onClick={openInvestigation}>
              <div className="alert-meta"><span>CARBOPLATIN · SUPPLY RISK</span><span>Human review</span></div><h3>{forecast?.total_unmet??'—'} vials projected unmet<ArrowUpRight size={16}/></h3>
              <p className="attention-facility">{data.snapshot.hospitals.find(h=>h.hospital_id===target)?.name}</p>
              <div className="alert-bottom"><span>From {forecast?.projected_stockout_date??'no unmet date'}</span><strong>{recommended(data)?.remaining_unmet??'—'} after mitigation</strong></div>
              <p className="attention-context">Red Sea advisory context · Simulated medicine dependency</p>
              <span className="attention-action">Review {recommended(data)?.candidate.kind==='transfer'?'hospital transfer':'supplier alternative'} <ArrowUpRight size={13}/></span>
            </button>:<p className="network-state">No saved shortfall forecast for this presentation. No cross-strength substitution is assumed.</p>}
            {data&&!error&&!loading&&<><button className="all-activity" onClick={()=>{setPanel('facilities');globe.current?.focusPoint([-89,40],1.1);}}>Explore {data.snapshot.hospitals.length} simulated US hospitals<ArrowUpRight size={14}/></button><button className="all-activity" onClick={()=>{const s=signals.find(s=>s.id==='advisory');if(s)focus(s);}}>Focus Red Sea advisory<ArrowUpRight size={14}/></button></>}
          </aside>}
          <div className="map-bottom-left"><div className="map-controls"><button aria-label="Zoom in" onClick={()=>globe.current?.zoomIn()}><Plus size={16}/></button><button aria-label="Zoom out" onClick={()=>globe.current?.zoomOut()}><Minus size={16}/></button><button aria-label="Reset map" onClick={()=>globe.current?.reset()}><Crosshair size={16}/></button></div></div>
          <div className="map-attribution">Natural Earth · Modeled route bounds · Fictional hospitals</div>
        </div>
      </section>
      {detail&&(loading?<section className="operator-loading" role="status">Loading saved revision…</section>:error?<section className="operator-loading" role="alert"><p>{error}</p><button onClick={()=>refresh(v=>v+1)}>Retry connection</button></section>:data?<InvestigationWorkspace data={data} family={family} cache={cache} onRevision={selectRevision} onBack={overview} onHospital={openFacility} onReview={setReview}/>:null)}
    </main>
    <button className="chat-trigger operator-chat" onClick={()=>setPanel('chat')}><BrandMark/>Ask PharmaX<ArrowUpRight size={14}/></button>
    <span className="operator-mode">Simulation · Deterministic tools · Live inference unavailable</span>
    </div>
    {facility&&data&&<Sheet open modal={false} onOpenChange={open=>{if(!open)setFacility(null);}} title="Modeled hospital" description="Fictional Illinois network · Illustrative map position"><div className="panel-body"><FacilityCard data={data} hospitalId={facility}/><button className="primary-button" onClick={openInvestigation}>Open investigation<ArrowUpRight size={14}/></button></div></Sheet>}
    <Sheet open={!!panel} onOpenChange={open=>{if(!open)setPanel(null);}} title={panel==='chat'?'Ask PharmaX':panel==='facilities'?'Simulated US hospitals':panel==='evidence'?'Evidence & provenance':'Investigation history'} description="Saved backend records · No live inference">
      <div className="panel-body operator-sheet">
        {panel==='investigations'&&groups.map(c=><button className="register-row" key={c.investigation_id} onClick={()=>{selectRevision(c.investigation_id);openInvestigation();}}><span>Carboplatin — Red Sea supply risk<small>{cases.filter(v=>investigationRoot(v,cases)===investigationRoot(c,cases)).length} immutable versions · {c.status}</small></span><ArrowUpRight size={16}/></button>)}
        {panel==='facilities'&&data&&<><p className="detail-copy">Three fictional facilities in Illinois. Marker positions are illustrative, not real addresses. Stock is modeled at the scenario anchor date.</p>{data.snapshot.hospitals.map(h=><button className="register-row" key={h.hospital_id} onClick={()=>openFacility(h.hospital_id)}><span>{h.name}<small>{facilityForecast(data,h.hospital_id,'disruption')?.initial_usable??'—'} usable vials · {facilityForecast(data,h.hospital_id,'disruption')?.total_unmet??'—'} projected unmet</small></span><ArrowUpRight size={16}/></button>)}</>}
        {panel==='evidence'&&data&&<EvidenceList data={data}/>}
        {panel==='chat'&&<><div className="chat-intro"><BrandMark/><h3>Ask about this investigation</h3><p>Live Nemotron inference is unavailable. Use the saved deterministic results below; no model requests are sent.</p></div>{data&&<><button className="register-row" onClick={openInvestigation}><span>Why is this facility exposed?<small>Open calculated shortage, retained evidence and assumptions.</small></span><ArrowUpRight size={16}/></button><button className="register-row" onClick={()=>{setPanel('facilities');globe.current?.focusPoint([-89,40],1.1);}}><span>Explore the simulated hospital network</span><ArrowUpRight size={16}/></button></>}<p className="chat-disclaimer">Simulation / mocked inference · Actual saved tool outputs</p></>}
        {error&&<p role="alert">{error}</p>}
      </div>
    </Sheet>
    {review&&data&&<Sheet open onOpenChange={open=>{if(!open)setReview(null);}} title="Review internal action draft" description="No dispatch, stock release or external order"><div className="panel-body"><ActionReviewForm key={review.action_id} action={review} client={client} onSaved={updated=>{setReview(updated);setCache(c=>({...c,[data.snapshot.investigation_id]:{...data,actions:data.actions.map(a=>a.action_id===updated.action_id?updated:a)}}));}}/></div></Sheet>}
  </div>;
}
export function EvidenceList({data}:{data:WorkspaceData}) {
  const [kind,setKind]=useState('observed');
  const records=data.snapshot.evidence.filter(e=>e.kind===kind);
  return <div className="provenance"><p className="detail-copy">Observed advisory evidence establishes regional maritime risk. Drug shipments, hospitals, inventory and patient schedules are simulated. Calculations are derived from those assumptions.</p><Dropdown label={`${kind} evidence (${records.length})`} items={['observed','derived','simulated'].map(k=>({label:k,action:()=>setKind(k)}))}/>{records.map(e=>{
    let href:string|undefined;try{const url=new URL(e.source_url);if(['http:','https:'].includes(url.protocol)&&url.hostname!=='example.invalid')href=url.href;}catch{/* Unusable source remains visible as text. */}
    return <article className="provenance-record" key={e.evidence_id}><strong>{e.kind} · {e.source}</strong>{href?<a href={href} target="_blank" rel="noreferrer">Open retained source reference <ArrowUpRight size={12}/></a>:<span>Simulated or internal source record</span>}<small>Retrieved {e.observed_at}</small><code>{e.evidence_id}</code><code>SHA-256 {e.content_sha256}</code></article>;
  })}</div>;
}
