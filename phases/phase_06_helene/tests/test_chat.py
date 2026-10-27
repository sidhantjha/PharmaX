"""Real store and FEFO chat integration; every provider is forbidden in offline mode."""
import socket
import pytest
from phases.phase_05_product.service import OperatorService
from phases.phase_06_helene.pipeline import replay_checkpoint


@pytest.fixture
def setup(tmp_path, monkeypatch):
    case = replay_checkpoint('2024-10-01')
    service = OperatorService(tmp_path)
    service.helene_store.save(case)
    def denied(*args, **kwargs):
        raise AssertionError('Offline chat attempted network/model construction')
    monkeypatch.setattr(socket, 'create_connection', denied)
    service.model_client_factory = denied
    return service, case


def ask(service, case, question, **extra):
    return service.handle('POST', f'/api/v1/helene/cases/{case.investigation_id}/chat',
                          dict(question=question, mode='offline', **extra))


@pytest.mark.parametrize('question,reference', [
    ("What happened to Baxter's North Cove facility?", 'manufacturer'),
    ('Which hospitals face the greatest saline shortage?', 'forecast'),
    ('Why were 800 units reportedly shipped but only 500 received?', 'reconciliation'),
    ('What mitigation options were evaluated?', 'mitigation'),
    ('What happens to the forecast if another 120 bags arrive?', 'forecast'),
    ('Did the Nemotron agents and H-MEM run?', 'agents'),
])
def test_evidence_answers_and_resolvable_citations(setup, question, reference):
    service, case = setup
    code, response = ask(service, case, question)
    assert code == 200, response
    assert response['model_executed'] is False
    assert response['mode'] == 'offline-deterministic'
    turn = response['turns'][0]
    assert turn['answer'] and turn['citations'][0]['reference'] == reference
    for citation in turn['citations']:
        status, resource = service.handle('GET', citation['href'])
        assert status == 200 and resource['investigation_id'] == case.investigation_id


def test_hypothetical_fefo_isolation_and_context(setup):
    service, case = setup
    original = service.helene_store.get(case.investigation_id).model_dump_json()
    _, first = ask(service, case, 'Which hospitals face the greatest saline shortage?')
    _, follow = ask(service, case, 'Tell me more about that', conversation_id=first['conversation_id'])
    assert len(follow['turns']) == 2
    assert follow['turns'][1]['citations'][0]['reference'] == 'forecast'
    status, changed = ask(service, case, 'What happens if another 120 bags arrive?')
    assert status == 200
    assert '172 to 52 bags' in changed['turns'][0]['answer']
    assert 'network total becomes 222' in changed['turns'][0]['answer']
    assert original == service.helene_store.get(case.investigation_id).model_dump_json()


def test_scope_fail_closed_and_schema(setup):
    service, case = setup
    path = f'/api/v1/helene/cases/{case.investigation_id}/chat'
    assert service.handle('POST', path, dict(question='Hello', mode='live'))[0] == 403
    for body in [dict(question=' ', mode='offline'), dict(question='Hi', budget=999),
                 dict(question='Hi', authorized=True), dict(question='x'*2001)]:
        assert service.handle('POST', path, body)[0] == 422
    assert ask(service, case, 'Hello', conversation_id='conversation-'+'a'*32)[0] == 404
    _, reply = ask(service, case, 'Which hospitals face shortages?')
    child = replay_checkpoint('2024-10-09')
    service.helene_store.save(child)
    assert ask(service, child, 'Tell me more', conversation_id=reply['conversation_id'])[0] == 409
    assert service.handle('GET', path+'-sources/../../private')[0] == 404


def test_invalid_scenarios_do_not_save_turn(setup):
    service, case = setup
    for quantity, arrival in [(0, '2024-10-01'), (120, '2025-01-01'), (True, '2024-10-01')]:
        assert ask(service, case, 'What if', scenario=dict(hospital_id='sim-nc-central',
            additional_bags=quantity, arrival_on=arrival))[0] == 422


def test_conversation_limit_and_persistence(setup):
    service, case = setup
    _, reply = ask(service, case, 'Baxter?')
    for _ in range(19):
        code, reply = ask(service, case, 'More', conversation_id=reply['conversation_id'])
        assert code == 200
    assert ask(service, case, 'More', conversation_id=reply['conversation_id'])[0] == 422
    restored = OperatorService(service.root)
    assert ask(restored, case, 'More', conversation_id=reply['conversation_id'])[0] == 422
