"""Deterministic offline integration tests, including the actual upstream memory engine."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import time
from unittest.mock import Mock
import httpx
import pytest
from pydantic import ValidationError
from phases.phase_01_foundation.http import ServiceError
from phases.phase_02_data.pipeline import load_bundle
from phases.phase_02_data.store import local_query
from phases.phase_04_agents.coordinator import investigate
from phases.phase_04_agents.memory import Memory, eligible
from phases.phase_04_agents.models import InvestigationRequest, Plan, Review
from phases.phase_04_agents.reasoning import NemotronReasoner
from phases.phase_04_agents.specialists import finding
from phases.phase_04_agents.tools import InvestigationTools
from phases.phase_04_agents.trace import Trace

ROOT = Path(__file__).resolve().parents[3]
NODE = os.environ.get('PHARMAX_NODE', 'node')
MODEL = 'nvidia/nemotron-3.5-lightning-30b-a3b'


@pytest.fixture
def bundle():
    return load_bundle(ROOT / 'docs/evidence/pharmax-phase2/dataset.json')


@pytest.fixture
def request_model():
    return InvestigationRequest(session_id='test-session', objective='Investigate all supply consequences',
        evidence_as_of=datetime(2026, 10, 24, 23, 59, tzinfo=timezone.utc))


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    monkeypatch.setattr(httpx.HTTPTransport, 'handle_request', Mock(side_effect=AssertionError('NETWORK DENIED')))


def test_full_coordinator_and_reproducibility(bundle, request_model, tmp_path):
    original = bundle.model_dump_json()
    a = investigate(bundle, request_model, tmp_path / 'a', node=NODE)
    b = investigate(bundle, request_model, tmp_path / 'b', node=NODE)
    assert a.status == b.status == 'complete'
    assert a.findings == b.findings
    assert a.evidence == b.evidence
    assert a.plan.tasks == ['trade', 'disruption', 'hospital']
    assert a.review.followup == 'disruption_link'
    assert len(a.tasks) == 4
    assert a.conflicts == []
    assert {t.role for t in a.tasks} == {'trade', 'hospital', 'disruption'}
    calc = next(t for t in a.tasks if t.role == 'hospital').calculations
    values = {c['hospital_id']: (c['baseline']['forecast']['total_unmet'],
        c['disruption']['forecast']['total_unmet'], c['incremental_unmet']) for c in calc}
    assert values == {'sim-central': (20, 80, 60), 'sim-north': (0, 0, 0), 'sim-south': (40, 40, 0)}
    assert {m for m in a.missing} == {'manufacturing_link', 'national_denominator', 'prior_period'}
    assert a.memory and all(m['memory']['provenance']['refs'] for m in a.memory)
    assert a.historical_backtest is False
    assert bundle.model_dump_json() == original
    assert a.evidence and all(e.raw_ref is None for e in a.evidence)
    ids = {e.evidence_id for e in a.evidence}
    assert all(set(e.parent_ids) <= ids for e in a.evidence)
    assert all(set(f.evidence_ids) <= ids for f in a.findings)
    assert any(e.operation == 'hmem.exchange' and e.data.get('writes') for e in a.events)


def test_selective_dispatch(bundle, request_model, tmp_path):
    r = request_model.model_copy(update={'objective': 'trade only: assess country exposure'})
    result = investigate(bundle, r, tmp_path, node=NODE)
    assert result.status == 'complete'
    assert [t.role for t in result.tasks] == ['trade']


@pytest.mark.parametrize('bad', [dict(tasks=['trade', 'trade'], decision='trade_context'),
    dict(tasks=['sql'], decision='trade_context'), dict(tasks=['trade'], decision='send_order')])
def test_invalid_plan(bad):
    with pytest.raises(ValidationError):
        Plan.model_validate(bad)


def test_permission_and_pre_dispatch_budget(bundle, request_model):
    tools = InvestigationTools(bundle, request_model, Trace(), max_calls=1)
    with pytest.raises(ServiceError, match='TOOL_PERMISSION_DENIED'):
        tools.query('trade', {'collection': 'inventory'})
    with pytest.raises(ServiceError, match='TOOL_PERMISSION_DENIED'):
        tools.calculate('disruption', 'risk.scenario_comparison', {})
    with pytest.raises(ValidationError):
        tools.query('trade', {'collection': 'trade_flows', 'sql': 'DROP TABLE x'})
    assert tools.calls == 0
    tools.query('trade', {'collection': 'trade_flows'})
    with pytest.raises(ServiceError, match='TOOL_CALL_BUDGET'):
        tools.query('trade', {'collection': 'trade_flows'})
    assert tools.calls == 1


def test_coordinator_partial_failure(bundle, request_model, tmp_path):
    result = investigate(bundle, request_model, tmp_path, node=NODE, max_calls=1)
    assert result.status == 'incomplete'
    assert any(t.error_code == 'TOOL_CALL_BUDGET' for t in result.tasks)
    assert not result.error_code


def test_timeout_before_dispatch(bundle, request_model):
    tools = InvestigationTools(bundle, request_model, Trace(), timeout=0.001)
    time.sleep(0.005)
    with pytest.raises(ServiceError, match='TASK_TIMEOUT'):
        tools.query('trade', {'collection': 'trade_flows'})
    assert tools.calls == 0


def test_coordinator_timeout(bundle, request_model, tmp_path, monkeypatch):
    def slow(tools):
        time.sleep(0.05)
        tools.query('trade', {'collection': 'trade_flows'})
    monkeypatch.setitem(__import__('phases.phase_04_agents.coordinator', fromlist=['SPECIALISTS']).SPECIALISTS,
                        'trade', slow)
    req = request_model.model_copy(update={'objective': 'trade only'})
    result = investigate(bundle, req, tmp_path, node=NODE, task_timeout=0.005)
    assert result.status == 'incomplete'
    assert result.tasks[0].error_code == 'TASK_TIMEOUT'


def test_memory_persistence_conflicts_scope_and_cutoff(bundle, request_model, tmp_path):
    trace = Trace()
    memory = Memory(tmp_path, request_model, bundle, trace, node=NODE)
    tools = InvestigationTools(bundle, request_model, trace)
    ev = bundle.trade_flows[0].evidence.evidence_id
    first = finding(tools, 'trade.claim', 'Value is 10', 'derived', [ev])
    second = first.model_copy(update={'value': 'Value is 20'})
    written = memory.exchange('trade', [first])
    assert written['ledger_stats']['create'] == 1
    reopened = Memory(tmp_path, request_model, bundle, trace, node=NODE)
    assert len(reopened.exchange('trade')['result']['memories']) == 1
    result = reopened.exchange('trade', [second])
    assert result['conflicts'] and len(result['result']['memories']) == 2
    assert all(not m['memory'].get('supersededBy') for m in result['result']['memories'])
    again = reopened.exchange('trade', [second])
    assert again['ledger_stats']['create'] == 2
    other = Memory(tmp_path, request_model.model_copy(update={'session_id': 'other'}), bundle, trace, node=NODE)
    assert not other.exchange('trade')['result']['memories']
    old_request = request_model.model_copy(update={
        'evidence_as_of': datetime(2023, 7, 24, tzinfo=timezone.utc), 'historical': True})
    old = Memory(tmp_path, old_request, bundle, trace, node=NODE)
    assert not old.exchange('trade')['result']['memories']
    with pytest.raises(ValueError, match='MEMORY_EVIDENCE_UNAVAILABLE'):
        old.exchange('trade', [first])
    with pytest.raises(ValueError, match='MEMORY_SOURCE_MISMATCH'):
        memory.exchange('trade', [first.model_copy(update={'source_ids': ['invented']})])
    with pytest.raises(ValueError, match='MEMORY_EVIDENCE_UNAVAILABLE'):
        memory.exchange('trade', [first.model_copy(update={'evidence_ids': ['invented']})])


def test_recursive_historical_availability(bundle, request_model):
    registry = {e.evidence_id: e for e in bundle.evidence}
    event = next(e for e in bundle.evidence if e.kind == 'derived')
    cutoff = request_model.evidence_as_of
    assert eligible(event.evidence_id, registry, cutoff)
    parent = registry[event.parent_ids[0]]
    registry[parent.evidence_id] = parent.model_copy(update={'version_available_at': cutoff + timedelta(days=1)})
    assert not eligible(event.evidence_id, registry, cutoff)
    assert not eligible('unknown', registry, cutoff)
    registry[parent.evidence_id] = parent.model_copy(update={'parent_ids': [event.evidence_id]})
    assert not eligible(event.evidence_id, registry, cutoff)


def test_historical_investigation_excludes_revisions(bundle, request_model, tmp_path):
    req = request_model.model_copy(update={
        'evidence_as_of': datetime(2023, 7, 24, 23, 59, tzinfo=timezone.utc), 'historical': True})
    result = investigate(bundle, req, tmp_path, node=NODE)
    assert result.status == 'incomplete'
    assert next(t for t in result.tasks if t.role == 'trade').status == 'unavailable'
    assert all(e.kind == 'simulated' for e in result.evidence)
    assert any(e.data.get('excluded_evidence_ids') for e in result.events)
    assert not any('fda:' in i for f in result.findings for i in f.evidence_ids)


def test_fixture_mode_and_live_model_denial(request_model, tmp_path):
    bundle = load_bundle(ROOT / 'phases/phase_02_data/fixtures/bundle.json')
    req = request_model.model_copy(update={'mode': 'fixture'})
    result = investigate(bundle, req, tmp_path, node=NODE)
    assert result.status == 'incomplete'  # Earlier fixtures lack comparable HS30 query scope.
    assert next(t for t in result.tasks if t.role == 'trade').error_code == 'TRADE_SCOPE_UNAVAILABLE'
    assert all(e.kind == 'simulated' for e in result.evidence)
    fake = Mock(live=True)
    blocked = investigate(bundle, req, tmp_path, reasoner=fake, node=NODE)
    assert blocked.error_code == 'OFFLINE_MODEL_DENIED'
    fake.plan.assert_not_called()


def test_live_zetaris_typed_read_and_no_fallback(bundle, request_model, monkeypatch):
    req = request_model.model_copy(update={'mode': 'live-zetaris'})
    query = {'collection': 'trade_flows', 'limit': 100}
    mock = Mock(return_value=local_query(bundle, query, mode='live-zetaris'))
    monkeypatch.setattr('phases.phase_04_agents.tools.DataStore.query', mock)
    tools = InvestigationTools(bundle, req, Trace(), client=Mock())
    rows, _ = tools.query('trade', query)
    assert len(rows) == 2
    mock.side_effect = ServiceError('UNREACHABLE')
    with pytest.raises(ServiceError, match='UNREACHABLE'):
        tools.query('trade', query)
    mock.side_effect = None
    altered = local_query(bundle, query, mode='live-zetaris').model_copy(deep=True)
    altered.items[0].value_usd = 1
    mock.return_value = altered
    with pytest.raises(ServiceError, match='DATASET_READ_MISMATCH'):
        tools.query('trade', query)


def response_for(value, model=MODEL):
    return {'model': model, 'meterless': {'winning_route': {'provider': 'nvidia', 'model': model}},
        'choices': [{'finish_reason': 'tool_calls', 'message': {'tool_calls': [{'type': 'function',
            'function': {'name': 'investigation_decision', 'arguments': value.model_dump_json()}}]}}],
        'usage': {'prompt_tokens': 20, 'completion_tokens': 10, 'total_tokens': 30}}


def provider_env(monkeypatch):
    monkeypatch.setenv('METERLESS_BASE_URL', 'http://127.0.0.1:8088/v1')
    monkeypatch.setenv('METERLESS_MODEL', MODEL)
    monkeypatch.setenv('METERLESS_RUNTIME_TOKEN', 'unit-private-token')
    monkeypatch.setenv('PHARMAX_MODEL_NO_COST', '1')


def test_nemotron_coordinator_transport(bundle, request_model, tmp_path, monkeypatch, mock_authorization):
    provider_env(monkeypatch)
    responses = [Plan(tasks=['trade', 'disruption', 'hospital'], decision='supply_consequences'),
                 Review(followup='disruption_link', reason='missing_link')]
    payloads = []
    def handle(req):
        payload = json.loads(req.content)
        payloads.append(payload)
        assert req.headers['meterless-app'] == 'pharmax'
        assert payload['max_tokens'] == 300
        assert payload['parallel_tool_calls'] is False
        return httpx.Response(200, json=response_for(responses[len(payloads) - 1]))
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        provider = NemotronReasoner(client, authorization=mock_authorization if os.environ.get('PHARMAX_MODEL_NO_COST') else None)
        result = investigate(bundle, request_model, tmp_path, reasoner=provider, node=NODE)
        assert result.status == 'complete'
        assert provider.calls == 2
        with pytest.raises(ServiceError, match='MODEL_CALL_BUDGET'):
            provider.plan(request_model, '', Trace())
    assert len(payloads) == 2
    assert '[MEMORY CONTEXT]' in payloads[1]['messages'][1]['content']
    trace = json.dumps(result.model_dump(mode='json'))
    assert 'unit-private-token' not in trace
    assert 'reasoning_content' not in trace
    assert any(e.data.get('winning_route') for e in result.events)


@pytest.mark.parametrize('case', ['paid', 'timeout', 'identity', 'malformed', 'oversized'])
def test_provider_fail_closed(request_model, monkeypatch, case, mock_authorization):
    provider_env(monkeypatch)
    calls = []
    def handle(req):
        calls.append(req)
        if case == 'timeout':
            raise httpx.ReadTimeout('private upstream details')
        body = response_for(Plan(tasks=['trade'], decision='trade_context'),
                            model='nvidia/wrong-model' if case == 'identity' else MODEL)
        if case == 'malformed':
            body['choices'] = []
        if case == 'oversized':
            body['private'] = 'x' * 33000
        return httpx.Response(200, json=body)
    if case == 'paid':
        monkeypatch.delenv('PHARMAX_MODEL_NO_COST')
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        provider = NemotronReasoner(client, authorization=mock_authorization if os.environ.get('PHARMAX_MODEL_NO_COST') else None)
        with pytest.raises(ServiceError):
            provider.plan(request_model, '', Trace())
    assert len(calls) == (0 if case == 'paid' else 1)


def test_official_conformance():
    base = ROOT / 'phases/phase_04_agents/vendor/hmem'
    process = subprocess.run([NODE, '--experimental-transform-types', '--disable-warning=ExperimentalWarning',
        str(base / 'conformance/runner.ts')], env={**os.environ, 'HMEM_IMPL': str(base / 'src/index.ts')},
        capture_output=True, text=True, timeout=15)
    assert process.returncode == 0, process.stdout + process.stderr
    assert '13/13 checks passed' in process.stdout


def test_vendor_integrity():
    import hashlib
    base = ROOT / 'phases/phase_04_agents/vendor/hmem'
    manifest = json.loads((base / 'UPSTREAM.json').read_text())
    for name, expected in manifest['sha256'].items():
        assert hashlib.sha256((base / name).read_bytes()).hexdigest() == expected


def test_unjustified_followup_denied(bundle, request_model, tmp_path):
    from phases.phase_04_agents.reasoning import FixtureReasoner
    class BadReview(FixtureReasoner):
        def review(self, summary, context, trace):
            return Review(followup='disruption_link', reason='missing_link')
    req = request_model.model_copy(update={'objective': 'hospital only'})
    result = investigate(bundle, req, tmp_path, node=NODE, reasoner=BadReview())
    assert result.status == 'complete'
    assert len(result.tasks) == 1
    assert any(e.data.get('error_code') == 'FOLLOWUP_UNJUSTIFIED_OR_TIMEOUT' for e in result.events)


def test_memory_runtime_failure_is_structured(bundle, request_model, tmp_path):
    result = investigate(bundle, request_model, tmp_path, node='/nonexistent/pharmax-node')
    assert result.status == 'blocked'
    assert result.error_code == 'MEMORY_RUNTIME_UNAVAILABLE'


def test_future_calculation_denied_before_dispatch(bundle, request_model):
    req = request_model.model_copy(update={'evidence_as_of': datetime(2022, 1, 1, tzinfo=timezone.utc)})
    tools = InvestigationTools(bundle, req, Trace())
    with pytest.raises(ServiceError, match='CALCULATION_EVIDENCE_UNAVAILABLE'):
        tools.calculate('trade', 'risk.trade_exposure', {'context_as_of': '2022-01-01'})
    with pytest.raises(ServiceError, match='SCENARIO_AFTER_CUTOFF'):
        tools.calculate('hospital', 'risk.scenario_comparison', {'hospital_id': 'sim-central'})
    assert tools.calls == 0


def test_conflicts_reach_review_and_partial_evidence_survives(bundle, request_model, tmp_path):
    from phases.phase_04_agents.reasoning import FixtureReasoner
    req = request_model.model_copy(update={'objective': 'trade only'})
    tools = InvestigationTools(bundle, req, Trace())
    ev = bundle.trade_flows[0].evidence.evidence_id
    memory = Memory(tmp_path, req, bundle, Trace(), node=NODE)
    memory.exchange('trade', [finding(tools, 'trade.sample', 'Conflicting earlier claim', 'derived', [ev])])
    class FailingReview(FixtureReasoner):
        def review(self, summary, context, trace):
            assert '[UNRESOLVED MEMORY CONFLICTS]' in context
            raise ServiceError('HTTP_502')
    result = investigate(bundle, req, tmp_path, node=NODE, reasoner=FailingReview())
    assert result.status == 'blocked'
    assert result.error_code == 'HTTP_502'
    assert result.findings and result.evidence and result.conflicts
