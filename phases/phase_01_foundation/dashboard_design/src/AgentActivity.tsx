import { ChevronDown } from 'lucide-react';

export type AgentActivityRecord={agentId:string;role:string;status:'pending'|'working'|'completed'|'failed';evidenceIds:string[];summary:string;inputs:string;output:string;};

const demoSteps:AgentActivityRecord[]=[
  {agentId:'Orchestrator',role:'Scope route review',status:'completed',evidenceIds:['RS-01'],inputs:'Selected alert and stored route geometry.',summary:'Prepared the risk review scope.',output:'Presentation flow only. No live agent run or external service call.'},
  {agentId:'Shipping Specialist',role:'Check corridor exposure',status:'completed',evidenceIds:['RS-01'],inputs:'Existing Mumbai–Rotterdam route vertices.',summary:'The stored route crosses the Red Sea corridor.',output:'Geographic relationship from demo route coordinates; not live vessel tracking.'},
  {agentId:'Risk Analyst',role:'Assess severity',status:'completed',evidenceIds:['RS-01'],inputs:'Preassigned demo severity and the selected point.',summary:'High severity is a preset demo classification.',output:'No independent risk scoring or fresh incident verification occurred.'},
  {agentId:'Trade Specialist',role:'Screen restrictions',status:'pending',evidenceIds:[],inputs:'No trade-policy feed is connected.',summary:'Restriction status remains unverified.',output:'No trade conclusion is available for this route.'},
  {agentId:'Tariff Specialist',role:'Estimate duties and charges',status:'pending',evidenceIds:[],inputs:'No tariff schedule or freight quote is connected.',summary:'Actual tariff impact is unavailable.',output:'The displayed $18.4k is a mock freight allowance, not a tariff estimate.'},
  {agentId:'Supplier Specialist',role:'Check downstream supply',status:'pending',evidenceIds:[],inputs:'Three fictional shipment rows in the prototype.',summary:'Supplier exposure is not observed.',output:'The count is illustrative and does not represent real shipments or inventory.'},
  {agentId:'Shipping Specialist',role:'Compare alternate corridor',status:'completed',evidenceIds:['SRC-UNCTAD'],inputs:'Existing Cape-route geometry and industry-level reference.',summary:'The alternate avoids the marked Red Sea segment.',output:'The 3–5 day exposure reduction is a mock comparison; no live transit model was run.'},
  {agentId:'Orchestrator',role:'Assemble operator handoff',status:'completed',evidenceIds:['SRC-IMO','SRC-UNCTAD'],inputs:'Demo route, preset risk, and public context links.',summary:'Review the Cape corridor before any decision.',output:'This is a static interface example. No private reasoning or executable action is exposed.'}
];

export function AgentActivity({records=demoSteps}:{records?:AgentActivityRecord[]}) {
  return <section className="agent-activity" aria-labelledby="agent-activity-title">
    <div className="agent-title-row"><h3 id="agent-activity-title">Agent activity</h3><span className="audit-note">8 demo steps · summaries only</span></div>
    <div className="agent-log" aria-label="Illustrative agent activity sequence">{records.map((record,index)=>{
      const tone=record.agentId.toLowerCase().replace(/ specialist| analyst| agent/g,'').replace(/\s+/g,'-');
      return <details className="agent-log-row" key={`${record.agentId}-${index}`}>
      <summary><span className="step-no">{String(index+1).padStart(2,'0')}</span><span className={`agent-state ${record.status}`} aria-hidden="true"/><span className={`agent-name agent-tone-${tone}`}>{record.agentId}</span><span className="agent-task">{record.role}</span><span className="agent-result">{record.summary}</span><ChevronDown size={13}/></summary>
      <div className="agent-log-detail"><p><b>Input</b>{record.inputs}</p><p><b>Decision summary</b>{record.output}</p><p><b>References</b>{record.evidenceIds.length?record.evidenceIds.join(' · '):'None connected'}</p></div>
    </details>})}</div>
    <p className="agent-demo-foot">Illustrative activity, not actual agent execution. Statuses and outcomes are mock examples.</p>
  </section>;
}
