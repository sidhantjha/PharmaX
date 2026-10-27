import hashlib
import json
from pathlib import Path

import httpx
import pytest
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError
from contracts import DataPage, DataQuery, DrugProduct, InventoryRecord, Shipment
from phases.phase_01_foundation.http import ServiceError
from phases.phase_02_data.pipeline import assemble, load_bundle
from phases.phase_02_data.snapshots import Snapshots, immutable
from phases.phase_02_data.sources import normalize_trade, normalize_shortages, curated_event, verify_receipt
from phases.phase_02_data.store import DataStore, compile_read, registration_sql, local_query
from phases.phase_02_data.terminal import main, explore
from phases.phase_02_data.validation import validate_bundle, version_for

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]


@pytest.fixture
def bundle():
    return load_bundle(ROOT / "fixtures/bundle.json")


@pytest.fixture
def inputs():
    raw = json.loads((ROOT / "fixtures/source_inputs.json").read_text())
    return {k: (v["id"], v["receipt"], v["body"].encode()) for k, v in raw.items()}


def rehash(value):
    value["dataset_version"] = version_for(value)
    return value


def test_fixture_reproducible_no_network(bundle, inputs, monkeypatch):
    def forbidden(*a, **kw):
        pytest.fail("Fixture mode touched HTTP")

    monkeypatch.setattr(httpx, "Client", forbidden)
    spec = json.loads((ROOT / "fixtures/curated_event.json").read_text())
    assert assemble(inputs, spec, fixture=True) == bundle
    assert all(e.kind == "simulated" for e in bundle.evidence)
    assert main(["explore", "--fixture"]) == 0
    with pytest.raises(SystemExit):
        main(["explore", "--fixture", "--live-zetaris"])


def test_raw_immutable_versioned_and_tamper_detection(tmp_path):
    store = Snapshots(tmp_path)
    kw = {"observed_at": "2023-07-24T00:00:00+00:00"}
    key, receipt, body = store.save(
        "comtrade", "https://example.org", b'{"data":[]}', "application/json", **kw
    )
    assert receipt["body_sha256"] == hashlib.sha256(body).hexdigest()
    assert store.read(key) == (key, receipt, body)
    assert store.save("comtrade", "https://example.org", body, "application/json", **kw)[0] == key
    later = store.save(
        "comtrade", "https://example.org", body, "application/json", observed_at="2023-07-25T00:00:00+00:00"
    )
    assert later[0] != key
    with pytest.raises(ValueError):
        immutable(tmp_path / "receipts" / f"{key}.json", b"overwrite")
    (tmp_path / "raw" / f"{receipt['body_sha256']}.bin").write_bytes(b"corruption")
    with pytest.raises(ValueError, match="hash"):
        store.read(key)
    with pytest.raises(ValueError):
        store.read("../escape")


def test_live_failure_does_not_fallback(tmp_path):
    with httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(429, text="secret"))
    ) as client:
        with pytest.raises(ServiceError, match="HTTP_429"):
            Snapshots(tmp_path).fetch(client, "comtrade", "https://example.org")
    assert not list(tmp_path.rglob("*.bin"))


def test_response_bound_before_snapshot(tmp_path):
    with httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, content=b"x" * 11))
    ) as client:
        with pytest.raises(ServiceError, match="RESPONSE_TOO_LARGE"):
            Snapshots(tmp_path).fetch(client, "comtrade", "https://example.org", max_bytes=10)
    assert not list(tmp_path.rglob("*.bin"))


def test_cached_source_query_must_match_contract(inputs):
    receipt = {**inputs["fda-shortages"][1], "source": "fda-shortages"}
    with pytest.raises(ValueError):
        verify_receipt("fda-shortages", receipt)
    receipt["url"] = "https://api.fda.gov/drug/shortages.json?search=generic_name%3Acarboplatin&limit=20"
    verify_receipt("fda-shortages", receipt)


def test_trade_units_and_countries(inputs):
    _, receipt, body = inputs["comtrade"]
    rows = json.loads(body)["data"]
    r = normalize_trade(rows, receipt, fixture=True)
    assert len(r) == 2 and all(x.quantity is None and x.quantity_unit is None for x in r)
    assert r[0].partner_iso == "DEU" and r[1].partner_iso == "IRL"
    rows[0]["reporterISO"] = "DEU"
    with pytest.raises(ValueError, match="Country"):
        normalize_trade(rows, receipt)


@pytest.mark.parametrize(
    "field,value", [("period", "202301"), ("partnerCode", 0), ("reporterCode", 840), ("cmdCode", "3004")]
)
def test_trade_filter_mismatch(inputs, field, value):
    _, receipt, body = inputs["comtrade"]
    rows = json.loads(body)["data"]
    rows[0][field] = value
    with pytest.raises(ValueError):
        normalize_trade(rows, receipt)


def test_fda_package_availability_not_drug_status(inputs):
    _, receipt, body = inputs["fda-shortages"]
    rows = json.loads(body)["results"]
    records, products = normalize_shortages(rows, receipt, fixture=True)
    assert records[0].status == "Current" and records[0].availability == "Available"
    assert str(records[0].initial_posting_date) == "2023-04-28"
    assert records[0].evidence.version_available_at is None
    assert products[0].storage_min_c is None
    rows[0]["update_date"] = "invalid"
    with pytest.raises(ValueError, match="date"):
        normalize_shortages(rows, receipt)


def test_curated_document_requires_matching_source(inputs):
    _, receipt, body = inputs["curated-document"]
    spec = json.loads((ROOT / "fixtures/curated_event.json").read_text())
    event, evidence = curated_event(spec, receipt, body, fixture=True)
    assert event.products_stated == [] and len(evidence) == 2
    with pytest.raises(ValueError):
        curated_event(spec, receipt, b"Wrong source")


@pytest.mark.parametrize(
    "change",
    [
        "reserved",
        "product",
        "ship_order",
        "quantity",
        "historical",
        "observed_supplier",
        "country",
        "parent",
        "hospitals",
    ],
)
def test_network_integrity(bundle, change):
    value = bundle.model_dump(mode="json")
    if change == "reserved":
        value["inventory"][0]["reserved"] = 999
    elif change == "product":
        value["inventory"][0]["product_id"] = "not-found"
    elif change == "ship_order":
        value["shipments"][0]["hospital_id"] = "sim-north"
    elif change == "quantity":
        value["shipments"][0]["quantity"] = 999
    elif change == "historical":
        value["consumption"][0]["day"] = "2023-07-24"
    elif change == "observed_supplier":
        value["suppliers"][0]["evidence"]["kind"] = "observed"
        for e in value["evidence"]:
            if e["evidence_id"] == value["suppliers"][0]["evidence"]["evidence_id"]:
                e["kind"] = "observed"
    elif change == "country":
        value["shipments"][0]["origin_country_iso"] = "USA"
    elif change == "parent":
        value["evidence"][0]["parent_ids"] = ["missing-parent"]
    else:
        value["hospitals"].pop()
    with pytest.raises((ValueError, ValidationError)):
        validate_bundle(rehash(value))


def test_units_strength_and_dates(bundle):
    product = bundle.products[0].model_dump(mode="json")
    product["strength_mg_per_ml"] = 10
    product["container_ml"] = 45
    product["total_mg"] = 150
    with pytest.raises(ValidationError):
        DrugProduct(**product)
    inventory = bundle.inventory[0].model_dump(mode="json")
    inventory["quantity_unit"] = "mg"
    with pytest.raises(ValidationError):
        InventoryRecord(**inventory)
    ship = bundle.shipments[0].model_dump(mode="json")
    ship["expected_arrival"] = "2023-07-01"
    with pytest.raises(ValidationError):
        Shipment(**ship)


def test_content_change_changes_version(bundle):
    value = bundle.model_dump(mode="json")
    value["coverage"].append("New version")
    with pytest.raises(ValueError, match="version"):
        validate_bundle(value)
    assert validate_bundle(rehash(value)).dataset_version != bundle.dataset_version


def test_sql_compiler_closed_and_bounded(bundle):
    for data in (
        {"collection": "shell"},
        {"collection": "inventory", "limit": 101},
        {"collection": "inventory", "hospital_id": "x'; DROP TABLE municipal"},
    ):
        with pytest.raises(ValidationError):
            DataQuery(**data)
    with pytest.raises(ValueError):
        compile_read("malicious", DataQuery(collection="hospitals"))
    sql, rows = registration_sql(bundle)
    assert len(rows) < 500 and sql.startswith("CREATE SCHEMASTORE VIEW v_")
    assert "DROP" not in sql and "REPLACE" not in sql
    assert "decode(unhex(" in sql
    read = compile_read(
        bundle.dataset_version, DataQuery(collection="trade_flows", partner_code=276, limit=2)
    )
    assert read.startswith("SELECT payload FROM pharmax_p2_data.") and read.endswith("LIMIT 2")


def test_mock_zetaris_read_auth_payloads_and_network(bundle, monkeypatch):
    monkeypatch.setenv("ZETARIS_ORG_ID", "1")
    monkeypatch.setenv("ZETARIS_API_KEY", "unit-token")
    seen = []

    def handler(req):
        assert req.headers["authorization"] == "Bearer unit-token"
        seen.append(json.loads(req.content)["sql"])
        if "SELECT collection, payload" in seen[-1]:
            return httpx.Response(
                200,
                json={
                    "data": [
                        [c, r.model_dump_json()]
                        for c in ("suppliers", "products", "purchase_orders", "shipments", "hospitals")
                        for r in getattr(bundle, c)
                    ]
                },
            )
        return httpx.Response(200, json={"data": [[r.model_dump_json()] for r in bundle.hospitals]})

    mock_data = bundle.model_dump(mode="json")
    mock_data["mode"] = "observed+simulated"
    mock_bundle = validate_bundle(rehash(mock_data))
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="Fixture"):
            DataStore(client, bundle)
        store = DataStore(client, mock_bundle)
        page = store.query(DataQuery(collection="hospitals"))
        assert page.items == bundle.hospitals
        paths = store.network()
        assert len(paths) == 3 and all(r["kind"] == "simulated" for r in paths)
    assert len(seen) == 2


def test_frontend_schema_fixtures(bundle):
    schema = DataPage.model_json_schema()
    for path in (REPO / "contracts/examples").glob("data-*.json"):
        page = json.loads(path.read_text())
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(page)
        parsed = DataPage.model_validate(page)
        assert parsed.dataset_version == bundle.dataset_version and parsed.mode == "fixture"
        assert parsed.returned_count == len(parsed.items)
        assert all(e.kind == "simulated" for e in parsed.evidence)


def test_terminal_exposes_constraints_without_forecast(bundle):
    text = explore(bundle)
    assert "ALL SIMULATED" in text and text.count("HOSPITAL ") == 3
    assert "quality=quarantined" in text and "PATH sim-atlas -> sim-carboplatin-450 -> po-central" in text
    assert "No forecast" in text
    assert (
        local_query(
            bundle, DataQuery(collection="trade_flows", partner_code=276), mode="fixture"
        ).returned_count
        == 1
    )


def test_cached_pipeline_replay_preserves_version_and_times(tmp_path, inputs, monkeypatch):
    from urllib.parse import urlencode
    from phases.phase_02_data.pipeline import ingest
    from phases.phase_02_data.sources import SOURCE_PLANS

    store = Snapshots(tmp_path / "snapshots")
    selected = {}
    for source, url, params in SOURCE_PLANS:
        url += "?" + urlencode(params) if params else ""
        selected[source] = store.save(
            source, url, inputs[source][2], "application/json", observed_at=inputs[source][1]["observed_at"]
        )[0]
    selection = tmp_path / "selection.json"
    selection.write_text(json.dumps(selected))

    def forbidden(*args, **kwargs):
        pytest.fail("Cache replay used HTTP")

    monkeypatch.setattr("phases.phase_02_data.pipeline.make_client", forbidden)
    first, path = ingest(tmp_path, selection=selection)
    second, again = ingest(tmp_path, selection=selection)
    assert first == second and path == again
    assert {e.observed_at.isoformat() for e in first.evidence} == {"2023-07-24T00:00:00+00:00"}


def test_fixture_cannot_create_live_store(bundle):
    with pytest.raises(ValueError, match="Fixture"):
        DataStore(None, bundle)


def test_truncation_is_unknown_at_exact_cap(bundle):
    page = local_query(bundle, DataQuery(collection="hospitals", limit=3), mode="fixture")
    assert page.truncated is None
    assert local_query(bundle, DataQuery(collection="hospitals", limit=4), mode="fixture").truncated is False
