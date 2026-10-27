"""Signal → simulated route exposure → configurable risk → existing mitigation agents."""
from datetime import timedelta
from contracts import DelayRisk, ForecastRequest, ScenarioOverlay, ArrivalOverride, IntelligenceRun, MitigationRequest
from phases.phase_01_foundation.sources import digest
from phases.phase_03_risk.tools import RiskTools
from phases.phase_04_agents.coordinator import investigate_mitigation
from phases.phase_04_agents.memory import Memory
from phases.phase_04_agents.models import Finding, InvestigationRequest
from phases.phase_04_agents.mitigation_scenario import prepare_scenario, seal
from phases.phase_04_agents.trace import Trace
from .matching import match_dependency
from .registry import investigation_key
from .scenario import current_catalog, simulated


def investigate_signal(signal, bundle, dependency, memory_root, *, mode='frozen-replay',
                       decision_at=None, delays=(0,3,7), trace=None, node=None, available_signals=(), zetaris=None, reasoner=None):
    cutoff = decision_at or signal.retrieved_at
    if (not delays or len(delays)>5 or any(type(d) is not int or not 0<=d<=21 for d in delays)
            or len(set(delays)) != len(delays) or 0 not in delays):
        raise ValueError('Use 1..5 unique delay assumptions, 0..21 days, including zero')
    trace = trace or Trace()
    match = match_dependency(signal,dependency,cutoff,available_signals=available_signals,bundle=bundle)
    key = investigation_key(signal,dependency,delays)
    run = IntelligenceRun(run_id=key,mode=mode,decision_at=cutoff,signal=signal,dependency=dependency,match=match,
        status='unmatched',zetaris=zetaris or {'mode':'not-contacted'},uncertainties=[
            'The authority notice establishes an advisory; it does not prove this shipment was delayed.',
            'The supplier, cargo, route, hospitals, stock and mitigation permissions are simulated.',
            'Delay-day values are configured sensitivity assumptions, not measured or model-inferred delays.',
            'Observed evidence remains at its true retrieval time; the day-start operational scenario is hypothetical.',
            'Publication/event times absent from sources remain unknown; indexing is not verification.',
            ('This run uses bounded Nightshift decisions; inspect actual route/tool traces and final status.' if reasoner and reasoner.live else
             'Nightshift live verification remains unverified; this run uses deterministic model fixtures.')])
    trace.event('intelligence.signal',event_id=signal.event_id,revision=signal.revision_id,title=signal.title,
        citations=[str(u) for u in signal.original_urls],publication_date=str(signal.source_published_on),
        effective_on=str(signal.effective_on),retrieved_at=signal.retrieved_at.isoformat(),
        confirmation=signal.confirmation,mode=mode,evidence_ids=signal.evidence_ids)
    trace.event('intelligence.match',match=match.model_dump(mode='json'))
    if match.status!='MATCHED':
        run.events=[e.model_dump(mode='json') for e in trace.events]
        return run
    if bundle.dataset_version!=dependency.dataset_version:
        raise ValueError('Dependency dataset mismatch')
    tools = RiskTools(bundle,max_calls=6)
    run.baseline=tools.hospital_risk({'hospital_id':dependency.hospital_id,'horizon_days':30})
    for days in sorted(delays):
        arrival=dependency.planned_arrival+timedelta(days=days)
        overlay=ScenarioOverlay(overlay_id=f'october-delay-{days}',base_dataset_version=bundle.dataset_version,
            as_of=bundle.scenario_as_of,description=f'Simulated sensitivity: +{days} days; not an observed delay.',
            arrivals=[ArrivalOverride(shipment_id=dependency.shipment_id,arrival_on=arrival)])
        trace.event('risk.hospital_risk','started',arguments={'hospital_id':dependency.hospital_id,
            'horizon_days':30,'arrival_overlay':overlay.model_dump(mode='json')})
        assessment=tools.hospital_risk(ForecastRequest(hospital_id=dependency.hospital_id,horizon_days=30,overlay=overlay))
        run.sensitivity.append(DelayRisk(delay_days=days,assumed_arrival=arrival,delivery_difference_days=days,assessment=assessment))
        f=assessment.forecast
        trace.event('risk.hospital_risk',delay_assumption_days=days,arrival=str(arrival),unmet_vials=f.total_unmet,
            first_unmet=str(f.projected_stockout_date),affected_days=f.shortage_days,risk=assessment.rating.level,
            analysis_id=assessment.analysis_id,input_evidence_ids=assessment.input_evidence_ids)
    selected=max(run.sensitivity,key=lambda s:s.delay_days)
    run.selected_delay_days=selected.delay_days
    trace.event('coordinator.signal_investigation',policy='evaluate-largest-configured-delay',
        selected_delay_days=selected.delay_days,observed_delay=False,uncertainties=run.uncertainties)
    delayed=bundle.model_copy(deep=True)
    for i,s in enumerate(delayed.shipments):
        if s.shipment_id==dependency.shipment_id:
            ev=simulated('configured-arrival-assumption',{'days':selected.delay_days,'arrival':str(selected.assumed_arrival)},
                         bundle.scenario_as_of,[s.evidence.evidence_id])
            delayed.evidence.append(ev)
            delayed.shipments[i]=s.model_copy(update={'expected_arrival':selected.assumed_arrival,'evidence':ev})
    # Observed context is carried for decision provenance/H-MEM, never as a backdated operational dependency.
    delayed.mode='observed+simulated'
    delayed.evidence.extend(e.model_copy(deep=True) for e in signal.evidence)
    delayed=seal(delayed)
    catalog=current_catalog(delayed)
    request=MitigationRequest(session_id='signal-'+key[:20],evidence_as_of=cutoff,horizon_days=30,
        objective='Investigate conditional October route exposure and safe simulated supply alternatives; human review required.',
        reasoning='nightshift' if reasoner and reasoner.live else 'fixture')
    shared,_=prepare_scenario(delayed,catalog,'original')
    partition=digest({'target':request.hospital_id,'product':request.product_id,'horizon':request.horizon_days})
    memory=Memory(memory_root/partition,InvestigationRequest(session_id=request.session_id,objective=request.objective,
        evidence_as_of=cutoff,horizon_days=30),shared,trace,node=node)
    fact=Finding(claim_key='source.'+signal.event_id,value=signal.title+'; '+' '.join(signal.supported_claims),
        kind='observed',evidence_ids=signal.evidence_ids,source_ids=sorted({e.source for e in signal.evidence}),
        limitations=signal.claim_limitations)
    memory.exchange('new signal October route exposure alternatives',[fact])
    run.mitigation=investigate_mitigation(delayed,catalog,request,memory_root,trace=trace,node=node,
        reasoner=reasoner,max_candidates=4 if reasoner and reasoner.live else 6)
    if run.mitigation.reference:
        central=next(r for r in run.mitigation.reference if r.hospital_id==dependency.hospital_id)
        if central.disruption.forecast.total_unmet!=selected.assessment.forecast.total_unmet:
            raise ValueError('Risk-to-mitigation scenario continuity failed')
    run.status='complete' if run.mitigation.status=='complete' else 'blocked'
    run.error_code=run.mitigation.error_code
    trace.event('intelligence.decision',result_status=run.status,recommendation=run.mitigation.recommendation,
                source_evidence_ids=signal.evidence_ids,requires_human_review=True)
    run.events=[e.model_dump(mode='json') for e in trace.events]
    return run
