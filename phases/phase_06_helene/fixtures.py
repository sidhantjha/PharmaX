"""Seeded independent fictional hospital exports; manufacturer PDFs are never inputs."""

from datetime import date, datetime, timedelta, timezone
import csv
import json
from pathlib import Path
import random
from zoneinfo import ZoneInfo

DAYS = ("2024-09-29", "2024-10-01", "2024-10-09", "2024-11-11")
FACILITIES = [
    ("central", "Piedmont Central (fictional)", -81.0, 35.9),
    ("north", "Foothills North (fictional)", -81.4, 36.3),
    ("south", "Catawba South (fictional)", -80.8, 35.4),
]


def write_csv(path, rows):
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def generate(root, seed=629, created_at=None):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    created = created_at or datetime.now(timezone.utc).isoformat()
    manifest = dict(
        classification="simulated",
        seed=seed,
        created_at=created,
        assumptions=[
            "Entire NC system, accounts, positions and all operations are fictional.",
            "Different ERP SKUs represent the exact same 1000 mL presentation.",
            "Case conversion 12 bags/case is an explicit synthetic catalog assumption, not a Baxter pack claim.",
            "Stocks/demand/ASN are separately authored simulation parameters, not parsed from letters.",
            "Explicit simulated accepted/ASN quantities by checkpoint: Sep 180/150, Oct1 84/67, Oct9 126/100, Nov 126/100.",
            "Daily consumption is unreserved demand; ring-fenced reservations are not consumed twice.",
            "Alternative supply is absent; only validated local same-product transfers are considered.",
            "14-day rolling forecast; inventory <=24h old; 7 complete demand days required.",
            "Weekly entitlement convention uses 184 baseline days, floor rounding; no entitlement implies an ASN.",
            "No real patient data, ERP access, FHIR service, EDI partner or Zetaris query.",
        ],
    )
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    for checkpoint in DAYS:
        folder = root / checkpoint
        folder.mkdir(exist_ok=True)
        anchor = date.fromisoformat(checkpoint) + timedelta(days=1)
        effective = (
            checkpoint + "T23:00:00-04:00" if checkpoint < "2024-11-01" else checkpoint + "T23:00:00-05:00"
        )
        groups = {
            k: []
            for k in (
                "facilities",
                "catalog",
                "inventory",
                "consumption",
                "purchasing",
                "purchase_orders",
                "acknowledgments",
                "shipments",
                "suppliers",
            )
        }
        for index, (name, label, lng, lat) in enumerate(FACILITIES):
            facility = "sim-nc-" + name
            sku = ["IV-NS1L", "SAL-1000", "NACL009-L"][index]
            common = dict(facility_id=facility, local_sku=sku)
            groups["facilities"].append(
                dict(
                    facility_id=facility,
                    name=label,
                    state="NC",
                    network_id="sim-nc-health-system",
                    longitude=lng,
                    latitude=lat,
                )
            )
            groups["catalog"].append(
                dict(
                    **common,
                    manufacturer_code="2B1324X",
                    ndc="00338-0049-04",
                    volume_ml=1000,
                    strength_mg_ml=9,
                    total_mg=9000,
                    base_unit="bag",
                    case_bags=12,
                    account_id="sim-account-" + name,
                    supplier_id="sim-baxter-channel",
                )
            )
            groups["suppliers"].append(
                dict(
                    facility_id=facility,
                    supplier_id="sim-baxter-channel",
                    account_id="sim-account-" + name,
                    approved="true",
                    transfers_allowed="true",
                )
            )
            # New exports at each anchor: snapshots are not carried forward as factual measurements.
            stock = [190, 720, 130][index] + rng.randrange(-8, 9)
            groups["inventory"].append(
                dict(
                    record_id="stock-" + name,
                    **common,
                    snapshot_at=effective,
                    lot_id="lot-" + name,
                    on_hand=stock,
                    reserved=[20, 100, 18][index],
                    unit="bag",
                    expires_on=(anchor + timedelta(days=90)).isoformat(),
                    quality="released",
                )
            )
            for offset in range(7, 0, -1):
                day = anchor - timedelta(days=offset)
                groups["consumption"].append(
                    dict(
                        record_id=f"use-{name}-{day}",
                        **common,
                        day=day.isoformat(),
                        quantity=[30, 24, 20][index] + rng.randrange(-2, 3),
                        unit="bag",
                    )
                )
            for month, days in ((3, 31), (4, 30), (5, 31), (6, 30), (7, 31), (8, 31)):
                groups["purchasing"].append(
                    dict(
                        record_id=f"purchase-{name}-{month}",
                        **common,
                        account_id="sim-account-" + name,
                        period_start=f"2024-{month:02d}-01",
                        period_end=f"2024-{month:02d}-{days}",
                        quantity=days * [30, 24, 20][index],
                        returned=0,
                        unit="bag",
                    )
                )
            order = dict(
                record_id="po-" + name,
                **common,
                account_id="sim-account-" + name,
                supplier_id="sim-baxter-channel",
                line_id="line-1",
                quantity=[210, 168, 140][index],
                unit="bag",
                ordered_on=(anchor - timedelta(days=1)).isoformat(),
                expected_on=(anchor + timedelta(days=2)).isoformat(),
            )
            groups["purchase_orders"].append(order)
            if index != 2:  # South's expected PO is deliberately not confirmed.
                accepted = {
                    "2024-09-29": [180, 150],
                    "2024-10-01": [84, 67],
                    "2024-10-09": [126, 100],
                    "2024-11-11": [126, 100],
                }[checkpoint][index]
                groups["acknowledgments"].append(
                    dict(
                        record_id="ack-" + name,
                        po_id="po-" + name,
                        **common,
                        account_id="sim-account-" + name,
                        line_id="line-1",
                        revision=1,
                        quantity=accepted,
                        unit="bag",
                        status="accepted",
                        expected_on=order["expected_on"],
                    )
                )
                groups["shipments"].append(
                    dict(
                        record_id="asn-" + name,
                        po_id="po-" + name,
                        **common,
                        account_id="sim-account-" + name,
                        line_id="line-1",
                        revision=1,
                        quantity=accepted,
                        unit="bag",
                        dispatched_on=checkpoint,
                        arrival_on=order["expected_on"],
                        status="in_transit",
                    )
                )
        for feed, rows in groups.items():
            write_csv(folder / (feed + ".csv"), rows)
        (folder / "clocks.json").write_text(
            json.dumps(
                dict(
                    exported_at=created,
                    modeled_available_at=datetime.combine(
                        anchor, datetime.min.time(), ZoneInfo("America/New_York")
                    ).isoformat(),
                    effective_at=effective,
                ),
                indent=2,
            )
            + "\n"
        )
    # Deliberately imperfect candidate rows are separately quarantined, never silently imported.
    write_csv(
        root / "imperfect.csv",
        [
            dict(
                record_id="bad-case",
                local_sku="NS-AMBIG",
                ndc="00338004904",
                quantity=2,
                unit="case",
                case_bags="",
                reason="Ambiguous NDC and unknown pack size",
            ),
            dict(
                record_id="stale-stock",
                local_sku="IV-NS1L",
                ndc="00338-0049-04",
                quantity=40,
                unit="bag",
                case_bags="",
                reason="Snapshot older than 24h; reject as current stock",
            ),
        ],
    )
    return root


if __name__ == "__main__":
    generate(Path(__file__).parent / "tests/fixtures/operations")
