"""Model-directed orchestration using explicit mocks; never live acceptance evidence."""
import json
import threading
import httpx
import pytest
from contracts.operator import CaseRequest
from phases.phase_01_foundation.http import ServiceError
from phases.phase_05_product.service import OperatorService
from phases.phase_05_product.live_specialists import ModelSession
from phases.phase_05_product.tests.test_model_chat import approval, response, MODEL, BASE


@pytest.fixture
def environment(monkeypatch):
    monkeypatch.setenv('METERLESS_BASE_URL', BASE)
    monkeypatch.setenv('METERLESS_MODEL', MODEL)
    monkeypatch.setenv('METERLESS_RUNTIME_TOKEN', 'SYNTHETIC-TEST-TOKEN')


class ModelTransport:
    def __init__(self, reverse=False, failure=None):
        self.reverse, self.failure = reverse, failure
        self.posts = []
        self.counts = {'verification': 0, 'dependency': 0, 'mitigation': 0}
        self.lock = threading.Lock()
        self.gate = threading.Barrier(2, timeout=10)

    def __call__(self, req):
        if req.method == 'GET':
            return httpx.Response(200, json={'data': [{'id': MODEL}]})
        payload = json.loads(req.content)
        prompt = payload['messages'][0]['content']
        role = next(r for r in self.counts if ('verification specialist' if r == 'verification' else
                    'dependency specialist' if r == 'dependency' else 'mitigation planning specialist') in prompt)
        with self.lock:
            self.counts[role] += 1
            turn = self.counts[role]
            self.posts.append((role, payload))
        assert payload['model'] == MODEL and payload['max_price'] == 0
        assert payload['max_tokens'] == 300 and payload['parallel_tool_calls'] is False
        assert 'models' not in payload
        if role != 'mitigation' and turn == 1:
            self.gate.wait()  # Two genuine concurrent role requests, even though provider responses are mocked.
        if self.failure and role == 'verification':
            if self.failure == 'timeout':
                raise httpx.ReadTimeout('DO-NOT-RETAIN-UPSTREAM-TEXT')
            return httpx.Response(int(self.failure), json={'error': {'code': 'provider_error',
                'meterless_detail': 'nvidia_429: rate limit exceeded DO-NOT-RETAIN-UPSTREAM-TEXT'}})
        offered = {t['function']['name'] for t in payload['tools']}
        context = json.loads(payload['messages'][-1]['content'])
        tools = [json.loads(m['content']) for m in payload['messages'] if m['role'] == 'tool']
        if role == 'verification':
            order = ['source_evidence', 'check_claims']
            if self.reverse:
                order.reverse()
            name = order[turn - 1] if turn <= 2 else 'assessment'
            args = {} if turn <= 2 else {'evidence_ids': tools[-1]['evidence_ids'][:2],
                                         'observations': ['advisory-only']}
        elif role == 'dependency':
            order = ['route_match', 'forecast_delay']
            if self.reverse:
                order.reverse()
            name = order[turn - 1] if turn <= 2 else 'assessment'
            args = {'delay_days': 7} if name == 'forecast_delay' else {} if turn <= 2 else {
                'evidence_ids': tools[-1]['evidence_ids'][:2], 'observations': ['simulated-route', 'conditional-forecast']}
        elif turn <= 4:
            name = 'evaluate_candidate'
            candidate = ['north-bulk', 'north-other-strength', 'lakes-additional', 'north-conservative'][turn - 1]
            if turn > 1:
                # Native tool results, not a scripted textual claim of model/tool use.
                assert tools[-1]['candidate_id'] == ['north-bulk', 'north-other-strength', 'lakes-additional'][turn - 2]
                assert tools[-1]['hmem']['memory_ids']
            if turn == 2:
                assert tools[-1]['feasible'] is False and 'DONOR_SHORTAGE' in tools[-1]['rejection_codes']
            args = {'candidate_id': candidate, 'reason': 'revise_after_rejection' if turn > 1 else 'investigate_supply'}
        else:
            name = 'review_mitigation'
            assert tools[-1]['candidate_id'] == 'north-conservative'
            assert tools[-1]['hmem']['memory_ids']
            args = {'candidate_id': context['deterministic_recommendation'],
                    'evaluation_ids': [e['evaluation_id'] for e in context['evaluations']],
                    'conclusion': 'feasible-for-human-review'}
        assert name in offered
        value = response()
        value['meterless']['receipt_id'] = f'mock-receipt-{role}-{turn}'
        call = value['choices'][0]['message']['tool_calls'][0]
        call['id'] = f'call_{role}_{turn}'
        call['function'] = {'name': name, 'arguments': json.dumps(args)}
        return httpx.Response(200, json=value)


@pytest.mark.parametrize('reverse', [False, True])
def test_existing_investigation_uses_role_prompts_native_results_memory_and_replans(tmp_path, environment, reverse):
    transport = ModelTransport(reverse=reverse)
    service = OperatorService(tmp_path, model_authorization=approval(),
        model_client_factory=lambda: httpx.Client(transport=httpx.MockTransport(transport)))
    case = service.create_live(CaseRequest())
    assert case.status == 'complete' and case.mode == 'retained-source-live-model'
    assert case.model_execution == 'nightshift'
    assert transport.counts == {'verification': 3, 'dependency': 3, 'mitigation': 5}
    assert len(transport.posts) == 11
    assert case.mitigation.recommendation_id == 'north-conservative'
    assert any(not e.feasible for e in case.mitigation.evaluations)
    assert any(e.feasible and e.remaining_unmet == 0 for e in case.mitigation.evaluations)
    for role in ('verification', 'dependency', 'mitigation'):
        agent = next(a for a in case.agents if a.agent_id == role)
        assert agent.execution == 'nightshift'
        posts = [p for r, p in transport.posts if r == role]
        assert any(m['role'] == 'tool' for m in posts[1]['messages'])
    assert any(d.operation == 'hmem.exchange' and d.memory_ids for d in case.decision_ledger)
    assert all(a.status == 'DRAFT' for a in case.actions)
    serialized = case.model_dump_json()
    assert 'PRIVATE-REASONING' not in serialized and 'UNTRUSTED-MODEL-PROSE' not in serialized
    assert 'SYNTHETIC-TEST-TOKEN' not in serialized
    assert service.store.get(case.investigation_id) == case
    attempts = list((tmp_path/'model-attempts').glob('*.json'))
    receipt = json.loads(attempts[0].read_text())
    assert receipt['model_requests'] == 11 and receipt['status'] == 'complete'
    assert receipt['requests_by_role'] == transport.counts
    selected = [d for d in receipt['decisions'] if d['operation'] == 'nightshift.specialist_decision' and d['status'] == 'complete']
    assert len(selected) == 11 and all(d['summary']['receipt_id'].startswith('mock-') for d in selected)


@pytest.mark.parametrize('failure', ['429', '402', '503', 'timeout'])
def test_failure_consumes_budget_without_retry_or_offline_substitute(tmp_path, environment, failure):
    transport = ModelTransport(failure=failure)
    service = OperatorService(tmp_path, model_authorization=approval(),
        model_client_factory=lambda: httpx.Client(transport=httpx.MockTransport(transport)))
    with pytest.raises(ServiceError):
        service.create_live(CaseRequest())
    assert transport.counts['verification'] == 1 and transport.counts['mitigation'] == 0
    assert service.store.summaries(10)[0] == 0
    receipt = json.loads(next((tmp_path/'model-attempts').glob('*.json')).read_text())
    assert receipt['status'] == 'blocked'
    assert 'DO-NOT-RETAIN' not in json.dumps(receipt)


def test_missing_authorization_blocks_before_client_or_catalog(tmp_path, environment):
    service = OperatorService(tmp_path, model_client_factory=lambda: pytest.fail('Unauthorized client'))
    with pytest.raises(ServiceError, match='NO_COST_ROUTE_UNCONFIRMED'):
        service.create_live(CaseRequest())
    assert not list((tmp_path/'model-attempts').glob('*.json'))


def test_request_budget_is_atomic_before_dispatch(environment):
    from phases.phase_05_product.execution import ExecutionTrace
    seen = []
    def transport(req):
        seen.append(req)
        return httpx.Response(429)
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        session = ModelSession(client, authorization=approval(), max_requests=1)
        session.ready = True
        from phases.phase_05_product.live_specialists import Empty
        with pytest.raises(ServiceError, match='HTTP_429'):
            session.select('verification', {'source_evidence': Empty}, {}, ExecutionTrace())
        with pytest.raises(ServiceError, match='MODEL_RUN_STOPPED'):
            session.select('dependency', {'route_match': Empty}, {}, ExecutionTrace())
        assert len(seen) == session.calls == 1


@pytest.mark.parametrize('kind', ['wrong-tool', 'wrong-citation', 'wrong-model', 'duplicate-call-id'])
def test_invalid_model_choices_never_complete_or_publish_a_case(tmp_path, environment, kind):
    original = ModelTransport()
    def invalid(req):
        result = original(req)
        if req.method != 'POST':
            return result
        payload = json.loads(req.content)
        if 'verification specialist' not in payload['messages'][0]['content']:
            return result
        value = result.json()
        call = value['choices'][0]['message']['tool_calls'][0]
        if kind == 'wrong-tool':
            call['function']['name'] = 'inventory_write'
        elif kind == 'wrong-model':
            value['model'] = 'nvidia/other'
        elif kind == 'wrong-citation' and original.counts['verification'] == 3:
            call['function']['arguments'] = json.dumps({'evidence_ids': ['UNTRUSTED-SECRET'], 'observations': ['advisory-only']})
        elif kind == 'duplicate-call-id':
            call['id'] = 'constant_call_id'
        return httpx.Response(200, json=value)
    service = OperatorService(tmp_path, model_authorization=approval(),
        model_client_factory=lambda: httpx.Client(transport=httpx.MockTransport(invalid)))
    with pytest.raises(ServiceError):
        service.create_live(CaseRequest())
    assert service.store.summaries(10)[0] == 0
    receipt = json.loads(next((tmp_path/'model-attempts').glob('*.json')).read_text())
    assert receipt['status'] == 'blocked' and 'UNTRUSTED-SECRET' not in json.dumps(receipt)


def test_timed_out_worker_cannot_dispatch_followup(environment):
    from phases.phase_05_product.execution import ExecutionTrace
    from phases.phase_05_product.live_specialists import Empty
    with httpx.Client(transport=httpx.MockTransport(lambda req: pytest.fail('Late dispatch'))) as client:
        session = ModelSession(client, authorization=approval())
        session.ready = True
        trace = ExecutionTrace()
        task = trace.launch('verification')
        trace.finish(task, 'timed_out', 'TASK_TIMEOUT')
        trace.local.task_id = task.task_id
        with pytest.raises(ServiceError, match='TASK_TIMEOUT'):
            session.select('verification', {'source_evidence': Empty}, {}, trace)
        assert session.calls == session.dispatches == 0
