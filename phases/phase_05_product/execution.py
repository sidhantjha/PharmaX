"""Bounded read-only fan-out and case-local public execution provenance."""
from concurrent.futures import ThreadPoolExecutor, wait
from contextlib import contextmanager
from datetime import datetime, timezone
import threading
from uuid import uuid4
from contracts.operator import ExecutionTask, DecisionRecord
from phases.phase_04_agents.models import TaskResult
from phases.phase_04_agents.trace import Trace
from phases.phase_01_foundation.http import ServiceError


def now():
    return datetime.now(timezone.utc)


class ExecutionTrace(Trace):
    def __init__(self, emit=None):
        super().__init__(emit)
        self.lock = threading.RLock()
        self.local = threading.local()
        self.timestamps, self.graph, self.decisions = [], [], []
        self.closed, self.pending = set(), {}
        self.memory_scopes = {}

    def event(self, operation, status='complete', **data):
        with self.lock:
            task_id = getattr(self.local, 'task_id', None)
            if task_id in self.closed:
                return
            stamp = now()
            role = data.get('role') or data.get('agent_id')
            # Import here to avoid an integration/execution dependency cycle.
            from .integration import owner, RESPONSIBILITIES
            from phases.phase_04_agents.models import TraceEvent
            role = role if role in RESPONSIBILITIES else owner(TraceEvent(sequence=1, operation=operation, status=status, data=data))
            data = {**data, 'evidence_ids': data.get('evidence_ids', data.get('input_evidence_ids', data.get('source_evidence_ids', [])))}
            if operation == 'hmem.exchange':
                memory_key = (threading.get_ident(), task_id)
                if status == 'started':
                    self.memory_scopes[memory_key] = data.get('scope')
                else:
                    data['scope'] = self.memory_scopes.get(memory_key)
            key = (threading.get_ident(), task_id, operation, role)
            if status == 'started':
                operation_id = 'op-' + uuid4().hex
                self.pending[key] = (operation_id, stamp)
            else:
                operation_id, start = self.pending.pop(key, ('op-' + uuid4().hex, None))
            data = {**data, 'task_id': task_id, 'operation_id': operation_id}
            self.timestamps.append(stamp)
            super().event(operation, status, **data)
            self.decisions.append(DecisionRecord(
                record_id=f'record-{len(self.events)}', sequence=len(self.events), task_id=task_id,
                operation_id=operation_id, agent_id=role, operation=operation, status=status,
                executed_at=stamp, started_at=stamp if status == 'started' else start,
                completed_at=None if status == 'started' else stamp,
                tool_identifier=operation if operation.startswith(('data.', 'risk.', 'hmem.', 'mitigation.inspect', 'mitigation.evaluate', 'mitigation.reference')) else None,
                evidence_ids=data.get('evidence_ids', []),
                memory_ids=sorted(set(data.get('memory_ids', []) + data.get('writes', []))),
                memory_refs=[f'{self.graph[0].task_id if self.graph else "standalone"}:{data["scope"]}:{i}'
                             for i in sorted(set(data.get('memory_ids', []) + data.get('writes', [])))
                             if data.get('scope')],
                summary={k: v for k, v in data.items() if k not in ('task_id', 'operation_id')},
                classification=('agent-recommendation' if operation in
                    ('mitigation.recommendation', 'coordination.next_decision_selected') else 'executed-operation')))

            if operation.startswith(('data.', 'risk.', 'hmem.', 'mitigation.inspect', 'mitigation.evaluate', 'mitigation.reference')):
                if status in ('started', 'complete'):
                    self.event('coordination.tool_called' if status == 'started' else 'coordination.evidence_returned',
                               agent_id=role, tool_identifier=operation, source_operation_id=operation_id,
                               evidence_ids=data.get('evidence_ids', []), memory_ids=data.get('memory_ids', []))
            if operation in ('coordinator.mitigation_choice', 'coordinator.replanning', 'mitigation.recommendation'):
                self.event('coordination.next_decision_selected', agent_id=role,
                           source_operation_id=operation_id, decision=data)

    def launch(self, role, *, parent_id=None, dependencies=(), read_only=False, require_success=True):
        with self.lock:
            known = {t.task_id: t for t in self.graph}
            allowed = {'complete'} if require_success else {'complete', 'failed', 'timed_out'}
            if any(d not in known or known[d].status not in allowed for d in dependencies):
                raise ValueError('DEPENDENCIES_UNSATISFIED')
            task = ExecutionTask(task_id='task-' + uuid4().hex, agent_id=role,
                parent_id=parent_id, dependency_ids=list(dependencies), read_only=read_only,
                dependency_policy='success' if require_success else 'settled',
                launched_at=now())
            self.graph.append(task)
            self.event('coordination.dependencies_satisfied', agent_id=role, dependencies=list(dependencies), child_task_id=task.task_id)
            self.event('coordination.task_launched', agent_id=role, child_task_id=task.task_id)
            return task

    @contextmanager
    def running(self, task):
        previous = getattr(self.local, 'task_id', None)
        self.local.task_id = task.task_id
        with self.lock:
            if task.task_id in self.closed:
                self.local.task_id = previous
                raise ServiceError('TASK_TIMEOUT')
            task.started_at = now()
            task.status = 'running'
            self.event('coordination.specialist_started', agent_id=task.agent_id)
        try:
            yield task
        except Exception:
            self.finish(task, 'failed', 'SPECIALIST_FAILED')
            raise
        else:
            self.finish(task, 'complete')
        finally:
            self.local.task_id = previous

    def finish(self, task, status, error_code=None):
        with self.lock:
            if task.task_id in self.closed:
                return
            task.completed_at, task.status, task.error_code = now(), status, error_code
            previous = getattr(self.local, 'task_id', None)
            self.local.task_id = task.task_id
            self.event('coordination.specialist_completed', agent_id=task.agent_id,
                       result_status=status, error_code=error_code)
            self.local.task_id = previous
            self.closed.add(task.task_id)

    def reconcile(self, task, results):
        self.event('coordination.evidence_reconciled', agent_id='coordinator',
                   result_roles=[r.role for r in results], statuses=[r.status for r in results],
                   evidence_ids=sorted({i for r in results for f in r.findings for i in f.evidence_ids}),
                   snapshot_task_id=task.task_id)


def concurrent_reads(trace, jobs, *, parent_id=None, dependencies=(), max_workers=2, timeout=45, result_model=TaskResult):
    """Jobs must be isolated read-only callables. Ordered results; late events fenced.

    A timeout closes acceptance, not the OS thread. Never submit stateful jobs here.
    """
    if type(max_workers) is not int or not 1 <= max_workers <= 3 or not 0 < timeout <= 120:
        raise ValueError('Invalid execution limits')
    if len({role for role, _ in jobs}) != len(jobs):
        raise ValueError('Duplicate specialist')
    tasks = [trace.launch(role, parent_id=parent_id, dependencies=dependencies, read_only=True)
             for role, _ in jobs]

    def execute(task, function):
        try:
            with trace.running(task):
                try:
                    result = result_model.model_validate(function())
                    if result.role != task.agent_id:
                        raise ValueError('SPECIALIST_ROLE_MISMATCH')
                    if result.status == 'failed':
                        result = result.model_copy(update={'findings': [], 'calculations': []})
                except ServiceError as exc:
                    result = result_model(role=task.agent_id, status='failed', error_code=exc.code)
                except Exception:
                    # Isolate failures without persisting arbitrary exception text.
                    result = result_model(role=task.agent_id, status='failed', error_code='SPECIALIST_FAILED')
                with trace.lock:
                    # Publish the result and close its task atomically with timeout fencing.
                    trace.event('operator.' + task.agent_id, result=result.model_dump(mode='json'),
                        evidence_ids=[i for f in result.findings for i in f.evidence_ids], availability=result.status)
                    status = ('timed_out' if result.error_code == 'TASK_TIMEOUT' else 'failed') if result.status == 'failed' else 'complete'
                    trace.finish(task, status, result.error_code)
                return result
        except ServiceError:
            return result_model(role=task.agent_id, status='failed', error_code='TASK_TIMEOUT')

    pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix='pharmax-read')
    try:
        futures = [pool.submit(execute, task, function) for task, (_, function) in zip(tasks, jobs)]
        _, pending = wait(futures, timeout=timeout)
        results = []
        for task, future in zip(tasks, futures):
            with trace.lock:
                expired = future in pending and task.task_id not in trace.closed
                if expired:
                    trace.finish(task, 'timed_out', 'TASK_TIMEOUT')
            if expired:
                future.cancel()
                results.append(result_model(role=task.agent_id, status='failed', error_code='TASK_TIMEOUT'))
            else:
                results.append(future.result())
        return results, tasks
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
