from copy import deepcopy
from datetime import date
import json
import shutil
import sqlite3

import httpx
import pytest

from contracts.replay import document_time
from phases.phase_05_product.service import OperatorService
from phases.phase_06_helene.fhir import inventory_report_rows, canonical_inventory_report
from phases.phase_06_helene.fixtures import DAYS, generate, write_csv
from phases.phase_06_helene.operational import bags, import_operations
from phases.phase_06_helene.pipeline import OPERATIONS, replay_checkpoint
from phases.phase_06_helene.sources import ROOT, admit_source, load_sources, extract_allocation
from phases.phase_06_helene.specialists import ReplayTools


@pytest.fixture(scope="module")
def sources():
    return load_sources()


def test_real_preserved_bytes_extract_verified_rows_and_page_locations(sources):
    assert len(sources) == 4
    assert [len(p) for _, _, p in sources] == [1, 7, 6, 2]
    assert [
        (r.page, r.allocation_percent, str(r.effective_on), r.kind) for _, rows, _ in sources for r in rows
    ] == [
        (5, 40, "2024-10-01", "observed"),
        (4, 60, "2024-10-09", "observed"),
        (1, 60, "2024-11-11", "observed"),
        (1, 80, "2024-11-25", "projected"),
        (1, 100, "2024-12-16", "projected"),
        (1, 100, "2024-12-30", "projected"),
    ]
    assert all(
        r.original_ndc == "00338-0049-04" and r.original_baxter_code == "2B1324X"
        for _, rows, _ in sources
        for r in rows
    )
    assert all(s.evidence.observed_at.year == 2026 and s.availability_proof is None for s, _, _ in sources)


def test_missing_corrupt_source_and_ambiguous_extraction(tmp_path, sources):
    manifest = json.loads((ROOT / "manifest.json").read_text())
    with pytest.raises(FileNotFoundError):
        admit_source(manifest[0], tmp_path)
    (tmp_path / manifest[0]["file"]).write_bytes(b"%PDF-corrupt")
    with pytest.raises(ValueError, match="HASH"):
        admit_source(manifest[0], tmp_path)
    source, _, pages = sources[1]
    broken = list(pages)
    broken[4] = broken[4].replace("2B1324X", "2B1323Q")
    with pytest.raises(ValueError, match="MISSING"):
        extract_allocation(source, broken)
    broken = list(pages)
    broken[4] += pages[4]
    with pytest.raises(ValueError, match="DUPLICATE"):
        extract_allocation(source, broken)


def test_seeded_exports_independent_and_reproducible(tmp_path):
    a = generate(tmp_path / "a", created_at="2026-10-26T21:00:00Z")
    b = generate(tmp_path / "b", created_at="2026-10-26T21:00:00Z")
    for path in a.rglob("*"):
        if path.is_file():
            assert path.read_bytes() == (b / path.relative_to(a)).read_bytes()
    ops, receipts, files = import_operations(a / DAYS[0], document_time(date.fromisoformat(DAYS[0])))
    assert len(ops.hospitals) == 3 and len(ops.consumption) == 21
    assert len({r["local_sku"] for r in files["catalog"]}) == 3
    assert all(r.evidence.kind == "simulated" and r.exported_at.year == 2026 for r in receipts)
    assert all(r.modeled_available_at == document_time(date.fromisoformat(DAYS[0])) for r in receipts)


def test_four_checkpoints_recalculate_and_exclude_future(monkeypatch, sources):
    monkeypatch.setattr(httpx.Client, "send", lambda *a, **kw: pytest.fail("offline replay touched HTTP"))
    parent = None
    totals = []
    for index, day in enumerate(DAYS):
        c = replay_checkpoint(day, sources=sources, parent=parent)
        assert len(c.admission.sources) == index + 1
        assert all(str(s.issue_date) <= day for s in c.admission.sources)
        assert len(c.forecasts) == len(c.mitigated) == 3
        for f in c.forecasts + c.mitigated:
            for p in f.points:
                assert p.closing_physical == p.opening_physical - p.expired_physical + p.received - p.served
                assert p.served + p.unmet == p.demand
                assert p.reserved in (20, 100, 18)
        assert any("DONOR_SHORTAGE" in x["failures"] for x in c.candidates)
        assert any("UNCONFIRMED_ALTERNATIVE_SUPPLY" in x["failures"] for x in c.candidates)
        assert all(x["conservation"]["conserved"] for x in c.candidates if x.get("conservation"))
        if parent:
            assert c.parent_id == parent.investigation_id
            assert c.reconciliation[-1]["deltas"]
        assert "MISSING_ACKNOWLEDGMENT:po-south" in c.unresolved
        assert next(f for f in c.forecasts if f.hospital_id == "sim-nc-south").total_received == 0
        totals.append([f.total_unmet for f in c.forecasts])
        parent = c
    assert totals[0] != totals[1] != totals[2]
    assert len([r for r in parent.admission.allocations if r.kind == "projected"]) == 3


def test_strict_historical_is_not_claimed(sources):
    with pytest.raises(ValueError):
        replay_checkpoint(DAYS[1], sources=sources, strict=True)


def test_raw_input_change_reimports_changes_decision_and_lineage(tmp_path, sources):
    folder = tmp_path / "operations"
    shutil.copytree(OPERATIONS, folder)
    before = replay_checkpoint(DAYS[1], operations=folder, sources=sources)
    import csv

    path = folder / DAYS[1] / "inventory.csv"
    rows = list(csv.DictReader(path.read_text().splitlines()))
    rows[0]["on_hand"] = str(int(rows[0]["on_hand"]) + 1000)
    write_csv(path, rows)
    after = replay_checkpoint(DAYS[1], operations=folder, sources=sources, parent=before)
    assert after.dataset_version != before.dataset_version and after.parent_id == before.investigation_id
    assert after.forecasts[0].total_unmet == 0 < before.forecasts[0].total_unmet
    assert after.selected != before.selected
    old = next(r for r in before.admission.imports if r.feed == "inventory")
    new = next(r for r in after.admission.imports if r.feed == "inventory")
    assert old.content_sha256 != new.content_sha256
    assert after.reconciliation[-1]["changed_input_ids"] == [new.import_id]
    service = OperatorService(tmp_path / "store")
    service.helene_store.save(before)
    service.helene_store.save(after)
    assert service.helene_store.get(before.investigation_id) == before


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_inventory",
        "stale",
        "identity",
        "case",
        "duplicate_order",
        "missing_field",
        "missing_baseline",
        "asn_ack",
        "future",
    ],
)
def test_negative_raw_operational_controls(tmp_path, sources, mutation):
    import csv

    folder = tmp_path / "ops"
    shutil.copytree(OPERATIONS, folder)
    feed = {
        "missing_inventory": "inventory",
        "stale": "inventory",
        "identity": "catalog",
        "case": "inventory",
        "duplicate_order": "purchase_orders",
        "missing_field": "purchase_orders",
        "missing_baseline": "purchasing",
        "asn_ack": "acknowledgments",
        "future": "consumption",
    }[mutation]
    path = folder / DAYS[1] / (feed + ".csv")
    rows = list(csv.DictReader(path.read_text().splitlines()))
    if mutation == "missing_inventory":
        rows = rows[1:]
    elif mutation == "stale":
        rows[0]["snapshot_at"] = "2024-09-25T12:00:00Z"
    elif mutation == "identity":
        rows[0]["ndc"] = "00338004904"
    elif mutation == "case":
        rows[0]["unit"] = "mL"
    elif mutation == "duplicate_order":
        rows.append(rows[0])
    elif mutation == "missing_field":
        rows[0]["account_id"] = ""
    elif mutation == "missing_baseline":
        rows = rows[1:]
    elif mutation == "asn_ack":
        rows[0]["quantity"] = "1"
    else:
        rows[0]["day"] = "2024-10-10"
    write_csv(path, rows)
    with pytest.raises((ValueError, KeyError)):
        replay_checkpoint(DAYS[1], operations=folder, sources=sources)


def test_allocation_only_cannot_invent_shipment(sources):
    altered = deepcopy(sources)
    altered[1][1][0].allocation_percent = 50
    base = replay_checkpoint(DAYS[1], sources=sources)
    changed = replay_checkpoint(DAYS[1], sources=altered)
    assert [f.total_received for f in changed.forecasts] == [f.total_received for f in base.forecasts]
    altered[1][1][0].allocation_percent = 20
    conflict = replay_checkpoint(DAYS[1], sources=altered)
    assert any(x.startswith("ALLOCATION_ASN_CONFLICT") for x in conflict.unresolved)
    assert conflict.selected == []


def test_case_unit_conversion_is_explicit():
    assert bags("2", "case", {"case_bags": "12"}) == 24
    for pack in ("", "0", "1.5"):
        with pytest.raises(ValueError):
            bags("2", "case", {"case_bags": pack})


def test_supplier_identity_and_split_asn_cannot_override_acknowledgment(tmp_path, sources):
    import csv

    for mutation in ("supplier", "duplicate_ack", "split_asn"):
        folder = tmp_path / mutation
        shutil.copytree(OPERATIONS, folder)
        feed = {"supplier": "purchase_orders", "duplicate_ack": "acknowledgments", "split_asn": "shipments"}[
            mutation
        ]
        path = folder / DAYS[1] / (feed + ".csv")
        rows = list(csv.DictReader(path.read_text().splitlines()))
        if mutation == "supplier":
            rows[0]["supplier_id"] = "unmatched-supplier"
        elif mutation == "duplicate_ack":
            duplicate = dict(rows[0], record_id="ack-extra")
            rows.append(duplicate)
        else:
            duplicate = dict(rows[0], record_id="asn-extra", quantity="50")
            rows[0]["quantity"] = "50"
            rows.append(duplicate)  # 100 <= PO 210, but exceeds independently acknowledged 84.
        write_csv(path, rows)
        with pytest.raises(ValueError):
            replay_checkpoint(DAYS[1], operations=folder, sources=sources)


def test_store_api_optimistic_review_and_restart(tmp_path, sources):
    service = OperatorService(tmp_path)
    c = replay_checkpoint(DAYS[0], sources=sources)
    service.helene_store.save(c)
    assert (
        service.handle("GET", "/api/v1/helene/cases")[1]["items"][0]["investigation_id"] == c.investigation_id
    )
    action = c.actions[0]
    url = f"/api/v1/helene/cases/{c.investigation_id}/actions/{action['action_id']}/review"
    payload = dict(expected_revision=1, status="READY_FOR_REVIEW", reviewer="test human", attested=True)
    assert service.handle("POST", url, payload)[0] == 200
    assert service.handle("POST", url, payload)[0] == 409
    second = OperatorService(tmp_path)
    assert second.helene_store.actions(c)[0]["dispatch_available"] is False
    with second.store.connect() as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("DELETE FROM helene_cases")


def test_mocked_model_selects_real_role_tools_with_budgets(sources):
    c = replay_checkpoint(DAYS[0], sources=sources)
    tools = ReplayTools(c)

    def selector(role, allowed, history):
        used = [r["tool"] for r in history if r["role"] == role]
        return next((t for t in allowed if t not in used), None)

    receipts = tools.mocked_investigation(selector)
    assert any(
        r["tool"] == "forecast.read" and r["result"][0]["total_unmet"] == c.forecasts[0].total_unmet
        for r in receipts
    )
    with pytest.raises(ValueError):
        tools.call("verification", "inventory.read")


def test_fhir_snapshot_with_required_supplements():
    resource = dict(
        resourceType="InventoryReport",
        id="sim-snapshot",
        status="active",
        countType="snapshot",
        reportedDateTime="2024-10-01T23:00:00-04:00",
        inventoryListing=[
            dict(
                location={"reference": "Location/sim-central"},
                item=[
                    dict(
                        quantity=dict(value=24, unit="bag"),
                        item={"reference": {"reference": "Medication/sim-saline"}},
                    )
                ],
            )
        ],
    )
    mapping = {
        ("Location/sim-central", "Medication/sim-saline"): dict(
            facility_id="sim-nc-central",
            local_sku="IV-NS1L",
            lot_id="sim-lot",
            reserved=4,
            expires_on="2025-01-01",
            quality="released",
        )
    }
    assert inventory_report_rows(resource, mapping)[0]["on_hand"] == 24
    resource["countType"] = "difference"
    with pytest.raises(ValueError):
        inventory_report_rows(resource, mapping)


def test_fhir_original_hash_and_canonical_inventory():
    from pathlib import Path

    path = Path(__file__).parent / "fixtures/fhir/inventory-report.json"
    mapping = {
        ("Location/sim-nc-central", "Medication/sim-saline-1000"): dict(
            facility_id="sim-nc-central",
            local_sku="IV-NS1L",
            lot_id="sim-lot",
            reserved=4,
            expires_on="2025-01-01",
            quality="released",
        )
    }
    inventory, receipt = canonical_inventory_report(
        path,
        mapping,
        as_of="2024-10-02",
        exported_at="2026-10-26T21:00:00Z",
        modeled_available_at="2024-10-01T23:00:00-04:00",
    )
    assert inventory[0].on_hand == 24 and inventory[0].reserved == 4 and inventory[0].quantity_unit == "bag"
    assert inventory[0].evidence.content_sha256 == receipt.content_sha256
    assert receipt.format == "fhir-r5-inventoryreport"


def test_held_asn_is_not_supply_and_expiry_blocks_transfer(tmp_path, sources):
    import csv

    folder = tmp_path / "ops"
    shutil.copytree(OPERATIONS, folder)
    path = folder / DAYS[1] / "shipments.csv"
    rows = list(csv.DictReader(path.read_text().splitlines()))
    rows[0]["status"] = "held"
    write_csv(path, rows)
    case = replay_checkpoint(DAYS[1], operations=folder, sources=sources)
    assert case.forecasts[0].total_received == 0
    assert "UNCONFIRMED_OR_PAST_DUE_ASN:asn-central" in case.unresolved
    path = folder / DAYS[1] / "inventory.csv"
    rows = list(csv.DictReader(path.read_text().splitlines()))
    rows[1]["expires_on"] = "2024-10-03"
    write_csv(path, rows)
    case = replay_checkpoint(DAYS[1], operations=folder, sources=sources)
    assert any("TRANSFER_LOT_EXPIRY_UNVERIFIED" in c["failures"] for c in case.candidates)
    assert not any(
        c["source_id"] == "sim-nc-north" for c in case.candidates if c["candidate_id"] in case.selected
    )
