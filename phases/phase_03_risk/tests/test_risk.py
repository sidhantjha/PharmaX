"""Numerical oracles, chronology, partial-coverage guards and read-only scenario tests."""

from copy import deepcopy
from datetime import date
import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError
from contracts import DataBundle, ForecastRequest, ScenarioOverlay, TradeFlow
from phases.phase_01_foundation.sources import digest
from phases.phase_02_data.pipeline import load_bundle
from phases.phase_02_data.validation import validate_bundle, version_for
from phases.phase_03_risk.core import available, country_level, hospital_rating
from phases.phase_03_risk.forecast import forecast, compare
from phases.phase_03_risk.shipments import assess_shipment
from phases.phase_03_risk.trade import exposure
from phases.phase_03_risk.tools import RiskTools
from phases.phase_03_risk.terminal import calculate, render, export

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def bundle():
    return load_bundle(ROOT / "docs/evidence/pharmax-phase2/dataset.json")


@pytest.fixture
def overlay():
    return ScenarioOverlay.model_validate_json(
        (ROOT / "phases/phase_03_risk/fixtures/demo_overlay.json").read_text()
    )


def modified(bundle, change):
    data = bundle.model_dump(mode="json")
    change(data)
    data = DataBundle.model_validate(data).model_dump(mode="json")
    data["dataset_version"] = version_for(data)
    return validate_bundle(data)


def q(hospital="sim-central", **kwargs):
    return ForecastRequest(hospital_id=hospital, **kwargs)


def test_original_baseline_preexisting_shortage_and_increment(bundle):
    c = compare(bundle, q())
    b, d = c.baseline.forecast, c.disruption.forecast
    assert b.initial_usable == 4 and b.expected_daily_demand == 6
    assert b.points[0].served == 4 and b.points[0].unmet == 2 and b.points[0].closing_physical == 8
    assert (b.total_unmet, b.shortage_days, b.longest_shortage_run) == (20, 4, 2)
    assert (d.total_unmet, d.shortage_days) == (80, 14)
    assert b.projected_stockout_date == d.projected_stockout_date == date(2023, 7, 24)
    assert c.baseline_already_short and c.incremental_unmet == 60 and c.stockout_advance_days == 0
    assert b.points[2].received == 60 and b.points[2].closing_usable == 54
    assert b.right_censored and d.right_censored


def test_clean_onset_overlay_preserves_original(bundle, overlay):
    original = bundle.model_dump_json()
    c = compare(bundle, q(overlay=overlay))
    assert c.baseline.forecast.total_unmet == 0
    assert c.disruption.forecast.total_unmet == 54
    assert c.disruption.forecast.shortage_days == 9
    assert c.disruption.forecast.projected_stockout_date == date(2023, 7, 29)
    assert c.baseline.rating.level == "Moderate" and c.disruption.rating.level == "High"
    assert c.incremental_unmet == 54 and not c.baseline_already_short
    assert c.baseline.forecast.initial_inventory_inputs[0].on_hand == 12
    assert c.baseline.forecast.initial_usable == 30
    assert bundle.model_dump_json() == original
    assert compare(bundle, q(overlay=overlay)).model_dump_json() == c.model_dump_json()


@pytest.mark.parametrize(
    "hospital,unmet,days,risk", [("sim-north", 0, 0, "Low"), ("sim-south", 40, 8, "Critical")]
)
def test_other_hospitals_independent_of_central_hold(bundle, hospital, unmet, days, risk):
    c = compare(bundle, q(hospital))
    assert c.baseline.forecast.total_unmet == c.disruption.forecast.total_unmet == unmet
    assert c.baseline.forecast.shortage_days == days and c.disruption.rating.level == risk
    assert c.incremental_unmet == 0


def test_mass_balance_all_days_and_reservations(bundle):
    for h in bundle.hospitals:
        for scenario in ("baseline", "disruption"):
            f = forecast(bundle, q(h.hospital_id, scenario=scenario))
            for p in f.points:
                assert p.closing_physical == p.opening_physical - p.expired_physical + p.received - p.served
                assert p.served + p.unmet == p.demand
                assert p.closing_usable == p.opening_usable + p.received - p.served
                assert p.closing_usable >= 0 and p.closing_physical >= p.reserved
            assert f.total_unmet + f.total_served == f.expected_daily_demand * 14


def test_quarantine_expiry_and_different_product_exclusion(bundle):
    south = forecast(bundle, q("sim-south", scenario="baseline"))
    assert south.initial_usable == 0 and south.points[0].quarantined == 100
    assert south.points[2].expired_physical == 100 and south.points[2].reserved == 0
    assert south.points[3].received == 30
    north = forecast(bundle, q("sim-north"))
    assert north.points[0].opening_physical == 80  # 200 other-strength vials are excluded.
    with pytest.raises(ValueError, match="seven"):
        forecast(bundle, q("sim-north", product_id="sim-carboplatin-150"))


def test_fefo_and_inclusive_expiry_day(bundle):
    def change(data):
        lot = deepcopy(next(r for r in data["inventory"] if r["hospital_id"] == "sim-central"))
        lot.update(
            inventory_id="extra-lot", lot_id="EXPIRES-FIRST", on_hand=3, reserved=0, expiry_date="2023-07-24"
        )
        data["inventory"].append(lot)

    f = forecast(modified(bundle, change), q())
    assert f.initial_usable == 7 and f.points[0].unmet == 0
    lots = {r.lot_id: r for r in f.points[0].lots}
    assert lots["EXPIRES-FIRST"].on_hand == 0 and lots["FICTIONAL-LOT-central"].on_hand == 9
    assert f.points[1].expired_physical == 0


def test_expired_reservations_are_removed_with_the_lot(bundle):
    def change(data):
        next(r for r in data["inventory"] if r["hospital_id"] == "sim-central")["expiry_date"] = "2023-07-24"

    f = forecast(modified(bundle, change), q())
    assert f.points[0].served == 4
    assert f.points[1].expired_physical == 8 and f.points[1].reserved == 0


def test_arrival_override_delay_and_release(bundle):
    overlay = ScenarioOverlay(
        overlay_id="release-test",
        base_dataset_version=bundle.dataset_version,
        as_of=bundle.scenario_as_of,
        description="Hypothetical release; no action",
        arrivals=[{"shipment_id": "ship-central", "arrival_on": "2023-07-30"}],
    )
    f = forecast(bundle, q(overlay=overlay))
    assert f.total_received == 60 and f.total_unmet == 32
    assert f.shipment_risks[0].arrival_basis == "hypothetical_override"
    assert f.shipment_risks[0].rating.level == "High"
    assert f.points[6].received == 60


def test_po_without_shipment_is_not_an_arrival(bundle):
    b = modified(
        bundle,
        lambda data: data["shipments"].remove(
            next(s for s in data["shipments"] if s["shipment_id"] == "ship-north")
        ),
    )
    f = forecast(b, q("sim-north"))
    assert f.total_received == 0 and f.ending_buffer_days == 1
    assert hospital_rating(f).level == "Moderate"


def test_storage_constraint_blocks_baseline_too(bundle):
    def change(data):
        data["shipments"][0]["temperature_max_c"] = 30

    f = forecast(modified(bundle, change), q(scenario="baseline"))
    assert f.total_received == 0 and f.total_unmet == 80
    assert f.shipment_risks[0].arrival_basis == "blocked_constraints"


def test_demand_ceiling_and_zero_demand(bundle):
    def change(data):
        rows = [r for r in data["consumption"] if r["hospital_id"] == "sim-central"]
        for r in rows:
            r["quantity"] = 1
        rows[0]["quantity"] = 2

    assert forecast(modified(bundle, change), q()).expected_daily_demand == 2

    def zero(data):
        for r in data["consumption"]:
            if r["hospital_id"] == "sim-central":
                r["quantity"] = 0

    f = forecast(modified(bundle, zero), q())
    assert f.total_unmet == 0 and f.ending_buffer_days is None


def test_future_fda_revisions_cannot_change_forecasts(bundle):
    def change(data):
        for row in data["shortages"]:
            row["status"] = "Unavailable future status"
            row["availability"] = "Unavailable"

    before, after = forecast(bundle, q()), forecast(modified(bundle, change), q())
    assert before.points == after.points and before.total_unmet == after.total_unmet
    assert len(before.excluded_temporal_evidence_ids) == 27
    assert set(before.input_evidence_ids).isdisjoint(before.excluded_temporal_evidence_ids)


@pytest.mark.parametrize("observed_at", ["2026-10-24T00:00:00Z", "2023-07-24T12:00:00Z"])
def test_future_simulated_dependency_rejected(bundle, observed_at):
    def change(data):
        row = next(r for r in data["consumption"] if r["hospital_id"] == "sim-central")
        row["evidence"]["observed_at"] = observed_at
        next(e for e in data["evidence"] if e["evidence_id"] == row["evidence"]["evidence_id"])[
            "observed_at"
        ] = observed_at

    with pytest.raises(ValueError, match="cutoff"):
        forecast(modified(bundle, change), q())


def test_publication_date_is_not_revision_availability(bundle):
    evidence = next(e for e in bundle.evidence if e.source == "fda")
    assert evidence.published_on.year == 2023
    assert not available(evidence, date(2023, 7, 24), {e.evidence_id: e for e in bundle.evidence})
    event = next(e for e in bundle.events if e.evidence.kind == "derived")
    assert not available(event.evidence, date(2023, 7, 24), {e.evidence_id: e for e in bundle.evidence})


def test_trade_partial_shares_no_global_claims(bundle):
    r = exposure(bundle, context_as_of=date(2026, 10, 24))
    assert r.observed_import_value_usd == 55146159753
    assert r.countries[0].observed_sample_share == pytest.approx(19118889945 / 55146159753)
    assert sum(c.observed_sample_share for c in r.countries) == pytest.approx(1)
    assert not r.complete_partner_coverage and r.national_hhi is None
    assert all(
        c.national_import_share is None and c.historical_change_pct is None and c.country_rating.level is None
        for c in r.countries
    )
    with pytest.raises(ValueError, match="unavailable"):
        exposure(bundle, context_as_of=date(2023, 7, 24))


def synthetic_trade(template, partner, value, period="2023"):
    data = template.model_dump(mode="json")
    data.update(partner_code=partner, value_usd=value, period=period, record_id=f"test-{partner}-{period}")
    ev = data["evidence"]
    ev.update(
        kind="simulated",
        observed_at="2026-10-24T00:00:00Z",
        source="unit-test",
        parent_ids=[],
        source_url=f"https://example.org/trade?period={period}&partner2Code=0&customsCode=C00&motCode=0",
        content_sha256=digest({"partner": partner, "value": value, "period": period}),
        evidence_id=f"test-{partner}-{period}",
    )
    return TradeFlow(**data)


def test_complete_comparable_trade_and_prior_period_math(bundle):
    rows = [
        synthetic_trade(bundle.trade_flows[0], 276, 100),
        synthetic_trade(bundle.trade_flows[1], 372, 200),
    ]
    b = bundle.model_copy(update={"trade_flows": rows})
    world = synthetic_trade(rows[0], 0, 300)
    previous = [synthetic_trade(row, row.partner_code, row.value_usd / 2, "2022") for row in rows]
    r = exposure(b, context_as_of=date(2026, 10, 24), world=world, previous=previous)
    assert r.complete_partner_coverage and r.value_coverage_fraction == 1
    assert r.national_hhi == pytest.approx((1 / 9 + 4 / 9) * 10000)
    assert [c.historical_change_pct for c in r.countries] == [100, 100]
    assert [c.country_rating.level for c in r.countries] == ["High", "Critical"]
    # Scope mismatch must not produce a trend.
    previous[0] = previous[0].model_copy(update={"classification": "H5"})
    assert (
        exposure(b, context_as_of=date(2026, 10, 24), previous=previous).countries[0].historical_change_pct
        is None
    )


@pytest.mark.parametrize(
    "share,level",
    [
        (0.099, "Low"),
        (0.10, "Moderate"),
        (0.249, "Moderate"),
        (0.25, "High"),
        (0.499, "High"),
        (0.50, "Critical"),
    ],
)
def test_country_rule_boundaries(share, level):
    assert country_level(share) == level


@pytest.mark.parametrize("days,level", [(0, "Low"), (1, "Moderate"), (2, "Moderate"), (3, "High")])
def test_shipment_delay_rules(bundle, days, level):
    ship = next(s for s in bundle.shipments if s.shipment_id == "ship-central")
    from datetime import timedelta

    overlay = ScenarioOverlay(
        overlay_id="delay",
        base_dataset_version=bundle.dataset_version,
        as_of=bundle.scenario_as_of,
        description="Test only",
        arrivals=[
            {"shipment_id": ship.shipment_id, "arrival_on": ship.expected_arrival + timedelta(days=days)}
        ],
    )
    assert assess_shipment(bundle, ship, "disruption", overlay).rating.level == level


def test_invalid_overlays_units_bounds_and_budget(bundle, overlay):
    with pytest.raises(ValidationError):
        q(horizon_days=61)
    with pytest.raises(ValidationError):
        q(quantity_unit="mg")
    with pytest.raises(ValueError):
        forecast(bundle, q(overlay=overlay.model_copy(update={"base_dataset_version": "0" * 64})))
    bad = ScenarioOverlay(
        overlay_id="bad",
        base_dataset_version=bundle.dataset_version,
        as_of=bundle.scenario_as_of,
        description="Invalid reservation",
        stock=[{"inventory_id": "stock-central", "on_hand": 1}],
    )
    with pytest.raises(ValueError):
        forecast(bundle, q(overlay=bad))
    tools = RiskTools(bundle, max_calls=1)
    tools.hospital_risk(q())
    with pytest.raises(ValueError, match="budget"):
        tools.hospital_risk(q())


def test_terminal_export_no_network_and_reproducible(monkeypatch, tmp_path):
    def forbidden(*a, **k):
        pytest.fail("Risk analysis touched network")

    monkeypatch.setattr(httpx, "Client", forbidden)
    args = dict(
        dataset=ROOT / "docs/evidence/pharmax-phase2/dataset.json",
        overlay_file=ROOT / "phases/phase_03_risk/fixtures/demo_overlay.json",
    )
    a, b = calculate(**args), calculate(**args)
    assert a == b
    assert "Baseline has 20 unmet vials" in render(a)
    export(a, tmp_path)
    assert len((tmp_path / "timeseries.csv").read_text().splitlines()) == 169
    assert json.loads((tmp_path / "analysis.json").read_text()) == a


def test_overdue_shipment_critical_and_not_recredited(bundle):
    def change(data):
        po = next(r for r in data["purchase_orders"] if r["purchase_order_id"] == "po-central")
        po.update(ordered_on="2023-07-10", expected_delivery="2023-07-17")
        ship = next(r for r in data["shipments"] if r["shipment_id"] == "ship-central")
        ship.update(dispatched_on="2023-07-15", expected_arrival="2023-07-17")

    b = modified(bundle, change)
    r = assess_shipment(b, b.shipments[0], "baseline")
    assert r.rating.level == "Critical" and r.arrival_basis == "past_due_not_recredited"
    assert forecast(b, q(scenario="baseline")).total_received == 0


def test_future_order_rejected_and_history_required(bundle):
    def future(data):
        next(r for r in data["purchase_orders"] if r["purchase_order_id"] == "po-central")["ordered_on"] = (
            "2023-07-25"
        )
        next(r for r in data["shipments"] if r["shipment_id"] == "ship-central")["dispatched_on"] = (
            "2023-07-25"
        )

    with pytest.raises(ValueError, match="not yet placed"):
        forecast(modified(bundle, future), q())

    def missing(data):
        data["consumption"].remove(next(r for r in data["consumption"] if r["hospital_id"] == "sim-central"))

    with pytest.raises(ValueError, match="seven"):
        forecast(modified(bundle, missing), q())


def test_frontend_calculated_examples_match_schemas():
    from jsonschema import Draft202012Validator, FormatChecker
    from contracts import (
        TradeExposure,
        ShipmentRisk,
        InventoryForecast,
        HospitalRiskAssessment,
        ScenarioComparison,
    )

    names = {
        "trade-exposure": TradeExposure,
        "shipment-risk": ShipmentRisk,
        "inventory-forecast": InventoryForecast,
        "hospital-risk-assessment": HospitalRiskAssessment,
        "scenario-comparison": ScenarioComparison,
    }
    for name, model in names.items():
        data = json.loads((ROOT / "contracts/examples" / f"risk-{name}.json").read_text())
        schema = json.loads((ROOT / "contracts" / f"{name}.schema.json").read_text())
        assert schema == model.model_json_schema()
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(data)
        result = model.model_validate(data)
        assert result.kind == "derived" and result.input_evidence_ids
        assert digest(result.model_dump(mode="json", exclude={"analysis_id"})) == result.analysis_id


def test_trade_rejects_exports_and_national_coverage_overflow(bundle):
    exports = bundle.model_copy(
        update={"trade_flows": [r.model_copy(update={"flow": "X"}) for r in bundle.trade_flows]}
    )
    with pytest.raises(ValueError, match="import"):
        exposure(exports, context_as_of=date(2026, 10, 24))
    world = synthetic_trade(bundle.trade_flows[0], 0, 1)
    with pytest.raises(ValueError, match="exceeds"):
        exposure(bundle, context_as_of=date(2026, 10, 24), world=world)


def test_tool_results_do_not_mutate_the_source_or_future_calls(bundle):
    tools = RiskTools(bundle.model_dump(mode="json"))
    first = tools.inventory_forecast(q())
    identity = first.analysis_id
    first.initial_inventory_inputs[0].on_hand = 999
    next(r for r in bundle.inventory if r.hospital_id == "sim-central").on_hand = 500
    second = tools.inventory_forecast(q())
    assert second.analysis_id == identity and second.initial_usable == 4
    assert second.initial_inventory_inputs[0].on_hand == 12
