"""Mocked end-to-end model selection -> actual immutable case tool -> cited result.

All responses/authorization here are synthetic. This is not live inference proof.
"""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import httpx
import pytest
from contracts.operator import CaseRequest, ChatRequest
from phases.phase_01_foundation.http import ServiceError, request_json
from phases.phase_01_foundation.inference_policy import InferenceAuthorization
from phases.phase_01_foundation.nightshift import Nightshift, runtime_base
from phases.phase_01_foundation.runtime_diagnostics import diagnose
from phases.phase_05_product.chat import answer
from phases.phase_05_product.model_chat import CaseReadReasoner
from phases.phase_05_product.service import OperatorService

MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b"
BASE = "http://127.0.0.1:8088/v1"


def approval():
    return InferenceAuthorization(
        BASE,
        MODEL,
        "nvidia",
        datetime.now(timezone.utc) + timedelta(minutes=5),
        ("mock://reviewed-synthetic-policy-not-live-authorization",),
        True,
        True,
        True,
    )


def response(view="forecast"):
    return {
        "model": MODEL,
        "meterless": {"winning_route": {"model": MODEL, "provider": "nvidia"}},
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "reasoning_content": "PRIVATE-REASONING-DO-NOT-RETAIN",
                    "content": "UNTRUSTED-MODEL-PROSE",
                    "tool_calls": [
                        {
                            "id": "call_mock_selection",
                            "type": "function",
                            "function": {
                                "name": "pharmax_case_read",
                                "arguments": json.dumps({"view": view}),
                            },
                        }
                    ],
                },
            }
        ],
        "usage": {"total_tokens": 42, "private": "SECRET-METADATA"},
    }


@pytest.fixture
def environment(monkeypatch):
    monkeypatch.setenv("METERLESS_BASE_URL", BASE)
    monkeypatch.setenv("METERLESS_MODEL", MODEL)
    monkeypatch.setenv("METERLESS_RUNTIME_TOKEN", "SYNTHETIC-TEST-TOKEN")
    monkeypatch.setenv("PHARMAX_MODEL_NO_COST", "1")


@pytest.fixture(scope="module")
def saved(tmp_path_factory):
    service = OperatorService(tmp_path_factory.mktemp("mock-model-chat"))
    case = service.create(CaseRequest())
    return service, case


def wire(service, handler, *, authorized=True):
    service.model_authorization = approval() if authorized else None
    service.model_client_factory = lambda: httpx.Client(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("view", ["forecast", "evidence"])
def test_selection_executes_actual_tool_and_retains_citations(saved, environment, view, monkeypatch):
    service, case = saved
    before = service.store.snapshot_hash(case.investigation_id)
    calls = []
    invoked = []
    from phases.phase_05_product import model_chat

    original = model_chat.execute_read

    def read(selected_case, selection):
        invoked.append((selected_case.investigation_id, selection.view))
        return original(selected_case, selection)

    monkeypatch.setattr(model_chat, "execute_read", read)

    def handler(req):
        calls.append(req)
        if req.method == "GET":
            return httpx.Response(200, json={"data": [{"id": MODEL}]})
        payload = json.loads(req.content)
        assert payload["max_tokens"] == 160 and payload["max_price"] == 0
        assert payload["tool_choice"] == "required" and payload["parallel_tool_calls"] is False
        assert payload["tools"][0]["function"]["parameters"]["properties"]["view"]["enum"] == [
            "evidence",
            "forecast",
        ]
        assert req.headers["meterless-tier-split"] == "off"
        assert req.extensions["timeout"]["read"] == 30
        return httpx.Response(200, json=response(view))

    wire(service, handler)
    result = answer(
        service,
        ChatRequest(
            question="Which source supports the risk?",
            investigation_id=case.investigation_id,
            execution_mode="live",
        ),
    )
    assert result.execution_state == "live-model-tool-execution"
    assert result.classification == "live-model-tool-response"
    assert invoked == [(case.investigation_id, view)]
    assert result.decisions[0]["tool"] == view + ".read"
    assert result.decisions[0]["arguments"] == {"investigation_id": case.investigation_id}
    assert result.citations == case.evidence and result.decisions[0]["evidence_ids"]
    assert result.decisions[0]["result"] and len(calls) == 2
    if view == "forecast":
        central = [
            r["unmet_vials"] for r in result.decisions[0]["result"] if r["hospital_id"] == "sim-central"
        ]
        assert central == [0, 24, 0]
        assert "24 unmet vials" in result.answer
    assert service.store.snapshot_hash(case.investigation_id) == before
    serialized = result.model_dump_json()
    for private in ("PRIVATE-REASONING", "UNTRUSTED-MODEL-PROSE", "SECRET-METADATA", "SYNTHETIC-TEST-TOKEN"):
        assert private not in serialized
    with service.store.connect() as db:
        retained = db.execute("SELECT response FROM chat ORDER BY id DESC LIMIT 1").fetchone()[0]
    assert retained == serialized


def test_env_flag_and_zero_catalog_do_not_authorize(saved, environment):
    service, case = saved
    wire(service, lambda req: pytest.fail("Unauthorized inference must not even discover"), authorized=False)
    result = answer(
        service,
        ChatRequest(question="Show risk", investigation_id=case.investigation_id, execution_mode="live"),
    )
    assert result.execution_state == "inference-not-authorized"
    assert result.error_code == "NO_COST_ROUTE_UNCONFIRMED" and not result.decisions
    with httpx.Client(transport=httpx.MockTransport(lambda req: pytest.fail("No dispatch"))) as client:
        provider = Nightshift(client)
        with pytest.raises(ServiceError, match="NO_COST_ROUTE_UNCONFIRMED"):
            provider.read([MODEL])


@pytest.mark.parametrize(
    "field,value",
    [
        ("model", "nvidia/other"),
        ("endpoint", "http://other/v1"),
        ("provider", "other"),
        ("expires_at", datetime(2000, 1, 1, tzinfo=timezone.utc)),
        ("expires_at", datetime(2030, 1, 1)),
        ("evidence_refs", ()),
        ("no_charge_confirmed", False),
        ("runtime_single_attempt", False),
        ("runtime_external_tools_disabled", False),
    ],
)
def test_authorization_is_expiring_route_bound_and_requires_all_controls(environment, field, value):
    with pytest.raises(ServiceError, match="NO_COST_ROUTE_UNCONFIRMED"):
        replace(approval(), **{field: value}).require(BASE, MODEL)


@pytest.mark.parametrize(
    "failure",
    [
        "timeout",
        "connect",
        "401",
        "403",
        "429",
        "502",
        "503",
        "wrong-model",
        "wrong-provider",
        "no-receipt",
        "wrong-tool",
        "bad-args",
        "extra-args",
        "duplicate-args",
        "two-calls",
        "two-choices",
        "no-calls",
        "truncated",
        "refusal",
        "oversized",
        "invalid-json",
        "non-object",
        "bad-message",
    ],
)
def test_failed_response_isolated_no_tool_retry_or_fallback(saved, environment, monkeypatch, failure):
    service, case = saved
    posts = []
    monkeypatch.setattr(
        "phases.phase_05_product.model_chat.execute_read",
        lambda *args: pytest.fail("Invalid response must not execute a tool"),
    )

    def handler(req):
        if req.method == "GET":
            return httpx.Response(200, json={"data": [{"id": MODEL}]})
        posts.append(req)
        if failure == "timeout":
            raise httpx.ReadTimeout("PRIVATE-UPSTREAM-ERROR")
        if failure == "connect":
            raise httpx.ConnectError("PRIVATE-UPSTREAM-ERROR")
        if failure.isdigit():
            return httpx.Response(int(failure), text="PRIVATE-UPSTREAM-ERROR")
        if failure == "invalid-json":
            return httpx.Response(200, text="PRIVATE-UPSTREAM-ERROR")
        body = response()
        choice = body["choices"][0]
        call = choice["message"]["tool_calls"][0]
        if failure == "wrong-model":
            body["model"] = "nvidia/other"
        if failure == "wrong-provider":
            body["meterless"]["winning_route"]["provider"] = "other"
        if failure == "no-receipt":
            body.pop("meterless")
        if failure == "wrong-tool":
            call["function"]["name"] = "inventory.write"
        if failure == "bad-args":
            call["function"]["arguments"] = '{"view":"write"}'
        if failure == "extra-args":
            call["function"]["arguments"] = '{"view":"forecast","url":"http://untrusted"}'
        if failure == "duplicate-args":
            call["function"]["arguments"] = '{"view":"evidence","view":"forecast"}'
        if failure == "two-calls":
            choice["message"]["tool_calls"].append(call)
        if failure == "two-choices":
            body["choices"].append(choice)
        if failure == "no-calls":
            choice["message"]["tool_calls"] = []
        if failure == "truncated":
            choice["finish_reason"] = "length"
        if failure == "refusal":
            choice["message"]["refusal"] = "PRIVATE-UPSTREAM-ERROR"
        if failure == "oversized":
            body["private"] = "x" * 33000
        if failure == "non-object":
            body = []
        if failure == "bad-message":
            choice["message"] = []
        return httpx.Response(200, json=body)

    wire(service, handler)
    result = answer(
        service,
        ChatRequest(question="Show risk", investigation_id=case.investigation_id, execution_mode="live"),
    )
    assert result.execution_state == "model-unavailable" and result.error_code
    assert not result.decisions and not result.citations and len(posts) == 1
    assert "PRIVATE-UPSTREAM-ERROR" not in result.model_dump_json()


def test_offline_default_never_constructs_model_client(saved, environment):
    service, case = saved
    service.model_client_factory = lambda: pytest.fail("Offline replay cannot contact a provider")
    result = answer(
        service, ChatRequest(question="Show inventory risk", investigation_id=case.investigation_id)
    )
    assert result.execution_state == "deterministic-offline-replay"
    assert result.classification == "deterministic-tool-response"
    assert "24 unmet vials" in result.answer


def test_missing_saved_case_blocks_before_model_client(saved, environment):
    service, _ = saved
    service.model_authorization = approval()
    service.model_client_factory = lambda: pytest.fail("No implicit new case or model call")
    result = answer(service, ChatRequest(question="Show risk", execution_mode="live"))
    assert result.error_code == "SAVED_CASE_REQUIRED"


def test_single_provider_instance_cannot_dispatch_again(environment):
    seen = []

    def handler(req):
        seen.append(req)
        if req.method == "GET":
            return httpx.Response(200, json={"data": [{"id": MODEL}]})
        raise httpx.ReadTimeout("private")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = CaseReadReasoner(client, authorization=approval())
        with pytest.raises(ServiceError, match="TIMEOUT"):
            provider.select("Show risk")
        with pytest.raises(ServiceError, match="MODEL_CALL_BUDGET"):
            provider.select("Show risk")
    assert len(seen) == 2


def test_bounded_stream_deadline_without_wall_clock_assumptions(monkeypatch):
    ticks = iter([0, 1, 61])
    monkeypatch.setattr("phases.phase_01_foundation.http.monotonic", lambda: next(ticks))

    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            yield b"{"
            yield b"}"

    with httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, stream=Stream()))
    ) as client:
        with pytest.raises(ServiceError, match="TIMEOUT"):
            request_json(client, "GET", BASE, deadline_seconds=60)


def test_diagnostics_only_gets_closed_metadata(environment, monkeypatch):
    from contextlib import nullcontext

    monkeypatch.setattr("socket.create_connection", lambda *args, **kwargs: nullcontext())
    seen = []

    def handler(req):
        assert req.method == "GET"
        seen.append(req)
        if req.url.path == "/healthz":
            assert "authorization" not in req.headers
            return httpx.Response(200, json={"status": "ok", "private": "DO-NOT-LOG"})
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": MODEL,
                        "private": "DO-NOT-LOG",
                        "meterless": {
                            "price": {"input_per_m": 0, "output_per_m": 0, "private": "DO-NOT-LOG"}
                        },
                    }
                ]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = diagnose(client=client)
    assert result["checks"]["catalog"]["selected_present"]
    assert result["checks"]["catalog"]["catalog_price_per_million"] == {"input_per_m": 0, "output_per_m": 0}
    assert result["inference_authorization"]["status"] == "not-authorized"
    assert result["inference_posts"] == 0 and len(seen) == 2
    assert "DO-NOT-LOG" not in json.dumps(result) and "SYNTHETIC-TEST-TOKEN" not in json.dumps(result)


def test_explicit_current_windows_cgnat_origin_only():
    assert runtime_base("100.64.0.2:8088") == "http://100.64.0.2:8088/v1"
    for bad in ("http://100.128.0.1:8088", "http://user:secret@100.64.0.2:8088"):
        with pytest.raises(ServiceError):
            runtime_base(bad)


@pytest.mark.parametrize('code', [401, 403, 404, 429, 500, 503])
def test_catalog_rejections_never_reach_inference(saved, environment, code):
    service, case = saved
    calls = []
    def handler(req):
        calls.append(req)
        assert req.method == 'GET'
        return httpx.Response(code, text='PRIVATE-CATALOG-ERROR')
    wire(service, handler)
    result = answer(service, ChatRequest(question='Show risk', investigation_id=case.investigation_id,
                                        execution_mode='live'))
    assert result.error_code == f'HTTP_{code}' and len(calls) == 1
    assert result.execution_state == 'model-unavailable'
    assert 'PRIVATE-CATALOG-ERROR' not in result.model_dump_json()


@pytest.mark.parametrize('case', ['timeout', 'connect', '401', '403', 'bad-catalog', 'bad-health'])
def test_safe_diagnostics_reports_failed_layer(environment, monkeypatch, case):
    def tcp(*args, **kwargs):
        assert kwargs['timeout'] == 2
        if case == 'timeout':
            raise TimeoutError('SECRET-TCP-DETAIL')
        raise ConnectionRefusedError('SECRET-TCP-DETAIL')
    monkeypatch.setattr('socket.create_connection', tcp)
    def handler(req):
        assert req.method == 'GET'
        assert req.extensions['timeout']['read'] == 5
        assert req.extensions['timeout']['connect'] == 2
        if case == 'timeout':
            raise httpx.ReadTimeout('SECRET-HTTP-DETAIL')
        if case == 'connect':
            raise httpx.ConnectError('SECRET-HTTP-DETAIL')
        if req.url.path == '/healthz':
            return httpx.Response(200, json={'status': 'starting' if case == 'bad-health' else 'ok'})
        if case.isdigit():
            return httpx.Response(int(case), text='SECRET-HTTP-DETAIL')
        return httpx.Response(200, json={'data': None} if case == 'bad-catalog' else {'data': []})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = diagnose(client=client)
    assert result['checks']['tcp']['status'] == 'blocked'
    if case == 'bad-health':
        assert result['checks']['health']['code'] == 'RUNTIME_NOT_READY'
    else:
        assert result['checks']['catalog']['status'] == 'blocked'
    assert result['inference_posts'] == 0 and 'SECRET-' not in json.dumps(result)


def test_offline_metadata_timeout_cannot_break_replay(monkeypatch):
    import subprocess
    from phases.phase_01_foundation.terminal import repository_status
    def timeout(*args, **kwargs):
        assert args[0][:2] == ['git', '--no-optional-locks']
        raise subprocess.TimeoutExpired('git', 10)
    monkeypatch.setattr('subprocess.run', timeout)
    result = repository_status()
    assert result['branch'] == result['commit'] == 'unavailable'
    assert result['status_available'] is False and result['dirty'] is True


def test_api_execution_mode_is_explicit_and_cannot_carry_authorization(saved, environment):
    service, case = saved
    wire(service, lambda req: pytest.fail('No authorization'), authorized=False)
    status, body = service.handle('POST', '/api/v1/chat', {
        'question': 'Show risk', 'investigation_id': case.investigation_id, 'execution_mode': 'live'})
    assert status == 200 and body['execution_state'] == 'inference-not-authorized'
    assert body['classification'] == 'inference-not-authorized'
    status, body = service.handle('POST', '/api/v1/chat', {
        'question': 'Show risk', 'execution_mode': 'live', 'authorization': {'no_cost': True}})
    assert status == 422 and body['error'] == 'REQUEST_SCHEMA_INVALID'
    status, body = service.handle('POST', '/api/v1/chat', {'question': 'Show risk', 'execution_mode': 'guess'})
    assert status == 422
