"""Deterministic, fictional planning inputs only. No forecast or mitigation result."""

from datetime import date, datetime, timedelta, timezone
from contracts import (
    DrugProduct,
    Supplier,
    Hospital,
    SupplierApproval,
    InventoryRecord,
    DailyConsumption,
    PurchaseOrder,
    Shipment,
    DisruptionEvent,
    SourceEvidence,
)
from phases.phase_01_foundation.sources import digest

AS_OF = date(2023, 7, 24)
SCENARIO_URL = "https://github.com/Project-Lumyn/Project-Dot4/blob/codex/pharmax-phase-2-data/phases/phase_02_data/scenario.py"


def simulated(model, **data):
    data = {k: v.isoformat() if isinstance(v, date) else v for k, v in data.items()}
    evidence = SourceEvidence(
        evidence_id="sim:" + digest(data),
        kind="simulated",
        source="fictional-illinois-v1",
        source_url=SCENARIO_URL,
        observed_at=datetime(2023, 7, 24, tzinfo=timezone.utc),
        content_sha256=digest(data),
        note="Synthetic scenario timestamp, not a real observation. Fictional supply relationship; no clinical or operational authority.",
    )
    return model(**data, evidence=evidence)


def build_scenario():
    products = [
        simulated(
            DrugProduct,
            product_id=f"sim-carboplatin-{mg}",
            name="Fictional carboplatin presentation",
            presentation=f"{mg} mg / {ml} mL unopened single-use vial; modeled product, not a clinical substitution",
            strength_mg_per_ml=10,
            container_ml=ml,
            total_mg=mg,
            quantity_unit="vial",
            storage_min_c=20,
            storage_max_c=25,
            protect_from_light=True,
        )
        for mg, ml in ((450, 45), (150, 15))
    ]
    suppliers = [
        simulated(
            Supplier,
            supplier_id="sim-atlas",
            name="Fictional Atlas Pharma",
            country_iso="IND",
            role="modeled_manufacturer",
        ),
        simulated(
            Supplier,
            supplier_id="sim-lakes",
            name="Fictional Great Lakes Distributor",
            country_iso="USA",
            role="modeled_distributor",
        ),
    ]
    hospitals = [
        simulated(Hospital, hospital_id="sim-" + key, name=name)
        for key, name in (
            ("central", "Central Illinois Regional Hospital"),
            ("north", "North Illinois Medical Center"),
            ("south", "South Illinois Oncology Center"),
        )
    ]
    primary = "sim-carboplatin-450"
    approvals = [
        simulated(
            SupplierApproval,
            approval_id=f"approval-{h.hospital_id}-{s.supplier_id}",
            hospital_id=h.hospital_id,
            supplier_id=s.supplier_id,
            product_id=primary,
            approved=True,
        )
        for h in hospitals
        for s in suppliers
    ]
    inventory = [
        simulated(
            InventoryRecord,
            inventory_id="stock-" + key,
            hospital_id="sim-" + key,
            product_id=primary,
            supplier_id="sim-lakes",
            lot_id="FICTIONAL-LOT-" + key,
            as_of=AS_OF,
            on_hand=stock,
            reserved=reserved,
            expiry_date=expiry,
            quality_status=status,
        )
        for key, stock, reserved, expiry, status in (
            ("central", 12, 8, date(2023, 9, 30), "released"),
            ("north", 80, 20, date(2023, 9, 30), "released"),
            ("south", 100, 10, date(2023, 7, 25), "quarantined"),
        )
    ]
    inventory.append(
        simulated(
            InventoryRecord,
            inventory_id="stock-north-other-strength",
            hospital_id="sim-north",
            product_id="sim-carboplatin-150",
            supplier_id="sim-lakes",
            lot_id="FICTIONAL-OTHER",
            as_of=AS_OF,
            on_hand=200,
            reserved=0,
            expiry_date=date(2023, 10, 31),
            quality_status="released",
        )
    )
    consumption = [
        simulated(
            DailyConsumption,
            consumption_id=f"use-{key}-{offset}",
            hospital_id="sim-" + key,
            product_id=primary,
            day=AS_OF - timedelta(days=offset),
            quantity=qty,
        )
        for key, qty in (("central", 6), ("north", 4), ("south", 5))
        for offset in range(1, 8)
    ]
    orders, shipments = [], []
    for key, supplier, country, quantity, arrival, status, events in (
        ("central", "sim-atlas", "IND", 60, date(2023, 7, 26), "held", ["sim-dispatch-hold"]),
        ("north", "sim-lakes", "USA", 40, date(2023, 7, 25), "in_transit", []),
        ("south", "sim-lakes", "USA", 30, date(2023, 7, 27), "planned", []),
    ):
        orders.append(
            simulated(
                PurchaseOrder,
                purchase_order_id="po-" + key,
                hospital_id="sim-" + key,
                supplier_id=supplier,
                product_id=primary,
                quantity=quantity,
                ordered_on=date(2023, 7, 20),
                expected_delivery=arrival,
            )
        )
        shipments.append(
            simulated(
                Shipment,
                shipment_id="ship-" + key,
                purchase_order_id="po-" + key,
                hospital_id="sim-" + key,
                supplier_id=supplier,
                product_id=primary,
                quantity=quantity,
                dispatched_on=date(2023, 7, 24),
                expected_arrival=arrival,
                status=status,
                origin_country_iso=country,
                temperature_min_c=20,
                temperature_max_c=25,
                protects_from_light=True,
                disruption_event_ids=events,
            )
        )
    events = [
        simulated(
            DisruptionEvent,
            event_id="sim-dispatch-hold",
            title="Fictional Atlas dispatch hold",
            event_type="logistics",
            location="Modeled dispatch location, India",
            country_iso="IND",
            starts_on=date(2023, 7, 22),
            products_stated=[primary],
            established="Synthetic planning input: modeled export dispatch is held.",
            uncertain_implications="No observed supplier outage or causal link to the real FDA storm statement. No forecast, release date or mitigation outcome is asserted.",
        )
    ]
    return dict(
        products=products,
        suppliers=suppliers,
        hospitals=hospitals,
        approvals=approvals,
        inventory=inventory,
        consumption=consumption,
        purchase_orders=orders,
        shipments=shipments,
        events=events,
    )
