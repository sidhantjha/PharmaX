"""Shipment eligibility and exposure; no supplier selection or mitigation actions."""

from contracts import ShipmentRisk, ScenarioOverlay
from phases.phase_01_foundation.sources import digest
from .core import require_synthetic, rating, result


def overlay_version(overlay):
    return digest(overlay.model_dump(mode="json")) if overlay else None


def validate_overlay(bundle, overlay):
    if overlay is None:
        return
    overlay = ScenarioOverlay.model_validate(overlay)
    if overlay.base_dataset_version != bundle.dataset_version or overlay.as_of != bundle.scenario_as_of:
        raise ValueError("Overlay dataset version or anchor mismatch")
    inventory = {r.inventory_id: r for r in bundle.inventory}
    ships = {s.shipment_id: s for s in bundle.shipments}
    if len({s.inventory_id for s in overlay.stock}) != len(overlay.stock) or len(
        {s.shipment_id for s in overlay.arrivals}
    ) != len(overlay.arrivals):
        raise ValueError("Duplicate overrides")
    for stock in overlay.stock:
        if stock.inventory_id not in inventory:
            raise ValueError("Unknown inventory override")
        reserved = inventory[stock.inventory_id].reserved if stock.reserved is None else stock.reserved
        if reserved > stock.on_hand:
            raise ValueError("Override stock below reservations")
    for arrival in overlay.arrivals:
        if arrival.shipment_id not in ships:
            raise ValueError("Unknown shipment override")
        if arrival.arrival_on and arrival.arrival_on < ships[arrival.shipment_id].dispatched_on:
            raise ValueError("Arrival precedes modeled dispatch")


def compatible(product, hospital, shipment=None):
    if (
        product.storage_min_c is None
        or product.storage_max_c is None
        or product.protect_from_light is None
        or hospital.storage_min_c > hospital.storage_max_c
    ):
        return False
    if hospital.storage_min_c < product.storage_min_c or hospital.storage_max_c > product.storage_max_c:
        return False
    if product.protect_from_light and not hospital.protects_from_light:
        return False
    if shipment and (
        shipment.temperature_min_c < product.storage_min_c
        or shipment.temperature_max_c > product.storage_max_c
        or (product.protect_from_light and not shipment.protects_from_light)
    ):
        return False
    return True


def assess_shipment(bundle, shipment, scenario, overlay=None):
    if scenario not in ("baseline", "disruption"):
        raise ValueError("Invalid scenario")
    overlay = ScenarioOverlay.model_validate(overlay) if overlay is not None else None
    validate_overlay(bundle, overlay)
    hospital = next(h for h in bundle.hospitals if h.hospital_id == shipment.hospital_id)
    product = next(p for p in bundle.products if p.product_id == shipment.product_id)
    supplier = next(s for s in bundle.suppliers if s.supplier_id == shipment.supplier_id)
    po = next(p for p in bundle.purchase_orders if p.purchase_order_id == shipment.purchase_order_id)
    approvals = [
        a
        for a in bundle.approvals
        if (a.hospital_id, a.product_id, a.supplier_id)
        == (hospital.hospital_id, product.product_id, supplier.supplier_id)
    ]
    events = [
        e
        for e in bundle.events
        if e.event_id in shipment.disruption_event_ids and e.starts_on <= bundle.scenario_as_of
    ]
    require_synthetic([shipment, hospital, product, supplier, po, *approvals, *events], bundle)
    if po.ordered_on > bundle.scenario_as_of:
        raise ValueError("Purchase order not yet placed at evaluation cutoff")
    if shipment.status != "planned" and shipment.dispatched_on > bundle.scenario_as_of:
        raise ValueError("Shipment state precedes its dispatch")
    ids = [r.evidence.evidence_id for r in (shipment, hospital, product, supplier, po, *approvals, *events)]
    allowed = any(a.approved for a in approvals) and compatible(product, hospital, shipment)
    override = (
        next((o for o in overlay.arrivals if o.shipment_id == shipment.shipment_id), None)
        if overlay
        else None
    )
    arrival = shipment.expected_arrival
    basis = "modeled_schedule"
    if not allowed:
        arrival, basis = None, "blocked_constraints"
    elif override:
        arrival = override.arrival_on
        basis = "held" if override.hold else "hypothetical_override"
    elif scenario == "disruption" and shipment.status == "held":
        arrival, basis = None, "held"
    if arrival and arrival < bundle.scenario_as_of:
        arrival, basis = None, "past_due_not_recredited"
    overdue = max(0, (bundle.scenario_as_of - shipment.expected_arrival).days)
    delay = (arrival - shipment.expected_arrival).days if arrival else None
    if arrival is None and overdue >= 7:
        level, rule = "Critical", "shipment.overdue_7_days_no_arrival"
    elif arrival is None or (delay is not None and delay > 2):
        level, rule = "High", "shipment.held_blocked_or_delay_over_2_days"
    elif delay is not None and delay > 0:
        level, rule = "Moderate", "shipment.delay_1_or_2_days"
    else:
        level, rule = "Low", "shipment.modeled_schedule_on_time"
    risk = rating(
        "shipment",
        level,
        rule,
        f"{basis}; original status={shipment.status}; modeled arrival={arrival}.",
        {"quantity_vials": shipment.quantity, "delay_days": delay, "overdue_days": overdue},
        ids,
    )
    return result(
        ShipmentRisk,
        bundle,
        as_of=bundle.scenario_as_of,
        scenario=scenario,
        overlay_version=overlay_version(overlay),
        shipment_id=shipment.shipment_id,
        supplier_id=supplier.supplier_id,
        hospital_id=hospital.hospital_id,
        product_id=product.product_id,
        quantity=shipment.quantity,
        original_status=shipment.status,
        scheduled_arrival=shipment.expected_arrival,
        modeled_arrival=arrival,
        arrival_basis=basis,
        delay_days=delay,
        linked_simulated_event_ids=[e.event_id for e in events],
        rating=risk,
        input_values={
            "approved": any(a.approved for a in approvals),
            "handling_compatible": compatible(product, hospital, shipment),
            "temperature_min_c": shipment.temperature_min_c,
            "temperature_max_c": shipment.temperature_max_c,
            "protects_from_light": shipment.protects_from_light,
            "product_storage_min_c": product.storage_min_c,
            "product_storage_max_c": product.storage_max_c,
            "product_requires_light_protection": product.protect_from_light,
            "hospital_storage_min_c": hospital.storage_min_c,
            "hospital_storage_max_c": hospital.storage_max_c,
            "hospital_protects_from_light": hospital.protects_from_light,
            "order_quantity_vials": po.quantity,
            "supplier_country": supplier.country_iso,
            "purchase_order_id": po.purchase_order_id,
            "expected_delivery": po.expected_delivery.isoformat(),
        },
        input_evidence_ids=sorted(set(ids)),
        assumptions=[
            "Baseline counterfactual ignores modeled holds; disruption case honors them.",
            "Eligible scheduled arrivals and override releases are simulated, not confirmed physical deliveries.",
            "POs do not create additional arrivals. Past-due schedules are never recredited to starting inventory.",
        ],
        limitations=[
            "No linkage to the real Rocky Mount event is inferred. Supplier origin is a simulated relationship."
        ],
    )
