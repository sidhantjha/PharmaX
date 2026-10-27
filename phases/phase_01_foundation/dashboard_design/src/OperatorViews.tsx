import { useState } from 'react';
import { ArrowLeft, ArrowUpRight, Check, ShieldCheck } from 'lucide-react';
import type { CaseSummary, OperatorClient } from './operatorApi';
import { facilityForecast, labelMedicine, recommended, type ActionDraft, type WorkspaceData } from './operatorWorkspace';
import { EvidenceList } from './OperatorExperience';

export function FacilityCard({data,hospitalId}:{data:WorkspaceData;hospitalId:string}) {
  const h=data.snapshot.hospitals.find(h=>h.hospital_id===hospitalId);
  const forecast=facilityForecast(data,hospitalId,'disruption'), mitigation=facilityForecast(data,hospitalId,'mitigation');
  const chosen=recommended(data), donor=chosen?.impacts.find(i=>i.hospital_id===hospitalId)?.donor;
  if(!h)return <p role="alert">This facility is not in the selected investigation.</p>;
  const inventory=forecast?.initial_inventory_inputs??[];
  return <article className="facility-card"><span className="execution-pill">Simulated facility · {h.state}, {h.country_iso}</span><h3>{h.name}</h3>
    <p>{labelMedicine(data)} · Exact product only</p>
    <dl className="facility-facts"><div><dt>Usable inventory</dt><dd>{forecast?.initial_usable??'—'} vials</dd></div><div><dt>On hand / reserved</dt><dd>{inventory.reduce((s,l)=>s+l.on_hand,0)} / {inventory.reduce((s,l)=>s+l.reserved,0)} vials</dd></div><div><dt>Projected shortfall</dt><dd>{forecast?.total_unmet??'—'} vials</dd></div><div><dt>After mitigation</dt><dd>{mitigation?.total_unmet??'—'} vials unmet</dd></div><div><dt>First unmet demand</dt><dd>{forecast?.projected_stockout_date??'None in horizon'}</dd></div><div><dt>Planning role</dt><dd>{donor?'Evaluated donor':hospitalId===data.snapshot.mitigation.request.hospital_id?'Recipient / affected facility':'Network facility'}</dd></div></dl>
    {donor&&chosen?.supply_balance&&<p>Selected source balance: {chosen.supply_balance.available_before} → {chosen.supply_balance.available_after} unreserved vials. Donor forecast is checked independently.</p>}
    <p className="operator-note">Inventory as of {forecast?.as_of??'saved scenario'}. Fictional organization; illustrative map position, not a real address. No clinical substitution is authorized.</p>
  </article>;
}
export function InvestigationWorkspace({data,family,cache,onRevision,onBack,onHospital,onReview}:{
  data:WorkspaceData;family:CaseSummary[];cache:Record<string,WorkspaceData>;onRevision:(id:string)=>void;
  onBack:()=>void;onHospital:(id:string)=>void;onReview:(action:ActionDraft)=>void;
}) {
  const c=data.snapshot, target=c.mitigation.request.hospital_id;
  const baseline=facilityForecast(data,target,'baseline'), disrupted=facilityForecast(data,target,'disruption'), mitigated=facilityForecast(data,target,'mitigation');
  const chosen=recommended(data), h=c.hospitals.find(h=>h.hospital_id===target);
  const clinical=c.clinical_impact.find(i=>i.hospital_id===target&&i.case==='disruption');
  const afterClinical=c.clinical_impact.find(i=>i.hospital_id===target&&i.case==='mitigation');
  const action=data.actions.find(a=>a.kind===(chosen?.candidate.kind==='transfer'?'stock_transfer':'supplier_inquiry'));
  const values=[baseline?.total_unmet,disrupted?.total_unmet,mitigated?.total_unmet];
  return <section className="risk-detail connected-detail" aria-labelledby="connected-risk-title">
    <div className="risk-intro"><button className="back-button" onClick={onBack}><ArrowLeft size={15}/>Full map</button>
      <div className="section-kicker"><span>Carboplatin · Supply risk</span><span className="severity-chip">{c.parent_id?'Revised scenario':'Original scenario'}</span></div>
      <h1 id="connected-risk-title">Prevent a modeled<br/>hospital shortfall.</h1>
      <p className="risk-location">{h?.name} · {labelMedicine(data)}</p>
      <div className="shortfall-lead"><strong>{disrupted?.total_unmet??'—'}</strong><span>vials projected unmet<small>First unmet demand · {disrupted?.projected_stockout_date??'None in horizon'}</small></span></div>
      <p className="risk-summary">{clinical?.exposed_sessions??'—'} synthetic sessions exposed across {clinical?.unique_exposed_patients??'—'} modeled patients. The selected feasible plan leaves {mitigated?.total_unmet??'—'} vials unmet and {afterClinical?.exposed_sessions??'—'} sessions exposed in the calculated horizon.</p>
      <p className="operator-note">Observed maritime advisory → simulated pharmaceutical dependency → deterministic hospital forecast. No observed drug delay or patient outcome is asserted.</p>
      <div className="context-controls"><button className="secondary-button" onClick={()=>onHospital(target)}>Inspect affected hospital<ArrowUpRight size={13}/></button></div>
    </div>
    <section className="impact-section"><div className="section-kicker"><span>Impact forecast</span><span>Calculated · synthetic inputs</span></div>
      <figure className="impact-curve" role="img" aria-label={`Unmet vials: baseline ${values[0]??'unavailable'}, disruption ${values[1]??'unavailable'}, mitigation ${values[2]??'unavailable'}`}>
        <svg viewBox="0 0 480 125" aria-hidden="true"><path className="curve-guide" d="M 40 95 L 440 95"/><path className="curve-exposure" d="M40 95 C130 95 150 25 240 25"/><path className="curve-recovery" d="M240 25 C330 25 350 95 440 95"/>{[40,240,440].map((x,i)=><g key={x}><circle cx={x} cy={i===1?25:95} r="5" className={i===1?'curve-risk':'curve-safe'}/><text x={x} y={i===1?10:75} textAnchor="middle">{values[i]??'—'}</text></g>)}</svg>
        <figcaption><span>Baseline<strong>{values[0]??'—'} unmet</strong></span><span>Disruption<strong>{values[1]??'—'} unmet</strong></span><span>Mitigation<strong>{values[2]??'—'} unmet</strong></span></figcaption>
      </figure>
      <p className="operator-note">Vials · {disrupted?.as_of} to {disrupted?.end_on}. Exact product and presentation; no cross-strength conversion.</p>
      {c.revision?.previous_option_recheck&&<div className="revision-caution"><strong>Previous option: {c.revision.previous_option_recheck.remaining_unmet} vials still unmet</strong><p>{c.revision.previous_option_recheck.candidate_id} remains {c.revision.previous_option_recheck.feasible?'feasible':'infeasible'}, but does not meet the revised requirement. Feasibility and sufficiency are separate checks.</p></div>}
    </section>
    <section className="recommendation-section"><div className="section-kicker"><span>Recommended action</span><span>Human review required</span></div>
      {chosen?<article className="recommendation"><div className="recommendation-title"><ShieldCheck size={19}/><div><h3>{chosen.candidate.kind==='transfer'?'Review a hospital transfer':'Review supplier delivery'}</h3><p>{chosen.candidate.quantity} vials · {labelMedicine(data)}<br/>{chosen.candidate.source_id} → {h?.name}</p></div></div>
        <div className="recommendation-meta"><span>{chosen.original_unmet} → {chosen.remaining_unmet} unmet vials</span><span>Modeled arrival {chosen.candidate.arrival_on}</span></div>
        <p className="feasibility-line"><Check size={13}/>Exact product / presentation · {chosen.impacts.filter(i=>i.effective.unmet_vials===0).length} facilities with no effective unmet demand</p>
        {chosen.supply_balance&&<p className="operator-note">Source balance {chosen.supply_balance.available_before} → {chosen.supply_balance.available_after}; {chosen.supply_balance.conserved?'stock conserved':'inspect stock balance'}. {chosen.quantity_moved} modeled vials moved.</p>}
        {action?<><button className="primary-button review-draft" onClick={()=>onReview(action)}>Review internal draft<ArrowUpRight size={14}/></button><span className="draft-state">{action.status.replaceAll('_',' ')} · Dispatch unavailable</span></>:<p>No retained internal draft is available.</p>}
      </article>:<p className="operator-note">No feasible recommendation is retained. Operator review is required.</p>}
    </section>
    <section className="investigation-history"><div className="section-kicker"><span>One investigation · immutable history</span><span>{family.length} versions</span></div>
      <div className="revision-timeline">{family.map((version,i)=>{const d=cache[version.investigation_id],f=d?facilityForecast(d,d.snapshot.mitigation.request.hospital_id,'disruption'):undefined;return <button key={version.investigation_id} className={version.investigation_id===c.investigation_id?'current':''} aria-pressed={version.investigation_id===c.investigation_id} onClick={()=>onRevision(version.investigation_id)}><span className="timeline-node">{i+1}</span><span><strong>{version.parent_id?'Revised evidence':'Original investigation'}{i===family.length-1?' · Latest':''}</strong><small>{f?.total_unmet??'—'} → {d?recommended(d)?.remaining_unmet??'—':'—'} unmet vials</small><small>{d?recommended(d)?.candidate.candidate_id:'Loading saved calculation'}</small></span><ArrowUpRight size={14}/></button>;})}</div>
      {c.revision&&<p className="operator-note">{c.revision.decision_summary}</p>}
      <details className="immutable-identity"><summary>Immutable case & calculation IDs</summary><code>{c.investigation_id}</code>{c.parent_id&&<code>Parent: {c.parent_id}</code>}<code>Forecast: {disrupted?.analysis_id}</code><code>Mitigation: {chosen?.evaluation_id??'unavailable'}</code></details>
    </section>
    <section className="facility-section"><div className="section-kicker"><span>Simulated US hospital network</span><span>Illustrative positions</span></div><div className="facility-strip">{c.hospitals.map(h=>{const f=facilityForecast(data,h.hospital_id,'disruption');return <button key={h.hospital_id} onClick={()=>onHospital(h.hospital_id)}><i className={(f?.total_unmet??0)>0?'facility-pip exposed':'facility-pip'}/><strong>{h.name}</strong><span>{f?.initial_usable??'—'} usable · {f?.total_unmet??'—'} unmet vials</span><ArrowUpRight size={14}/></button>;})}</div></section>
    <section className="alternative-section"><div className="section-kicker"><span>Why this alternative?</span><span>Saved feasibility checks</span></div><div className="alternative-list">{c.mitigation.evaluations.map(e=><details key={e.evaluation_id} className={e.candidate.candidate_id===c.mitigation.recommendation_id?'chosen-alternative':''}><summary><span>{e.candidate.candidate_id}<small>{e.candidate.quantity} vials · {e.candidate.kind.replaceAll('_',' ')}</small></span><span>{!e.feasible?'Rejected':e.candidate.candidate_id===c.mitigation.recommendation_id?'Recommended':e.remaining_unmet>0?'Insufficient':'Feasible alternative'}<small>{e.remaining_unmet} unmet remain</small></span></summary><p>{e.candidate.presentation}</p>{e.failures.map((f,i)=><p key={i}><strong>{f.code.replaceAll('_',' ')}</strong> · {f.detail}</p>)}{e.feasible&&<p>Passed the retained product, source, route, storage and donor-safety evaluation. This is conditional on simulated permissions and availability; actual release still requires human verification.</p>}<code>{e.evaluation_id}</code></details>)}</div></section>
    <section className="saved-evidence"><details><summary>Evidence & provenance · {c.evidence.length} retained references</summary><EvidenceList data={data}/></details></section>
    <section className="agent-activity connected-trace"><div className="agent-title-row"><h3>Decision explanation</h3><span className="execution-pill">Simulation / mocked inference</span></div>
      <p className="operator-note">{chosen?`${chosen.candidate.candidate_id} is preferred among the evaluated feasible alternatives. ${chosen.original_unmet-chosen.remaining_unmet} modeled unmet vials are avoided.`:'No feasible plan is retained.'} The recommendation comes from deterministic calculations and fixture-directed decisions; genuine live Nemotron inference is unverified.</p>
      <div className="decision-sequence"><span>Retained advisory</span><span>Simulated dependency</span><span>Calculated shortfall</span><span>Safety evaluation</span><span>Retained rejection</span><span>H-MEM constraint recall</span><span>Re-evaluated alternative</span><span>Human review</span></div>
      <details className="technical-trace"><summary>Mocked inference & executed tool trace · {c.agents.length} specialist records</summary><p>Execution mode: {c.model_execution} / {c.mode}. These are saved backend execution receipts, not live model reasoning. Tool calls executed locally; model decisions are fixture-directed.</p>
        <div className="agent-log">{c.agents.map(a=><details className="saved-agent" key={a.agent_id}><summary><strong>{a.agent_id}</strong><span>{a.status} · {a.execution}</span><small>{a.tool_records.length} tool records · {a.memory_ids.length} memory IDs</small></summary><p>{a.responsibility}</p><p>{a.explanation}</p><p>Next: {a.next_action}</p><div className="reference-ids">{a.evidence_ids.map(e=><code key={e}>{e}</code>)}{a.memory_ids.map(e=><code key={e}>Memory {e}</code>)}</div></details>)}</div>
        <details className="retained-ledger"><summary>Executed operation ledger · {c.decision_ledger.length} records</summary>{c.decision_ledger.map(r=><article key={r.record_id}><strong>{r.sequence}. {r.operation}</strong><small>{r.agent_id} · {r.status} · {r.classification}</small>{r.tool_identifier&&<code>Tool: {r.tool_identifier}</code>}{[...r.evidence_ids,...r.memory_refs,...r.memory_ids].map((ref,i)=><code key={i}>{ref}</code>)}</article>)}</details>
        <p>{c.mitigation.memory_ids.length} mitigation memory IDs retained. {c.revision?.prior_constraint_memory_refs.length??0} prior constraint memory references carried into this revision.</p>
      </details>
    </section>
  </section>;
}

export function ActionReviewForm({action,client,onSaved}:{action:ActionDraft;client:OperatorClient;onSaved:(a:ActionDraft)=>void}) {
  const [operator,setOperator]=useState(''),[note,setNote]=useState(''),[checks,setChecks]=useState<string[]>([]),[busy,setBusy]=useState(false),[error,setError]=useState<string|null>(null),[saved,setSaved]=useState(false);
  const attestations=action.status==='DRAFT'?['Draft reviewed for accuracy and simulation labels']:[...action.required_authorizations,...action.outstanding_verification];
  const canSave=['DRAFT','READY_FOR_REVIEW'].includes(action.status);
  async function submit(e:React.FormEvent){e.preventDefault();setBusy(true);setError(null);setSaved(false);
    try{const updated=await client.reviewAction(action.investigation_id,action.action_id,{expected_revision:action.revision,status:action.status==='DRAFT'?'READY_FOR_REVIEW':'APPROVED',operator:operator.trim(),note:note.trim(),attestations:checks});onSaved(updated);setChecks([]);setSaved(true);}
    catch{setError('Review was not saved. The action may have changed or the API may be unavailable. Reload its current revision before retrying.');}
    finally{setBusy(false);}
  }
  return <form className="action-review-form" onSubmit={submit}><span className="execution-pill">{action.status.replaceAll('_',' ')} · Internal draft revision {action.revision}</span><h3>{action.kind.replaceAll('_',' ')}</h3><p>{action.expected_benefit}</p><pre>{action.draft}</pre><h4>Required human authorizations</h4><ul>{action.required_authorizations.map(s=><li key={s}>{s}</li>)}</ul><h4>Outstanding verification</h4><ul>{action.outstanding_verification.map(s=><li key={s}>{s}</li>)}</ul>
    <p className="operator-note">Saving review only updates this internal draft. No purchase, transfer, message, stock release or clinical change is dispatched. Do not attest to checks you have not performed.</p>
    {canSave&&<><label>Your reviewer name<input required minLength={2} maxLength={100} value={operator} onChange={e=>setOperator(e.target.value)}/></label><label>Review note<textarea required minLength={3} maxLength={1000} value={note} onChange={e=>setNote(e.target.value)}/></label>{attestations.map(s=><label className="review-attestation" key={s}><input type="checkbox" checked={checks.includes(s)} onChange={e=>setChecks(c=>e.target.checked?[...c,s]:c.filter(v=>v!==s))}/>{s}</label>)}<button className="primary-button" disabled={busy||operator.trim().length<2||note.trim().length<3||attestations.some(a=>!checks.includes(a))}>{busy?'Saving…':action.status==='DRAFT'?'Mark internal draft reviewed':'Approve internal draft only'}</button></>}
    {error&&<div role="alert"><p>{error}</p><button type="button" className="secondary-button" disabled={busy} onClick={async()=>{setBusy(true);try{const fresh=await client.workspace(action.investigation_id);const current=fresh.actions.find(a=>a.action_id===action.action_id);if(current){onSaved(current);setError(null);setChecks([]);}}catch{setError('Current action could not be reloaded. No review was sent.');}finally{setBusy(false);}}}>Reload current draft</button></div>}
    {saved&&<p role="status">Internal review saved. Dispatch remains unavailable.</p>}
  </form>;
}
