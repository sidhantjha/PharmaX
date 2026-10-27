"""Integer-vial daily FEFO simulation. No transfers, substitution or planning."""

from datetime import timedelta
from contracts import ForecastRequest, InventoryForecast, HospitalRiskAssessment, ScenarioComparison
from .core import available, require_synthetic, result, rating, hospital_rating, LEVELS
from .shipments import assess_shipment, compatible, overlay_version, validate_overlay


def forecast(bundle, request):
    request = ForecastRequest.model_validate(request)
    validate_overlay(bundle, request.overlay)
    start = bundle.scenario_as_of
    hospital = next((h for h in bundle.hospitals if h.hospital_id == request.hospital_id), None)
    product = next((p for p in bundle.products if p.product_id == request.product_id), None)
    if hospital is None or product is None:
        raise ValueError("Unknown hospital/product")
    if any(v is None for v in (product.strength_mg_per_ml, product.container_ml, product.total_mg)):
        raise ValueError("Forecast requires unambiguous modeled product strength/presentation")
    inventory = [
        r
        for r in bundle.inventory
        if (r.hospital_id, r.product_id) == (hospital.hospital_id, product.product_id)
    ]
    consumption = [
        r
        for r in bundle.consumption
        if (r.hospital_id, r.product_id) == (hospital.hospital_id, product.product_id)
        and start - timedelta(days=7) <= r.day < start
    ]
    expected_days = {start - timedelta(days=i) for i in range(1, 8)}
    if len(consumption) != 7 or {r.day for r in consumption} != expected_days:
        raise ValueError("Require exactly seven complete prior days of demand history")
    require_synthetic([hospital, product, *inventory, *consumption], bundle)
    if any(r.quantity_unit != product.quantity_unit for r in [*inventory, *consumption]):
        raise ValueError("Quantity units disagree with selected product")
    demand = (sum(r.quantity for r in consumption) + 6) // 7
    if (
        len(inventory) > 32
        or sum(
            (s.hospital_id, s.product_id) == (hospital.hospital_id, product.product_id)
            for s in bundle.shipments
        )
        > 16
    ):
        raise ValueError("Pilot forecast budget: at most 32 initial lots and 16 shipments")
    overrides = {r.inventory_id: r for r in request.overlay.stock} if request.overlay else {}
    states = []
    for row in inventory:
        override = overrides.get(row.inventory_id)
        qty = override.on_hand if override else row.on_hand
        reserved = override.reserved if override and override.reserved is not None else row.reserved
        states.append(
            dict(
                lot_id=row.lot_id,
                on_hand=qty,
                reserved=reserved,
                expiry_date=row.expiry_date,
                quarantined=row.quality_status != "released",
                handling=compatible(product, hospital),
            )
        )

    def usable(lot):
        return max(0, lot["on_hand"] - lot["reserved"]) if not lot["quarantined"] and lot["handling"] else 0

    initial = sum(usable(s) for s in states if s["expiry_date"] >= start)
    ships = [
        assess_shipment(bundle, s, request.scenario, request.overlay)
        for s in bundle.shipments
        if (s.hospital_id, s.product_id) == (hospital.hospital_id, product.product_id)
    ]
    points = simulate_inventory(start, request.horizon_days, states, ships, demand,
                                request.arrival_shelf_life_days)
    shortage = [p for p in points if p["unmet"] > 0]
    longest = current = 0
    for p in points:
        current = current + 1 if p["unmet"] else 0
        longest = max(longest, current)
    registry = {e.evidence_id: e for e in bundle.evidence}
    excluded = sorted(
        e.evidence_id
        for e in bundle.evidence
        if e.kind != "simulated" and not available(e, start, registry, day_start=True)
    )
    ids = {r.evidence.evidence_id for r in [hospital, product, *inventory, *consumption]}
    ids.update(e for ship in ships for e in ship.input_evidence_ids)
    return result(
        InventoryForecast,
        bundle,
        request=request,
        as_of=start,
        end_on=points[-1]["day"],
        product_identity=dict(
            product_id=product.product_id,
            presentation=product.presentation,
            strength_mg_per_ml=product.strength_mg_per_ml,
            container_ml=product.container_ml,
            total_mg=product.total_mg,
            quantity_unit=product.quantity_unit,
        ),
        overlay_version=overlay_version(request.overlay),
        initial_inventory_inputs=inventory,
        consumption_inputs=sorted(consumption, key=lambda r: r.day),
        excluded_temporal_evidence_ids=excluded,
        expected_daily_demand=demand,
        initial_usable=initial,
        total_received=sum(p["received"] for p in points),
        total_served=sum(p["served"] for p in points),
        total_unmet=sum(p["unmet"] for p in points),
        shortage_days=len(shortage),
        longest_shortage_run=longest,
        projected_stockout_date=shortage[0]["day"] if shortage else None,
        right_censored=bool(points[-1]["unmet"]),
        ending_buffer_days=points[-1]["buffer_days"],
        points=points,
        shipment_risks=ships,
        input_evidence_ids=sorted(ids),
        assumptions=[
            "Demand is ceiling(mean of exactly seven complete pre-anchor daily quantities).",
            "Demand is additional unreserved demand. Existing reservations remain ring-fenced; no double consumption of reservations.",
            "Expire at start of the day AFTER expiry; receive at start of day; serve by FEFO, then lot ID.",
            "No backlog carryover: unmet demand is counted once each day, not automatically ordered later.",
            f"Incoming lots are modeled released, unreserved and expire arrival + {request.arrival_shelf_life_days} days.",
            "Stockout date means first unmet-demand date; shortage duration counts unmet days within horizon, not elapsed span.",
            "Different product IDs/strengths are never substituted; POs are never additional receipts.",
            "Only simulated inputs available at anchor-day start UTC drive projections; historical backtest=false.",
        ],
        limitations=[
            "Synthetic scenario projection, not an observed hospital shortage or calibrated clinical forecast.",
            "Future FDA revisions and later-retrieved documents do not enter demand, eligibility or risk.",
            "Held shipments have no arrival unless explicitly released in a hypothetical override.",
            "Horizon limits shortage duration; right_censored flags unmet demand on its final day.",
        ],
    )


def assess_hospital(bundle, request):
    f = forecast(bundle, request)
    supplier_ratings = {}
    for supplier in sorted({s.supplier_id for s in f.shipment_risks}):
        shipments = [s for s in f.shipment_risks if s.supplier_id == supplier]
        worst = max(shipments, key=lambda s: LEVELS.index(s.rating.level))
        supplier_ratings[supplier] = rating(
            "supplier",
            worst.rating.level,
            "supplier.maximum_shipment_level",
            "Maximum rule level among this hospital/product supplier shipments; independent of hospital availability.",
            {"shipment_count": len(shipments)},
            [i for s in shipments for i in s.input_evidence_ids],
        )
    return result(
        HospitalRiskAssessment,
        bundle,
        hospital_id=f.request.hospital_id,
        product_id=f.request.product_id,
        forecast=f,
        rating=hospital_rating(f),
        supplier_ratings=supplier_ratings,
        input_evidence_ids=f.input_evidence_ids,
        assumptions=f.assumptions,
        limitations=f.limitations,
    )


def compare(bundle, request):
    request = ForecastRequest.model_validate(request)
    baseline = assess_hospital(bundle, request.model_copy(update={"scenario": "baseline"}))
    disrupted = assess_hospital(bundle, request.model_copy(update={"scenario": "disruption"}))
    b, d = baseline.forecast, disrupted.forecast
    advance = (
        (b.projected_stockout_date - d.projected_stockout_date).days
        if b.projected_stockout_date and d.projected_stockout_date
        else None
    )
    explanation = (
        f"Baseline has {b.total_unmet} unmet vials; disruption has {d.total_unmet}; "
        f"increment={d.total_unmet - b.total_unmet}. "
    )
    if b.total_unmet:
        explanation += "A baseline shortfall exists independently of modeled holds; do not attribute its onset to disruption."
    elif d.total_unmet:
        explanation += "Under this identical starting-stock overlay, modeled holds introduce unmet demand absent from baseline."
    else:
        explanation += "Neither scenario shows unmet demand in this horizon."
    return result(
        ScenarioComparison,
        bundle,
        as_of=b.as_of,
        hospital_id=request.hospital_id,
        product_id=request.product_id,
        overlay=request.overlay,
        overlay_version=overlay_version(request.overlay),
        baseline=baseline,
        disruption=disrupted,
        incremental_unmet=d.total_unmet - b.total_unmet,
        incremental_shortage_days=d.shortage_days - b.shortage_days,
        stockout_advance_days=advance,
        baseline_already_short=bool(b.total_unmet),
        causal_explanation=explanation,
        input_evidence_ids=sorted(set(b.input_evidence_ids + d.input_evidence_ids)),
        assumptions=[
            "Only modeled hold handling differs between baseline and disruption; all other request inputs are identical."
        ],
        limitations=[
            "This controlled comparison supports scenario attribution, not a causal claim about real hospitals or the Rocky Mount event."
        ],
    )


def simulate_inventory(start, horizon_days, states, ships, demand, arrival_shelf_life_days=30):
    """Public unit-neutral container FEFO arithmetic shared by legacy and replay.

    Callers validate identity, unit, source eligibility and supply sufficiency first.
    Quantities are whole containers. This function has no shipment inference.
    """
    from copy import deepcopy
    if not 1 <= horizon_days <= 60 or demand < 0:
        raise ValueError("Invalid simulation horizon/demand")
    states = deepcopy(states)
    def usable(lot):
        return max(0, lot["on_hand"] - lot["reserved"]) if not lot["quarantined"] and lot["handling"] else 0
    points = []
    for offset in range(horizon_days):
        day = start + timedelta(days=offset)
        physical = sum(s["on_hand"] for s in states)
        expired = 0
        for lot in states:
            if lot["expiry_date"] < day:
                expired += lot["on_hand"]
                lot["on_hand"] = lot["reserved"] = 0
        opening = sum(usable(s) for s in states)
        arrivals = [s for s in ships if s.modeled_arrival == day]
        for shipment in arrivals:
            states.append(
                dict(
                    lot_id=getattr(shipment, 'lot_id', "arrival:" + shipment.shipment_id),
                    on_hand=shipment.quantity,
                    reserved=0,
                    expiry_date=getattr(shipment, 'expiry_date', day + timedelta(days=arrival_shelf_life_days)),
                    quarantined=getattr(shipment, 'quarantined', False),
                    handling=True,
                )
            )
        remaining = demand
        for lot in sorted(states, key=lambda s: (s["expiry_date"], s["lot_id"])):
            take = min(remaining, usable(lot))
            lot["on_hand"] -= take
            remaining -= take
        closing = sum(usable(s) for s in states)
        received = sum(s.quantity for s in arrivals)
        served = demand - remaining
        physical_end = sum(s["on_hand"] for s in states)
        if physical_end != physical - expired + received - served:
            raise ValueError("Physical mass balance failed")
        points.append(
            dict(
                day=day,
                opening_physical=physical,
                expired_physical=expired,
                opening_usable=opening,
                received=received,
                demand=demand,
                served=served,
                unmet=remaining,
                closing_usable=closing,
                closing_physical=physical_end,
                reserved=sum(s["reserved"] for s in states),
                quarantined=sum(s["on_hand"] for s in states if s["quarantined"]),
                buffer_days=closing / demand if demand else None,
                received_shipment_ids=[s.shipment_id for s in arrivals],
                lots=[
                    {k: lot[k] for k in ("lot_id", "on_hand", "reserved", "expiry_date", "quarantined")}
                    | {"available": usable(lot)}
                    for lot in sorted(states, key=lambda s: s["lot_id"])
                ],
            )
        )
    return points
