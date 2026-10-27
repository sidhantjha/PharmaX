"""Bounded parallel investigation, memory reinjection and one justified follow-up."""
import json
from concurrent.futures import ThreadPoolExecutor, wait
from phases.phase_01_foundation.http import ServiceError
from phases.phase_02_data.store import evidence_closure
from .memory import Memory
from .models import InvestigationRequest, InvestigationResult, TaskResult
from .reasoning import FixtureReasoner
from .specialists import SPECIALISTS, disruption, trade
from .tools import InvestigationTools
from .trace import Trace


def investigate(bundle, request, memory_root, *, reasoner=None, trace=None, node=None,
                max_calls=24, task_timeout=45, client=None):
    request = InvestigationRequest.model_validate(request)
    reasoner = reasoner or FixtureReasoner()
    trace = trace or Trace()
    result = InvestigationResult(request=request, dataset_version=bundle.dataset_version, status='incomplete')
    trace.event('investigation.objective', objective=request.objective, evidence_as_of=request.evidence_as_of.isoformat(),
                mode=request.mode, reasoning='nightshift-nemotron' if reasoner.live else 'deterministic-fixture-policy')
    if request.mode == 'fixture' and reasoner.live:
        result.status, result.error_code = 'blocked', 'OFFLINE_MODEL_DENIED'
        result.events = list(trace.events)
        return result
    tools = None
    try:
        memory = Memory(memory_root, request, bundle, trace, node=node)
        prior = memory.exchange(request.objective)
        context = prior['result']['context']
        plan = reasoner.plan(request, context, trace)
        result.plan = plan
        trace.event('coordinator.dispatch', tasks=plan.tasks, decision=plan.decision, parallel=len(plan.tasks) > 1)
        tools = InvestigationTools(bundle, request, trace, max_calls=max_calls, timeout=task_timeout, client=client)

        def execute(role, handler):
            trace.event('specialist.task', 'started', role=role)
            try:
                value = handler(tools)
            except ServiceError as exc:
                value = TaskResult(role=role, status='failed', error_code=exc.code)
            except (ValueError, KeyError, TypeError):
                value = TaskResult(role=role, status='failed', error_code='TOOL_VALIDATION_FAILED')
            if not tools.cancelled.is_set():
                trace.event('specialist.task', 'complete' if value.status == 'complete' else 'failed',
                            role=role, result_status=value.status, missing=value.missing, error_code=value.error_code)
            return value

        executor = ThreadPoolExecutor(max_workers=3)
        futures = {role: executor.submit(execute, role, SPECIALISTS[role]) for role in plan.tasks}
        _, pending = wait(futures.values(), timeout=task_timeout)
        if pending:
            tools.cancelled.set()
        for role in sorted(futures):
            future = futures[role]
            if future in pending:
                future.cancel()
                value = TaskResult(role=role, status='failed', error_code='TASK_TIMEOUT')
                trace.event('specialist.task', 'failed', role=role, error_code='TASK_TIMEOUT')
            else:
                value = future.result()
            result.tasks.append(value)
        executor.shutdown(wait=False, cancel_futures=True)
        findings = [f for task in result.tasks for f in task.findings]
        current = memory.exchange(request.objective, findings)

        def capture():
            result.findings = list({f.model_dump_json(): f for f in findings}.values())
            result.evidence = [e.model_copy(update={'raw_ref': None}) for e in evidence_closure(
                bundle, {i for f in findings for i in f.evidence_ids})]
            result.memory = current['result']['memories']
            result.conflicts = current['conflicts']
            result.missing = sorted({m for t in result.tasks for m in t.missing})

        capture()  # Preserve validated findings even if the subsequent model review fails.
        summary = [t.model_dump(mode='json', exclude={'calculations'}) for t in result.tasks]
        # Keep model reinjection bounded; numerical series remain in the public trace result.
        for task in summary:
            task['findings'] = [{k: f[k] for k in ('claim_key', 'value', 'evidence_ids')} for f in task['findings']]
        review_context = current['result']['context']
        if current['conflicts']:
            review_context += '\n[UNRESOLVED MEMORY CONFLICTS]\n' + json.dumps(current['conflicts'])
        review = reasoner.review(summary, review_context, trace)
        result.review = review
        conflicts = current['conflicts']
        missing = {m for t in result.tasks for m in t.missing}
        target = review.followup
        justified = ((target == 'disruption_link' and ('manufacturing_link' in missing or conflicts))
                     or (target == 'trade_coverage' and ('national_denominator' in missing or conflicts)))
        if target != 'none':
            if justified and not tools.cancelled.is_set():
                role, handler = ('disruption', disruption) if target == 'disruption_link' else ('trade', trade)
                trace.event('coordinator.followup', target=target, reason=review.reason)
                # Same shared deadline and budget; no retries, one targeted task maximum.
                extra = execute(role, lambda t: handler(t, followup=True))
                result.tasks.append(extra)
                findings.extend(extra.findings)
                current = memory.exchange(request.objective, extra.findings)
            else:
                trace.event('coordinator.followup', 'blocked', target=target, error_code='FOLLOWUP_UNJUSTIFIED_OR_TIMEOUT')
        capture()
        result.status = 'complete' if all(t.status == 'complete' for t in result.tasks) and not result.conflicts else 'incomplete'
        trace.event('investigation.findings', result_status=result.status, missing=result.missing,
                    conflicts=result.conflicts, findings=[f.model_dump(mode='json') for f in result.findings])
    except ServiceError as exc:
        result.status, result.error_code = 'blocked', exc.code
        trace.event('investigation.failure', 'blocked', error_code=exc.code, diagnostics=exc.diagnostics)
    except (ValueError, OSError) as exc:
        code = str(exc) if str(exc).startswith('MEMORY_') else 'INVESTIGATION_VALIDATION_FAILED'
        result.status, result.error_code = 'blocked', code
        trace.event('investigation.failure', 'blocked', error_code=code)
    finally:
        if tools:
            tools.cancelled.set()
    result.events = list(trace.events)
    return result


def investigate_mitigation(*args, **kwargs):
    """Phase 4B extension; retains the existing Phase 4A investigation API."""
    from .mitigation import investigate_mitigation as run
    return run(*args, **kwargs)


def investigate_signal(*args, **kwargs):
    """Phase 4C entry point reuses the existing mitigation/H-MEM runtime."""
    from .live_intelligence.pipeline import investigate_signal as run
    return run(*args, **kwargs)
