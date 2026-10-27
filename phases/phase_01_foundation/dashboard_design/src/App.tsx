import React, { lazy, Suspense, useState, useRef, useEffect } from 'react';
import { Input } from './OpenUIInput';
import { ArrowUpRight, ArrowRight, Bell, Box, Check, ChevronRight, CircleHelp, Crosshair, FileText, Layers, Map, MessageSquare, Minus, Plus, Send, ShieldCheck, Sun, Moon, TriangleAlert } from 'lucide-react';
import { Dropdown, Sheet } from './primitives';
const GlobeScene=lazy(()=>import('./GlobeScene').then(module=>({default:module.GlobeScene})));
import type { GeoPath, GlobeSceneHandle, GlobeSignal } from './GlobeScene';
import { RiskDetail } from './RiskDetail';
import { InvestigationsPanel } from './InvestigationsPanel';
import { BrandMark } from './BrandMark';
import './styles.css';
import './review.css';
import { ScenarioExperience } from './HeleneExperience';

type Signal = GlobeSignal & { heading:string; detail:string; route:string; };
const signals: Signal[] = [
  {id:'RS-01', title:'Red Sea disruption',location:'Bab el-Mandeb',impact:'+7 days',time:'12 min',kind:'risk',point:[43,13],routeId:'main',heading:'Route exposure',detail:'Illustrative disruption scenario affecting a modeled pharmaceutical corridor. The seven-day delay is an assumption, not an observed vessel delay.',route:'Mumbai → Rotterdam'},
  {id:'SH-02', title:'Shipment delayed',location:'Arabian Sea',impact:'+2 days',time:'28 min',kind:'shipment',point:[64,18],routeId:'main',heading:'Arrival revised',detail:'Simulated shipment PX-204 is used to demonstrate route selection and arrival changes. No shipment telemetry is connected.',route:'Mumbai → Rotterdam'},
  {id:'UP-03', title:'Supply update',location:'Singapore',impact:'On schedule',time:'46 min',kind:'update',point:[104,1],routeId:'east',heading:'Supply confirmed',detail:'Illustrative supplier confirmation for a fictional shipment. This is a design state, not an observed supplier update.',route:'Singapore → Los Angeles'}
];
const paths:GeoPath[] = [
  {id:'main',kind:'risk',coords:[[73,19],[66,15],[57,11],[46,12],[43,13],[39,21],[33,30],[29,34],[13,38],[0,43],[4,52]]},
  {id:'east',kind:'shipment',coords:[[104,1],[97,6],[81,8],[73,19]]},
  {id:'west',kind:'update',coords:[[-118,34],[-129,30],[-146,24],[-169,17],[-179,13]]},
  {id:'alternate',kind:'alternate',coords:[[73,19],[60,6],[49,-9],[38,-24],[18,-35],[3,-30],[-13,-2],[-19,23],[-5,43],[4,52]]}
];
const routeRows = [ ['PX-204','Mumbai → Rotterdam','Delayed'],['PX-218','Singapore → Los Angeles','In transit'],['PX-231','Basel → New York','On schedule'] ];
const shipmentSignals: Signal[] = [signals[1], signals[2], {
  id:'PX-231', title:'PX-231', location:'New York', impact:'On schedule', time:'Demo',
  kind:'update', point:[-74,41], heading:'Arrival planned', route:'Basel → New York',
  detail:'Fictional shipment PX-231 demonstrates the on-schedule detail state. No observed delivery or vessel position is connected.'
}];

export function App(){ return <ScenarioExperience/>; }

/** Retained design reference and original interaction regression surface; never an API fallback. */
export function DesignReferenceApp(){
  const [selected,setSelected]=useState<Signal|null>(null);
  const [panel,setPanel]=useState<string|null>(null);
  const [filter,setFilter]=useState('All activity');
  const [medicine,setMedicine]=useState('All medicines');
  const globeRef=useRef<GlobeSceneHandle>(null);
  const mainRef=useRef<HTMLElement>(null);
  const [layers,setLayers]=useState({routes:true,alerts:true,alternate:false});
  const [showAlerts,setShowAlerts]=useState(true);
  const [chat,setChat]=useState<{role:string;text:string}[]>([]);
  const [message,setMessage]=useState('');
  const [theme,setTheme]=useState<'dark'|'light'>(()=>{const saved=localStorage.getItem('pharmax-theme');return saved==='light'||saved==='dark'?saved:typeof window!=='undefined'&&window.matchMedia?.('(prefers-color-scheme: light)').matches?'light':'dark'});
  const visible=signals.filter(s=>filter==='All activity'||s.kind===filter).sort((a,b)=>(a.kind==='risk'?-1:0)-(b.kind==='risk'?-1:0));
  function focusWhenReady(signal:Signal,attempt=0){if(globeRef.current){globeRef.current.focus(signal);return;}if(attempt<24)window.setTimeout(()=>focusWhenReady(signal,attempt+1),100);}
  function openSignal(s:Signal){if(mainRef.current)mainRef.current.scrollTop=0;setSelected(s);setPanel(null);window.setTimeout(()=>focusWhenReady(s),360);}
  function reset(){globeRef.current?.reset();}
  function goOverview(){setPanel(null);setSelected(null);window.setTimeout(reset,360);}
  useEffect(()=>{localStorage.setItem('pharmax-theme',theme);document.documentElement.dataset.theme=theme;},[theme]);
  useEffect(()=>{const focus=(event:Event)=>globeRef.current?.focus((event as CustomEvent<Signal>).detail);document.addEventListener('pharmax-focus-risk',focus);return()=>document.removeEventListener('pharmax-focus-risk',focus)},[]);
  function ask(text:string){
    if(!text.trim())return;
    const lower=text.toLowerCase();
    const answer=lower.includes('risk')||lower.includes('sea')||lower.includes('delay')?'The demo highlights Red Sea route exposure. The +7 day delay is a scenario assumption. Open the alert to inspect its route and evidence label.':lower.includes('shipment')||lower.includes('route')?'Three fictional routes are included for interaction design. PX-204 has a simulated delay; no real vessel positions are connected.':lower.includes('source')||lower.includes('evidence')?'All operational markers, alerts, counts, times, and chat replies in this prototype are simulated. Country boundaries come from world-atlas / Natural Earth.':'This local demo supports route, risk, and evidence questions. No live assistant is connected.';
    setChat(c=>[...c,{role:'user',text},{role:'assistant',text:answer}]);setMessage('');
  }
  return <div className={`app-shell theme-${theme} ${selected?'risk-mode':''}`}>
    <aside className="rail"><a className="brand" href="#" aria-label="PharmaX overview" onClick={e=>{e.preventDefault();goOverview();}}><BrandMark/></a>
      <nav aria-label="Main navigation"><button className={`rail-item ${panel===null?'active':''}`} onClick={goOverview} aria-label="Overview"><Map/><span>Overview</span></button>
      <button className={`rail-item ${panel==='Investigations'?'active':''}`} onClick={()=>setPanel('Investigations')} aria-label="Investigations"><FileText/><span>Investigations</span></button>
      <button className={`rail-item ${panel==='Shipments'?'active':''}`} onClick={()=>setPanel('Shipments')} aria-label="Shipments"><Box/><span>Shipments</span></button>
      <button className={`rail-item ${panel==='Risks'?'active':''}`} onClick={()=>setPanel('Risks')} aria-label="Risks"><ShieldCheck/><span>Risks</span></button>
      <button className={`rail-item ${panel==='Reports'?'active':''}`} onClick={()=>setPanel('Reports')} aria-label="Reports"><FileText/><span>Reports</span></button></nav>
    </aside>
    <div className="workspace"><header className="topbar"><div className="wordmark" aria-label="PharmaX"><BrandMark/><span className="brand-name">PharmaX</span></div>
      <div className="top-actions"><button className="icon-button theme-toggle" aria-label={`Switch to ${theme==='dark'?'light':'dark'} mode`} onClick={()=>setTheme(theme==='dark'?'light':'dark')}>{theme==='dark'?<Sun size={16}/>:<Moon size={16}/>}</button><button className="icon-button alerts-toggle" aria-label="Toggle alerts" aria-pressed={showAlerts} onClick={()=>setShowAlerts(!showAlerts)}><Bell size={17}/><span className="notification-dot"/></button></div></header>
    <main ref={mainRef}><section className="overview-bar"><div><div className="eyebrow">YOUR SUPPLY NETWORK</div><h1>Supply overview</h1></div><div className="overview-stats"><button onClick={()=>setPanel('Shipments')}><span>Shipments</span><strong>03<ArrowUpRight/></strong></button><button onClick={()=>setPanel('Risks')}><span>At risk</span><strong className="amber">01<ArrowUpRight/></strong></button><button onClick={()=>setPanel('Updates')}><span>Updates</span><strong>01<ArrowUpRight/></strong></button></div></section>
      <section className="map-section" aria-label="Supply network map"><div className="map-toolbar"><Dropdown label={medicine} items={['All medicines','Carboplatin'].map(label=>({label,action:()=>setMedicine(label)}))}/></div>
      <div className="map-canvas"><Suspense fallback={<div className="globe-loading" role="status">Rendering map…</div>}><GlobeScene ref={globeRef} signals={visible} paths={paths} routes={layers.routes} alternate={layers.alternate} alerts={layers.alerts} theme={theme} selectedId={selected?.id} selectedRouteId={selected?.routeId} onSelect={signal=>openSignal(signal as Signal)}/></Suspense>
        <div className="map-legend" aria-label="Map legend"><span><i className="legend-line"/>Shipment</span><span><i className="legend-risk"/>Bottleneck</span><span><i className="legend-update"/>Update</span></div>
        <div className="map-context"><span className="crosshair-label">01 / ROUTE NETWORK</span><h2>Route visibility.</h2></div>
        {showAlerts&&<aside className="alerts-overlay" aria-label="Priority alerts"><div className="alerts-heading"><span>Needs attention <b>{String(visible.length).padStart(2,'0')}</b></span><Dropdown label={filter==='All activity'?'All':filter==='risk'?'Risks':filter==='shipment'?'Shipments':'Updates'} items={[{label:'All activity',action:()=>setFilter('All activity')},{label:'Risks',action:()=>setFilter('risk')},{label:'Shipments',action:()=>setFilter('shipment')},{label:'Updates',action:()=>setFilter('update')}]}/></div>
        {visible.map((s,i)=><button className={`alert-row alert-${s.kind} ${s.kind==='risk'&&i===0?'alert-priority':''}`} key={s.id} onClick={()=>openSignal(s)}><div className="alert-meta"><span>{s.kind==='risk'?<TriangleAlert size={13}/>:s.kind==='shipment'?<Box size={13}/>:<Check size={13}/>} {s.kind==='risk'?'ROUTE RISK':s.kind==='shipment'?'SHIPMENT':'SUPPLY UPDATE'}</span><time>{s.time}</time></div><h3>{s.title}<ArrowUpRight size={17}/></h3><div className="alert-bottom"><span>{s.location}</span><strong>{s.impact}</strong></div><span className="alert-index">0{i+1}</span></button>)}
        {!visible.length&&<div className="empty-state">No matching activity<button onClick={()=>setFilter('All activity')}>Clear filter</button></div>}
        <button className="all-activity" onClick={()=>setPanel('Activity')}>View activity<ArrowRight size={14}/></button></aside>}
        <div className="map-bottom-left"><div className="map-controls"><button aria-label="Zoom in" onClick={()=>globeRef.current?.zoomIn()}><Plus size={16}/></button><button aria-label="Zoom out" onClick={()=>globeRef.current?.zoomOut()}><Minus size={16}/></button><span/><button aria-label="Reset map" onClick={reset}><Crosshair size={16}/></button></div></div>
        <div className="map-attribution">Natural Earth · Demo routes</div><button className="chat-trigger" onClick={()=>setPanel('chat')}><BrandMark/>Ask PharmaX<ArrowUpRight size={14}/></button>
      </div>
      <footer className="map-footer"><span><i/>Demo snapshot <span className="footer-divider">/</span> 24 Oct 2026</span><span>Illustrative routes · No live tracking<button onClick={()=>setPanel('About')} aria-label="About map data"><CircleHelp size={13}/></button></span></footer></section>
      <section className="bottom-shelf" aria-label="Explore details"><span className="eyebrow">A CLOSER LOOK</span>{[{label:'Shipments',value:'03',icon:Box},{label:'Bottlenecks',value:'01',icon:TriangleAlert},{label:'Supply health',value:'Review',icon:ShieldCheck}].map(({label,value,icon:Icon})=><button key={label} onClick={()=>setPanel(label)}><Icon size={17}/><span>{label}</span><span className={label==='Bottlenecks'?'amber':''}>{value}</span><ChevronRight size={15}/></button>)}</section>
      {selected&&<RiskDetail key={selected.id} signal={selected} onBack={()=>{setSelected(null);window.setTimeout(reset,360);}}/>}
    </main><footer className="page-footer"><span>PHARMAX / OPERATIONS</span><span></span><span>DESIGN PROTOTYPE · V.01</span></footer></div>
    <Sheet open={panel!==null&&panel!=='signal'} onOpenChange={open=>{if(!open)setPanel(null);}} title={panel==='chat'?'Ask PharmaX':panel||'Details'} description={panel==='Investigations'?'Operator API · Saved investigations':panel==='chat'?'Demo assistant · Local replies':'Design prototype · Simulated data'} side={panel==='Activity'?'bottom':'right'}>
      <div className={`panel-body ${panel==='chat'?'chat-body':''}`}>
        {panel==='Investigations'&&<InvestigationsPanel/>}
        {panel==='signal'&&selected&&<><div className={`detail-status ${selected.kind}`}>{selected.impact}<span>{selected.heading}</span></div><dl className="detail-list"><div><dt>Route</dt><dd>{selected.route}</dd></div><div><dt>Location</dt><dd>{selected.location}</dd></div><div><dt>Evidence</dt><dd>Simulated</dd></div><div><dt>Reference</dt><dd>{selected.id}</dd></div></dl><p className="detail-copy">{selected.detail}</p><details><summary>Evidence details</summary><p>No live source or vessel telemetry is connected. Relative times are illustrative. Country geography does not establish a pharmaceutical shipment route.</p></details><button className="primary-button" onClick={()=>{setPanel(null);globeRef.current?.focus(selected);}}>Focus route<Crosshair size={16}/></button></>}
        {panel==='chat'&&<><div className="chat-intro"><BrandMark/><h3>What needs a closer look?</h3><p>Explore the demo’s routes, risks, and evidence.</p></div><div className="chat-messages" aria-live="polite">{chat.map((m,i)=><div className={`message message-${m.role}`} key={i}><span>{m.role==='user'?'You':<><BrandMark/>PharmaX · Demo</>}</span><p>{m.text}</p></div>)}</div>{chat.length===0&&<div className="chat-suggestions">{['Explain route risk','Show shipments','Check evidence'].map(s=><button key={s} onClick={()=>ask(s)}>{s}<ArrowUpRight size={14}/></button>)}</div>}<form className="chat-form" onSubmit={e=>{e.preventDefault();ask(message);}}><Input aria-label="Message PharmaX" placeholder="Ask about this view" value={message} onChange={e=>setMessage(e.target.value)}/><button aria-label="Send message" disabled={!message.trim()}><Send size={17}/></button></form><p className="chat-disclaimer">Scripted demo. No model calls.</p></>}
        {panel==='Layers'&&<><p className="detail-copy">Choose what appears on the map.</p>{([{key:'routes',label:'Shipment routes'},{key:'alerts',label:'Activity markers'},{key:'alternate',label:'Alternate route'}] as const).map(l=><label className="layer-option" key={l.key}>{l.label}<input type="checkbox" checked={layers[l.key]} onChange={e=>setLayers({...layers,[l.key]:e.target.checked})}/></label>)}</>}
        {['Shipments','Reports','Supply health'].includes(panel||'')&&<>{panel==='Reports'?<><p className="detail-copy">Export the illustrative route register. No backend reports are connected.</p><button className="primary-button" onClick={()=>{const content='Evidence,Shipment,Route,Status\n'+routeRows.map(r=>'Simulated,'+r.join(',')).join('\n');const url=URL.createObjectURL(new Blob([content],{type:'text/csv'}));const a=document.createElement('a');a.href=url;a.download='pharmax-demo-routes.csv';a.click();URL.revokeObjectURL(url);}}>Export demo<FileText size={16}/></button></>:<><p className="detail-copy">{panel==='Supply health'?'Carboplatin · Illustrative supply overview. Real stock and patient impact are unavailable.':'Selected demo routes from the illustrative network.'}</p>{routeRows.map((r,i)=><button className="register-row" key={r[0]} onClick={()=>openSignal(shipmentSignals[i])}><span>{r[0]}<small>{r[1]}</small></span><span>{r[2]}<ChevronRight size={14}/></span></button>)}<details><summary>Network counts</summary><p>Three fictional shipments and three illustrative alerts are included. Only one risk alert and one supply update are shown.</p></details></>}</>}
        {['Risks','Bottlenecks','Updates','Activity'].includes(panel||'')&&<>{signals.filter(s=>panel==='Updates'?s.kind==='update':['Risks','Bottlenecks'].includes(panel||'')?s.kind==='risk':true).map(s=><button className="register-row" key={s.id} onClick={()=>openSignal(s)}><span>{s.title}<small>{s.location} · Simulated</small></span><span>{s.impact}<ArrowUpRight size={14}/></span></button>)}<p className="detail-copy">Demo activity only. No monitoring service is running.</p></>}
        {panel==='About'&&<><p className="detail-copy">A local design prototype for PharmaX. All shipments, routes, alerts, timestamps, counts, and assistant replies are simulated.</p><details open><summary>Map & sources</summary><p>Interactive 3D map: react-globe.gl / ThreeJS. Country geometry: world-atlas / Natural Earth. Icons: Lucide. Drawers and menus: useLayouts patterns with Base UI. Chat input: OpenUI.</p></details><details><summary>Design references</summary><p>useLayouts, ThreeUI Community, and OpenUI were reviewed. The existing data and interfaces are retained; 3D rendering uses react-globe.gl with the bundled country outlines and existing route coordinates.</p></details><p className="detail-copy">Prototype only. No actions are sent, no real shipment locations are shown, and no backend connection is implied.</p></>}
      </div>
    </Sheet>
  </div>;
}
