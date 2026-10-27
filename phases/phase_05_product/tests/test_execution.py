"""Execution provenance, synchronization-based overlap and failure containment."""
import threading
import pytest
from contracts.operator import CaseRequest, OperatorCase, ApiResponse
from phases.phase_04_agents.models import TaskResult
from phases.phase_05_product.execution import ExecutionTrace, concurrent_reads
from phases.phase_05_product.service import OperatorService
from phases.phase_05_product import integration


def test_real_specialists_overlap_at_synchronization_gate(tmp_path, monkeypatch):
    gate = threading.Barrier(2, timeout=10)
    entered, crossed = set(), set()
    lock = threading.Lock()

    def instrument(function):
        def execute(tools):
            with lock:
                entered.add(threading.get_ident())
            gate.wait()  # Neither investigation can proceed until both workers are executing.
            with lock:
                assert len(entered) == 2
                crossed.add(threading.get_ident())
            return function(tools)
        return execute

    monkeypatch.setattr(integration, 'trade', instrument(integration.trade))
    monkeypatch.setattr(integration, 'disruption', instrument(integration.disruption))
    service = OperatorService(tmp_path)
    case = service.create(CaseRequest())
    assert case.status == 'complete' and len(crossed) == 2
    reads = {t.agent_id: t for t in case.execution_graph if t.read_only}
    left, right = reads['trade'], reads['disruption']
    assert max(left.started_at, right.started_at) < min(left.completed_at, right.completed_at)
    assert [t['role'] for t in case.context_findings] == ['trade', 'disruption']
    operations = {e.operation for e in case.decision_ledger}
    assert {'coordination.task_launched', 'coordination.specialist_started', 'coordination.tool_called',
            'coordination.evidence_returned', 'coordination.specialist_completed',
            'coordination.dependencies_satisfied', 'coordination.evidence_reconciled',
            'coordination.next_decision_selected'} <= operations
    assert service.store.get(case.investigation_id) == case
    known = {t.task_id: t for t in case.execution_graph}
    for task in case.execution_graph:
        assert task.started_at <= task.completed_at
        assert task.parent_id is None or task.parent_id in known
        for dependency in task.dependency_ids:
            assert known[dependency].completed_at <= task.started_at
    records = {e.sequence: e for e in case.decision_ledger}
    assert len(records) == len(case.decision_ledger)
    starts = {e.operation_id: e for e in records.values() if e.status == 'started'}
    for record in records.values():
        if record.status == 'complete' and record.operation_id in starts:
            assert record.started_at == starts[record.operation_id].executed_at
            assert record.completed_at >= record.started_at
    assert all(e.task_id in known for e in records.values() if e.task_id)
    assert any(e.memory_ids for e in records.values())
    for endpoint, field in [('execution-graph', 'execution_graph'), ('decision-ledger', 'decision_ledger')]:
        status, body = service.handle('GET', f'/api/v1/investigations/{case.investigation_id}/{endpoint}?limit=100')
        assert status == 200 and body['total'] == len(getattr(case, field))
        ApiResponse.model_validate(body)
    from phases.phase_05_product.terminal import render
    assert 'OBSERVED OVERLAP trade' in render(case)


def test_failure_isolated_and_no_failed_findings_published(tmp_path, monkeypatch):
    def failed(tools):
        raise RuntimeError('private error text must never be retained')
    monkeypatch.setattr(integration, 'trade', failed)
    case = OperatorService(tmp_path).create(CaseRequest())
    assert case.status == 'incomplete' and case.mitigation.status == 'complete'
    assert case.context_findings[0]['status'] == 'failed'
    assert not case.context_findings[0]['findings']
    assert case.context_findings[1]['status'] == 'complete'
    assert 'private error text' not in case.model_dump_json()
    assert next(t for t in case.execution_graph if t.agent_id == 'trade').status == 'failed'


def test_completion_order_does_not_change_result_order():
    released = threading.Event()
    completed = []
    trace = ExecutionTrace()

    def trade():
        assert released.wait(5)
        completed.append('trade')
        return TaskResult(role='trade', status='complete')

    def disruption():
        completed.append('disruption')
        released.set()
        return TaskResult(role='disruption', status='complete')

    results, _ = concurrent_reads(trace, [('trade', trade), ('disruption', disruption)])
    assert completed == ['disruption', 'trade']
    assert [r.role for r in results] == ['trade', 'disruption']


def test_timeout_fences_late_worker_events_and_preserves_other_result():
    entered, release, exited, returned = (threading.Event() for _ in range(4))
    trace = ExecutionTrace()
    captured = []

    def blocked():
        entered.set()
        try:
            assert release.wait(5)
            trace.event('late.private_result', evidence_ids=['must-not-be-published'])
            return TaskResult(role='trade', status='complete')
        finally:
            exited.set()

    def run():
        captured.extend(concurrent_reads(trace, [('trade', blocked),
            ('disruption', lambda: TaskResult(role='disruption', status='complete'))], timeout=0.1))
        returned.set()

    runner = threading.Thread(target=run)
    runner.start()
    try:
        assert entered.wait(5) and returned.wait(5)
        results, tasks = captured
        assert results[0].error_code == 'TASK_TIMEOUT' and results[1].status == 'complete'
        assert tasks[0].status == 'timed_out'
        count = len(trace.decisions)
    finally:
        release.set()
        runner.join(5)
    assert exited.wait(5)
    assert len(trace.decisions) == count
    assert not any(e.operation == 'late.private_result' for e in trace.decisions)


def test_limits_and_dependency_gate_before_dispatch():
    trace = ExecutionTrace()
    with pytest.raises(ValueError, match='limits'):
        concurrent_reads(trace, [], max_workers=4)
    with pytest.raises(ValueError, match='UNSATISFIED'):
        trace.launch('trade', dependencies=['absent'])
    assert not trace.graph
    parent = trace.launch('coordinator')
    with trace.running(parent):
        with pytest.raises(ValueError, match='UNSATISFIED'):
            trace.launch('trade', dependencies=[parent.task_id])


def test_repeated_runs_have_isolated_memory_and_stable_calculations(tmp_path):
    service = OperatorService(tmp_path)
    first = service.create(CaseRequest())
    second = service.create(CaseRequest(parent_id=first.investigation_id))
    assert first.investigation_id != second.investigation_id
    assert first.context_findings == second.context_findings
    assert first.mitigation.recommendation_id == second.mitigation.recommendation_id
    def scopes(case):
        return {e.summary['scope'] for e in case.decision_ledger if e.operation == 'hmem.exchange' and e.status == 'started'}
    # Signal scopes are intentionally content-addressed; physical roots are case-local.
    assert scopes(first) != scopes(second)
    for case in (first, second):
        assert any((tmp_path/'memory'/case.investigation_id).rglob('*.json'))
    def memory_ids(case):
        return {i for e in case.decision_ledger for i in e.memory_refs}
    assert memory_ids(first).isdisjoint(memory_ids(second))
    for case in (first, second):
        ids = [(e.summary['scope'], i) for e in case.decision_ledger if e.operation == 'hmem.exchange' for i in e.summary.get('writes', [])]
        assert len(ids) == len(set(ids))
        writes = [e for e in case.decision_ledger if e.operation == 'hmem.exchange' and e.status == 'complete']
        assert all(a.completed_at <= b.started_at for a, b in zip(writes, writes[1:]))
    assert service.store.get(first.investigation_id) == first
    historical = first.model_dump()
    historical.pop('execution_graph')
    historical.pop('decision_ledger')
    assert OperatorCase.model_validate(historical).execution_graph == []


def test_saved_pre_5b_snapshot_hash_remains_readable(tmp_path):
    import json
    from phases.phase_01_foundation.sources import digest
    from phases.phase_05_product.store import CaseStore
    case = OperatorService(tmp_path/'new').create(CaseRequest(delay_days=0))
    payload = case.model_dump(mode='json', exclude={'execution_graph', 'decision_ledger'})
    legacy = CaseStore(tmp_path/'legacy.sqlite')
    with legacy.connect() as db:
        db.execute('INSERT INTO cases VALUES (?,?,?,?)',
                   (case.investigation_id, case.created_at.isoformat(), json.dumps(payload), digest(payload)))
    assert legacy.get(case.investigation_id).execution_graph == []
    with legacy.connect() as db:
        db.execute('INSERT INTO cases VALUES (?,?,?,?)', ('tampered', 'now', json.dumps(payload), 'invalid'))
    with pytest.raises(ValueError, match='INTEGRITY'):
        legacy.get('tampered')


def test_pool_bound_and_fixed_call_allocations(tmp_path, monkeypatch):
    active, peak = 0, 0
    lock = threading.Lock()
    gate = threading.Barrier(2, timeout=10)
    allocations = {}
    def instrument(role, function):
        def read(tools):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                allocations[role] = tools.max_calls
            try:
                gate.wait()
                return function(tools)
            finally:
                with lock:
                    active -= 1
        return read
    monkeypatch.setattr(integration, 'trade', instrument('trade', integration.trade))
    monkeypatch.setattr(integration, 'disruption', instrument('disruption', integration.disruption))
    case = OperatorService(tmp_path).create(CaseRequest())
    assert peak == 2 and active == 0 and case.status == 'complete'
    assert allocations == {'trade': 2, 'disruption': 3}
