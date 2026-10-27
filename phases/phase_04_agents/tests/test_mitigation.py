"""Phase 4B: real deterministic tools/engine with network denied and model fixtures."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from unittest.mock import Mock
import httpx
import jsonschema
import pytest
from pydantic import ValidationError
from contracts import MitigationRequest, MitigationResult
from phases.phase_01_foundation.http import ServiceError
from phases.phase_02_data.pipeline import load_bundle
from phases.phase_04_agents.coordinator import investigate_mitigation
from phases.phase_04_agents.feasibility import evaluate
from phases.phase_04_agents.mitigation_reasoning import FixtureMitigationReasoner, NemotronMitigationReasoner
from phases.phase_04_agents.mitigation_scenario import load_catalog, prepare_scenario
from phases.phase_04_agents.mitigation_tools import MitigationTools
from phases.phase_04_agents.trace import Trace

ROOT = Path(__file__).resolve().parents[3]
NODE = os.environ.get("PHARMAX_NODE", "node")


@pytest.fixture
def bundle():
    return load_bundle(ROOT / "docs/evidence/pharmax-phase2/dataset.json")


@pytest.fixture
def catalog():
    return load_catalog()


@pytest.fixture
def req():
    return MitigationRequest(
        session_id="phase4b-tests", evidence_as_of=datetime(2026, 10, 24, tzinfo=timezone.utc)
    )


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(
        httpx.HTTPTransport, "handle_request", Mock(side_effect=AssertionError("NETWORK DENIED"))
    )


def context(bundle, catalog, req):
    scenario, overlay = prepare_scenario(bundle, catalog, req.scenario)
    tools = MitigationTools(scenario, catalog, req, Trace())
    tools.baseline()
    return scenario, tools


def candidate(catalog, name="north-conservative", **updates):
    return next(c for c in catalog.candidates if c.candidate_id == name).model_copy(update=updates, deep=True)


@pytest.mark.parametrize(
    "scenario,base,disrupted,best,remaining,onset,days",
    [
        ("original", 20, 80, "north-conservative", 56, "2023-07-28", 10),
        ("clean-onset", 0, 54, "lakes-additional", 18, "2023-08-04", 3),
    ],
)
def test_adaptive_end_to_end(
    bundle, catalog, req, tmp_path, scenario, base, disrupted, best, remaining, onset, days
):
    req = req.model_copy(update={"scenario": scenario})
    original, fixture = bundle.model_dump_json(), catalog.model_dump_json()
    result = investigate_mitigation(bundle, catalog, req, tmp_path, node=NODE)
    assert result.status == "complete", result.error_code
    assert result.stop_reason == "exhausted"
    assert len(result.evaluations) == 6
    assert result.recommendation_id == best
    reference = next(c for c in result.reference if c.hospital_id == "sim-central")
    assert (reference.baseline.forecast.total_unmet, reference.disruption.forecast.total_unmet) == (
        base,
        disrupted,
    )
    outcome = next(e for e in result.evaluations if e.candidate.candidate_id == best)
    assert outcome.remaining_unmet == remaining
    central = next(i for i in outcome.impacts if i.hospital_id == "sim-central")
    assert str(central.effective.shortage_onset) == onset
    assert central.effective.shortage_days == days
    assert {i.hospital_id: i.effective.unmet_vials for i in outcome.impacts} == {
        "sim-central": remaining,
        "sim-north": 0,
        "sim-south": 40,
    }
    assert result.memory_ids and all(e.simulated and e.requires_human_review for e in result.evaluations)
    choices = [e for e in result.events if e["operation"] == "coordinator.mitigation_choice"]
    assert choices[1]["data"]["recalled_rejections"]
    reject_seq = next(
        e["sequence"]
        for e in result.events
        if e["operation"] == "mitigation.evaluate" and e["status"] == "complete" and not e["data"]["feasible"]
    )
    assert any(
        e["operation"] == "hmem.exchange" and e["data"].get("writes") and e["sequence"] > reject_seq
        for e in result.events
    )
    assert any(e["operation"] == "coordinator.replanning" and e["data"]["rejected"] for e in result.events)
    ids = {e.evidence_id for e in result.evidence}
    assert all(set(e.parent_ids) <= ids for e in result.evidence)
    assert all(
        set(f.input_evidence_ids) <= ids
        for e in result.evaluations
        for i in e.impacts
        if (f := i.projected) is not None
    )
    assert all(
        len(i.projected.forecast.points) == 14 for e in result.evaluations for i in e.impacts if i.projected
    )
    assert bundle.model_dump_json() == original and catalog.model_dump_json() == fixture
    jsonschema.validate(result.model_dump(mode="json"), MitigationResult.model_json_schema())


def test_donor_safety_conservation_and_no_cumulative_stock(bundle, catalog, req):
    scene, tools = context(bundle, catalog, req)
    bad = evaluate(scene, catalog, req, candidate(catalog, "north-bulk"), tools.reference)
    assert not bad.feasible
    assert {"DONOR_SHORTAGE", "DONOR_RISK_WORSE"} <= {r.code for r in bad.failures}
    donor = next(i for i in bad.impacts if i.donor)
    assert donor.before.unmet_vials == donor.effective.unmet_vials == 0
    assert donor.projected.forecast.total_unmet == 16
    assert bad.quantity_moved == 0 and bad.remaining_unmet == 80
    assert bad.supply_balance.diagnostic_only
    good = evaluate(scene, catalog, req, candidate(catalog), tools.reference)
    assert good.feasible and good.quantity_moved == 24
    assert good.supply_balance.available_before == 60 and good.supply_balance.available_after == 36
    assert good.supply_balance.debited == good.supply_balance.recipient_receipt == 24
    assert good.supply_balance.conserved and not good.supply_balance.diagnostic_only
    donor = next(i for i in good.impacts if i.donor)
    assert donor.projected.forecast.initial_inventory_inputs[0].reserved == 20
    assert donor.projected.forecast.points[-1].closing_usable == 20
    central = next(i for i in good.impacts if i.hospital_id == "sim-central")
    assert central.before.risk == "Critical" and central.effective.risk == "High"
    assert evaluate(scene, catalog, req, candidate(catalog), tools.reference) == good
    assert next(s for s in scene.inventory if s.inventory_id == "stock-north").on_hand == 80


@pytest.mark.parametrize(
    "name,updates,code",
    [
        ("north-other-strength", {}, "PRODUCT_MISMATCH"),
        ("south-quarantined", {}, "QUARANTINED"),
        ("north-conservative", {"quantity": 61}, "INSUFFICIENT_UNRESERVED_STOCK"),
        ("north-conservative", {"presentation": "similar carboplatin name"}, "PRESENTATION_MISMATCH"),
        ("north-conservative", {"source_id": "invented"}, "UNKNOWN_SOURCE"),
        ("north-conservative", {"route_id": "invented"}, "UNKNOWN_ROUTE"),
        ("north-conservative", {"hospital_id": "sim-south"}, "TARGET_MISMATCH"),
        ("north-conservative", {"arrival_on": datetime(2023, 7, 25).date()}, "DELIVERY_TOO_LATE"),
        ("north-conservative", {"dispatch_on": datetime(2023, 7, 25).date()}, "FUTURE_TRANSFER_UNSUPPORTED"),
        ("north-conservative", {"arrival_on": datetime(2023, 7, 23).date()}, "INVALID_DELIVERY_DATE"),
        ("atlas-release", {}, "HOLD_NOT_RELEASED"),
        ("atlas-release", {"quantity": 10}, "SOURCE_AUTHORIZATION"),
        ("lakes-additional", {}, "DELIVERY_TOO_LATE"),
    ],
)
def test_constraint_rejections(bundle, catalog, req, name, updates, code):
    scene, tools = context(bundle, catalog, req)
    result = evaluate(scene, catalog, req, candidate(catalog, name, **updates), tools.reference)
    assert not result.feasible and result.quantity_moved == 0
    assert code in {f.code for f in result.failures}
    assert result.remaining_unmet == 80


@pytest.mark.parametrize(
    "mutation,code",
    [
        ("authorization", "SOURCE_AUTHORIZATION"),
        ("transfer", "TRANSFER_NOT_AUTHORIZED"),
        ("capacity", "ROUTE_CAPACITY"),
        ("temperature", "TRANSPORT_REQUIREMENTS"),
        ("light", "TRANSPORT_REQUIREMENTS"),
        ("expiry", "EXPIRED"),
        ("shelf_life", "INSUFFICIENT_SHELF_LIFE"),
        ("recipient_storage", "STORAGE_REQUIREMENTS"),
        ("donor_storage", "STORAGE_REQUIREMENTS"),
        ("future_evidence", "EVIDENCE_AFTER_CUTOFF"),
        ("future_parent", "EVIDENCE_AFTER_CUTOFF"),
    ],
)
def test_operational_constraints(bundle, catalog, req, mutation, code):
    scene, tools = context(bundle, catalog, req)
    route = next(r for r in catalog.routes if r.route_id == "route-north")
    source = next(s for s in scene.inventory if s.inventory_id == "stock-north")
    if mutation == "authorization":
        route.authorized = False
    elif mutation == "transfer":
        route.transfer_permitted = False
    elif mutation == "capacity":
        route.max_quantity = 10
    elif mutation == "temperature":
        route.temperature_min_c = 2
    elif mutation == "light":
        route.protects_from_light = False
    elif mutation == "expiry":
        source.expiry_date = datetime(2023, 7, 23).date()
    elif mutation == "shelf_life":
        source.expiry_date = datetime(2023, 7, 30).date()
    elif mutation in ("recipient_storage", "donor_storage"):
        hid = "sim-central" if mutation == "recipient_storage" else "sim-north"
        next(h for h in scene.hospitals if h.hospital_id == hid).protects_from_light = False
    elif mutation == "future_evidence":
        next(
            e for e in scene.evidence if e.evidence_id == route.evidence.evidence_id
        ).version_available_at = req.evidence_as_of + timedelta(days=1)
    else:
        # Existing parent revision dates cannot be bypassed by an earlier route publication date.
        parent = next(e for e in scene.evidence if e.evidence_id == route.evidence.parent_ids[0])
        parent.version_available_at = req.evidence_as_of + timedelta(days=1)
    result = evaluate(scene, catalog, req, candidate(catalog), tools.reference)
    assert not result.feasible and code in {f.code for f in result.failures}


def test_supplier_delivery_and_authorized_reschedule(bundle, catalog, req):
    req = req.model_copy(update={"scenario": "clean-onset"})
    scene, tools = context(bundle, catalog, req)
    supplier = evaluate(scene, catalog, req, candidate(catalog, "lakes-additional"), tools.reference)
    assert supplier.feasible and supplier.remaining_unmet == 18
    assert supplier.supply_balance.available_before == 36 and supplier.supply_balance.available_after == 0
    assert not supplier.supply_balance.existing_shipment_reused
    next(r for r in catalog.routes if r.route_id == "route-atlas-release").release_hold_authorized = True
    release = evaluate(scene, catalog, req, candidate(catalog, "atlas-release"), tools.reference)
    assert release.feasible and release.remaining_unmet == 0
    assert release.supply_balance.existing_shipment_reused
    central = next(i for i in release.impacts if i.hospital_id == "sim-central")
    assert central.projected.forecast.total_received == 60
    assert len(central.projected.forecast.shipment_risks) == 1  # No second receipt/PO.
    assert central.effective.risk == "Moderate"


def test_history_catalog_availability_and_modes(bundle, catalog, req, tmp_path):
    cutoff = req.model_copy(update={"evidence_as_of": datetime(2023, 7, 23, tzinfo=timezone.utc)})
    r = investigate_mitigation(bundle, catalog, cutoff, tmp_path, node=NODE)
    assert r.status == "blocked" and r.error_code == "EVIDENCE_AFTER_CUTOFF"
    catalog.available_at = datetime(2026, 10, 25, tzinfo=timezone.utc)
    r = investigate_mitigation(bundle, catalog, req, tmp_path, node=NODE)
    assert r.error_code == "EVIDENCE_AFTER_CUTOFF"
    r = investigate_mitigation(bundle, catalog, req, tmp_path, reasoner=Mock(live=True), node=NODE)
    assert r.error_code == "REASONING_MODE_MISMATCH"


def test_typed_permissions_and_tool_limit(bundle, catalog, req):
    scene, _ = prepare_scenario(bundle, catalog, "original")
    tools = MitigationTools(scene, catalog, req, Trace(), max_calls=1)
    with pytest.raises(ValidationError):
        tools.inspect({"collection": "sql", "sql": "SELECT 1"})
    with pytest.raises(ValidationError):
        tools.check({"candidate_id": "north-conservative", "quantity": 1000})
    with pytest.raises(ServiceError, match="UNKNOWN_CANDIDATE"):
        tools.check({"candidate_id": "invented"})
    with pytest.raises(ServiceError, match="TOOL_PERMISSION_DENIED"):
        tools.query("supply", {"collection": "inventory"})
    assert tools.calls == 0
    tools.inspect({"collection": "hospital_lots"})
    with pytest.raises(ServiceError, match="TOOL_CALL_BUDGET"):
        tools.inspect({"collection": "supplier_inventory"})
    assert tools.calls == 1
    tools = MitigationTools(scene, catalog, req, Trace())
    tools.deadline = 0
    with pytest.raises(ServiceError, match="TASK_TIMEOUT"):
        tools.inspect({"collection": "hospital_lots"})
    assert tools.calls == 0


def test_bounded_stopping_and_state_driven_order(bundle, catalog, req, tmp_path):
    small = investigate_mitigation(bundle, catalog, req, tmp_path / "small", node=NODE, max_candidates=1)
    assert small.status == "incomplete" and small.stop_reason == "candidate_budget"
    limited = investigate_mitigation(bundle, catalog, req, tmp_path / "limit", node=NODE, max_calls=5)
    assert limited.status == "incomplete" and limited.stop_reason == "tool_budget"
    assert not limited.evaluations
    chooser = FixtureMitigationReasoner()
    memory = {"result": {"memories": []}}
    first = chooser.choose(catalog.candidates, [], memory, Trace())
    changed = [
        c.model_copy(update={"quantity": 1}) if c.candidate_id == first.candidate_id else c
        for c in catalog.candidates
    ]
    assert chooser.choose(changed, [], memory, Trace()).candidate_id != first.candidate_id


def test_replay_and_overlay_lineage(bundle, catalog, req, tmp_path):
    one = investigate_mitigation(bundle, catalog, req, tmp_path / "one", node=NODE)
    two = investigate_mitigation(bundle, catalog, req, tmp_path / "two", node=NODE)
    assert one.model_dump_json() == two.model_dump_json()
    original, _ = prepare_scenario(bundle, catalog, "original")
    clean, overlay = prepare_scenario(bundle, catalog, "clean-onset")
    assert original.dataset_version != clean.dataset_version
    assert overlay.overlay_id == "central-clean-onset-v1"
    assert next(s for s in clean.inventory if s.inventory_id == "stock-central").on_hand == 38
    ev = next(s for s in clean.inventory if s.inventory_id == "stock-central").evidence
    assert (
        next(s for s in bundle.inventory if s.inventory_id == "stock-central").evidence.evidence_id
        in ev.parent_ids
    )


def test_catalog_integrity(catalog, bundle, req, tmp_path):
    body = catalog.model_dump(mode="json")
    body["supplier_stocks"][0]["on_hand"] = 900
    path = tmp_path / "corrupt.json"
    path.write_text(json.dumps(body))
    with pytest.raises(ValueError, match="hash mismatch"):
        load_catalog(path)
    invalid = catalog.model_copy(deep=True)
    invalid.supplier_stocks[0].on_hand = 900
    result = investigate_mitigation(bundle, invalid, req, tmp_path / 'invalid', node=NODE)
    assert result.status == 'blocked' and result.error_code == 'MITIGATION_VALIDATION_FAILED'


def test_nemotron_replanning_transport_is_bounded(bundle, catalog, req, tmp_path, monkeypatch, mock_authorization):
    model = "nvidia/nemotron-3.5-lightning-30b-a3b"
    monkeypatch.setenv("METERLESS_MODEL", model)
    monkeypatch.setenv("METERLESS_RUNTIME_TOKEN", "fixture-private-token")
    monkeypatch.setenv("METERLESS_BASE_URL", "http://127.0.0.1:8088/v1")
    monkeypatch.setenv("PHARMAX_MODEL_NO_COST", "1")
    calls = []

    def handle(request):
        payload = json.loads(request.content)
        calls.append(payload)
        choice = "north-bulk" if len(calls) == 1 else "north-conservative" if len(calls) == 2 else None
        answer = {
            "candidate_id": choice,
            "reason": "investigate_supply" if len(calls) == 1 else "revise_after_rejection",
        }
        return httpx.Response(
            200,
            json={
                "model": model,
                "meterless": {"winning_route": {"model": model, "provider": "nvidia"}},
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "tool_calls": [
                                {
                                    "type": "function",
                                    "function": {
                                        "name": "investigation_decision",
                                        "arguments": json.dumps(answer),
                                    },
                                }
                            ]
                        },
                    }
                ],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        provider = NemotronMitigationReasoner(client, authorization=mock_authorization)
        result = investigate_mitigation(
            bundle,
            catalog,
            req.model_copy(update={"reasoning": "nightshift"}),
            tmp_path,
            node=NODE,
            reasoner=provider,
            max_candidates=4,
        )
        assert result.recommendation_id == "north-conservative"
        assert result.stop_reason == "model_stop" and provider.calls == 3
        assert "DONOR_SHORTAGE" in calls[1]["messages"][1]["content"]
        assert "[MEMORY CONTEXT]" in calls[1]["messages"][1]["content"]
        provider.calls = 4
        with pytest.raises(ServiceError, match="MODEL_CALL_BUDGET"):
            provider.choose(catalog.candidates, [], {"result": {"context": ""}}, Trace())
    assert "fixture-private-token" not in result.model_dump_json()
    assert all(p["max_tokens"] == 300 for p in calls)


def test_repeated_model_candidate_fails_closed(bundle, catalog, req, tmp_path):
    from phases.phase_04_agents.mitigation_reasoning import CandidateChoice

    class Repeater(FixtureMitigationReasoner):
        def choose(self, *args):
            return CandidateChoice(candidate_id="north-conservative", reason="investigate_supply")

    r = investigate_mitigation(bundle, catalog, req, tmp_path, node=NODE, reasoner=Repeater())
    assert r.error_code == "REPEATED_CANDIDATE" and len(r.evaluations) == 1
    assert r.evaluations[0].feasible
    assert r.recommendation_id is None  # Withhold a recommendation on a blocked investigation.


def test_no_shortage_stops_without_search(bundle, catalog, req, tmp_path):
    result = investigate_mitigation(bundle, catalog, req.model_copy(update={'hospital_id': 'sim-north'}),
                                    tmp_path, node=NODE)
    assert result.status == 'complete' and result.stop_reason == 'no_shortage'
    assert not result.evaluations and not result.recommendation_id
    assert 'no intervention needed' in result.recommendation
    assert not any(e['operation'] == 'coordinator.mitigation_choice' for e in result.events)


def test_recalled_rejections_deprioritize_without_permanently_skipping(catalog):
    chooser = FixtureMitigationReasoner()
    remembered = {'claim_key': 'mitigation.original.north-other-strength',
                  'value': json.dumps({'candidate_id': 'north-other-strength', 'feasible': False})}
    memory = {'result': {'memories': [{'memory': {'content': json.dumps(remembered)}}]}}
    first = chooser.choose(catalog.candidates, [], memory, Trace())
    assert first.candidate_id != 'north-other-strength'
    retried = chooser.choose([candidate(catalog, 'north-other-strength')], [], memory, Trace())
    assert retried.candidate_id == 'north-other-strength'  # Revalidate in a new run; never silently omit it.
