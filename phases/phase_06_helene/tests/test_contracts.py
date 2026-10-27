"""Synthetic contract examples only: no Baxter bytes or hospital observations."""

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path

import httpx
import pytest
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from contracts.data import DrugProduct, Hospital, ProductAlias, ReplayProduct
from contracts.replay import (
    AllocationNotice, OperationalImportReceipt, ScenarioOperations, ScenarioReplayRequest, document_time,
)
from contracts.risk import Quantity
from phases.phase_02_data.pipeline import load_bundle

ROOT = Path(__file__).resolve().parents[3]


def evidence(key="letter", kind="observed", **changes):
    value = dict(evidence_id=key, kind=kind, source="synthetic-contract-example",
                 source_record_id=key, source_url="https://example.org/contract-example",
                 observed_at="2026-10-26T12:00:00Z", published_on="2024-10-01",
                 content_sha256="a" * 64, raw_ref="example-only/letter", parent_ids=[])
    value.update(changes)
    return value


def product():
    return dict(product_id="sim-saline", name="Contract test saline", package_ndc="00338-0049-04",
                presentation="1000 mL bag", strength_mg_per_ml=9, container_ml=1000, total_mg=9000,
                quantity_unit="bag", evidence=evidence("catalog", "simulated",
                    observed_at="2024-09-28T12:00:00Z", published_on=None),
                aliases=[dict(scheme="baxter", original="2B1324X", normalized="2B1324X",
                              review_reference="synthetic-test-assertion-only"),
                         dict(scheme="ndc", original="00338-0049-04", normalized="00338-0049-04",
                              review_reference="synthetic-test-assertion-only")])


def request(strict=False):
    ev = evidence()
    source = dict(revision_id="revision-1", publisher="Example only", document_id="letter",
                  issue_date="2024-10-01", evidence=ev, extraction_status="validated",
                  extractor_version="test-only", validation_reference="synthetic-test")
    if strict:
        ev["version_available_at"] = "2024-10-02T04:00:00Z"
        source["availability_proof"] = evidence("proof", published_on=None, parent_ids=["letter"],
            version_available_at="2024-10-02T04:00:00Z", content_sha256="b" * 64,
            raw_ref="example-only/independent-proof")
    return dict(checkpoint=dict(checkpoint_id="oct-1", scenario_id="helene-north-cove",
                   cutoff="2024-10-02T04:00:00Z",
                   mode="strict-historical" if strict else "reconstructed-historical-simulation",
                   source_revision_ids=["revision-1"], import_ids=[],
                   limitations=[] if strict else ["Assumed document-time availability; synthetic test only"]),
                reconstruction_acknowledged=not strict, sources=[source], products=[product()])


def allocation():
    return dict(notice_id="allocation-1", source_revision_id="revision-1", source_sha256="a" * 64,
                product_id="sim-saline", original_baxter_code="2B1324X", original_ndc="00338-0049-04",
                container_ml=1000, allocation_percent=40, effective_on="2024-10-01", kind="observed",
                exact_claim="Synthetic test cell, not extracted evidence", customer_class="example",
                page=5, table_location="synthetic row", validation_status="validated")


def receipt():
    return dict(import_id="inventory-export", scenario_id="helene-north-cove", feed="inventory", format="csv",
                source_path="synthetic/inventory.csv", content_sha256="a" * 64,
                exported_at="2026-10-26T12:00:00Z", effective_at="2024-10-01T10:00:00Z",
                modeled_available_at="2024-10-01T12:00:00Z",
                evidence=evidence("inventory-import", "simulated", published_on=None,
                                  observed_at="2026-10-26T12:00:00Z"),
                mapping_version="example-v1", record_ids=["lot-1"], validation_status="validated",
                assumptions=["Simulated contract row; no connected hospital"])


def operations():
    return dict(scenario_id="helene-north-cove", network_id="sim-nc-health-system", products=[product()],
                hospitals=[dict(hospital_id="sim-nc-" + name, name="Fictional " + name, state="NC",
                    network_id="sim-nc-health-system", evidence=evidence(name, "simulated"))
                    for name in ("central", "north", "south")],
                inventory=[dict(inventory_id="lot-1", hospital_id="sim-nc-central", product_id="sim-saline",
                    supplier_id="sim-supplier", lot_id="lot", as_of="2024-10-01", on_hand=100, reserved=4,
                    expiry_date="2025-01-01", quality_status="released", quantity_unit="bag",
                    evidence=evidence("stock", "simulated"))], consumption=[], purchase_orders=[], shipments=[],
                purchasing=[], acknowledgments=[])


def test_legacy_bundle_unchanged_and_offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Contract validation performed network I/O")
    monkeypatch.setattr(httpx.Client, "send", forbidden)
    bundle = load_bundle(ROOT / "phases/phase_02_data/fixtures/bundle.json")
    assert all(p.quantity_unit == "vial" for p in bundle.products)
    assert all(h.state == "IL" for h in bundle.hospitals)
    ScenarioReplayRequest.model_validate(request())


def test_bag_roundtrip_and_legacy_isolation():
    model = ScenarioOperations.model_validate(operations())
    assert ScenarioOperations.model_validate_json(model.model_dump_json()) == model
    assert model.inventory[0].quantity_unit == "bag"
    assert Quantity(value=100, unit="bag").model_dump()["unit"] == "bag"
    # Typed subclass instances must not bypass legacy wire guards.
    with pytest.raises(ValidationError):
        DrugProduct.model_validate(model.products[0])
    with pytest.raises(ValidationError):
        Hospital.model_validate(model.hospitals[0])


@pytest.mark.parametrize("field,value", [("quantity_unit", "case"), ("total_mg", 900),
                                        ("container_ml", None)])
def test_invalid_presentation(field, value):
    p = product()
    p[field] = value
    with pytest.raises(ValidationError):
        ReplayProduct.model_validate(p)


@pytest.mark.parametrize("unit,value", [("ml", 1), ("bag", None), ("bag", 1.5), ("bag", True), ("bag", -1)])
def test_invalid_quantity(unit, value):
    with pytest.raises(ValidationError):
        Quantity(unit=unit, value=value)


@pytest.mark.parametrize("original,normalized", [("0338-0049-04", "00338-0049-04"),
    ("12345-123-12", "12345-0123-12"), ("12345-1234-1", "12345-1234-01")])
def test_ndc_alias_padding(original, normalized):
    assert ProductAlias(scheme="ndc", original=original, normalized=normalized, review_reference="test")
    with pytest.raises(ValidationError):
        ProductAlias(scheme="ndc", original=original.replace("-", ""), normalized=normalized,
                     review_reference="test")


@pytest.mark.parametrize("mutation", ["IL", "wrong-network", "wrong-hospital", "wrong-unit", "missing-stock",
                                    "duplicate-stock", "observed-stock", "alias-collision"])
def test_scenario_guards(mutation):
    value = operations()
    if mutation == "IL":
        value["hospitals"][0]["state"] = "IL"
    elif mutation == "wrong-network":
        value["network_id"] = "other"
    elif mutation == "wrong-hospital":
        value["inventory"][0]["hospital_id"] = "sim-central"
    elif mutation == "wrong-unit":
        value["inventory"][0]["quantity_unit"] = "vial"
    elif mutation == "missing-stock":
        del value["inventory"][0]["on_hand"]
    elif mutation == "duplicate-stock":
        value["inventory"] *= 2
    elif mutation == "observed-stock":
        value["inventory"][0]["evidence"]["kind"] = "observed"
    else:
        other = deepcopy(value["products"][0])
        other["product_id"] = "other-product"
        value["products"].append(other)
    with pytest.raises(ValidationError):
        ScenarioOperations.model_validate(value)


def test_strict_and_reconstructed_admission_and_provenance():
    for strict in (True, False):
        value = request(strict)
        value["allocations"] = [allocation()]
        model = ScenarioReplayRequest.model_validate(value)
        assert model.allocations[0].source_sha256 == model.sources[0].evidence.content_sha256
        assert model.sources[0].evidence.observed_at.year == 2026
        assert ScenarioReplayRequest.model_validate_json(model.model_dump_json()) == model


@pytest.mark.parametrize("mutation", ["no-ack", "no-limitations", "future-source", "future-parent",
    "missing-parent", "cycle", "future-checkpoint-parent", "no-proof", "false-availability",
    "pending", "missing-raw", "wrong-hash", "wrong-product", "wrong-volume", "duplicate",
    "contradictory", "future-observed", "quarantined", "missing-source", "duplicate-source"])
def test_replay_rejections(mutation):
    value = request(strict=mutation in ("no-proof", "false-availability"))
    value["allocations"] = [allocation()]
    src = value["sources"][0]
    if mutation == "no-ack":
        value["reconstruction_acknowledged"] = False
    elif mutation == "no-limitations":
        value["checkpoint"]["limitations"] = []
    elif mutation == "future-source":
        src["issue_date"] = src["evidence"]["published_on"] = "2024-10-09"
    elif mutation in ("future-parent", "missing-parent", "cycle"):
        src["evidence"]["parent_ids"] = ["parent"]
        if mutation != "missing-parent":
            value["parent_evidence"] = [evidence("parent", "simulated", published_on=None,
                observed_at="2024-10-03T12:00:00Z" if mutation == "future-parent" else "2024-09-01T00:00:00Z",
                parent_ids=["letter"] if mutation == "cycle" else [])]
    elif mutation == "future-checkpoint-parent":
        value["checkpoint"].update(parent_checkpoint_id="future", parent_cutoff="2024-11-01T00:00:00Z",
                                   parent_sha256="b" * 64)
    elif mutation == "no-proof":
        del src["availability_proof"]
    elif mutation == "false-availability":
        src["evidence"]["version_available_at"] = "2024-10-01T00:00:00Z"
    elif mutation == "pending":
        src["extraction_status"] = "pending"
    elif mutation == "missing-raw":
        src["evidence"]["raw_ref"] = None
    elif mutation == "wrong-hash":
        value["allocations"][0]["source_sha256"] = "b" * 64
    elif mutation == "wrong-product":
        value["allocations"][0]["original_baxter_code"] = "unmatched"
    elif mutation == "wrong-volume":
        value["allocations"][0]["container_ml"] = 500
    elif mutation in ("duplicate", "contradictory"):
        second = deepcopy(value["allocations"][0])
        if mutation == "contradictory":
            second.update(notice_id="conflict", allocation_percent=60)
        value["allocations"].append(second)
    elif mutation == "future-observed":
        value["allocations"][0]["effective_on"] = "2024-11-25"
    elif mutation == "quarantined":
        value["allocations"][0]["validation_status"] = "quarantined"
    elif mutation == "missing-source":
        value["sources"] = []
    else:
        value["sources"] *= 2
    with pytest.raises(ValidationError):
        ScenarioReplayRequest.model_validate(value)


def test_projection_stays_projection():
    value = request()
    row = allocation()
    row.update(kind="projected", effective_on="2024-11-25", allocation_percent=80)
    value["allocations"] = [row]
    assert ScenarioReplayRequest.model_validate(value).allocations[0].kind == "projected"


def test_date_only_timezone_and_dst():
    assert document_time(datetime(2024, 10, 1).date()).isoformat() == "2024-10-02T00:00:00-04:00"
    assert document_time(datetime(2024, 11, 11).date()).isoformat() == "2024-11-12T00:00:00-05:00"
    value = request()
    value["checkpoint"]["cutoff"] = "2024-10-02T03:59:59Z"
    with pytest.raises(ValidationError):
        ScenarioReplayRequest.model_validate(value)


def test_independent_import_and_replay_cutoff():
    r = receipt()
    assert OperationalImportReceipt.model_validate(r).feed == "inventory"
    value = request()
    value["imports"] = [r]
    value["checkpoint"]["import_ids"] = [r["import_id"]]
    assert ScenarioReplayRequest.model_validate(value)
    r["modeled_available_at"] = "2024-10-03T12:00:00Z"
    with pytest.raises(ValidationError):
        ScenarioReplayRequest.model_validate(value)


@pytest.mark.parametrize("mutation", ["hash", "duplicate", "observed", "missing-fields", "format", "clocks"])
def test_invalid_import(mutation):
    value = receipt()
    if mutation == "hash":
        value["content_sha256"] = "b" * 64
    elif mutation == "duplicate":
        value["record_ids"] *= 2
    elif mutation == "observed":
        value["evidence"]["kind"] = "observed"
    elif mutation == "missing-fields":
        del value["mapping_version"]
    elif mutation == "format":
        value["format"] = "edi-850"
    else:
        value["effective_at"] = "2026-10-27T00:00:00Z"
    with pytest.raises(ValidationError):
        OperationalImportReceipt.model_validate(value)


def test_exported_schema_validates_request():
    schema = ScenarioReplayRequest.model_json_schema()
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(request())
    # Export check covers all schemas; do not claim JSON Schema enforces Python cross-row validators.
    path = ROOT / "contracts/scenario-replay-request.schema.json"
    assert json.loads(path.read_text()) == schema


def test_allocation_bounds():
    value = allocation()
    value["allocation_percent"] = 101
    with pytest.raises(ValidationError):
        AllocationNotice.model_validate(value)


def test_checkpoint_is_immutable():
    cp = ScenarioReplayRequest.model_validate(request()).checkpoint
    with pytest.raises(ValidationError):
        cp.cutoff = datetime.fromisoformat("2024-11-12T00:00:00+00:00")
    assert isinstance(cp.source_revision_ids, tuple)
    assert isinstance(cp.limitations, tuple)


def test_document_cannot_attest_its_own_availability():
    value = request(strict=True)
    src = value["sources"][0]
    src["availability_proof"]["content_sha256"] = src["evidence"]["content_sha256"]
    with pytest.raises(ValidationError):
        ScenarioReplayRequest.model_validate(value)


@pytest.mark.parametrize("mutation", ["updated", "document-id", "naive-cutoff", "false-parent-clock"])
def test_additional_clock_and_identity_guards(mutation):
    value = request()
    if mutation == "updated":
        value["sources"][0]["evidence"]["updated_on"] = "2025-01-01"
    elif mutation == "document-id":
        value["sources"][0]["document_id"] = "different-letter"
    elif mutation == "naive-cutoff":
        value["checkpoint"]["cutoff"] = "2024-10-02T04:00:00"
    else:
        value["sources"][0]["evidence"]["parent_ids"] = ["unproven-parent"]
        value["parent_evidence"] = [evidence("unproven-parent", published_on=None,
                                            version_available_at="2024-09-01T00:00:00Z")]
    with pytest.raises(ValidationError):
        ScenarioReplayRequest.model_validate(value)


def test_order_acknowledgment_and_shipment_boundary():
    value = operations()
    po = dict(purchase_order_id="po-1", hospital_id="sim-nc-central", supplier_id="sim-supplier",
              product_id="sim-saline", quantity=100, quantity_unit="bag", ordered_on="2024-09-28",
              expected_delivery="2024-10-01", account_id="account-1", order_line_id="line-1",
              customer_item_id="item-1", evidence=evidence("po", "simulated"))
    ack = dict(acknowledgment_id="ack-1", purchase_order_id="po-1", order_line_id="line-1",
               account_id="account-1", hospital_id="sim-nc-central", supplier_id="sim-supplier",
               product_id="sim-saline", revision=1, acknowledged_on="2024-09-29", status="backordered",
               quantity=50, quantity_unit="bag", expected_delivery=None,
               evidence=evidence("ack", "simulated"))
    value.update(purchase_orders=[po], acknowledgments=[ack])
    assert ScenarioOperations.model_validate(value).acknowledgments[0].expected_delivery is None
    for key in ("account_id", "quantity", "order_line_id"):
        broken = deepcopy(value)
        del broken["purchase_orders"][0][key]
        with pytest.raises(ValidationError):
            ScenarioOperations.model_validate(broken)
    value["acknowledgments"][0]["order_line_id"] = "unmatched"
    with pytest.raises(ValidationError):
        ScenarioOperations.model_validate(value)
