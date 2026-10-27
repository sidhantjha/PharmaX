"""Parent-preserving forecast revision, real rejection memory and adaptive rechecks."""
from datetime import date
import json
from pathlib import Path
from contracts.operator import CaseRequest
from contracts.reconciliation import CaseRevision, ForecastDelta, OptionRecheck
from phases.phase_01_foundation.http import ServiceError
from phases.phase_04_agents.memory import Memory, eligible
from phases.phase_04_agents.models import Finding, InvestigationRequest
from phases.phase_04_agents.mitigation_reasoning import CandidateChoice, recalled_rejections
from phases.phase_04_agents.mitigation_scenario import seal
from phases.phase_04_agents.live_intelligence.scenario import build_current
from .execution import ExecutionTrace
from .integration import run_case, ledger
from .reconciliation import reconcile, alternatives


class RevisionPlanner:
    """Select using actual candidate/results and retrieved constraints, never a script."""
    live=False

    def __init__(self, parent, memory):
        self.previous=parent.mitigation.recommendation_id
        self.memory=memory
        self.memory_refs=set()

    def choose(self, candidates, evaluations, memory, trace):
        recalled=self.memory.exchange('prior rejected candidate exact product donor shortage reserved stock constraint')
        constraints=[]
        for item in recalled['result']['memories']:
            fact=json.loads(item['memory']['content'])
            if fact['claim_key'].startswith('prior-rejection.'):
                constraints.append(json.loads(fact['value']))
                self.memory_refs.add(self.memory.scope+':'+item['memory']['id'])
        prior_rejected={c['candidate_id'] for c in constraints}
        current_rejected=recalled_rejections(memory)
        tried={e.candidate.candidate_id for e in evaluations}
        remaining=[c for c in candidates if c.candidate_id not in tried]
        # Re-evaluate the previous preference first. Past rejection only orders later
        # investigation: every remaining proposal still gets its current feasibility check.
        previous=next((c for c in remaining if c.candidate_id==self.previous),None)
        chosen=previous or (min(remaining,key=lambda c:(c.candidate_id in current_rejected,
            c.candidate_id in prior_rejected,-c.quantity,c.candidate_id)) if remaining else None)
        reason='compare_alternatives' if evaluations else 'investigate_supply'
        if current_rejected:
            reason='revise_after_rejection'
        choice=CandidateChoice(candidate_id=chosen.candidate_id if chosen else None,reason=reason)
        trace.event('coordinator.revision_choice',agent_id='coordinator',**choice.model_dump(),
            previous_recommendation=self.previous, prior_constraints=constraints,
            prior_constraint_memory_refs=sorted(self.memory_refs),
            memory_ids=[item['memory']['id'] for item in recalled['result']['memories']],
            recalled_rejections=sorted(current_rejected),
            justification='Recheck prior preference first; prioritize alternatives without remembered constraints, then revalidate all proposals.')
        trace.event('coordination.next_decision_selected',agent_id='coordinator',decision=choice.model_dump(),
                    prior_constraint_memory_refs=sorted(self.memory_refs))
        return choice


def decision_memory(parent, resolution, base, root, trace, node):
    """Decision evidence uses true receipt times; never feed this bundle to forecasts."""
    context=base.model_copy(deep=True)
    registry={e.evidence_id:e for e in context.evidence}
    for evidence in [*parent.evidence,*(r.evidence for r in resolution.claims)]:
        registry.setdefault(evidence.evidence_id,evidence.model_copy(deep=True))
    context.evidence=list(registry.values())
    context.mode='observed+simulated'
    context=seal(context)
    request=InvestigationRequest(session_id=resolution.resolution_id, objective='Resolve simulated arrival conflicts and retrieve prior mitigation constraints.',
        evidence_as_of=resolution.decision_at,horizon_days=parent.mitigation.request.horizon_days)
    memory=Memory(Path(root)/'revision-context'/parent.investigation_id,request,context,trace,node=node)
    facts=[]
    for evaluation in parent.mitigation.evaluations:
        if evaluation.feasible:
            continue
        ids=evaluation.evidence_ids
        facts.append(Finding(claim_key='prior-rejection.'+evaluation.candidate.candidate_id,
            value=json.dumps({'candidate_id':evaluation.candidate.candidate_id,'feasible':False,
                'reasons':[f.code for f in evaluation.failures], 'origin_case':parent.investigation_id,
                'requires_revalidation':True},sort_keys=True),kind='derived',evidence_ids=ids,
            source_ids=sorted({registry[i].source for i in ids}),analysis_ids=evaluation.analysis_ids,
            limitations=['Actual prior simulated feasibility result, not a timeless ban; revalidate against the revised forecast.']))
    available=[r.evidence.evidence_id for r in resolution.claims
               if eligible(r.evidence.evidence_id,registry,resolution.decision_at)]
    if available:
        facts.append(Finding(claim_key='arrival-conflict.'+resolution.resolution_id,
            value=json.dumps({'outcome':resolution.outcome,'selected_claim':resolution.selected_claim_id,
                'summary':resolution.summary},sort_keys=True),kind='derived',evidence_ids=available,
            source_ids=sorted({registry[i].source for i in available}),
            limitations=resolution.uncertainty))
    memory.exchange('arrival conflict prior rejected donor product constraints',facts)
    return memory,context


def revise_case(parent, request, memory_root, parent_hash, *, node=None, policy=None):
    if (parent.scenario!='red-sea' or parent.intelligence is None or parent.mitigation.status!='complete'
            or parent.mode!='offline-replay'):
        raise ValueError('REVISION_REQUIRES_COMPLETE_RED_SEA_REPLAY')
    if request.decision_at<parent.intelligence.decision_at:
        raise ValueError('REVISION_BEFORE_PARENT_CUTOFF')
    trace=ExecutionTrace()
    root=trace.launch('coordinator')
    with trace.running(root):
        def step(role,function):
            prior=trace.graph[-1]
            dependencies=[prior.task_id] if prior.status=='complete' else []
            task=trace.launch(role,parent_id=root.task_id,dependencies=dependencies)
            with trace.running(task):
                return function()
        trace.event('evidence.update_received',agent_id='coordinator',parent_id=parent.investigation_id,
                    decision_at=request.decision_at.isoformat(),classification='simulated-operational-update')
        resolution,usable=reconcile(parent,request,trace,policy=policy)
        base,_=build_current(date(2026,10,24),request.decision_at)
        resolution.alternatives=step('hospital',lambda:alternatives(resolution,usable,parent,base,trace))
        memory,context=step('memory',lambda:decision_memory(parent,resolution,base,memory_root,trace,node))
        planner=RevisionPlanner(parent,memory)
        delay=resolution.selected_delay_days if resolution.selected_delay_days is not None else resolution.prior_delay_days
        child=run_case(CaseRequest(scenario='red-sea',delay_days=delay,parent_id=parent.investigation_id),memory_root,
            node=node,reasoner=planner,decision_at=request.decision_at,trace=trace,parent_task_id=root.task_id,
            dependency_ids=[trace.graph[-1].task_id])
        if child.mitigation.status!='complete':
            raise ServiceError('REVISION_FORECAST_INCOMPLETE')
        previous=parent.mitigation.recommendation_id
        recheck=next((e for e in child.mitigation.evaluations if e.candidate.candidate_id==previous),None)
        deltas=[]
        def mitigated(case,hospital):
            chosen=next((e for e in case.mitigation.evaluations if e.candidate.candidate_id==case.mitigation.recommendation_id),None)
            return next((i.effective.unmet_vials for i in chosen.impacts if i.hospital_id==hospital),None) if chosen else None
        for old in parent.mitigation.reference:
            new=next(r for r in child.mitigation.reference if r.hospital_id==old.hospital_id)
            a,b=old.disruption.forecast,new.disruption.forecast
            deltas.append(ForecastDelta(hospital_id=old.hospital_id,previous_analysis_id=a.analysis_id,revised_analysis_id=b.analysis_id,
                previous_unmet_vials=a.total_unmet,revised_unmet_vials=b.total_unmet,unmet_delta=b.total_unmet-a.total_unmet,
                previous_first_unmet=a.projected_stockout_date,revised_first_unmet=b.projected_stockout_date,
                previous_mitigated_unmet=mitigated(parent,old.hospital_id),revised_mitigated_unmet=mitigated(child,old.hospital_id)))
        unresolved=resolution.outcome=='unresolved'
        disposition='unresolved' if unresolved else 'maintained' if previous==child.mitigation.recommendation_id else 'revised'
        winner=next((e for e in child.mitigation.evaluations if e.candidate.candidate_id==child.mitigation.recommendation_id),None)
        benefit=(f'{winner.quantity_moved} supplied vials leave {winner.remaining_unmet} unmet; existing feasibility ranking selects this evaluated option.' if winner else 'No intervention selected.')
        summary=(f'{resolution.summary} Previous option {previous}: '+
            (f'{recheck.remaining_unmet} residual vials, feasible={recheck.feasible}.' if recheck else
             'No intervention recheck required because the revised recipient forecast has no shortage.')+
            f' Recalculated preference: {child.mitigation.recommendation_id}; disposition={disposition}. {benefit} '
            'All mitigation proposals remain simulated and require human review.')
        child.conflict_resolution=resolution
        child.revision=CaseRevision(parent_id=parent.investigation_id,parent_snapshot_sha256=parent_hash,
            resolution_id=resolution.resolution_id,previous_scenario_version=parent.mitigation.scenario_dataset_version,
            revised_scenario_version=child.mitigation.scenario_dataset_version,
            forecast_changed=parent.mitigation.scenario_dataset_version!=child.mitigation.scenario_dataset_version,
            disposition=disposition,previous_recommendation_id=previous,
            revised_recommendation_id=None if unresolved else child.mitigation.recommendation_id,
            previous_option_recheck=OptionRecheck(evaluation_id=recheck.evaluation_id,
                candidate_id=recheck.candidate.candidate_id,feasible=recheck.feasible,
                original_unmet=recheck.original_unmet,remaining_unmet=recheck.remaining_unmet,
                rejection_codes=[f.code for f in recheck.failures],evidence_ids=recheck.evidence_ids,
                analysis_ids=recheck.analysis_ids) if recheck else None,forecast_deltas=deltas,
            prior_constraint_memory_refs=sorted(planner.memory_refs),decision_summary=summary)
        # This evidence is decision-time provenance, separate from the forecast inputs.
        evidence={e.evidence_id:e for e in [*context.evidence,*child.evidence]}
        child.evidence=list(evidence.values())
        child.limitations.extend(resolution.uncertainty)
        if unresolved:
            child.status='incomplete'
            trace.event('evidence.actions_withheld',agent_id='actions',
                action_ids=[a.action_id for a in child.actions],reason='Unresolved arrival conflict; conditional calculations do not adopt an action.')
            child.actions=[]
            child.limitations.append('Arrival conflict unresolved. Retained-assumption mitigation is conditional; action drafts withheld.')
        trace.event('evidence.case_revised',agent_id='coordinator',parent_id=parent.investigation_id,
                    child_id=child.investigation_id,revision=child.revision.model_dump(mode='json'),
                    evidence_ids=[r.evidence.evidence_id for r in resolution.claims])
    child.execution_graph=trace.graph
    child.decision_ledger=trace.decisions
    child.agents=ledger(trace,child.mitigation)
    return child
