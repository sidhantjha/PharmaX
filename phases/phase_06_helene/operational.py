"""Independent hospital CSV admission; every normalized row retains its export hash."""

from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import csv
import json
from pathlib import Path

from contracts.data import (
    ReplayProduct,
    ReplayHospital,
    ReplayInventory,
    ReplayConsumption,
    ReplayPurchaseOrder,
    ReplayShipment,
    ReplayPurchasing,
    ReplayAcknowledgment,
)
from contracts.models import SourceEvidence
from contracts.replay import OperationalImportReceipt, ScenarioOperations
from .sources import PRODUCT_ID


def bags(value, unit, catalog):
    try:
        quantity = Decimal(str(value))
        if unit == "case":
            pack = Decimal(str(catalog["case_bags"]))
            if pack <= 0 or pack != int(pack):
                raise ValueError("INVALID_CASE_CONVERSION")
            quantity *= pack
        elif unit != "bag":
            raise ValueError("UNSUPPORTED_UNIT")
        if not quantity.is_finite() or quantity < 0 or quantity != int(quantity):
            raise ValueError("INVALID_CONTAINER_QUANTITY")
        return int(quantity)
    except (InvalidOperation, KeyError, TypeError):
        raise ValueError("UNRESOLVED_CASE_CONVERSION") from None


def import_operations(folder, cutoff, *, identity_resolver=None, exclude_unusable=False):
    folder = Path(folder)
    clocks = json.loads((folder / "clocks.json").read_text())
    export = datetime.fromisoformat(clocks["exported_at"])
    modeled = datetime.fromisoformat(clocks["modeled_available_at"])
    if export.tzinfo is None or modeled.tzinfo is None or modeled > cutoff:
        raise ValueError("OPERATION_AFTER_CUTOFF")
    anchor = cutoff.astimezone(modeled.tzinfo).date()
    files, receipts, evidences = {}, [], {}
    for feed in (
        "facilities",
        "catalog",
        "inventory",
        "consumption",
        "purchasing",
        "purchase_orders",
        "acknowledgments",
        "shipments",
        "suppliers",
    ):
        path = folder / (feed + ".csv")
        raw = path.read_bytes()
        rows = list(csv.DictReader(raw.decode().splitlines()))
        if not rows:
            raise ValueError("MISSING_OPERATIONAL_FEED:" + feed)
        keys = [r.get("record_id") or r.get("facility_id") for r in rows]
        if len(keys) != len(set(keys)):
            raise ValueError("DUPLICATE_OPERATIONAL_RECORD:" + feed)
        digest = sha256(raw).hexdigest()
        ev = SourceEvidence(
            evidence_id="sim-" + folder.name + "-" + feed,
            kind="simulated",
            source="Fictional NC ERP " + feed,
            source_record_id=folder.name + "/" + feed,
            source_url="https://example.invalid/pharmax/simulated/" + feed,
            observed_at=export,
            content_sha256=digest,
            raw_ref=str(path),
            note="Synthetic export; modeled availability is separate from actual creation.",
        )
        evidences[feed] = ev
        receipts.append(
            OperationalImportReceipt(
                import_id=ev.evidence_id,
                scenario_id="helene-north-cove",
                feed=feed,
                format="csv",
                source_path=str(path),
                content_sha256=digest,
                exported_at=export,
                effective_at=clocks["effective_at"],
                modeled_available_at=modeled,
                evidence=ev,
                mapping_version="nc-csv-v1",
                record_ids=keys,
                validation_status="validated",
                assumptions=["Fictional operational feed; no organizational connectivity."],
            )
        )
        files[feed] = rows
    catalogs = {(r["facility_id"], r["local_sku"]): r for r in files["catalog"]}
    if len(catalogs) != len(files["catalog"]):
        raise ValueError("CATALOG_ALIAS_COLLISION")
    for c in catalogs.values():
        if identity_resolver is not None:
            identity_resolver(c)
        elif (
            c["manufacturer_code"],
            c["ndc"],
            c["volume_ml"],
            c["strength_mg_ml"],
            c["total_mg"],
            c["base_unit"],
        ) != ("2B1324X", "00338-0049-04", "1000", "9", "9000", "bag"):
            raise ValueError("AMBIGUOUS_PRODUCT_IDENTITY")
        if not c["account_id"] or int(c["case_bags"]) <= 0:
            raise ValueError("MISSING_CATALOG_ACCOUNT_OR_PACK")
    product = ReplayProduct(
        product_id=PRODUCT_ID,
        name="Baxter 0.9% Sodium Chloride Injection",
        package_ndc="00338-0049-04",
        presentation="0.9% / 1000 mL unopened bag",
        quantity_unit="bag",
        strength_mg_per_ml=9,
        container_ml=1000,
        total_mg=9000,
        storage_min_c=20,
        storage_max_c=25,
        protect_from_light=False,
        evidence=evidences["catalog"],
        aliases=[
            dict(
                scheme="baxter",
                original="2B1324X",
                normalized="2B1324X",
                review_reference="catalog-exact-presentation",
            ),
            dict(
                scheme="ndc",
                original="00338-0049-04",
                normalized="00338-0049-04",
                review_reference="catalog-segmented-ndc",
            ),
        ],
    )
    hospitals = [
        ReplayHospital(
            hospital_id=r["facility_id"],
            name=r["name"],
            state=r["state"],
            network_id=r["network_id"],
            evidence=evidences["facilities"],
        )
        for r in files["facilities"]
    ]
    supplier_map = {r["facility_id"]: r for r in files["suppliers"]}

    def common(row, feed):
        c = catalogs.get((row["facility_id"], row["local_sku"]))
        if not c:
            raise ValueError("UNMATCHED_LOCAL_SKU")
        relationship = supplier_map.get(row["facility_id"])
        if (
            not relationship
            or relationship["approved"] != "true"
            or relationship["account_id"] != c["account_id"]
            or relationship["supplier_id"] != c["supplier_id"]
        ):
            raise ValueError("SUPPLIER_RELATIONSHIP_UNRESOLVED")
        if row.get("account_id", c["account_id"]) != c["account_id"]:
            raise ValueError("UNMATCHED_CUSTOMER_ACCOUNT")
        if feed == "purchase_orders" and row["supplier_id"] != c["supplier_id"]:
            raise ValueError("UNMATCHED_ORDER_SUPPLIER")
        return dict(
            hospital_id=row["facility_id"],
            product_id=PRODUCT_ID,
            quantity_unit="bag",
            evidence=evidences[feed],
        ), c

    inventory, consumption, purchasing, orders, acks, shipments = [], [], [], [], [], []
    for row in files["inventory"]:
        fields, c = common(row, "inventory")
        snapshot = datetime.fromisoformat(row["snapshot_at"])
        if snapshot.tzinfo is None or not timedelta(0) <= cutoff - snapshot <= timedelta(hours=24):
            raise ValueError("STALE_OR_FUTURE_INVENTORY")
        if exclude_unusable:
            if not row['lot_id'] or row['quality'] not in {'released', 'quarantined', 'recalled'}:
                raise ValueError('MISSING_OR_INVALID_LOT_QUALITY')
            # Validate even excluded quantities; malformed stock is never silently accepted.
            if bags(row['reserved'], row['unit'], c) > bags(row['on_hand'], row['unit'], c):
                raise ValueError('INVALID_RESERVED_QUANTITY')
            if row['quality'] != 'released' or date.fromisoformat(row['expires_on']) < anchor:
                continue
        inventory.append(
            ReplayInventory(
                **fields,
                inventory_id=row["record_id"],
                supplier_id=c["supplier_id"],
                lot_id=row["lot_id"],
                as_of=anchor,
                on_hand=bags(row["on_hand"], row["unit"], c),
                reserved=bags(row["reserved"], row["unit"], c),
                expiry_date=row["expires_on"],
                quality_status=row["quality"],
            )
        )
    for row in files["consumption"]:
        fields, c = common(row, "consumption")
        if date.fromisoformat(row["day"]) >= anchor:
            raise ValueError("FUTURE_CONSUMPTION")
        consumption.append(
            ReplayConsumption(
                **fields,
                consumption_id=row["record_id"],
                day=row["day"],
                quantity=bags(row["quantity"], row["unit"], c),
            )
        )
    for row in files["purchasing"]:
        fields, c = common(row, "purchasing")
        if date.fromisoformat(row["period_end"]) >= anchor:
            raise ValueError("FUTURE_PURCHASING")
        purchasing.append(
            ReplayPurchasing(
                **fields,
                purchasing_id=row["record_id"],
                supplier_id=c["supplier_id"],
                account_id=c["account_id"],
                customer_item_id=row["local_sku"],
                purchased_on=row["period_end"],
                quantity=bags(row["quantity"], row["unit"], c),
                returned_quantity=bags(row["returned"], row["unit"], c),
            )
        )
    for row in files["purchase_orders"]:
        fields, c = common(row, "purchase_orders")
        if date.fromisoformat(row["ordered_on"]) > anchor:
            raise ValueError("FUTURE_ORDER")
        orders.append(
            ReplayPurchaseOrder(
                **fields,
                purchase_order_id=row["record_id"],
                supplier_id=c["supplier_id"],
                account_id=row["account_id"],
                order_line_id=row["line_id"],
                customer_item_id=row["local_sku"],
                quantity=bags(row["quantity"], row["unit"], c),
                ordered_on=row["ordered_on"],
                expected_delivery=row["expected_on"],
            )
        )
    if len({r["po_id"] for r in files["acknowledgments"]}) != len(files["acknowledgments"]):
        raise ValueError("DUPLICATE_ACTIVE_ACKNOWLEDGMENT")
    for row in files["acknowledgments"]:
        fields, c = common(row, "acknowledgments")
        acks.append(
            ReplayAcknowledgment(
                **fields,
                acknowledgment_id=row["record_id"],
                purchase_order_id=row["po_id"],
                order_line_id=row["line_id"],
                account_id=row["account_id"],
                supplier_id=c["supplier_id"],
                revision=int(row["revision"]),
                acknowledged_on=modeled.date(),
                status=row["status"],
                quantity=bags(row["quantity"], row["unit"], c),
                expected_delivery=row["expected_on"] or None,
            )
        )
    for row in files["shipments"]:
        fields, c = common(row, "shipments")
        if date.fromisoformat(row["dispatched_on"]) > modeled.date():
            raise ValueError("UNSUPPORTED_FUTURE_ASN")
        shipments.append(
            ReplayShipment(
                **fields,
                shipment_id=row["record_id"],
                purchase_order_id=row["po_id"],
                supplier_id=c["supplier_id"],
                quantity=bags(row["quantity"], row["unit"], c),
                dispatched_on=row["dispatched_on"],
                expected_arrival=row["arrival_on"],
                status=row["status"],
                origin_country_iso="USA",
                temperature_min_c=20,
                temperature_max_c=25,
                protects_from_light=True,
                account_id=row["account_id"],
                order_line_id=row["line_id"],
                revision=int(row["revision"]),
            )
        )
    ops = ScenarioOperations(
        scenario_id="helene-north-cove",
        network_id="sim-nc-health-system",
        products=[product],
        hospitals=hospitals,
        inventory=inventory,
        consumption=consumption,
        purchasing=purchasing,
        purchase_orders=orders,
        acknowledgments=acks,
        shipments=shipments,
    )
    for h in hospitals:
        if not any(i.hospital_id == h.hospital_id for i in inventory) and not (
            exclude_unusable and any(r['facility_id'] == h.hospital_id for r in files['inventory'])
        ):
            raise ValueError("MISSING_HOSPITAL_INVENTORY:" + h.hospital_id)
        history = [r for r in consumption if r.hospital_id == h.hospital_id]
        if len(history) != 7 or {r.day for r in history} != {anchor - timedelta(days=d) for d in range(1, 8)}:
            raise ValueError("INCOMPLETE_DEMAND_HISTORY:" + h.hospital_id)
    return ops, receipts, files
