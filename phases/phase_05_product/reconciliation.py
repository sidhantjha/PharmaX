"""Bounded receipt inspection and explicit simulation-only planning reconciliation."""
from datetime import datetime, time, timedelta, timezone
import json
from pathlib import Path
import time as clock
from contracts import ForecastRequest, ScenarioOverlay, ArrivalOverride, SourceEvidence
from contracts.reconciliation import (ArrivalClaim, ClaimCheck, ClaimInspection, ConflictResolution,
                                      EvidenceConflict, ForecastAlternative)
from phases.phase_01_foundation.sources import digest
from phases.phase_01_foundation.http import ServiceError
from phases.phase_03_risk.tools import RiskTools
from .execution import concurrent_reads

HERE = Path(__file__).resolve().parent
POLICY_PATH = HERE/'fixtures/arrival_authority_v1.json'
RECEIPTS_PATH = HERE/'fixtures/arrival_update_v1.json'


def receipt_hash(record):
    return digest(record.model_dump(mode='json', exclude={'evidence'}))


def make_claim(**fields):
    """Construct a labelled simulated receipt; hashing alone grants no authority."""
    fields.setdefault('classification', 'simulated-operational-communication')
    record=ArrivalClaim(**fields, evidence=SourceEvidence(
        evidence_id='pending', kind='simulated', source=fields['source'],
        source_record_id=fields['claim_id'], source_url='https://example.invalid/pharmax/arrival-update-v1',
        observed_at=fields['retrieved_at'], version_available_at=fields['retrieved_at'],
        content_sha256='0'*64, note='Simulated operational communication. Not an observed shipment, supplier statement or delay.'))
    identity=receipt_hash(record)
    record.evidence=record.evidence.model_copy(update={'evidence_id':'sim:p5c-'+identity,'content_sha256':identity})
    return record



def inherited_claim(parent):
    run = parent.intelligence
    arrival = run.dependency.planned_arrival + timedelta(days=run.selected_delay_days)
    return make_claim(claim_id='prior-assumption:'+parent.investigation_id,
        shipment_id=run.dependency.shipment_id, product_id=run.dependency.product_id,
        supplier_id=run.dependency.supplier_id, source='retained-operator-assumption',
        effective_at=datetime.combine(run.dependency.legs[0].ends_on, time.min, tzinfo=timezone.utc),
        retrieved_at=run.decision_at, arrival_on=arrival, authorization_ref='immutable-parent-case',
        document=json.dumps({'shipment_id':run.dependency.shipment_id, 'product_id':run.dependency.product_id,
            'supplier_id':run.dependency.supplier_id, 'arrival_on':arrival.isoformat(),
            'classification':'simulated', 'parent_case':parent.investigation_id}, sort_keys=True))


def inspect(role, records, dependency, decision_at, policy, trace, prior_effective):
    """Read-only tool: fixed one-call allowance and bounded serialized inputs/outputs."""
    deadline = clock.monotonic()+15
    if len(records)>12 or sum(len(r.model_dump_json().encode()) for r in records)>100_000:
        raise ServiceError('TOOL_RESPONSE_BUDGET')
    ids = [r.evidence.evidence_id for r in records]
    trace.event('data.claim_inspection', 'started', agent_id=role, evidence_ids=ids,
                arguments={'role':role, 'record_count':len(records), 'policy_version':policy['version']}, call=1)
    checks=[]
    for record in records:
        def check(name, passed, reason):
            checks.append(ClaimCheck(claim_id=record.claim_id, check=name, passed=bool(passed), reason=reason))
        if role=='verification':
            check('receipt_integrity', record.evidence.content_sha256==receipt_hash(record)
                  and record.evidence.evidence_id=='sim:p5c-'+receipt_hash(record)
                  and record.source==record.evidence.source
                  and record.evidence.source_record_id==record.claim_id
                  and record.evidence.kind=='simulated',
                  'Compare exact receipt payload hash, ID and declared source; a hash is not authentication.')
            check('timestamp_consistency', record.effective_at<=record.retrieved_at
                  and record.retrieved_at==record.evidence.observed_at
                  and record.evidence.version_available_at==record.retrieved_at,
                  'Effective time cannot follow receipt time in this current-estimate pilot; receipt and availability clocks must agree.')
            check('not_stale',record.effective_at>=prior_effective,
                  'Compare effective time to the retained estimate; later retrieval alone cannot refresh an old assertion.')
            check('available', record.retrieved_at<=decision_at and record.effective_at<=decision_at,
                  'Require actual revision availability at the decision cutoff; publication or effective dates alone are insufficient.')
        elif role=='disruption':
            try:
                body=json.loads(record.document)
            except (ValueError, TypeError):
                body={}
            supported=isinstance(body,dict) and all(body.get(k)==v for k,v in {
                'shipment_id':record.shipment_id,'product_id':record.product_id,
                'supplier_id':record.supplier_id,'arrival_on':record.arrival_on.isoformat(),
                'classification':'simulated'}.items())
            check('document_support', supported, 'Compare asserted fields with the retained simulated communication body, never execute source text.')
            check('planning_authority', policy['approved_receipts'].get(record.authorization_ref)==receipt_hash(record),
                  'Exact receipt approval from a separate versioned simulation policy; source wording and caller flags cannot grant authority.')
            check('shipment_corroboration', False,
                  'No independent shipment-arrival evidence. Real MARAD supports regional advisory risk only.')
        else:
            check('relevance', all(getattr(record,k)==getattr(dependency,k) for k in ('shipment_id','product_id','supplier_id')),
                  'Require the exact simulated shipment, product and supplier; no medicine-name or regional inference.')
            delay=(record.arrival_on-dependency.planned_arrival).days
            check('forecast_scope', 0<=delay<=21, 'Only the existing 0..21 day delay sensitivity range is supported.')
            others=[r.claim_id for r in records if r.claim_id!=record.claim_id
                    and r.shipment_id==record.shipment_id and r.product_id==record.product_id
                    and r.arrival_on!=record.arrival_on]
            check('no_contradiction', not others, 'Different arrival estimates: '+(', '.join(others) if others else 'none'))
    result=ClaimInspection(role=role, status='complete', checks=checks)
    if clock.monotonic()>deadline:
        raise ServiceError('TASK_TIMEOUT')
    if len(result.model_dump_json().encode())>100_000:
        raise ServiceError('TOOL_RESPONSE_BUDGET')
    trace.event('data.claim_inspection', agent_id=role, evidence_ids=ids,
                inspection_checks=[c.model_dump(mode='json') for c in checks])
    return result


def reconcile(parent, request, trace, *, policy=None):
    policy=json.loads(POLICY_PATH.read_text()) if policy is None else json.loads(json.dumps(policy))
    incoming=request.records or [ArrivalClaim.model_validate(r) for r in json.loads(RECEIPTS_PATH.read_text())['records']]
    previous=parent.conflict_resolution.claims if parent.conflict_resolution else [inherited_claim(parent)]
    records={r.claim_id:r.model_copy(deep=True) for r in previous}
    for record in incoming:
        if record.claim_id in records and record!=records[record.claim_id]:
            raise ValueError('CLAIM_ID_COLLISION')
        if record.claim_id.startswith('prior-assumption:') and record.claim_id not in records:
            raise ValueError('RESERVED_CLAIM_ID')
        records[record.claim_id]=record.model_copy(deep=True)
    records=list(records.values())
    if len(records)>12 or len({r.evidence.evidence_id for r in records})!=len(records):
        raise ValueError('CLAIM_RECORD_BUDGET_OR_COLLISION')
    # The inherited estimate is approved only because it came from a hash-checked saved parent.
    for record in previous:
        if record.claim_id.startswith('prior-assumption:'):
            policy['approved_receipts'][record.authorization_ref]=receipt_hash(record)
    prior_delay=parent.intelligence.selected_delay_days
    prior_arrival=parent.intelligence.dependency.planned_arrival+timedelta(days=prior_delay)
    prior_effective=max((r.effective_at for r in previous if r.arrival_on==prior_arrival), default=parent.intelligence.decision_at)
    inspections,nodes=concurrent_reads(trace, [(role, lambda role=role: inspect(role,
        [r.model_copy(deep=True) for r in records], parent.intelligence.dependency, request.decision_at, policy, trace, prior_effective))
        for role in ('verification','disruption','dependency')], parent_id=trace.local.task_id,
        max_workers=3, timeout=15, result_model=ClaimInspection)
    node=trace.launch('coordinator', parent_id=trace.local.task_id,
        dependencies=[n.task_id for n in nodes], require_success=False)
    with trace.running(node):
        checks={r.claim_id:{} for r in records}
        for inspection in inspections:
            for c in inspection.checks:
                checks[c.claim_id][c.check]=c.passed
        needed=('receipt_integrity','timestamp_consistency','available','document_support','relevance','forecast_scope')
        usable=[r for r in records if all(checks[r.claim_id].get(k,False) for k in needed)]
        authorized=[r for r in usable if checks[r.claim_id].get('planning_authority',False)]
        fresh=[r for r in usable if r.effective_at>=prior_effective]
        authoritative=[r for r in authorized if r.effective_at>=prior_effective]
        latest=max((r.effective_at for r in authoritative), default=None)
        leaders=[r for r in authoritative if r.effective_at==latest]
        uncertainty=['No observed pharmaceutical shipment delay or arrival is established.',
            'Supplier communications, stock, routes and estimates are simulated.',
            'MARAD confirms regional advisory risk, not shipment delay; independent arrival corroboration is missing.',
            'New knowledge supports a conditional reprojection of the original planning network, not a historical backtest.']
        selected=None
        if leaders and len({r.arrival_on for r in leaders})==1:
            selected=min(leaders,key=lambda r:r.claim_id)
        # A fresh unapproved disagreement cannot replace the retained assumption.
        competing_unverified=any(r.arrival_on!=prior_arrival and not checks[r.claim_id].get('planning_authority')
                                 and r.effective_at>=prior_effective for r in fresh)
        unresolved=(not selected or (selected.arrival_on==prior_arrival and competing_unverified))
        if unresolved:
            selected=None
            outcome='unresolved'
        else:
            outcome='unchanged' if selected.arrival_on==prior_arrival else 'revised'
        summary=('No unique supported authorized newer assumption; retain the prior forecast conditionally and calculate admissible alternatives.'
            if unresolved else 'Adopt the most recent effective supported planning-authorized simulated receipt; retain other claims and missing corroboration.')
        if selected:
            summary+=f' Selected {selected.claim_id}, effective {selected.effective_at.isoformat()}, retrieved {selected.retrieved_at.isoformat()}.'
        exclusions=[]
        for record in records:
            failed=[k for k in (*needed,'planning_authority','not_stale') if not checks[record.claim_id].get(k,False)]
            if failed:
                exclusions.append(record.claim_id+': '+', '.join(failed))
        if exclusions:
            summary+=' Not adopted: '+'; '.join(exclusions)+'.'
        disagrees=len({r.arrival_on for r in records})>1
        conflict=EvidenceConflict(conflict_id='conflict-'+digest([r.model_dump(mode='json') for r in records]),
            competing_claims=records, evidence_ids=[r.evidence.evidence_id for r in records],
            reason='Different submitted arrival estimates; inspections identify unsupported, stale or unrelated submissions and distinguish effective from retrieval time.' if disagrees else 'No arrival-date disagreement.',
            assessments=[c for inspection in inspections for c in inspection.checks],
            verification_status=('unresolved' if unresolved else 'resolved-for-planning') if disagrees else 'no-disagreement',
            selected_claim_id=selected.claim_id if selected else None, justification=summary,
            unresolved_uncertainty=uncertainty)
        result=ConflictResolution(resolution_id='resolution-'+digest({'parent':parent.investigation_id,
            'records':[r.model_dump(mode='json') for r in records], 'cutoff':request.decision_at.isoformat(), 'policy':policy}),
            decision_at=request.decision_at, policy_version=policy['version'], outcome=outcome,
            prior_delay_days=prior_delay,
            selected_delay_days=(selected.arrival_on-parent.intelligence.dependency.planned_arrival).days if selected else None,
            selected_claim_id=selected.claim_id if selected else None, claims=records, inspections=inspections,
            conflicts=[conflict], advisory_evidence_ids=parent.intelligence.signal.evidence_ids,
            uncertainty=uncertainty, summary=summary)
        # Only supported, relevant, clock-consistent claims become conditional projections.
        trace.event('evidence.conflict_reconciled', agent_id='coordinator', evidence_ids=conflict.evidence_ids,
                    resolution=result.model_dump(mode='json'))
    return result, usable


def alternatives(resolution, usable, parent, bundle, trace):
    by_delay={}
    for record in usable:
        delay=(record.arrival_on-parent.intelligence.dependency.planned_arrival).days
        by_delay.setdefault(delay,[]).append(record.claim_id)
    if len(by_delay)>5:
        raise ServiceError('TOOL_CALL_BUDGET')
    tools=RiskTools(bundle,max_calls=5)
    results=[]
    for delay,claim_ids in sorted(by_delay.items()):
        arrival=parent.intelligence.dependency.planned_arrival+timedelta(days=delay)
        overlay=ScenarioOverlay(overlay_id=f'p5c-delay-{delay}',base_dataset_version=bundle.dataset_version,
            as_of=bundle.scenario_as_of,description='Conditional simulated arrival assumption; not an observed delay.',
            arrivals=[ArrivalOverride(shipment_id=parent.intelligence.dependency.shipment_id,arrival_on=arrival)])
        trace.event('risk.hospital_risk','started',agent_id='hospital',arguments=overlay.model_dump(mode='json'))
        assessment=tools.hospital_risk(ForecastRequest(hospital_id=parent.intelligence.dependency.hospital_id,
            horizon_days=parent.mitigation.request.horizon_days,overlay=overlay))
        trace.event('risk.hospital_risk',agent_id='hospital',analysis_id=assessment.analysis_id,
                    evidence_ids=assessment.input_evidence_ids,unmet_vials=assessment.forecast.total_unmet,delay_days=delay)
        adoption=('selected-assumption' if delay==resolution.selected_delay_days else
                  'retained-assumption' if delay==resolution.prior_delay_days else 'conditional-only')
        results.append(ForecastAlternative(delay_days=delay,arrival_on=arrival,claim_ids=sorted(claim_ids),
                       adoption=adoption,assessment=assessment))
    return results
