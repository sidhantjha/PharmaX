"""Acceptance of actual local calculations, real memory, immutable history and review boundaries."""
import json
import sqlite3
from pathlib import Path
import pytest
from jsonschema import validate
from contracts.operator import CaseRequest, ActionReview, ChatRequest, ApiResponse
from phases.phase_05_product.service import OperatorService
from phases.phase_05_product.chat import answer
from phases.phase_05_product.reports import forecasts
from phases.phase_05_product.clinical import allocate


@pytest.fixture(scope='module')
def service(tmp_path_factory):
    return OperatorService(tmp_path_factory.mktemp('operator'))


@pytest.fixture(scope='module')
def cases(service):
    return {scenario:service.create(CaseRequest(scenario=scenario)) for scenario in ('red-sea','original','clean-onset')}


@pytest.mark.parametrize('scenario,baseline,disruption,remaining',[
    ('red-sea',0,24,0),('original',20,80,56),('clean-onset',0,54,18)])
def test_real_calculation_regressions(cases,scenario,baseline,disruption,remaining):
    case=cases[scenario]
    assert case.status=='complete'
    rows={(label,f.request.hospital_id):f for label,f in forecasts(case)}
    assert rows['baseline','sim-central'].total_unmet==baseline
    assert rows['disruption','sim-central'].total_unmet==disruption
    assert rows['mitigation','sim-central'].total_unmet==remaining
    assert rows['mitigation','sim-north'].total_unmet==0
    assert rows['mitigation','sim-north'].points[-1].reserved==20
    assert any(not e.feasible and any(f.code=='DONOR_SHORTAGE' for f in e.failures) for e in case.mitigation.evaluations)


def test_genuine_memory_and_replanning(cases):
    case=cases['red-sea']
    memory=next(a for a in case.agents if a.agent_id=='memory')
    assert memory.memory_ids
    assert any(e['data'].get('writes') for e in memory.tool_records)
    assert any(e['data'].get('memory_ids') for e in memory.tool_records)
    assert any(e['operation']=='coordinator.replanning' and e['data']['rejected']
               for a in case.agents for e in a.tool_records)
    assert {'news','verification','dependency','hospital','trade','mitigation','feasibility','actions'}<={a.agent_id for a in case.agents}
    assert all(a.started_at<=a.completed_at for a in case.agents)


def test_synthetic_clinical_allocation(cases):
    case=cases['red-sea']
    impact={(i.case,i.hospital_id):i for i in case.clinical_impact}
    assert impact['baseline','sim-central'].exposed_sessions==0
    assert impact['disruption','sim-central'].unique_exposed_patients==12
    assert impact['disruption','sim-central'].exposed_sessions==12
    assert impact['mitigation','sim-central'].unique_exposed_patients==0
    assert all(s.classification=='synthetic' for s in case.schedules)
    assert any(s.required_quantity>1 for s in case.schedules)
    historical=next(i for i in cases['original'].clinical_impact if i.case=='disruption' and i.hospital_id=='sim-central')
    assert historical.unique_exposed_patients < historical.exposed_sessions
    forecast=next(f for label,f in forecasts(case) if label=='disruption' and f.request.hospital_id=='sim-central')
    with pytest.raises(ValueError,match='Duplicate'):
        allocate([*case.schedules,case.schedules[0]],forecast,'disruption')


def test_drafts_review_and_immutable_history(service,cases):
    case=cases['red-sea']
    action=case.actions[0]
    assert len(case.actions)==6
    assert all(a.status=='DRAFT' and not a.dispatch_available for a in case.actions)
    with pytest.raises(ValueError,match='ATTESTATIONS'):
        service.store.review(case.investigation_id,action.action_id,ActionReview(expected_revision=1,
            status='READY_FOR_REVIEW',operator='test reviewer',note='Review labels'))
    request=ActionReview(expected_revision=1,status='READY_FOR_REVIEW',operator='test reviewer',
        note='Reviewed synthetic labels and quantities',attestations=['Draft reviewed for accuracy and simulation labels'])
    reviewed=service.store.review(case.investigation_id,action.action_id,request)
    assert reviewed.status=='READY_FOR_REVIEW' and reviewed.revision==2
    with pytest.raises(ValueError,match='REVISION_CONFLICT'):
        service.store.review(case.investigation_id,action.action_id,request)
    assert service.store.get(case.investigation_id).actions[0].status=='DRAFT'
    with service.store.connect() as db:
        with pytest.raises(sqlite3.IntegrityError,match='immutable'):
            db.execute('UPDATE cases SET hash=? WHERE id=?',('x',case.investigation_id))


def test_historical_cutoff(cases):
    case=cases['original']
    assert all(e.kind!='observed' for e in case.evidence)
    assert any(a.agent_id=='trade' and a.status=='unavailable' for a in case.agents)
    assert all(t['status']=='unavailable' or not t['findings'] or
               all(f['kind']!='observed' for f in t['findings']) for t in case.context_findings)


@pytest.mark.parametrize('path',['health','sources/status','medicines','hospitals','disruptions','map/layers',
    'investigations','agents','evidence','forecast','clinical-impact','mitigations','actions','report'])
def test_api_contracts(service,cases,path):
    case=cases['red-sea']
    resource='/api/v1/'+path if '/' in path or path in ('health','medicines','hospitals','disruptions','investigations') else f'/api/v1/investigations/{case.investigation_id}/{path}'
    suffix=('&' if '?' in resource else '?')+'investigation_id='+case.investigation_id
    status,body=service.handle('GET',resource+suffix)
    assert status==200,body
    ApiResponse.model_validate(body)
    schema=json.loads(Path('contracts/operator-api-response.schema.json').read_text())
    validate(body,schema)


@pytest.mark.parametrize('path,status',[('/api/v1/investigations/missing',404),('/api/v1/medicines?limit=101',400),
    ('/api/v1/hospitals?offset=-1',400),('/api/v1/unknown',404),('/api/v1/health?secret=x',400)])
def test_errors(service,cases,path,status):
    assert service.handle('GET',path)[0]==status


@pytest.mark.parametrize('question',['Why is carboplatin at risk?','How credible is that source?',
    'Which response options were rejected and why?','How many modeled patients may be affected?',
    'Draft a transfer request.','What is an unsupported arbitrary fact?'])
def test_chat_tools(service,cases,question):
    response=answer(service,ChatRequest(investigation_id=cases['red-sea'].investigation_id,question=question))
    assert response.answer and response.decisions and response.citations
    assert response.classification=='deterministic-tool-response'


def test_chat_versioned_sensitivity(service,cases):
    parent=cases['red-sea']
    response=answer(service,ChatRequest(investigation_id=parent.investigation_id,question='What happens if delivery is three days later?'))
    child=service.store.get(response.investigation_id)
    assert child.parent_id==parent.investigation_id
    assert child.intelligence.selected_delay_days==10
    assert child.intelligence.baseline.forecast.total_unmet==0
    assert service.store.get(parent.investigation_id)==parent


def test_empty_service_returns_unavailable(tmp_path):
    empty=OperatorService(tmp_path/'empty')
    assert empty.handle('GET','/api/v1/medicines')[1]['availability']=='unavailable'
    assert empty.handle('GET','/api/v1/unknown')[0]==404
    assert empty.handle('GET','/api/v1/health?format=csv')[0]==400


def test_refresh_skipped_cannot_reuse_an_old_receipt(tmp_path,monkeypatch):
    import subprocess
    import sys
    from phases.phase_05_product.terminal import main
    target=tmp_path/'refresh'
    target.mkdir()
    # Old successful manifest must not be mistaken for this cycle's success.
    (target/'manifest.json').write_text('{"fetch_status":[{"source":"marad","status":"success"}]}')
    monkeypatch.setattr(sys,'argv',['demo','--mode','refresh','--output-dir',str(tmp_path)])
    monkeypatch.setattr(subprocess,'run',lambda *a,**kw:subprocess.CompletedProcess(a,0))
    assert main()==2


def test_drafts_resolve_actual_organization_records(cases):
    for scenario,organization in [('red-sea','sim-north'),('original','sim-north'),('clean-onset','sim-lakes')]:
        case=cases[scenario]
        assert all(a.source_organization==organization and a.source_record_id!=organization for a in case.actions)
        assert all(a.evidence_url.endswith('/evidence') and a.calculation_url.endswith('/forecast') for a in case.actions)
    assert all(s.scenario_id=='october-2026-red-sea-v1' for s in cases['red-sea'].schedules)


def test_offline_path_never_constructs_http_client(tmp_path,monkeypatch):
    import httpx
    def denied(*args,**kwargs):
        raise AssertionError('Offline replay attempted network client construction')
    monkeypatch.setattr(httpx,'Client',denied)
    case=OperatorService(tmp_path/'offline').create(CaseRequest(delay_days=0))
    assert case.status=='complete' and not case.actions
    assert case.intelligence.selected_delay_days==0
    assert all(i.exposed_sessions==0 for i in case.clinical_impact)


def test_case_evidence_is_recursively_closed(cases):
    for case in cases.values():
        ids={e.evidence_id for e in case.evidence}
        assert all(parent in ids for e in case.evidence for parent in e.parent_ids)
        assert all(i in ids for a in case.actions for i in a.evidence_ids)
        assert all(i in ids for a in case.agents for i in a.evidence_ids)
