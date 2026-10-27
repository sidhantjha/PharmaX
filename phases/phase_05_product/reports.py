"""Reproducible briefing and daily inventory exports from an immutable case."""
import csv
import io


def forecasts(case):
    rows = []
    result = case.mitigation
    for comparison in result.reference:
        for label in ('baseline', 'disruption'):
            forecast=(case.intelligence.baseline.forecast if case.intelligence and label=='baseline' and
                      case.intelligence.dependency.hospital_id==comparison.hospital_id else getattr(comparison,label).forecast)
            rows.append((label,forecast))
    chosen = next((e for e in result.evaluations if e.feasible and
                   e.candidate.candidate_id == result.recommendation_id), None)
    if chosen:
        rows.extend(('mitigation',i.projected.forecast) for i in chosen.impacts if i.projected)
    return rows


def series_csv(case):
    stream=io.StringIO(newline='')
    writer=csv.writer(stream)
    writer.writerow(['investigation_id','case','hospital_id','product_id','day','opening_usable',
                     'received','demand','served','unmet','closing_usable','quantity_unit','analysis_id'])
    for label, forecast in forecasts(case):
        for point in forecast.points:
            writer.writerow([case.investigation_id,label,forecast.request.hospital_id,forecast.request.product_id,
                point.day,point.opening_usable,point.received,point.demand,point.served,point.unmet,
                point.closing_usable,'vial',forecast.analysis_id])
    return stream.getvalue()


def briefing(case):
    lines=[f'# PharmaX operator briefing — {case.investigation_id}',
           f'Version: {case.report_version}; snapshot created {case.created_at.isoformat()}',
           f'Scenario: {case.scenario}; mode: {case.mode}; status: {case.status}',
           case.objective, '\n## Evidence checks']
    lines.extend(f'- {v.check}: {v.status}. {v.explanation}' for v in case.verification)
    if case.intelligence:
        run=case.intelligence
        lines.extend(['\n## Conditional shipment exposure',
            f'{run.dependency.shipment_id}: {run.dependency.quantity_vials} simulated vials; '
            f'scheduled {run.dependency.planned_arrival}; assumed delay {run.selected_delay_days} days. '
            'MATCHED means modeled corridor exposure, not confirmed delay.'])
    if case.conflict_resolution:
        resolution=case.conflict_resolution
        lines.extend(['\n## Evidence reconciliation and case revision',
            f'Parent: {case.parent_id}; outcome: {resolution.outcome}; decision cutoff: {resolution.decision_at.isoformat()}.',
            case.revision.decision_summary])
        lines.extend(f'- [{r.evidence.kind}] {r.claim_id}: arrival {r.arrival_on}; effective {r.effective_at.isoformat()}; retrieved {r.retrieved_at.isoformat()}; evidence {r.evidence.evidence_id}.' for r in resolution.claims)
        lines.extend(f'- Conditional +{r.delay_days} days: {r.assessment.forecast.total_unmet} unmet vials ({r.adoption}).' for r in resolution.alternatives)
        if resolution.outcome=='unresolved':
            lines.append('No revised action adopted. Forecast and mitigation below retain the prior assumption conditionally; drafts withheld.')
    lines.append('\n## Forecast comparisons (vials)')
    lines.extend(f'- {label} / {f.request.hospital_id}: unmet {f.total_unmet}, first {f.projected_stockout_date}, '
                 f'initial usable {f.initial_usable}, ending usable {f.points[-1].closing_usable}.' for label,f in forecasts(case))
    lines.append('\n## Evaluated alternatives')
    lines.extend(f'- {e.candidate.candidate_id}: {"feasible" if e.feasible else "rejected"}; '
                 f'{e.original_unmet} → {e.remaining_unmet} unmet; '
                 + '; '.join(f'{f.code}: {f.detail}' for f in e.failures) for e in case.mitigation.evaluations)
    lines.extend(['\n## Recommendation',case.mitigation.recommendation or 'Unavailable',
                  '\n## Synthetic clinical exposure'])
    lines.extend(f'- {i.case} / {i.hospital_id}: {i.unique_exposed_patients} unique modeled patients, '
                 f'{i.exposed_sessions} exposed sessions; first {i.first_unmet_on}.' for i in case.clinical_impact)
    lines.append('Patient identifiers are synthetic. Preparation vial quantities are not verified clinical doses or outcomes.')
    lines.append('\n## Prepared actions at snapshot creation')
    lines.extend(f'- {a.kind}: {a.action_id}; {a.status}; {a.justification}' for a in case.actions)
    lines.append('\n## Agent audit')
    lines.extend(f'- {a.agent_id}: {a.status}; {len(a.tool_records)} actual records; '
                 f'{len(a.memory_ids)} referenced memory IDs. {a.explanation}' for a in case.agents)
    lines.append('\n## Citations')
    lines.extend(f'- {e.evidence_id} [{e.kind}]: {e.source_url}; retrieved {e.observed_at.isoformat()}; SHA256 {e.content_sha256}'
                 for e in case.evidence)
    lines.append('\n## Limitations and residual risks')
    lines.extend('- '+limit for limit in case.limitations)
    return '\n\n'.join(lines)+'\n'


def report(case):
    return {'version':case.report_version,'investigation_id':case.investigation_id,
            'snapshot_created_at':case.created_at.isoformat(),'markdown':briefing(case),
            'csv':series_csv(case),'json':case.model_dump(mode='json'),
            'pdf':{'availability':'unavailable','reason':'No compatible PDF infrastructure in this repository.'}}
