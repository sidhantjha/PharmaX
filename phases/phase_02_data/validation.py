"""Cross-record integrity checks only; do not assess risk or select an action."""

from contracts import DataBundle
from phases.phase_01_foundation.sources import digest

COLLECTIONS = (
    "trade_flows",
    "products",
    "shortages",
    "events",
    "recalls",
    "suppliers",
    "hospitals",
    "approvals",
    "inventory",
    "consumption",
    "purchase_orders",
    "shipments",
    "evidence",
)
ID_FIELDS = dict(
    zip(
        COLLECTIONS,
        (
            "record_id",
            "product_id",
            "record_id",
            "event_id",
            "recall_id",
            "supplier_id",
            "hospital_id",
            "approval_id",
            "inventory_id",
            "consumption_id",
            "purchase_order_id",
            "shipment_id",
            "evidence_id",
        ),
    )
)


def version_for(data):
    return digest({k: v for k, v in data.items() if k != "dataset_version"})


def validate_bundle(value):
    b = DataBundle.model_validate(value)
    if version_for(b.model_dump(mode="json")) != b.dataset_version:
        raise ValueError("Dataset version mismatch")
    groups = {}
    for collection in COLLECTIONS:
        items = getattr(b, collection)
        groups[collection] = {getattr(r, ID_FIELDS[collection]): r for r in items}
        if len(groups[collection]) != len(items):
            raise ValueError("Duplicate record identifiers: " + collection)
    if len(b.hospitals) != 3 or {h.name for h in b.hospitals} != {
        "Central Illinois Regional Hospital",
        "North Illinois Medical Center",
        "South Illinois Oncology Center",
    }:
        raise ValueError("Exactly the three fictional hospitals are required")
    for collection in COLLECTIONS:
        for item in getattr(b, collection):
            ev = item if collection == "evidence" else item.evidence
            if ev.evidence_id not in groups["evidence"] or ev != groups["evidence"][ev.evidence_id]:
                raise ValueError("Missing or inconsistent evidence")
            if any(parent not in groups["evidence"] for parent in ev.parent_ids):
                raise ValueError("Evidence parent missing")
            if b.mode == "fixture" and ev.kind != "simulated":
                raise ValueError("Fixtures must be entirely simulated")
            if (
                collection
                in (
                    "suppliers",
                    "hospitals",
                    "approvals",
                    "inventory",
                    "consumption",
                    "purchase_orders",
                    "shipments",
                )
                and ev.kind != "simulated"
            ):
                raise ValueError("Supply relationships must remain synthetic")
            for field, target in (
                ("hospital_id", "hospitals"),
                ("supplier_id", "suppliers"),
                ("product_id", "products"),
            ):
                ref = getattr(item, field, None)
                if ref and collection != target and ref not in groups[target]:
                    raise ValueError("Dangling relationship: " + field)
    for item in b.inventory:
        if item.as_of != b.scenario_as_of:
            raise ValueError("Inventory scenario date mismatch")
    for item in b.consumption:
        if item.day >= b.scenario_as_of:
            raise ValueError("Consumption must be historical")
    approval = {(a.hospital_id, a.supplier_id, a.product_id) for a in b.approvals if a.approved}
    for po in b.purchase_orders:
        if (po.hospital_id, po.supplier_id, po.product_id) not in approval:
            raise ValueError("Unapproved order")
    quantities = {}
    for ship in b.shipments:
        po = groups["purchase_orders"].get(ship.purchase_order_id)
        if po is None or any(
            getattr(ship, f) != getattr(po, f)
            for f in ("hospital_id", "supplier_id", "product_id", "quantity_unit")
        ):
            raise ValueError("Shipment order relationship mismatch")
        if ship.dispatched_on < po.ordered_on or ship.expected_arrival < po.ordered_on:
            raise ValueError("Shipment before order")
        quantities[po.purchase_order_id] = quantities.get(po.purchase_order_id, 0) + ship.quantity
        if quantities[po.purchase_order_id] > po.quantity:
            raise ValueError("Shipment quantities exceed order")
        if groups["suppliers"][ship.supplier_id].country_iso != ship.origin_country_iso:
            raise ValueError("Shipment origin disagrees with modeled supplier")
        for event_id in ship.disruption_event_ids:
            if event_id not in groups["events"] or groups["events"][event_id].evidence.kind != "simulated":
                raise ValueError("Shipment exposure must reference a modeled event")
    return b
