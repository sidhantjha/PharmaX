import { useState } from 'react';
import { ArrowLeft, ArrowUpRight, Check, ChevronDown, ExternalLink, FileText, ShieldCheck } from 'lucide-react';
import type { GlobeSignal } from './GlobeScene';
import { AgentActivity } from './AgentActivity';

const demoSources=[
  {id:'SRC-IMO',name:'IMO · Red Sea area',date:'Updated 2026',url:'https://www.imo.org/en/mediacentre/hottopics/pages/red-sea.aspx',summary:'IMO monitoring page for reported Red Sea incidents. Regional context only; it does not confirm an incident on this demo route.'},
  {id:'SRC-UNCTAD',name:'UNCTAD · Maritime Transport 2025',date:'2025 report',url:'https://unctad.org/publication/review-maritime-transport-2025',summary:'Reports continued Suez traffic disruption and Cape rerouting at industry scale. Historical context, not a shipment forecast.'}
];

export function RiskDetail({signal,onBack}:{signal:GlobeSignal;onBack:()=>void}) {
  const [reviewOpen,setReviewOpen]=useState(false);
  const [confirm,setConfirm]=useState(false);
  const [status,setStatus]=useState<'idle'|'reviewed'|'approved'>('idle');
  const isRisk=signal.kind==='risk';
  return <section className="risk-detail" aria-labelledby="risk-title">
    <div className="risk-intro">
    <button className="back-button" onClick={onBack}><ArrowLeft size={16}/>Full map</button>
    <div className="risk-heading"><div><div className="section-kicker"><span>{signal.id}</span><span className="severity-chip">{isRisk?'High · demo scenario':'Illustrative update'}</span></div>
      <h1 id="risk-title">{signal.title}</h1><p className="risk-location">{signal.location}<span>·</span>{signal.route}</p></div>
      <button className="focus-risk" onClick={()=>document.dispatchEvent(new CustomEvent('pharmax-focus-risk',{detail:signal}))} aria-label="Focus selected risk on globe"><ArrowUpRight size={16}/></button>
    </div>
    <p className="risk-summary">{signal.detail}</p>

    <section className="source-section"><div className="section-kicker"><span>Context sources</span><span>Not shipment-specific</span></div>
      <div className="source-list">{demoSources.map(source=><a className="source-chip" key={source.id} href={source.url} target="_blank" rel="noreferrer"><span className="source-icon"><FileText size={14}/></span><span><strong>{source.name}</strong><small>{source.date} · {source.id}</small></span><ExternalLink size={13}/></a>)}</div>
      <p className="source-caveat">These public references describe regional and industry context. No retrieved document confirms this illustrative alert or shipment.</p>
      <p className="decision-inputs"><b>Demo decision inputs</b> · Existing route geometry, the selected marker, and the two context references above. No live feed or agent output.</p>
    </section>

    </div>
    <section className="impact-section"><div className="section-kicker"><span>Impact forecast</span><span className="demo-chip">Illustrative model</span></div>
      <div className="impact-grid"><div><strong>{isRisk?'+7–10d':signal.impact}</strong><span>Delay exposure</span></div><div><strong>{isRisk?'$18.4k':'$4.2k'}</strong><span>Added cost</span></div><div><strong>{isRisk?'03':'01'}</strong><span>Demo shipments</span></div><div><strong>{isRisk?'68%':'—'}</strong><span>Scenario likelihood</span></div></div>
      <p className="quiet-copy">Mock figures for interface review. They are not observed, sourced forecasts, or actual shipment counts.</p>
    </section>

    <section className="recommendation-section"><div className="section-kicker"><span>Suggested action</span><span>Requires operator review</span></div>
      <article className="recommendation"><div className="recommendation-title"><ShieldCheck size={17}/><div><h3>Review Cape corridor</h3><p>Compare the existing alternate route to reduce exposure around Bab el-Mandeb.</p></div></div>
        <div className="recommendation-meta"><span>Demo outcome: −3–5d exposure</span><span>Trade-off: +$18.4k</span></div>
        <div className="recommendation-actions"><button className="secondary-button" aria-expanded={reviewOpen} onClick={()=>{setReviewOpen(!reviewOpen);setStatus('reviewed')}}>{reviewOpen?'Close review':'Review'}</button><button className="primary-button" onClick={()=>setConfirm(true)}>{status==='approved'?'Approved locally':'Approve locally'}<Check size={15}/></button></div>
        {reviewOpen&&<div className="recommendation-review"><div className="section-kicker"><span>Review details</span><span>Demo · RS-01</span></div>
          <dl className="review-facts"><div><dt>Current corridor</dt><dd>Mumbai → Rotterdam via Red Sea</dd></div><div><dt>Risk point</dt><dd>Bab el-Mandeb · route marker at 43°E, 13°N</dd></div><div><dt>Scenario case</dt><dd>7–10 day exposure across 3 fictional shipments</dd></div><div><dt>Alternate</dt><dd>Existing Cape corridor · 3–5 days less risk exposure in this mock scenario</dd></div><div><dt>Trade-off</dt><dd>+$18.4k modeled freight allowance; no live rate data</dd></div><div><dt>Inputs</dt><dd>SRC-IMO and SRC-UNCTAD provide context only. Route geometry and all estimates are illustrative.</dd></div></dl>
          <p className="review-note">This comparison is a UI demonstration. It has not checked cargo, medicine, customs, insurance, live conditions, or carrier availability.</p>
          <span className="review-state">Local review state {status==='reviewed'?'saved':'available'} · No external action</span>
        </div>}
        {status==='approved'&&<p className="approval-status" role="status">Local prototype approval recorded. No route or shipment was changed.</p>}
      </article>
    </section>
    <AgentActivity/>
    {confirm&&<div className="confirm-scrim"><div className="confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby="approve-title"><h3 id="approve-title">Record local approval?</h3><p>This records local prototype state only. It will not change a route or contact a logistics provider.</p><div><button className="secondary-button" onClick={()=>setConfirm(false)}>Cancel</button><button className="primary-button" onClick={()=>{setStatus('approved');setConfirm(false)}}>Confirm locally<Check size={14}/></button></div></div></div>}
  </section>;
}
