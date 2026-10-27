"""Synthetic schedule, independent of real patient doses; no clinical recommendations."""
from contracts.operator import ClinicalSession, ClinicalImpact, ClinicalAllocation
from phases.phase_01_foundation.sources import digest

LIMITS = ['Synthetic scheduling assumptions, not real patients, doses or outcomes.',
          'Vials represent pharmacy preparation requirements; no one-vial/one-patient inference.',
          'Reservations remain excluded by the forecast. No treatment schedule or regimen is changed.',
          'Exposure is modeled supply coverage, not clinical harm or a verified hospital procedure.']


def schedule(forecast, scenario):
    rows = []
    for day_index, point in enumerate(forecast.points):
        remaining = point.demand
        ordinal = 0
        while remaining:
            quantity = min(2, remaining)
            ordinal += 1
            hospital = forecast.request.hospital_id
            rows.append(ClinicalSession(session_id=f'{hospital}:{point.day}:{ordinal}',
                patient_id=f'synthetic:{hospital}:cycle-{day_index % 7}:{ordinal}', hospital_id=hospital,
                scheduled_on=point.day, product_id=forecast.request.product_id, required_quantity=quantity,
                scenario_id=scenario, dataset_version=forecast.dataset_version,
                source_evidence_ids=forecast.input_evidence_ids))
            remaining -= quantity
    return rows


def allocate(sessions, forecast, case):
    allocations, exposed = [], []
    relevant = [s for s in sessions if s.hospital_id == forecast.request.hospital_id]
    if len({s.session_id for s in relevant}) != len(relevant):
        raise ValueError('Duplicate clinical session')
    for point in forecast.points:
        rows = sorted((s for s in relevant if s.scheduled_on == point.day), key=lambda s:s.session_id)
        if sum(s.required_quantity for s in rows) != point.demand:
            raise ValueError('Schedule does not reconcile to forecast demand')
        if any(s.product_id != forecast.request.product_id or s.quantity_unit != 'vial' for s in rows):
            raise ValueError('Clinical allocation product/unit mismatch')
        available = point.served
        for session in rows:
            allocated = min(session.required_quantity, available)
            available -= allocated
            uncovered = session.required_quantity - allocated
            allocations.append(ClinicalAllocation(session_id=session.session_id, allocated=allocated, uncovered=uncovered))
            if uncovered:
                exposed.append(session)
    if sum(a.uncovered for a in allocations) != forecast.total_unmet:
        raise ValueError('Clinical uncovered quantity differs from inventory forecast')
    return ClinicalImpact(case=case, hospital_id=forecast.request.hospital_id,
        unique_exposed_patients=len({s.patient_id for s in exposed}), exposed_sessions=len(exposed),
        first_unmet_on=min((s.scheduled_on for s in exposed), default=None),
        services=sorted({s.clinical_service for s in exposed}), allocations=allocations,
        analysis_id=digest({'forecast':forecast.analysis_id, 'sessions':[s.model_dump(mode='json') for s in relevant]}),
        limitations=LIMITS)


def clinical(mitigation, scheduled=None, scenario_id=None):
    sessions, impacts = [], []
    chosen = next((e for e in mitigation.evaluations if e.feasible and
                   e.candidate.candidate_id == mitigation.recommendation_id), None)
    for comparison in mitigation.reference:
        rows = schedule(comparison.disruption.forecast, scenario_id or mitigation.request.scenario)
        sessions.extend(rows)
        for case in ('baseline', 'disruption'):
            forecast = (scheduled.forecast if scheduled and case == 'baseline' and
                        scheduled.hospital_id == comparison.hospital_id else getattr(comparison, case).forecast)
            impacts.append(allocate(rows, forecast, case))
        if chosen:
            impact = next(i for i in chosen.impacts if i.hospital_id == comparison.hospital_id)
            if impact.projected:
                impacts.append(allocate(rows, impact.projected.forecast, 'mitigation'))
    return sessions, impacts
