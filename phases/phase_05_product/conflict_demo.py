"""Explicit reproducible evidence-to-revision demonstration from executed outputs."""
import json
from pathlib import Path
from contracts.operator import CaseRequest
from contracts.reconciliation import ReconciliationRequest
from .reports import forecasts


def comparison(case):
    return {'investigation_id':case.investigation_id,'parent_id':case.parent_id,
        'delay_days':case.intelligence.selected_delay_days,
        'scenario_version':case.mitigation.scenario_dataset_version,
        'recommendation_id':case.mitigation.recommendation_id,
        'unmet_vials':{label:f.total_unmet for label,f in forecasts(case) if f.request.hospital_id=='sim-central'},
        'synthetic_patient_exposure':{i.case:i.unique_exposed_patients for i in case.clinical_impact if i.hospital_id=='sim-central'}}


def demonstrate(service, output):
    parent=service.create(CaseRequest(scenario='red-sea'))
    before=service.store.snapshot_hash(parent.investigation_id)
    child=service.reconcile(parent.investigation_id,ReconciliationRequest())
    resolution=child.conflict_resolution
    lines=['PharmaX Phase 5C | OFFLINE TOOL EXECUTION | no LLM calls',
           '1. ORIGINAL EVIDENCE / ASSUMPTION',json.dumps(comparison(parent),sort_keys=True),
           'MARAD is observed regional-advisory evidence; the seven-day shipment delay is simulated.',
           '2. NEW CONFLICTING INFORMATION']
    for record in resolution.claims:
        lines.append(f'[{record.classification}] {record.claim_id}: arrival {record.arrival_on}; '
            f'source={record.source}; effective={record.effective_at.isoformat()}; retrieved={record.retrieved_at.isoformat()}; '
            f'evidence={record.evidence.evidence_id}')
    lines.append('3. SPECIALISTS INVESTIGATE (executed receipt inspection tools)')
    for inspection in resolution.inspections:
        lines.append(f'{inspection.role}: {inspection.status}')
        lines.extend(f'  {c.claim_id} / {c.check}: {c.passed}. {c.reason}' for c in inspection.checks)
    lines.append('4. RECONCILER RECORDS DISAGREEMENT')
    for conflict in resolution.conflicts:
        lines.extend([f'{conflict.conflict_id}: {conflict.verification_status}',conflict.justification])
    lines.extend(['5. NEW CASE / SCENARIO VERSION',f'{parent.investigation_id} → {child.investigation_id}',
        f'{parent.mitigation.scenario_dataset_version} → {child.mitigation.scenario_dataset_version}',
        'Conditional reprojection using new simulated knowledge; no source is backdated and no inventory is invented.',
        '6. FORECAST RECALCULATED'])
    for alternative in resolution.alternatives:
        lines.append(f'Conditional +{alternative.delay_days}d / {alternative.adoption}: '
            f'{alternative.assessment.forecast.total_unmet} unmet vials; analysis={alternative.assessment.analysis_id}')
    lines.extend(json.dumps(d.model_dump(mode='json'),sort_keys=True) for d in child.revision.forecast_deltas)
    lines.append('7. PREVIOUS MITIGATION RE-EVALUATED')
    old=child.revision.previous_option_recheck
    if old:
        lines.append(f'{old.candidate_id}: feasible={old.feasible}; {old.original_unmet} → {old.remaining_unmet} unmet vials; '
                     f'evaluation={old.evaluation_id}; constraints={old.rejection_codes}')
    lines.append('H-MEM prior constraint references: '+', '.join(child.revision.prior_constraint_memory_refs))
    for record in child.decision_ledger:
        if record.operation=='coordinator.revision_choice':
            lines.append('REPLAN '+json.dumps(record.summary,sort_keys=True))
    lines.extend(['8. ACTION DISPOSITION',child.revision.decision_summary,
        json.dumps(comparison(child),sort_keys=True), '9. BOTH VERSIONS REMAIN INSPECTABLE'])
    assert service.store.snapshot_hash(parent.investigation_id)==before
    assert service.store.get(parent.investigation_id)==parent
    assert service.store.get(child.investigation_id)==child
    for case in (parent,child):
        lines.append(f'GET /api/v1/investigations/{case.investigation_id}')
    lines.extend(resolution.uncertainty)
    lines.append('Human review required; no purchase, transfer, dispatch or clinical substitution executed.')
    output=Path(output)/child.investigation_id
    output.mkdir(parents=True,exist_ok=True)
    transcript='\n'.join(lines)+'\n'
    (output/'terminal.txt').write_text(transcript)
    (output/'comparison.json').write_text(json.dumps({'original':comparison(parent),'revised':comparison(child),
        'parent_hash_before':before,'parent_hash_after':service.store.snapshot_hash(parent.investigation_id),
        'revision':child.revision.model_dump(mode='json')},indent=2)+'\n')
    for case,name in ((parent,'original'),(child,'revised')):
        (output/(name+'.json')).write_text(case.model_dump_json(indent=2)+'\n')
    (output/'resolution.json').write_text(resolution.model_dump_json(indent=2)+'\n')
    print(transcript,end='')
    print(f'ARTIFACTS {output}')
    return 0 if child.status=='complete' else 2
