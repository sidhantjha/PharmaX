"""Canonical operator workflow around public Phase 2–4 entry points."""
from datetime import date, datetime, timezone
import json
from pathlib import Path
from uuid import uuid4
from contracts import MitigationRequest
from contracts.operator import OperatorCase, AgentDecision, VerificationCheck
from phases.phase_02_data.pipeline import load_bundle
from phases.phase_02_data.snapshots import Snapshots
from phases.phase_02_data.store import evidence_closure
from phases.phase_04_agents.coordinator import investigate_mitigation, investigate_signal
from phases.phase_04_agents.live_intelligence.matching import match_dependency
from phases.phase_04_agents.live_intelligence.scenario import build_current, current_catalog
from phases.phase_04_agents.live_intelligence.sources import frozen
from phases.phase_04_agents.mitigation_scenario import load_catalog, seal
from phases.phase_04_agents.models import InvestigationRequest
from phases.phase_04_agents.memory import Memory
from phases.phase_04_agents.specialists import trade, disruption
from phases.phase_04_agents.tools import InvestigationTools
from .execution import ExecutionTrace, concurrent_reads
from .actions import prepare
from .clinical import clinical

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / 'docs/evidence/pharmax-phase4c/manifest.json'


def now():
    return datetime.now(timezone.utc)



def verify(signal, reparsed, match):
    """Checks actual parser output against retained, hash-verified listing/detail bytes."""
    ids = signal.evidence_ids
    identical = signal.model_dump() == reparsed.model_dump()
    return [VerificationCheck(check=check, status=status, explanation=explanation, evidence_ids=ids)
        for check, status, explanation in [
            ('identity', 'supported' if identical else 'unsupported',
             'Hash-checked retained receipts resolve to the allowlisted official MARAD listing and notice; this is replay of retrieval, not a fresh authenticity check.'),
            ('document_support', 'supported' if identical else 'unsupported',
             'Normalized title, listing status, validity dates and bounded claims reproduce from the exact retained documents.'),
            ('dates', 'limited', f'Effective {signal.effective_on}; expires {signal.expires_on}; publication unknown; retrieved {signal.retrieved_at.isoformat()}.'),
            ('corroboration', 'unavailable', 'No independent incident or shipment-delay corroboration in the retained evidence; GDELT articles unavailable.'),
            ('contradictions', 'limited', 'Listing/detail agree on advisory identity. No independent sources establish or contradict an actual cargo delay.'),
            ('operational_relevance', 'limited', f'{match.status}: corridor/time exposure is calculated against a simulated route. No observed delay or pharmaceutical dependency is established.')]]


RESPONSIBILITIES = {
    'coordinator': 'Coordinate bounded investigation, replan after rejection and rank calculated feasible options.',
    'news': 'Normalize retained or explicitly refreshed source receipts.',
    'verification': 'Check identity, document support, dates, corroboration, contradictions and operational relevance.',
    'dependency': 'Match corridor and validity window to the explicit simulated route.',
    'trade': 'Calculate bounded HS30 trade context; exclude evidence after cutoff.',
    'disruption': 'Inspect available source/product evidence; preserve missing manufacturing links.',
    'hospital': 'Calculate lot-level baseline, disruption and donor inventory forecasts.',
    'mitigation': 'Discover actual catalog stock/routes and choose untried candidates with retrieved rejection context.',
    'feasibility': 'Validate exact product, stock, authority, timing and donor safety using recalculation.',
    'memory': 'Write and retrieve eligible findings using the real H-MEM reference engine.',
    'clinical': 'Allocate synthetic administration preparation requirements against calculated served supply.',
    'actions': 'Prepare human-review drafts from the selected feasible mitigation.'}


def owner(event):
    op, data = event.operation, event.data
    if data.get('agent_id') in RESPONSIBILITIES:
        return data['agent_id']
    if op.startswith('hmem.'):
        return 'memory'
    if op in ('mitigation.evaluate',):
        return 'feasibility'
    if op == 'mitigation.reference' or op.startswith('risk.hospital'):
        return 'hospital'
    if data.get('role') in ('trade', 'disruption'):
        return data['role']
    if op.startswith('operator.'):
        return op.split('.')[1]
    if op == 'intelligence.signal':
        return 'news'
    if op == 'intelligence.match':
        return 'dependency'
    if op.startswith(('mitigation.inspect', 'specialist.mitigation', 'coordinator.fixture_choice')):
        return 'mitigation'
    return 'coordinator'


def ledger(trace, result):
    groups = {}
    for index, event in enumerate(trace.events):
        groups.setdefault(owner(event), []).append((event, trace.timestamps[index]))
    decisions = []
    for role, records in groups.items():
        ids, memories, rejected = set(), set(), []
        for event, _ in records:
            ids.update(event.data.get('evidence_ids', []))
            memories.update(event.data.get('memory_ids', []))
            rejected.extend(event.data.get('rejection_codes', []))
        blocked = any(e.status in ('blocked', 'failed') for e, _ in records)
        summaries = {
            'coordinator': result.recommendation,
            'feasibility': '; '.join(f'{e.candidate.candidate_id}: '+('feasible' if e.feasible else ','.join(f.code for f in e.failures)) for e in result.evaluations),
            'hospital': '; '.join(f'{r.hospital_id}: disruption {r.disruption.forecast.total_unmet} unmet vials' for r in result.reference),
            'memory': f'{len(memories)} memory IDs retrieved; '+str(sum(len(e.data.get('writes',[])) for e,_ in records))+' writes recorded by the actual reference bridge.',
            'mitigation': f'Inspected catalog and evaluated {len(result.evaluations)} independent alternatives; selected {result.recommendation_id}.',
            'actions': 'Prepared '+str(sum(len(e.data.get('actions',[])) for e,_ in records))+' DRAFT packages; dispatch unavailable.',
            'verification': '; '.join(c['check']+': '+c['status'] for e,_ in records for c in e.data.get('checks',[])),
            'trade': '; '.join(f['value'] for e,_ in records for f in e.data.get('result',{}).get('findings',[])) or 'No comparable trade evidence eligible at this cutoff.',
            'disruption': '; '.join(f['value'] for e,_ in records for f in e.data.get('result',{}).get('findings',[])) or 'No observed manufacturing link established at cutoff.',
        }
        explanation = summaries.get(role,RESPONSIBILITIES[role])
        decisions.append(AgentDecision(agent_id=role, responsibility=RESPONSIBILITIES[role],
            task=RESPONSIBILITIES[role], status='blocked' if blocked or any(e.data.get('result_status') in ('failed', 'timed_out') for e,_ in records) else ('unavailable' if any(e.data.get('availability') == 'unavailable' for e,_ in records) else 'complete'),
            started_at=records[0][1], completed_at=records[-1][1],
            execution='nightshift' if any(e.operation.startswith('nightshift.') for e,_ in records) else 'local-deterministic',
            tool_records=[{**e.model_dump(mode='json'), 'executed_at':stamp.isoformat()} for e,stamp in records],
            evidence_ids=sorted(ids), explanation=explanation or 'No feasible recommendation.',
            limitations=['Public tool/decision records only; no hidden reasoning.',
                         'Operational network is simulated; observed sources do not prove shipment delay.'],
            rejected=sorted(set(rejected)), next_action='Human review' if role == 'actions' else 'Inspect cited structured outputs',
            memory_ids=sorted(memories)))
    return decisions


def _run_case(request, memory_root, *, trace, node=None, manifest_path=MANIFEST, live_source=False, reasoner=None, decision_at=None, model_specialists=None):
    """No client/inference is created here; live-source requires explicit previously fetched receipts."""
    if reasoner and reasoner.live and not live_source and model_specialists is None:
        raise ValueError('OFFLINE_MODEL_DENIED')
    identity, created = 'case-' + uuid4().hex, now()
    step_ids = []

    def step(role, function, dependencies=None, require_success=True):
        node_task = trace.launch(role, parent_id=trace.local.task_id,
            dependencies=step_ids[-1:] if dependencies is None else dependencies, require_success=require_success)
        with trace.running(node_task):
            value = function()
        step_ids.append(node_task.task_id)
        return value

    base = load_bundle(ROOT / 'docs/evidence/pharmax-phase2/dataset.json')
    intelligence, checks, source_status, source_receipt = None, [], [], {}
    trace.event('operator.coordinator', task='Execute source-to-action workflow', scenario=request.scenario,
                inference='none', mode='live-source' if live_source else 'offline-replay')
    if request.scenario == 'red-sea':
        manifest = json.loads(Path(manifest_path).read_text())
        snapshots = Snapshots(Path(manifest_path).parent / 'snapshots')
        signals = step('news', lambda: frozen(snapshots, manifest))
        trace.event('operator.news', source='hash-checked retained receipts', count=len(signals),
                    evidence_ids=[i for s in signals for i in s.evidence_ids])
        selected = next(s for s in signals if s.provider == 'marad')
        # Pilot anchor is versioned; wall clock and refresh never silently shift stock or delivery dates.
        cutoff = decision_at or selected.retrieved_at
        if cutoff < selected.retrieved_at:
            raise ValueError('DECISION_BEFORE_SOURCE_AVAILABLE')
        bundle, dependency = build_current(date(2026, 10, 24), cutoff)
        def match_route():
            return match_dependency(selected, dependency, cutoff, available_signals=signals, bundle=bundle)
        def verify_claims():
            return verify(selected, next(s for s in frozen(snapshots, manifest) if s.event_id == selected.event_id), match_route())
        if model_specialists is not None:
            checks, match, model_findings, gate_id = model_specialists.source_and_dependency(
                selected, dependency, bundle, cutoff, request.delay_days, trace,
                verify_claims=verify_claims, match_route=match_route, dependencies=step_ids[-1:])
            step_ids.append(gate_id)
            def remember_assessments():
                context_bundle = bundle.model_copy(deep=True)
                context_bundle.evidence.extend(e.model_copy(deep=True) for e in selected.evidence)
                context_bundle.mode = 'observed+simulated'
                context_bundle = seal(context_bundle)
                memory = Memory(Path(memory_root)/identity/'model-assessments', InvestigationRequest(
                    session_id=identity, objective='Retain validated source and dependency assessments.',
                    evidence_as_of=cutoff), context_bundle, trace, node=node)
                return memory.exchange('MARAD advisory simulated route conditional forecast', model_findings)
            step('memory', remember_assessments)
        else:
            match = step('dependency', match_route)
            checks = step('verification', verify_claims)
        trace.event('operator.verification', checks=[c.model_dump(mode='json') for c in checks], evidence_ids=selected.evidence_ids)
        if any(c.status == 'unsupported' for c in checks):
            raise ValueError('SOURCE_VERIFICATION_FAILED')
        trace.event('operator.dependency', match=match.model_dump(mode='json'), evidence_ids=match.evidence_ids)
        delays = tuple(sorted({0, request.delay_days}))
        def mitigation_work():
            run = investigate_signal(selected, bundle, dependency, Path(memory_root)/identity,
                mode='live-fetch' if live_source else 'frozen-replay', decision_at=cutoff,
                delays=delays, trace=trace, node=node, available_signals=signals,
                reasoner=reasoner,zetaris=manifest.get('zetaris') if live_source else
                    {'mode':'frozen-replay', 'prior_live_receipt':manifest.get('zetaris', {})})
            if model_specialists is not None:
                if run.mitigation is None or run.mitigation.status != 'complete':
                    from phases.phase_01_foundation.http import ServiceError
                    raise ServiceError(run.error_code or 'MODEL_INVESTIGATION_INCOMPLETE')
                model_specialists.review(run.mitigation, trace)
            return run
        source_status = manifest.get('source_status', [])
    else:
        bundle = base
        cutoff = datetime(2023, 7, 24, 23, 59, 59, tzinfo=timezone.utc)
        trace.event('operator.news', status='complete', available='synthetic historical event only',
            excluded='FDA/trade source revisions retrieved in 2026; no historical availability inferred.')
        checks = [VerificationCheck(check='document_support', status='unavailable', evidence_ids=[],
            explanation='July 2023 operational scenario is synthetic. Later observed revisions cannot establish historical availability or a carboplatin manufacturing link.')]
        trace.event('operator.verification', checks=[c.model_dump(mode='json') for c in checks])
        def mitigation_work():
            return investigate_mitigation(bundle, load_catalog(), MitigationRequest(session_id=identity,
                scenario=request.scenario, evidence_as_of=cutoff, reasoning='nightshift' if reasoner and reasoner.live else 'fixture'),
                Path(memory_root)/identity, trace=trace, node=node, reasoner=reasoner,
                max_candidates=4 if reasoner and reasoner.live else 6)
    context_request = InvestigationRequest(session_id=identity,
        objective='Bounded source and trade context; never infer shipment dependencies.', evidence_as_of=cutoff,
        historical=request.scenario != 'red-sea')

    def read_context(role, function, allowance):
        # Fixed allocations prevent thread scheduling from deciding who gets the last call.
        tools = InvestigationTools(base, context_request, trace, max_calls=allowance)
        try:
            return function(tools)
        finally:
            tools.cancelled.set()

    tasks, context_nodes = concurrent_reads(trace, [
        ('trade', lambda: read_context('trade', trade, 2)),
        ('disruption', lambda: read_context('disruption', disruption, 3))],
        parent_id=trace.local.task_id, dependencies=step_ids[-1:])
    step('coordinator', lambda: trace.reconcile(context_nodes[0], tasks),
         dependencies=[t.task_id for t in context_nodes], require_success=False)
    context = [task.model_dump(mode='json') for task in tasks]
    accepted_findings = [f for task in tasks if task.status == 'complete' for f in task.findings]
    if accepted_findings:
        def share_context():
            memory = Memory(Path(memory_root)/identity/'context', context_request, base, trace, node=node)
            return memory.exchange(context_request.objective, accepted_findings)
        step('memory', share_context)
    if request.scenario == 'red-sea':
        intelligence = step('coordinator', mitigation_work)
        source_receipt = intelligence.zetaris
        result = intelligence.mitigation
        if result is None:
            raise ValueError('NO_VERIFIED_RELEVANT_SIGNAL')
    else:
        result = step('mitigation', mitigation_work)
    sessions, impact = step('clinical', lambda: clinical(result, intelligence.baseline if intelligence else None,
        intelligence.dependency.scenario_id if intelligence else 'july-2023-'+request.scenario))
    trace.event('operator.clinical', schedules=len(sessions), impacts=[i.model_dump(mode='json', exclude={'allocations'}) for i in impact])
    source_orgs={r.inventory_id:r.hospital_id for r in bundle.inventory}
    catalog=current_catalog(bundle) if intelligence else load_catalog()
    source_orgs.update({r.stock_id:r.supplier_id for r in catalog.supplier_stocks})
    drafts = step('actions', lambda: prepare(identity, result, source_orgs))
    trace.event('operator.actions', actions=[a.model_dump(mode='json') for a in drafts], dispatch='unavailable')
    evidence = {e.evidence_id:e for e in result.evidence}
    if intelligence:
        evidence.update({e.evidence_id:e for e in intelligence.signal.evidence})
    used_context_ids = {i for t in context for f in t['findings'] for i in f['evidence_ids']}
    # Retain all inspected context records as well as cited findings so every ledger reference resolves.
    used_context_ids.update(i for event in trace.events if event.data.get('role') in ('trade','disruption')
                            for i in event.data.get('evidence_ids',[]))
    evidence.update({e.evidence_id:e for e in evidence_closure(base,used_context_ids)})
    return OperatorCase(investigation_id=identity, parent_id=request.parent_id, created_at=created,
        scenario=request.scenario, mode=('retained-source-live-model' if model_specialists else 'live-source' if live_source else 'offline-replay'),
        objective='Investigate conditional supply risk, donor-safe alternatives and reviewable operator actions.',
        model_execution='nightshift' if reasoner and reasoner.live else 'deterministic-local',
        dataset_version=bundle.dataset_version, status=result.status, intelligence=intelligence, mitigation=result,
        verification=checks, agents=ledger(trace,result), evidence=list(evidence.values()),
        medicines=bundle.products, hospitals=bundle.hospitals, schedules=sessions, clinical_impact=impact,
        actions=drafts, source_status=source_status, source_receipt=source_receipt, context_findings=context,
        limitations=[*result.uncertainties, ('Bounded Nightshift execution attempted; completion requires actual validated tool responses.' if reasoner and reasoner.live else
                     'No live Nightshift inference in this operator case.'),
                    'Synthetic clinical schedules are preparation requirements, not verified doses, procedures or outcomes.',
                    'Therapeutic alternatives require clinical review; no validated substitution provided.',
                    'PDF unavailable: repository has no compatible PDF report generator. JSON, CSV and Markdown are available.'])


def run_case(request, memory_root, *, node=None, emit=None, manifest_path=MANIFEST, live_source=False, reasoner=None,
             decision_at=None, trace=None, parent_task_id=None, dependency_ids=(), model_specialists=None):
    trace = trace or ExecutionTrace(emit)
    root = trace.launch('coordinator', parent_id=parent_task_id, dependencies=dependency_ids)
    with trace.running(root):
        case = _run_case(request, memory_root, trace=trace, node=node, manifest_path=manifest_path,
                         live_source=live_source, reasoner=reasoner, decision_at=decision_at, model_specialists=model_specialists)
    case.execution_graph = trace.graph
    case.decision_ledger = trace.decisions
    case.agents = ledger(trace, case.mitigation)
    if any(t.status in ('failed', 'timed_out') for t in trace.graph):
        case.status = 'incomplete'
    return case
