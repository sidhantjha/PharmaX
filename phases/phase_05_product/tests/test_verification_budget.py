import pytest
from phases.phase_01_foundation.http import ServiceError
from phases.phase_05_product.verification_budget import VerificationBudget
from phases.phase_05_product.helene_model_verification import READS
from phases.phase_05_product.integration import RESPONSIBILITIES

PAYLOAD={'messages':[{'role':'user','content':'read'}],'tools':[],'max_tokens':32}


def test_one_smoke_plus_twelve_two_turn_roles_reserves_75_attempts():
    budget=VerificationBudget()
    roles=['smoke']+[r for role in RESPONSIBILITIES for r in (role,role)]
    for role in roles:
        ticket=budget.reserve(role,PAYLOAD)
        budget.reserve_attempts(3)
        budget.settle(ticket,{'usage':{'total_tokens':10}})
    assert budget.turns==25 and budget.attempts==75
    with pytest.raises(ServiceError,match='BUDGET'):
        budget.reserve('extra',PAYLOAD)
    with pytest.raises(ServiceError,match='ATTEMPT_BUDGET'):
        budget.reserve_attempts(3)
    assert set(READS)==set(RESPONSIBILITIES) and len(READS)==12


def test_role_and_token_caps_block_before_dispatch():
    budget=VerificationBudget()
    for _ in range(2):
        t=budget.reserve('hospital',PAYLOAD)
        budget.settle(t,{'usage':{'total_tokens':10}})
    with pytest.raises(ServiceError,match='BUDGET'):
        budget.reserve('hospital',PAYLOAD)
    tiny=VerificationBudget(token_cap=1)
    with pytest.raises(ServiceError,match='BUDGET'):
        tiny.reserve('hospital',PAYLOAD)
    assert tiny.turns==0


def test_failure_stops_new_work_and_retains_unknown_token_reservation():
    budget=VerificationBudget()
    t=budget.reserve('verification',PAYLOAD)
    budget.reserve_attempts(3)
    budget.fail(t)
    assert budget.pending_tokens>0 and budget.stopped
    with pytest.raises(ServiceError,match='BUDGET'):
        budget.reserve('hospital',PAYLOAD)


def test_deadline_blocks_attempt_reservation_and_bounds_http_timeout():
    budget=VerificationBudget(seconds=5)
    assert 0<budget.request_seconds()<=5
    budget.deadline=0
    with pytest.raises(ServiceError,match='TIME_BUDGET'):
        budget.reserve_attempts(3)
    with pytest.raises(ServiceError,match='TIME_BUDGET'):
        budget.request_seconds()
    assert budget.attempts==0


def test_runtime_configuration_change_revokes_reviewed_authorization(tmp_path,monkeypatch):
    import json
    from hashlib import sha256
    from phases.phase_05_product import verified_runtime as runtime
    provider=tmp_path/'js/engine/providers.js'
    provider.parent.mkdir(parents=True)
    provider.write_text("const NVIDIA_BASE_URL = 'https://integrate.api.nvidia.com/v1'")
    monkeypatch.setattr(runtime,'SOURCE_HASHES',{'js/engine/providers.js':sha256(provider.read_bytes()).hexdigest()})
    (tmp_path/'state').mkdir()
    (tmp_path/'state/app-policy.json').write_text(json.dumps({'pharmax':{'mode':'auto'}}))
    tools=tmp_path/'tools.json'
    tools.write_text('{"servers":[]}')
    with pytest.raises(ServiceError,match='ENDPOINT_REQUIRED'):
        runtime.reviewed_authorization('https://partner.example/v1',tmp_path,tmp_path,VerificationBudget())
    approval=runtime.reviewed_authorization('http://127.0.0.1:8088/v1',tmp_path,tmp_path,VerificationBudget())
    approval.require(approval.endpoint,runtime.MODEL)
    tools.write_text('{"servers":[{"name":"unexpected"}]}')
    with pytest.raises(ServiceError,match='EXTERNAL_TOOLS'):
        approval.require(approval.endpoint,runtime.MODEL)
    tools.write_text('{"servers":[]}')
    provider.write_text('changed')
    with pytest.raises(ServiceError,match='SOURCE_CHANGED'):
        approval.require(approval.endpoint,runtime.MODEL)


def test_local_finalizer_cannot_overwrite_closed_run(tmp_path,monkeypatch):
    import json
    import sys
    from phases.phase_05_product.finalize_role_verification import main
    path=tmp_path/'summary.json'
    path.write_text(json.dumps({'finalized_at':'2026-10-27T06:30:40Z'}))
    before=path.read_bytes()
    monkeypatch.setattr(sys,'argv',['finalize','--run-dir',str(tmp_path),'--store-root',str(tmp_path)])
    with pytest.raises(ValueError,match='ALREADY_FINALIZED'):
        main()
    assert path.read_bytes()==before


def test_new_authorization_caps_36_turns_and_108_possible_attempts():
    budget=VerificationBudget(max_turns=36,max_attempts=108,token_cap=90000,seconds=900,per_role=3)
    for role in RESPONSIBILITIES:
        for _ in range(3):
            ticket=budget.reserve(role,PAYLOAD)
            budget.reserve_attempts(3)
            budget.settle(ticket,{'usage':{'total_tokens':10}})
    assert budget.turns==36 and budget.attempts==108
    with pytest.raises(ServiceError,match='BUDGET'):
        budget.reserve('extra',PAYLOAD)


def test_rate_reservation_survives_until_60_seconds_after_completion(monkeypatch):
    from phases.phase_05_product import verification_budget as module
    now=[100.]
    monkeypatch.setattr(module,'monotonic',lambda:now[0])
    monkeypatch.setattr(module,'sleep',lambda seconds:now.__setitem__(0,now[0]+seconds))
    budget=VerificationBudget(seconds=900,rate_limit=1)
    first=budget.reserve('news',PAYLOAD)
    now[0]+=5
    budget.settle(first,{'usage':{'total_tokens':10}})
    second=budget.reserve('hospital',PAYLOAD)
    assert now[0]>=165 and budget.turns==2
    budget.settle(second,{'usage':{'total_tokens':10}})
