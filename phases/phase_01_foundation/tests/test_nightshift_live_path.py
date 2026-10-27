"""Transport-mocked checks for the Phase 1.5 native tool boundary."""
import json

import httpx
import pytest

from phases.phase_01_foundation.http import ServiceError
from phases.phase_01_foundation.nightshift import Nightshift, runtime_base
from phases.phase_01_foundation.terminal import diagnose, render

MODEL = 'nvidia/nemotron-3.5-lightning-30b-a3b'


def response(*, model=MODEL, name='fda.sample', limit=1, count=1):
    return {'model': model, 'meterless': {'winning_route': {'model': model, 'provider': 'nvidia'}}, 'choices': [{'finish_reason': 'tool_calls', 'message': {
        'reasoning_content': 'private reasoning never retained', 'tool_calls': [{
            'type': 'function', 'function': {'name': 'pharmax_read', 'arguments': json.dumps({
                'request_id': 'model-read', 'name': name, 'limit': limit})}}] * count}}],
        'usage': {'total_tokens': 80, 'private': 'hidden'}}


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv('METERLESS_BASE_URL', 'http://192.168.10.11:8088/v1')
    monkeypatch.setenv('METERLESS_MODEL', MODEL)
    monkeypatch.setenv('METERLESS_RUNTIME_TOKEN', 'test-private-token')
    monkeypatch.delenv('PHARMAX_MODEL_NO_COST', raising=False)


@pytest.mark.parametrize('address', ['10.1.2.3', '172.16.1.2', '192.168.10.11'])
def test_explicit_private_lan_runtime(address):
    assert runtime_base(f'http://{address}:8088/v1') == f'http://{address}:8088/v1'


@pytest.mark.parametrize('address', ['http://8.8.8.8', 'http://169.254.169.254',
                                    'http://user:secret@192.168.10.11:8088/v1',
                                    'http://192.168.10.11:8088/v1?token=secret'])
def test_reject_public_or_credential_urls(address):
    with pytest.raises(ServiceError):
        runtime_base(address)


def test_native_call_budget_and_cost_gate(configured):
    seen = []
    def handler(req):
        seen.append(req)
        payload = json.loads(req.content)
        assert payload['max_tokens'] == 160
        assert payload['parallel_tool_calls'] is False
        assert payload['tool_choice']['function']['name'] == 'pharmax_read'
        return httpx.Response(200, json=response())
    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        provider = Nightshift(c)
        with pytest.raises(ServiceError, match='NO_COST_ROUTE_UNCONFIRMED'):
            provider.read([MODEL])
        assert seen == []
        req, tokens = provider.read([MODEL], allow_paid=True)
        assert req.name == 'fda.sample' and req.limit == 1
        assert tokens == {'total_tokens': 80} and provider.response_model == MODEL
        with pytest.raises(ServiceError, match='MODEL_CALL_BUDGET'):
            provider.read([MODEL], allow_paid=True)
    assert len(seen) == 1


@pytest.mark.parametrize('body,code', [
    (response(model='some-other-model'), 'MODEL_IDENTITY_UNVERIFIED'),
    (response(model=None), 'MODEL_IDENTITY_UNVERIFIED'),
    (response(count=2), 'MODEL_TOOL_CALL_INVALID'),
    (response(name='comtrade.sample'), 'MODEL_TOOL_SCOPE_DENIED'),
    (response(limit=5), 'MODEL_TOOL_SCOPE_DENIED'),
    ({'model': MODEL, 'choices': []}, 'MODEL_PROVIDER_UNVERIFIED'),
])
def test_fail_closed_before_tool_dispatch(configured, body, code):
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))) as c:
        p = Nightshift(c)
        with pytest.raises(ServiceError, match=code):
            p.read([MODEL], allow_paid=True)
        assert p.calls == 1


def test_native_transport_failure_consumes_budget(configured):
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401))) as c:
        p = Nightshift(c)
        with pytest.raises(ServiceError, match='HTTP_401'):
            p.read([MODEL], allow_paid=True)
        with pytest.raises(ServiceError, match='MODEL_CALL_BUDGET'):
            p.read([MODEL], allow_paid=True)


def test_terminal_native_dispatch_citations_and_safe_trace(configured, monkeypatch, tmp_path):
    monkeypatch.setenv('ZETARIS_ORG_ID', '1')
    monkeypatch.setenv('ZETARIS_API_KEY', 'test-zetaris-token')
    paths = []
    def handler(req):
        paths.append(req.url.path)
        if req.url.path == '/v1/models':
            return httpx.Response(200, json={'data': [{'id': MODEL}]})
        if req.url.path == '/v1/chat/completions':
            return httpx.Response(200, json=response())
        if req.url.path.endswith('/run-query'):
            return httpx.Response(200, json={'data': [[1]]})
        if req.url.host == 'api.fda.gov':
            return httpx.Response(200, json={'results': [{'generic_name': 'Example', 'status': 'Current'}]})
        return httpx.Response(503)
    monkeypatch.setattr('phases.phase_01_foundation.terminal.make_client',
                        lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    report = diagnose(model_smoke=True, allow_paid_model=True, output_dir=tmp_path)
    events = [e for e in report.events if e.request_id == 'model-read']
    assert [e.operation for e in events] == ['nightshift.tool_call', 'fda.sample.started', 'fda.sample']
    assert events[-1].evidence_ids == [report.shortages[0].evidence.evidence_id]
    output = render(report)
    assert 'backend-formatted' in output and MODEL in output and 'https://api.fda.gov' in output
    trace = (tmp_path / 'events.jsonl').read_text()
    assert 'private reasoning' not in trace and 'test-private-token' not in trace
    assert paths.count('/v1/chat/completions') == 1


def test_programmatic_offline_rejects_inference(tmp_path):
    with pytest.raises(ValueError, match='offline'):
        diagnose(offline=True, model_smoke=True, output_dir=tmp_path)


@pytest.mark.parametrize('code', [400, 401, 429, 500, 502, 503])
def test_legacy_two_attempt_parameter_cannot_retry(configured, code):
    calls, failures = [], []
    def handler(req):
        calls.append(req)
        return httpx.Response(code, json={'error': {'code': 'provider_error',
                              'meterless_detail': 'nvidia_503 private secret'}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        p = Nightshift(c)
        with pytest.raises(ServiceError, match=f'HTTP_{code}'):
            p.read([MODEL], allow_paid=True, max_attempts=2,
                   on_error=lambda e, n, retry: failures.append((n, retry)))
        with pytest.raises(ServiceError, match='MODEL_CALL_BUDGET'):
            p.read([MODEL], allow_paid=True, max_attempts=2)
    assert len(calls) == 1 and failures == [(1, False)]
    assert 'private secret' not in json.dumps(p.diagnostics)


@pytest.mark.parametrize('route', [{'provider': 'other', 'model': MODEL},
                                  {'provider': 'nvidia', 'model': None}])
def test_route_receipt_mismatch_never_dispatches_tool(configured, route):
    body = response()
    body['meterless'] = {'winning_route': route}
    with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=body))) as c:
        with pytest.raises(ServiceError, match='MODEL_PROVIDER_UNVERIFIED'):
            Nightshift(c).read([MODEL], allow_paid=True, max_attempts=2)


def test_layered_zetaris_file_preserves_process_and_meterless(monkeypatch, tmp_path):
    from phases.phase_01_foundation.config import load_env
    monkeypatch.setenv('METERLESS_RUNTIME_TOKEN', 'working-token')
    monkeypatch.setenv('ZETARIS_ORG_ID', '123')
    monkeypatch.delenv('ZETARIS_USERNAME', raising=False)
    private = tmp_path / 'selected.env'
    private.write_text('METERLESS_RUNTIME_TOKEN=overwrite\nZETARIS_ORG_ID=999\nZETARIS_USERNAME=test-user\n')
    load_env(str(private), prefix='ZETARIS_')
    import os
    assert os.environ['METERLESS_RUNTIME_TOKEN'] == 'working-token'
    assert os.environ['ZETARIS_ORG_ID'] == '123'
    assert os.environ['ZETARIS_USERNAME'] == 'test-user'


@pytest.mark.parametrize('upstream', [400, 401, 403, 404])
def test_permanent_upstream_wrapped_as_502_never_retried(configured, monkeypatch, upstream):
    calls = []
    def no_sleep(n):
        pytest.fail('Permanent upstream rejection must not back off or retry')
    def handler(req):
        calls.append(req)
        return httpx.Response(502, json={'error': {'code': 'provider_error',
                              'meterless_detail': f'nvidia_{upstream}: private message'}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        p = Nightshift(c)
        with pytest.raises(ServiceError, match='HTTP_502'):
            p.read([MODEL], allow_paid=True, max_attempts=2)
    assert len(calls) == 1


def test_error_inspection_response_cap(configured):
    body = {'error': {'code': 'provider_error', 'meterless_detail': 'nvidia_403:' + 'x' * 33000}}
    with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(502, json=body))) as c:
        p = Nightshift(c)
        with pytest.raises(ServiceError, match='HTTP_502') as exc:
            p.read([MODEL], allow_paid=True)
        assert exc.value.diagnostics == {}


@pytest.mark.parametrize('code', [403, 502, 503])
def test_single_attempt_authorization_never_retries(configured, monkeypatch, code):
    calls = []
    def no_sleep(n):
        pytest.fail('Single-attempt authorization cannot retry')
    def handler(req):
        calls.append(req)
        return httpx.Response(code, json={'error': {'code': 'provider_error',
                              'meterless_detail': 'nvidia_403'}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        p = Nightshift(c)
        with pytest.raises(ServiceError, match=f'HTTP_{code}'):
            p.read([MODEL], allow_paid=True, max_attempts=1)
        with pytest.raises(ServiceError, match='MODEL_CALL_BUDGET'):
            p.read([MODEL], allow_paid=True, max_attempts=1)
    assert len(calls) == 1


def test_successful_single_attempt_validates_routing_metadata(configured):
    body = response()
    body['meterless'] = {'winning_route': {'provider': 'nvidia', 'model': MODEL},
                         'receipt': {'private_reasoning': 'discard me'}}
    body['usage'] = {'prompt_tokens': 472, 'completion_tokens': 28, 'total_tokens': 500}
    seen = []
    def handler(req):
        seen.append(req)
        return httpx.Response(200, json=body)
    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        p = Nightshift(c)
        request, usage = p.read([MODEL], allow_paid=True, max_attempts=1)
        assert p.response_provider == 'nvidia' and p.response_model == MODEL
        assert request.model_dump() == {'request_id': 'model-read', 'name': 'fda.sample', 'limit': 1}
        assert usage == {'prompt_tokens': 472, 'completion_tokens': 28, 'total_tokens': 500}
        assert p.calls == 1 and p.diagnostics == []
    assert len(seen) == 1
