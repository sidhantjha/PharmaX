"""Diagnostic credential handling and GET-only guarantees, not inference tests."""
import json
import httpx
from phases.phase_01_foundation.sponsor_diagnostics import diagnose, MODEL


def test_runtime_diagnostic_is_get_only_and_never_retains_token_or_provider_metadata(tmp_path, monkeypatch):
    monkeypatch.delenv('METERLESS_RUNTIME_TOKEN', raising=False)
    path = tmp_path/'proxy-token'
    path.write_text('PRIVATE-RUNTIME-TOKEN')
    calls = []
    def transport(request):
        calls.append((request.method, str(request.url)))
        assert request.method == 'GET'
        if request.url.path == '/healthz':
            return httpx.Response(200, json={'status':'ok', 'private':'MUST-NOT-LEAK'})
        if 'authorization' not in request.headers:
            return httpx.Response(401, json={'error':'MUST-NOT-LEAK'})
        assert request.headers['authorization']=='Bearer PRIVATE-RUNTIME-TOKEN'
        return httpx.Response(200, json={'data':[{'id':MODEL, 'secret':'MUST-NOT-LEAK',
            'meterless':{'price':{'input_per_m':0,'output_per_m':0},'private':'MUST-NOT-LEAK'}}]})
    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        result = diagnose('http://127.0.0.1:8088', token_file=path, client=client)
    assert len(calls)==3 and result['inference_posts']==0
    assert result['checks']['authenticated_catalog']['selected_present']
    assert result['live_inference']=='unverified'
    assert all(value not in json.dumps(result) for value in ['PRIVATE-RUNTIME-TOKEN','MUST-NOT-LEAK'])


def test_malformed_catalog_fails_closed(monkeypatch):
    monkeypatch.setenv('METERLESS_RUNTIME_TOKEN','PRIVATE-RUNTIME-TOKEN')
    with httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(200,json=[]))) as client:
        result=diagnose('http://127.0.0.1:8088',client=client)
    assert result['checks']['authenticated_catalog']['code']=='UNEXPECTED_RESPONSE_SCHEMA'
    assert result['inference_posts']==0
