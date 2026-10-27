"""Role-scoped Helene profile for the existing ModelSession and execution graph.

Only reads and scoped H-MEM persistence. No forecast duplication, approval, stock
reservation, external communication or arbitrary tool execution.
"""
from datetime import datetime,timezone
from hashlib import sha256
import json
import threading
from time import monotonic
from typing import Literal
from pydantic import Field
from contracts.models import Contract
from phases.phase_01_foundation.http import ServiceError
from phases.phase_04_agents.models import Finding
from phases.phase_06_helene.specialists import remember
from phases.phase_07_reconciliation.pipeline import verify_case
from phases.phase_08_operations.specialists import OperationalTools
from phases.phase_08_operations.workflow import WorkflowStore
from .execution import ExecutionTrace, concurrent_reads
from .integration import RESPONSIBILITIES
from .live_specialists import Empty, ModelSession
from .service import OperatorService
from .verified_runtime import MODEL
from contracts.operator import ExecutionTask

OBSERVATIONS=Literal['evidence_custody_unproven','simulated_hospital_records','trade_unavailable',
                     'clinical_model_unavailable','human_review_required']


class AgentFinding(Contract):
    observation: OBSERVATIONS
    evidence_ids: list[str] = Field(min_length=1,max_length=3)


class RoleExecution(Contract):
    role: str
    status: Literal['complete','failed']
    findings: list[Finding] = Field(default_factory=list)
    calculations: list[dict] = Field(default_factory=list)
    error_code: str | None = None


# Exact existing role IDs. Aliases are wire-compatible names for closed existing
# reads; the canonical identifiers remain in every tool receipt.
READS={
    'news':{'manufacturer_read':'manufacturer.read'},
    'verification':{'manufacturer_read':'manufacturer.read','evidence_findings':'evidence.findings'},
    'dependency':{'catalog_read':'catalog.read','orders_read':'orders.read','identity_resolve':'identity.resolve'},
    'trade':{'evidence_records':'evidence.records'},
    'disruption':{'manufacturer_read':'manufacturer.read'},
    'hospital':{'forecast_read':'forecast.read','readiness_inspect':'readiness.inspect'},
    'memory':{'hmem_exchange':'hmem.exchange'},
    'mitigation':{'mitigation_read':'mitigation.read','supply_validated':'supply.validated'},
    'feasibility':{'mitigation_read':'mitigation.read'},
    'clinical':{'forecast_read':'forecast.read'},
    'actions':{'actions_inspect':'actions.inspect'},
    'coordinator':{'mitigation_read':'mitigation.read','actions_inspect':'actions.inspect'},
}
WAVES=[('news',),('verification','disruption'),('dependency','trade'),('hospital',),('memory',),
       ('mitigation',),('feasibility','clinical'),('actions',),('coordinator',)]
ROLE_PROMPT=(' Read one offered tool, then submit agent_finding citing its evidence IDs. '
    'Classify unproven historical source custody as evidence_custody_unproven; '
    'fictional stock/identity/forecasts as simulated_hospital_records; missing trade '
    'as trade_unavailable; unsupported saline clinical outcomes as clinical_model_unavailable; '
    'drafts, memory and mitigations as human_review_required. No clinical or physical authority.')


def run_roles(args,client,approval,budget):
    if not args.store_root:
        raise ValueError('EXISTING_STORE_REQUIRED')
    service=OperatorService(args.store_root)
    cases=[c for c in service.helene_store.cases() if c.investigation_id.startswith('helene-2024-10-01-')
           and c.verification is None]
    if len(cases)!=1:
        raise ValueError('SELECT_SINGLE_BASELINE_HELENE_CASE')
    case=cases[0]
    verification=verify_case(case)
    read_case=case.model_copy(update={'verification':verification})
    workflow=WorkflowStore(service.store)
    operational=workflow.view(case)
    registry={e.evidence_id:e for e in case.evidence}
    basis='replay-dataset-'+case.dataset_version
    if basis not in registry:
        basis=case.evidence[-1].evidence_id
    trace=ExecutionTrace()
    profiles={r:'You are the existing PharmaX '+r+' specialist.'+ROLE_PROMPT for r in RESPONSIBILITIES}
    session=ModelSession(client,authorization=approval,max_requests=24,profiles=profiles,budget=budget,max_tokens=192)
    if MODEL not in session.discover():
        raise ServiceError('NEMOTRON_NOT_DISCOVERED')
    session.ready=True
    root=args.output_dir
    lock=threading.Lock()
    completed={}
    memory_proof={}
    previous_events=[]
    settled_roles=set()
    if getattr(args,'resume',False):
        old=json.loads((root/'summary.json').read_text())
        if old['investigation_id']!=case.investigation_id or old['error_code']!='MODEL_FINDING_CONFLICTS_WITH_TOOL_EVIDENCE':
            raise ValueError('RESUME_NOT_ALLOWED_FOR_PROVIDER_AUTH_OR_BUDGET_FAILURE')
        previous_events=json.loads((root/'trace.json').read_text())['events']
        (root/('segment-'+str(old['actual_logical_model_posts'])+'-summary.json')).write_text(json.dumps(old,indent=2)+'\n')
        (root/('segment-'+str(old['actual_logical_model_posts'])+'-trace.json')).write_text(json.dumps(dict(events=previous_events),indent=2)+'\n')
        trace.graph=[ExecutionTask.model_validate(t) for t in old['execution_graph']]
        records=[json.loads((root/(r+'.json')).read_text()) for r in RESPONSIBILITIES]
        times=[t['started_at'] for r in records for t in r.get('request_records',[])]
        elapsed=(datetime.now(timezone.utc)-datetime.fromisoformat(old['budget'].get('started_at_utc',min(times)))).total_seconds()
        budget.started=monotonic()-elapsed
        budget.started_at=old['budget'].get('started_at_utc',min(times))
        budget.deadline=budget.started+240
        budget.turns=old['budget']['logical_turns_reserved']
        budget.attempts=old['budget']['possible_upstream_attempts_reserved']
        budget.reported_tokens=old['budget']['reported_or_reserved_tokens']
        session.calls=session.dispatches=old['actual_logical_model_posts']
        review_task=None
        for rec in records:
            r=rec['role_id']
            rs=rec.get('model_receipts',[])
            budget.roles[r]=session.by_role[r]=len(rs)
            session.response_receipts.extend(rs)
            if not rs:
                continue
            if len(rs)!=2 or not rec.get('continuation_occurred') or any(x['returned_model']!=MODEL or x['winning_route']['provider']!='nvidia' for x in rs):
                raise ValueError('RESUME_RECEIPT_VALIDATION_FAILED')
            if sha256((root/rec['tool_result_reference']).read_bytes()).hexdigest()!=rec['tool_result_sha256']:
                raise ValueError('RESUME_TOOL_HASH_CHANGED')
            answer=AgentFinding.model_validate(rs[-1]['arguments'])
            if rec['status']=='failed':
                actual=next(e['data']['result'] for e in previous_events if e['operation']=='data.specialist_read'
                    and e['data'].get('agent_id')==r)
                inherited={v['observation'] for v in actual.get('predecessor_findings',[])}
                if answer.observation not in inherited:
                    # Do not turn this off-scope finding into success or retry it.
                    # A settled dependency permits read-only verification to continue
                    # using deterministic tool results, excluding the bad finding.
                    settled_roles.add(r)
                    failed=next(t for t in trace.graph if t.agent_id==r)
                    review_task=trace.launch('coordinator',dependencies=[failed.task_id],read_only=True,require_success=False)
                    trace.finish(review_task,'complete')
                    continue
                if not set(answer.evidence_ids)<=set(actual['evidence_ids']):
                    raise ValueError('CONCLUSION_REVIEW_NOT_SUPPORTED_BY_RETAINED_EVIDENCE')
                (root/(r+'-original-validation.json')).write_text(json.dumps(rec,indent=2)+'\n')
                rec.update(status='complete',live_verified=True,structured_finding=answer.model_dump(),
                    evidence_ids=answer.evidence_ids,validation_review='Original exact-code check was overly restrictive: unchanged model finding is supported by actual predecessor findings in its tool result. No repeated model call.')
                rec.pop('error_code',None)
                (root/(r+'.json')).write_text(json.dumps(rec,indent=2)+'\n')
                failed=next(t for t in trace.graph if t.agent_id==r)
                review_task=trace.launch(r,dependencies=[failed.task_id],read_only=True,require_success=False)
                trace.finish(review_task,'complete')
            f=Finding(claim_key='live-'+r,value=answer.model_dump_json(),kind='derived',
                evidence_ids=answer.evidence_ids,source_ids=sorted({registry[i].source for i in answer.evidence_ids}),
                limitations=['Actual unchanged model finding; simulated operations and reconstructed source availability.'])
            completed[r]=RoleExecution(role=r,status='complete',findings=[f],calculations=[answer.model_dump()])
        resume_dependencies=[review_task.task_id] if review_task else []
        if monotonic()>=budget.deadline:
            raise ServiceError('VERIFICATION_TIME_BUDGET_EXHAUSTED')

    def read(role,wire):
        canonical=READS[role][wire]
        # Use existing allowlisted adapters for their original roles. Other named
        # responsibilities receive explicitly scoped saved-case projections only.
        tools=OperationalTools(read_case,workflow)
        original_role=role if role in tools.ALLOWED else {
            'news':'verification','disruption':'verification','trade':'verification',
            'feasibility':'mitigation','clinical':'hospital','actions':'mitigation','coordinator':'mitigation'}.get(role)
        raw=tools.call(original_role,canonical) if canonical!='hmem.exchange' else None
        ids=[basis]
        if canonical=='manufacturer.read':
            projection=dict(sources=[dict(issue_date=str(s.issue_date),publisher=s.publisher,
                sha256=s.evidence.content_sha256,historical_custody='unproven') for s in case.admission.sources],
                allocations=[dict(percent=a.allocation_percent,kind=a.kind,effective_on=str(a.effective_on))
                    for a in case.admission.allocations],assumption='Reconstructed availability; not strict historical proof')
            ids=[s.evidence.evidence_id for s in case.admission.sources][:3]
            expected='evidence_custody_unproven'
        elif canonical=='evidence.findings':
            projection=dict(findings=[dict(category=f.category,reason=f.reason) for f in verification.findings[:4]],
                historical_custody='unproven',requires_human_review=verification.requires_human_review)
            expected='evidence_custody_unproven'
        elif role=='trade':
            # There is no admitted trade evidence in this Helene case. A trade
            # aggregate must never be manufactured from operational shipment rows.
            raw=[r for r in raw if r.get('meaning')=='trade_aggregate']
            projection=dict(admitted_trade_records=len(raw),status='unavailable',
                limitation='No comparable trade feed in this admitted Helene dataset; never infer shipments.')
            expected='trade_unavailable'
        elif canonical in {'catalog.read','orders.read','identity.resolve'}:
            projection=dict(simulated=True,product=case.operations.products[0].presentation,
                mappings=[dict(sku=i['original']['local_sku'],ndc=i['canonical_ndc'],classification=i['classification'])
                          for i in operational['readiness']['identities']],
                orders=[dict(id=o.purchase_order_id,ordered=o.quantity,acknowledged=next(
                    (a.quantity for a in case.operations.acknowledgments if a.purchase_order_id==o.purchase_order_id),None))
                    for o in case.operations.purchase_orders],orders_are_receipts=False)
            expected='simulated_hospital_records'
        elif role=='clinical':
            projection=dict(saline_clinical_outcome_model_supported=False,
                limitation='Existing clinical allocation is oncology-specific; no saline patient outcomes are calculated.',
                deterministic_forecasts=[dict(hospital=f['hospital_id'],unmet_bags=f['total_unmet']) for f in operational['forecasts']])
            expected='clinical_model_unavailable'
        elif canonical in {'forecast.read','readiness.inspect'}:
            projection=dict(simulated=True,forecast_kind='Conservative Phase 8; unconfirmed orders excluded',
                hospitals=[dict(hospital=f['hospital_id'],usable_bags=f['initial_usable'],unmet_bags=f['total_unmet'])
                    for f in operational['forecasts']],quality=operational['readiness']['classification'])
            expected='simulated_hospital_records'
        elif canonical=='hmem.exchange':
            prior=[f for value in completed.values() for f in value.findings]
            proof=remember(root/'hmem','live-verification',case.dataset_version,case.checkpoint.cutoff,[],[],
                node=service.node,additional_findings=prior[:12],query_text='source custody simulated stock unavailable trade review')
            memory_proof.update(proof)
            raw=proof
            projection=dict(actual_hmem=True,writes=len(proof['writes']),
                retrieved=len(proof.get('result',{}).get('memories',[])),
                context=str(proof.get('result',{}).get('context',''))[:700],source='Derived findings over simulated operations')
            expected='human_review_required'
        else:
            projection=dict(requires_human_review=True,dispatch_authorized=False,
                selected_candidates=case.selected,
                candidates=[dict(id=c['candidate_id'],quantity=c['quantity'],feasible=c['feasible'],failures=c['failures'])
                            for c in sorted(case.candidates,key=lambda c:c['feasible'])[:4]],
                saved_drafts=len(case.actions),missing_evidence=case.unresolved[:3])
            expected='human_review_required'
        body=dict(canonical_tool=canonical,scope_role=role,evidence_ids=ids,result=projection,
                  predecessor_findings=[dict(role=r,observation=v.calculations[0]['observation'])
                    for r,v in list(completed.items())[-3:] if v.calculations])
        encoded=json.dumps(raw,sort_keys=True,default=str).encode()
        raw_path=root/(role+'-tool.json')
        raw_path.write_bytes(encoded)
        body['tool_result_ref']=raw_path.name
        body['tool_result_sha256']=sha256(encoded).hexdigest()
        return body,expected

    def execute(role):
        record=dict(role_id=role,specialist=role,investigation_id=case.investigation_id,
            requested_model=MODEL,live_verified=False,continuation_occurred=False,
            status='started',started_at=datetime.now(timezone.utc).isoformat())
        try:
            wire,arguments,call_id=session.select(role,{name:Empty for name in READS[role]},
                {'question':RESPONSIBILITIES[role],'case':case.investigation_id},trace)
            result,expected=read(role,wire)
            record.update(tool_selected=READS[role][wire],tool_arguments=arguments.model_dump(),
                tool_execution_status='complete',tool_result_reference=result['tool_result_ref'],
                tool_result_sha256=result['tool_result_sha256'])
            session.tool_result(role,call_id,wire,arguments.model_dump(),result,trace)
            _,answer,_=session.select(role,{'agent_finding':AgentFinding},
                {'instruction':'Interpret the returned tool result; cite its evidence IDs.'},trace)
            record['continuation_occurred']=True
            if not set(answer.evidence_ids)<=set(result['evidence_ids']):
                raise ServiceError('MODEL_FINDING_CITATION_INVALID')
            inherited={f['observation'] for f in result.get('predecessor_findings',[])}
            if answer.observation!=expected and not (role not in {'clinical','trade'} and answer.observation in inherited):
                raise ServiceError('MODEL_FINDING_CONFLICTS_WITH_TOOL_EVIDENCE')
            finding=Finding(claim_key='live-'+role,value=answer.model_dump_json(),kind='derived',
                evidence_ids=answer.evidence_ids,source_ids=sorted({registry[i].source for i in answer.evidence_ids}),
                limitations=['Native model interpretation; simulated hospital data; deterministic validators retain all authority.'])
            value=RoleExecution(role=role,status='complete',findings=[finding],calculations=[answer.model_dump()])
            record.update(status='complete',live_verified=True,structured_finding=answer.model_dump(),
                          evidence_ids=answer.evidence_ids)
            with lock:
                completed[role]=value
            return value
        except (ServiceError,ValueError,KeyError,TypeError) as exc:
            code=exc.code if isinstance(exc,ServiceError) else 'ROLE_VALIDATION_FAILED'
            record.update(status='budget_exhausted' if 'BUDGET' in code else 'failed',error_code=code)
            with session.lock:
                session.failure=session.failure or code
            return RoleExecution(role=role,status='failed',error_code=code)
        finally:
            record['model_receipts']=[r for r in session.response_receipts if r['role']==role]
            record['request_records']=[{k:v for k,v in t.items() if k not in {'start','settled'}}
                                       for t in budget.tickets if t['role']==role]
            record['completed_at']=datetime.now(timezone.utc).isoformat()
            (root/(role+'.json')).write_text(json.dumps(record,indent=2)+'\n')

    dependencies=resume_dependencies if getattr(args,'resume',False) else []
    for wave in WAVES:
        wave=tuple(r for r in wave if r not in completed and r not in settled_roles)
        if not wave:
            continue
        if session.failure:
            break
        results,tasks=concurrent_reads(trace,[(r,lambda r=r:execute(r)) for r in wave],
            dependencies=dependencies,max_workers=2,timeout=max(.1,min(120,budget.deadline-monotonic())),
            result_model=RoleExecution)
        dependencies=[t.task_id for t in tasks]
        if any(r.status!='complete' for r in results):
            break
    for role in RESPONSIBILITIES:
        if not (root/(role+'.json')).exists():
            (root/(role+'.json')).write_text(json.dumps(dict(role_id=role,investigation_id=case.investigation_id,
                status='not_attempted',live_verified=False,error_code=session.failure or 'DEPENDENCY_UNSATISFIED'),indent=2)+'\n')
    summary=dict(classification='actual Nightshift/NVIDIA model-tool-model verification',
        investigation_id=case.investigation_id,specialists_completed=len(completed),
        specialists_attempted=len({r['role'] for r in session.response_receipts}),
        actual_logical_model_posts=session.dispatches,smoke_logical_posts=1,
        actual_model_turns_including_smoke=1+session.dispatches,budget=budget.summary(),
        requested_model=MODEL,status='complete' if len(completed)==12 else 'partial',error_code=session.failure,
        roles={r:json.loads((root/(r+'.json')).read_text())['status'] for r in RESPONSIBILITIES},
        execution_graph=[t.model_dump(mode='json') for t in trace.graph],
        limitations=['No physical execution or pharmacy approval; saline clinical outcomes unsupported.',
            'Native continuation findings are coded/citation-validated; forecasts and safety remain deterministic.',
            'Runtime configuration/source hashes checked before every dispatch; no installed runtime modification.'])
    (root/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    (root/'hmem.json').write_text(json.dumps(memory_proof,indent=2)+'\n')
    (root/'trace.json').write_text(json.dumps(dict(events=previous_events+[e.model_dump(mode='json') for e in trace.events]),indent=2)+'\n')
    print(json.dumps({k:v for k,v in summary.items() if k not in {'execution_graph','limitations'}},indent=2),flush=True)
    return 0 if len(completed)==12 else 1
