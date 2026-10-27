"""Read-only R5 snapshot subset; requires explicit local mappings and lot supplements."""

from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path

from contracts.data import ReplayInventory
from contracts.models import SourceEvidence
from contracts.replay import OperationalImportReceipt


def inventory_report_rows(resource, mappings):
    if (
        resource.get("resourceType") != "InventoryReport"
        or resource.get("status") != "active"
        or resource.get("countType") != "snapshot"
        or "operationType" in resource
    ):
        raise ValueError("UNSUPPORTED_FHIR_INVENTORY_SEMANTICS")
    reported = datetime.fromisoformat(resource["reportedDateTime"].replace("Z", "+00:00"))
    if reported.tzinfo is None:
        raise ValueError("FHIR_REPORT_TIMEZONE_REQUIRED")
    rows = []
    for group_index, listing in enumerate(resource.get("inventoryListing", [])):
        location = listing["location"]["reference"]
        for index, item in enumerate(listing["item"]):
            reference = item["item"]["reference"]["reference"]
            mapping = mappings.get((location, reference))
            if mapping is None:
                raise ValueError("UNMATCHED_FHIR_PRODUCT_OR_LOCATION")
            quantity = item["quantity"]
            if (
                quantity.get("unit") != "bag"
                or type(quantity.get("value")) is not int
                or quantity["value"] < 0
            ):
                raise ValueError("UNSUPPORTED_FHIR_UNIT")
            rows.append(
                dict(
                    record_id=resource["id"] + "-" + str(group_index) + "-" + str(index),
                    facility_id=mapping["facility_id"],
                    local_sku=mapping["local_sku"],
                    snapshot_at=listing.get("countingDateTime", resource["reportedDateTime"]),
                    on_hand=quantity["value"],
                    reserved=mapping["reserved"],
                    lot_id=mapping["lot_id"],
                    expires_on=mapping["expires_on"],
                    quality=mapping["quality"],
                    unit="bag",
                )
            )
    if not rows:
        raise ValueError("EMPTY_FHIR_REPORT")
    return rows


def canonical_inventory_report(path, mappings, *, as_of, exported_at, modeled_available_at):
    """Same canonical inventory/receipt types as CSV, preserving original FHIR bytes."""
    raw = Path(path).read_bytes()
    if len(raw) > 500000:
        raise ValueError("FHIR_RESPONSE_BUDGET")
    resource = json.loads(raw)
    rows = inventory_report_rows(resource, mappings)
    digest = sha256(raw).hexdigest()
    ev = SourceEvidence(
        evidence_id="sim-fhir-" + digest[:16],
        kind="simulated",
        source="Fictional R5 inventory export",
        source_url="https://example.invalid/pharmax/fhir",
        observed_at=exported_at,
        content_sha256=digest,
        raw_ref=str(path),
        note="Synthetic R5 snapshot with required explicit lot/reservation supplements.",
    )
    inventory = []
    for row in rows:
        inventory.append(
            ReplayInventory(
                inventory_id=row["record_id"],
                hospital_id=row["facility_id"],
                product_id="sim-baxter-saline-1000",
                supplier_id="sim-baxter-channel",
                lot_id=row["lot_id"],
                as_of=as_of,
                on_hand=row["on_hand"],
                reserved=row["reserved"],
                expiry_date=row["expires_on"],
                quality_status=row["quality"],
                quantity_unit="bag",
                evidence=ev,
            )
        )
    receipt = OperationalImportReceipt(
        import_id=ev.evidence_id,
        scenario_id="helene-north-cove",
        feed="inventory",
        format="fhir-r5-inventoryreport",
        source_path=str(path),
        content_sha256=digest,
        exported_at=exported_at,
        effective_at=resource["reportedDateTime"],
        modeled_available_at=modeled_available_at,
        evidence=ev,
        mapping_version="r5-snapshot-subset-v1",
        record_ids=[r.inventory_id for r in inventory],
        validation_status="validated",
        assumptions=["Simulated snapshot, explicit location/item mapping and lot/reservations."],
    )
    return inventory, receipt
