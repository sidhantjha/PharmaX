from datetime import datetime, timezone
import json
import httpx
import pytest
from pydantic import ValidationError
from contracts import SourceEvidence, ToolRequest, DiagnosticReport
from phases.phase_01_foundation.http import ServiceError, request_json
from phases.phase_01_foundation.sources import (
    fetch_fda, fetch_comtrade, normalize_fda, normalize_comtrade, digest,
)
from phases.phase_01_foundation.zetaris import Zetaris
from phases.phase_01_foundation.nightshift import Nightshift, runtime_base
from phases.phase_01_foundation.runtime import JSONLTrace, Runtime
from phases.phase_01_foundation.terminal import diagnose

NOW = datetime(2026, 10, 23, tzinfo=timezone.utc)


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fda_dates_missing_fields_and_record_hash():
    row = {'generic_name': 'Example', 'package_ndc': 'example-001',
           'initial_posting_date': '07/14/2023', 'update_date': 'invalid'}
    result = normalize_fda([row], 'https://api.fda.gov/drug/shortages.json', NOW)[0]
    assert result.availability is None
    assert str(result.evidence.published_on) == '2023-07-14'
    assert result.evidence.updated_on is None
    assert result.evidence.version_available_at is None
    assert result.evidence.source_record_id == 'example-001'
    assert result.evidence.kind == 'observed'
    assert result.evidence.content_sha256 == digest(row)
    revised = normalize_fda([{**row, 'status': 'Resolved'}], str(result.evidence.source_url), NOW)[0]
    assert revised.record_id != result.record_id


def test_comtrade_preserves_codes_zero_and_unknown():
    row = {'typeCode': 'C', 'freqCode': 'A', 'cmdCode': '30', 'period': '2023',
           'classificationCode': 'H6', 'reporterCode': 842, 'partnerCode': 0,
           'flowCode': 'M', 'primaryValue': 0, 'isAggregate': True, 'isReported': False}
    result = normalize_comtrade([row], 'https://comtradeapi.un.org', NOW)[0]
    assert result.value_usd == 0 and result.partner_code == 0
    assert result.is_reported is False and result.is_aggregate is True
    assert result.evidence.published_on is None
    assert '842' in result.evidence.source_record_id
    row.pop('primaryValue')
    assert normalize_comtrade([row], 'https://comtradeapi.un.org', NOW)[0].value_usd is None


@pytest.mark.parametrize('fetch', [fetch_fda, fetch_comtrade])
def test_limit_enforced_before_network(fetch):
    def no_call(request):
        pytest.fail('No transport should run')
    with client(no_call) as c:
        for value in (0, 6, True):
            with pytest.raises(ValueError):
                fetch(c, value)


@pytest.mark.parametrize('code', [401, 403, 429, 500])
def test_live_errors_never_become_fixtures(code):
    with client(lambda r: httpx.Response(code, text='secret response body')) as c:
        rt = Runtime({'fda.sample': lambda n: fetch_fda(c, n)})
        result, rows = rt.execute(ToolRequest(request_id='x', name='fda.sample'))
    assert result.status == ('FAIL' if code == 500 else 'BLOCKED')
    assert rows == [] and result.mode == 'live'
    assert result.error_code == f'HTTP_{code}'
    assert 'secret response body' not in json.dumps([e.model_dump(mode='json') for e in rt.events])


def test_transport_response_bytes_and_invalid_json():
    with client(lambda r: httpx.Response(200, content=b'123456')) as c:
        with pytest.raises(ServiceError, match='RESPONSE_TOO_LARGE'):
            request_json(c, 'GET', 'https://example.org', max_bytes=5)
    with client(lambda r: httpx.Response(200, text='html')) as c:
        with pytest.raises(ServiceError, match='INVALID_JSON'):
            request_json(c, 'GET', 'https://example.org')


def test_source_response_bounds_and_query():
    def handler(req):
        assert req.url.params['limit'] == '2'
        return httpx.Response(200, json={'results': [{}, {}, {}]})
    with client(handler) as c:
        with pytest.raises(ServiceError, match='FDA_INVALID_RESPONSE'):
            fetch_fda(c, 2)
    def trade(req):
        assert req.url.params['cmdCode'] == '30'
        assert req.url.params['maxRecords'] == '3'
        return httpx.Response(200, json={'error': 'subscription required'})
    with client(trade) as c:
        with pytest.raises(ServiceError, match='COMTRADE_API_ERROR'):
            fetch_comtrade(c)


def test_zetaris_existing_proxy_auth_and_no_unapproved_sql(monkeypatch):
    monkeypatch.setenv('ZETARIS_ORG_ID', '1')
    monkeypatch.setenv('ZETARIS_API_KEY', 'test-only-token')
    calls = []
    def handler(req):
        calls.append(req)
        assert req.headers['X-Org-ID'] == '1'
        assert req.headers['Authorization'] == 'Bearer test-only-token'
        assert req.url.path == '/api/proxy/sql-editor/sqls/run-query'
        assert json.loads(req.content) == {'sql': 'SELECT 1 AS pharmax_connection_check', 'limit': 1}
        return httpx.Response(200, json={'headers': ['pharmax_connection_check'], 'data': [['1']]})
    with client(handler) as c:
        adapter = Zetaris(c)
        assert adapter.query() == [['1']]
        with pytest.raises(ServiceError, match='QUERY_NOT_APPROVED'):
            adapter.query('DROP TABLE old_data')
    assert len(calls) == 1


def test_zetaris_username_password_login(monkeypatch):
    monkeypatch.setenv('ZETARIS_ORG_ID', '1')
    monkeypatch.delenv('ZETARIS_API_KEY', raising=False)
    monkeypatch.setenv('ZETARIS_USERNAME', 'unit-user')
    monkeypatch.setenv('ZETARIS_PASSWORD', 'unit-password')
    def handler(req):
        if req.url.path == '/api/auth/login':
            assert json.loads(req.content)['username'] == 'unit-user'
            return httpx.Response(200, json={'idToken': 'unit-token'})
        assert req.headers['Authorization'] == 'Bearer unit-token'
        return httpx.Response(200, json={'data': [[1]]})
    with client(handler) as c:
        assert Zetaris(c).query() == [[1]]


def test_budget_reserved_before_failed_transport(tmp_path):
    calls = []
    def fail(n):
        calls.append(n)
        raise ServiceError('UNREACHABLE', blocked=True)
    rt = Runtime({'fda.sample': fail}, max_calls=1, trace=JSONLTrace(tmp_path/'events.jsonl'))
    req = ToolRequest(request_id='x', name='fda.sample')
    rt.execute(req)
    second, _ = rt.execute(req)
    assert calls == [3] and second.error_code == 'TOOL_CALL_BUDGET'
    assert len((tmp_path/'events.jsonl').read_text().splitlines()) == 3


def test_unknown_tool_and_extra_authority_rejected():
    for data in ({'request_id': 'x', 'name': 'shell.run'},
                 {'request_id': 'x', 'name': 'fda.sample', 'sql': 'DROP TABLE'}):
        with pytest.raises(ValidationError):
            ToolRequest.model_validate(data)


def test_evidence_rules():
    base = dict(evidence_id='a', kind='derived', source='model', source_url='https://example.org',
                observed_at=NOW, content_sha256='a'*64)
    with pytest.raises(ValidationError, match='parent'):
        SourceEvidence(**base)
    assert SourceEvidence(**base, parent_ids=['source:1']).kind == 'derived'
    with pytest.raises(ValidationError):
        SourceEvidence(**{**base, 'observed_at': datetime(2026, 10, 23)}, parent_ids=['s'])


@pytest.mark.parametrize('suffix', ['', '/v1', '/v1/models', '/v1/chat/completions', '/healthz'])
def test_runtime_base_normalizes(suffix):
    assert runtime_base('127.0.0.1:8088' + suffix) == 'http://127.0.0.1:8088/v1'


@pytest.mark.parametrize('value', ['http://user:secret@localhost:8088', 'http://remote.example',
                                  'http://localhost:8088/connect#k=secret', 'https://example.org/v1/v1'])
def test_runtime_rejects_unsafe_urls(value):
    with pytest.raises(ServiceError):
        runtime_base(value)


def test_mock_nightshift_structured_response_dispatch_trace_and_budget(monkeypatch, tmp_path, mock_authorization):
    monkeypatch.setenv('METERLESS_RUNTIME_TOKEN', 'unit-runtime-token')
    monkeypatch.setenv('METERLESS_MODEL', 'nvidia/nemotron-unit')
    monkeypatch.setenv('PHARMAX_MODEL_NO_COST', '1')
    calls = []
    def handler(req):
        assert req.headers['meterless-app'] == 'pharmax'
        assert req.headers['Authorization'] == 'Bearer unit-runtime-token'
        if req.url.path.endswith('/models'):
            return httpx.Response(200, json={'data': [{'id': 'nvidia/nemotron-unit'}]})
        calls.append(req)
        payload = json.loads(req.content)
        assert payload['max_tokens'] == 160 and payload['response_format']['type'] == 'json_schema'
        return httpx.Response(200, json={'model': 'nvidia/nemotron-unit', 'meterless': {'winning_route': {
            'model': 'nvidia/nemotron-unit', 'provider': 'nvidia'}}, 'choices': [{'message': {'content': json.dumps({
            'request_id': 'smoke', 'name': 'zetaris.connection_check', 'limit': 1})}}],
            'usage': {'total_tokens': 40, 'private_reasoning': 'never-log'}})
    with client(handler) as c:
        provider = Nightshift(c, authorization=mock_authorization)
        request, tokens = provider.smoke(provider.discover())
        rt = Runtime({'zetaris.connection_check': lambda n: [[1]]}, mode='mock',
                     trace=JSONLTrace(tmp_path/'trace.jsonl'))
        result, _ = rt.execute(request)
        assert result.status == 'PASS' and result.mode == 'mock' and tokens == {'total_tokens': 40}
        assert all(e.mode == 'mock' for e in rt.events)
        with pytest.raises(ServiceError, match='MODEL_CALL_BUDGET'):
            provider.smoke(['nvidia/nemotron-unit'])
    assert len(calls) == 1
    assert 'unit-runtime-token' not in (tmp_path/'trace.jsonl').read_text()


def test_no_cost_and_missing_token_block_without_transport(monkeypatch):
    monkeypatch.delenv('METERLESS_RUNTIME_TOKEN', raising=False)
    monkeypatch.delenv('PHARMAX_MODEL_NO_COST', raising=False)
    def forbidden(req):
        pytest.fail('Transport must not run')
    with client(forbidden) as c:
        p = Nightshift(c)
        with pytest.raises(ServiceError, match='NIGHTSHIFT_TOKEN_MISSING'):
            p.discover()
        with pytest.raises(ServiceError, match='NO_COST_ROUTE_UNCONFIRMED'):
            p.smoke([])


def test_offline_terminal_never_uses_network(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail('Offline mode used network')
    monkeypatch.setattr('httpx.Client', forbidden)
    report = diagnose(offline=True, output_dir=tmp_path)
    assert report.mode == 'fixture'
    assert len(report.shortages) == 1 and len(report.trade_flows) == 1
    assert all(r.evidence.kind == 'simulated' for r in report.shortages + report.trade_flows)
    assert all(e.mode == 'fixture' for e in report.events)
    assert DiagnosticReport.model_validate_json((tmp_path/'report.json').read_text()) == report


def test_raw_snapshot_hash_matches_provenance(tmp_path):
    row = {'generic_name': 'Fixture test', 'status': 'Current'}
    record = normalize_fda([row], 'https://api.fda.gov/drug/shortages.json', NOW, raw_dir=tmp_path)[0]
    from pathlib import Path
    stored = json.loads(Path(record.evidence.raw_ref).read_text())
    assert stored == row and digest(stored) == record.evidence.content_sha256


def test_exported_schemas_and_examples():
    from pathlib import Path
    from jsonschema import Draft202012Validator, FormatChecker
    from contracts import ToolResult
    root = Path(__file__).resolve().parents[3]
    for name, model in [('diagnostic', DiagnosticReport), ('tool-request', ToolRequest), ('tool-result', ToolResult)]:
        schema = json.loads((root/'contracts'/(name+'.schema.json')).read_text())
        assert schema == model.model_json_schema()
        example_name = 'diagnostic.fixture.json' if name == 'diagnostic' else name+'.json'
        example = json.loads((root/'contracts/examples'/example_name).read_text())
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(example)
        model.model_validate(example)


def test_nightshift_unauthorized_and_failure_consumes_inference_budget(monkeypatch, mock_authorization):
    monkeypatch.setenv('METERLESS_RUNTIME_TOKEN', 'unit-only')
    monkeypatch.setenv('METERLESS_MODEL', 'nvidia/nemotron-unit')
    monkeypatch.setenv('PHARMAX_MODEL_NO_COST', '1')
    calls = []
    def handler(req):
        calls.append(req)
        return httpx.Response(401)
    with client(handler) as c:
        p = Nightshift(c, authorization=mock_authorization)
        with pytest.raises(ServiceError, match='HTTP_401'):
            p.discover()
        with pytest.raises(ServiceError, match='HTTP_401'):
            p.smoke(['nvidia/nemotron-unit'])
        with pytest.raises(ServiceError, match='MODEL_CALL_BUDGET'):
            p.smoke(['nvidia/nemotron-unit'])
    assert len(calls) == 2


def test_model_cannot_increase_tool_authority(monkeypatch, mock_authorization):
    monkeypatch.setenv('METERLESS_RUNTIME_TOKEN', 'unit-only')
    monkeypatch.setenv('METERLESS_MODEL', 'nvidia/nemotron-unit')
    monkeypatch.setenv('PHARMAX_MODEL_NO_COST', '1')
    with client(lambda req: httpx.Response(200, json={'model': 'nvidia/nemotron-unit', 'meterless': {'winning_route': {
            'model': 'nvidia/nemotron-unit', 'provider': 'nvidia'}}, 'choices': [{'message': {
        'content': '{"request_id":"smoke","name":"fda.sample","limit":5}'}}]})) as c:
        with pytest.raises(ServiceError, match='MODEL_TOOL_SCOPE_DENIED'):
            Nightshift(c, authorization=mock_authorization).smoke(['nvidia/nemotron-unit'])
