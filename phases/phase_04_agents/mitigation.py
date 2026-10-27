"""Phase 4B coordinator extension: discover, check, remember failures, and replan."""
import json
from pathlib import Path
from contracts import MitigationRequest, MitigationResult
from phases.phase_01_foundation.http import ServiceError
from phases.phase_01_foundation.sources import digest
from phases.phase_02_data.store import evidence_closure
from .feasibility import LEVELS
from .memory import Memory
from .models import Finding, InvestigationRequest
from .mitigation_reasoning import FixtureMitigationReasoner
from .mitigation_scenario import prepare_scenario
from .mitigation_tools import MitigationTools
from .trace import Trace


def investigate_mitigation(bundle, catalog, request, memory_root, *, trace=None, reasoner=None,
                           node=None, max_candidates=6, max_calls=20, timeout=90):
    request = MitigationRequest.model_validate(request)
    if type(max_candidates) is not int or not 1 <= max_candidates <= 6:
        raise ValueError('Candidate budget must be 1..6')
    trace = trace or Trace()
    reasoner = reasoner or FixtureMitigationReasoner()
    result = MitigationResult(request=request, base_dataset_version=bundle.dataset_version,
        catalog_version=digest(catalog.model_dump(mode='json')), status='incomplete', stop_reason='blocked',
        assumptions=catalog.assumptions, uncertainties=[
            'All operational stock, supply, authorizations and routes are simulated; human review is required.',
            'Alternatives are independent; improvements cannot be added together or treated as executed actions.',
            'No documented Rocky Mount–carboplatin manufacturing link; simulated Atlas exposure is separate.',
            'Other facilities are evaluated independently; annual trade samples do not establish medicine supply links.',
            'Arrival at start of the first unmet day is treated as before demand, not a verified courier commitment.',
            'Search is bounded and not exhaustive; additional stock/options may be unavailable or uninvestigated.'])
    trace.event('mitigation.objective', objective=request.objective, scenario=request.scenario,
                reasoning=request.reasoning, evidence_as_of=request.evidence_as_of.isoformat(), simulated=True)
    tools = None
    try:
        if reasoner.live != (request.reasoning == 'nightshift'):
            raise ServiceError('REASONING_MODE_MISMATCH')
        scenario, overlay = prepare_scenario(bundle, catalog, request.scenario)
        result.scenario_dataset_version = scenario.dataset_version
        result.overlay, result.overlay_version = overlay, digest(overlay.model_dump(mode='json')) if overlay else None
        tools = MitigationTools(scenario, catalog, request, trace, max_calls=max_calls, timeout=timeout)
        result.reference = tools.baseline()
        target = next((r for r in result.reference if r.hospital_id == request.hospital_id), None)
        if target is None:
            raise ServiceError('UNKNOWN_TARGET')
        # Include target/product/horizon in memory partitioning in addition to Phase 4A's scope.
        partition = digest({'target': request.hospital_id, 'product': request.product_id, 'horizon': request.horizon_days})
        mem_request = InvestigationRequest(session_id=request.session_id, objective=request.objective,
            evidence_as_of=request.evidence_as_of, horizon_days=request.horizon_days)
        memory = Memory(Path(memory_root) / partition, mem_request, scenario, trace, node=node)

        def remember(key, payload, ids, analyses=()):
            fact = Finding(claim_key=key, value=json.dumps(payload, sort_keys=True), kind='derived',
                evidence_ids=sorted(set(ids)), source_ids=sorted({memory.registry[i].source for i in ids}),
                analysis_ids=list(analyses), limitations=['Simulated feasibility investigation; no physical action.'])
            recall = memory.exchange('mitigation shortage rejection alternatives ' + key, [fact])
            result.memory_ids = sorted(set(result.memory_ids + recall['writes'] + [
                item['memory']['id'] for item in recall['result']['memories']]))
            if recall['conflicts']:
                raise ServiceError('MEMORY_CONFLICT')
            return recall

        recall = remember('shortage.' + request.scenario, {
            'unmet_vials': target.disruption.forecast.total_unmet,
            'baseline_unmet_vials': target.baseline.forecast.total_unmet,
            'shortage_onset': str(target.disruption.forecast.projected_stockout_date)},
            target.input_evidence_ids, [target.analysis_id])
        if not target.disruption.forecast.total_unmet:
            result.status, result.stop_reason = 'complete', 'no_shortage'
        else:
            for collection in ('supplier_inventory', 'hospital_lots', 'transfers', 'shipments', 'delivery_constraints'):
                trace.event('specialist.mitigation_task', 'started', responsibility=collection)
                tools.inspect({'collection': collection})
                trace.event('specialist.mitigation_task', responsibility=collection)
            for _ in range(max_candidates):
                candidates = tools.inspect({'collection': 'candidates'})
                tried = {e.candidate.candidate_id for e in result.evaluations}
                if not any(c.candidate_id not in tried for c in candidates):
                    result.stop_reason = 'exhausted'
                    break
                tools.remaining()
                choice = reasoner.choose(candidates, result.evaluations, recall, trace)
                tools.remaining()
                if choice.candidate_id is None:
                    result.stop_reason = 'model_stop'
                    break
                if choice.candidate_id in tried:
                    raise ServiceError('REPEATED_CANDIDATE')
                evaluation = tools.check({'candidate_id': choice.candidate_id})
                result.evaluations.append(evaluation)
                recall = remember('mitigation.' + request.scenario + '.' + choice.candidate_id, {
                    'candidate_id': choice.candidate_id, 'feasible': evaluation.feasible,
                    'reasons': [f.code for f in evaluation.failures],
                    'remaining_unmet': evaluation.remaining_unmet,
                    'original_unmet': evaluation.original_unmet, 'quantity_moved': evaluation.quantity_moved},
                    evaluation.evidence_ids, evaluation.analysis_ids)
                trace.event('coordinator.replanning', previous_candidate=choice.candidate_id,
                    rejected=not evaluation.feasible, reasons=[f.code for f in evaluation.failures],
                    memory_ids=result.memory_ids, next_action='investigate_untried_alternative')
            else:
                tried = {e.candidate.candidate_id for e in result.evaluations}
                result.stop_reason = 'exhausted' if all(c.candidate_id in tried for c in catalog.candidates) else 'candidate_budget'
            result.status = 'complete' if result.stop_reason == 'exhausted' else 'incomplete'
    except ServiceError as exc:
        result.error_code = exc.code
        result.stop_reason = {'TASK_TIMEOUT': 'timeout', 'TOOL_CALL_BUDGET': 'tool_budget',
                              'MODEL_CALL_BUDGET': 'candidate_budget'}.get(exc.code, 'blocked')
        result.status = 'incomplete' if result.stop_reason in ('timeout', 'tool_budget', 'candidate_budget') else 'blocked'
        trace.event('mitigation.failure', 'blocked', error_code=exc.code)
    except (ValueError, OSError, KeyError, StopIteration):
        result.status, result.error_code, result.stop_reason = 'blocked', 'MITIGATION_VALIDATION_FAILED', 'blocked'
        trace.event('mitigation.failure', 'blocked', error_code=result.error_code)
    finally:
        if tools:
            tools.cancelled.set()
    feasible = [e for e in result.evaluations if e.feasible] if result.status != 'blocked' else []
    if feasible:
        # Only evaluated feasible alternatives compete; never let model prose choose numerical outcomes.
        def rank(e):
            recipient = next(i.effective for i in e.impacts if i.hospital_id == request.hospital_id)
            return (e.remaining_unmet, LEVELS.index(recipient.risk), recipient.shortage_days,
                sum(i.effective.unmet_vials-i.before.unmet_vials for i in e.impacts if i.donor),
                sum(LEVELS.index(i.effective.risk)-LEVELS.index(i.before.risk) for i in e.impacts if i.donor),
                e.quantity_moved, e.candidate.candidate_id)

        best = min(feasible, key=rank)
        result.recommendation_id = best.candidate.candidate_id
        result.recommendation = (f'For human review: {best.candidate.candidate_id}; '
            f'{best.original_unmet} → {best.remaining_unmet} unmet vials; '
            f'{best.quantity_moved} simulated vials supplied. '
            'Best among evaluated feasible alternatives; other hospitals are checked. ' +
            ('Residual shortage remains unresolved.' if best.remaining_unmet else 'No recipient unmet demand in this horizon.'))
        trace.event('mitigation.recommendation', candidate_id=result.recommendation_id,
            recommendation=result.recommendation, evidence_ids=best.evidence_ids,
            requires_human_review=True, simulated=True)
    else:
        result.recommendation = ('No projected recipient shortage in this horizon; no intervention needed.'
            if result.stop_reason == 'no_shortage' else
            'No evaluated feasible intervention; no physical action is authorized.')
    if tools:
        ids = {i for e in result.evaluations for i in e.evidence_ids}
        ids.update(i for r in result.reference for i in r.input_evidence_ids)
        result.evidence = [e.model_copy(update={'raw_ref': None}) for e in evidence_closure(tools.bundle, ids)]
        known = {e.evidence_id for e in result.evidence}
        for e in result.evaluations:
            for ev in e.projection_evidence:
                if ev.evidence_id not in known:
                    result.evidence.append(ev.model_copy(update={'raw_ref': None}))
                    known.add(ev.evidence_id)
    trace.event('mitigation.stopped', result_status=result.status, stop_reason=result.stop_reason,
                evaluated=len(result.evaluations), recommendation_id=result.recommendation_id)
    result.events = [e.model_dump(mode='json') for e in trace.events]
    return result
