"""Reproducible ingestion from explicit receipts, bounded live reads, or synthetic fixtures."""

from pathlib import Path
import json
from contracts import DataBundle
from phases.phase_01_foundation.http import make_client
from phases.phase_01_foundation.sources import digest
from .snapshots import Snapshots, immutable
from .sources import (
    SOURCE_PLANS,
    normalize_trade,
    normalize_shortages,
    normalize_recalls,
    curated_event,
    parse_source,
    verify_receipt,
)
from .scenario import build_scenario, AS_OF
from .validation import COLLECTIONS, ID_FIELDS, validate_bundle, version_for

ROOT = Path(__file__).resolve().parent


def assemble(snapshots, spec, *, fixture=False):
    required = {s[0] for s in SOURCE_PLANS}
    if set(snapshots) != required:
        raise ValueError("Exactly four source snapshots are required")
    _, trade_meta, trade_body = snapshots["comtrade"]
    rows, _ = parse_source(trade_body, "data", 10)
    trades = normalize_trade(rows, trade_meta, fixture=fixture)
    _, fda_meta, fda_body = snapshots["fda-shortages"]
    rows, fda_response = parse_source(fda_body, "results", 20)
    shortages, products = normalize_shortages(rows, fda_meta, fixture=fixture)
    _, recall_meta, recall_body = snapshots["fda-recalls"]
    recalls, recall_response = parse_source(recall_body, "results", 3)
    recalls = normalize_recalls(recalls, recall_meta, fixture=fixture)
    _, doc_meta, doc_body = snapshots["curated-document"]
    event, event_evidence = curated_event(spec, doc_meta, doc_body, fixture=fixture)
    scenario = build_scenario()
    data = dict(
        schema_version="2.0",
        mode="fixture" if fixture else "observed+simulated",
        scenario_as_of=AS_OF.isoformat(),
        snapshot_ids=sorted(item[0] for item in snapshots.values()),
        coverage=[
            f"Comtrade: 2023 US imports HS30 requested partners 276,356,372; returned {len(trades)}. Missing partners are unknown, not zero.",
            f"FDA shortages: {len(shortages)} of {fda_response['meta']['results']['total']} matching current revisions; no historical revision availability established.",
            f"FDA recalls: {len(recalls)} of {recall_response['meta']['results']['total']}; bounded, not complete or necessarily active.",
            "Annual chapter aggregates only; quantity unknown when source units unavailable. No carboplatin trade mapping.",
            "Scenario 2023-07-24 is fictional, not a predictive backtest with known-at-cutoff evidence.",
        ],
        trade_flows=trades,
        shortages=shortages,
        recalls=recalls,
        **scenario,
    )
    data["products"] = products + scenario["products"]
    data["events"] = [event] + scenario["events"]
    evidence = {e.evidence_id: e for e in event_evidence}
    for collection in COLLECTIONS:
        if collection == "evidence":
            continue
        for record in data[collection]:
            old = evidence.get(record.evidence.evidence_id)
            if old is not None and old != record.evidence:
                raise ValueError("Conflicting evidence identity")
            evidence[record.evidence.evidence_id] = record.evidence
    data["evidence"] = list(evidence.values())
    for collection in COLLECTIONS:
        data[collection] = sorted(data[collection], key=lambda r: getattr(r, ID_FIELDS[collection]))
    data["dataset_version"] = "0" * 64
    b = DataBundle(**data)
    encoded = b.model_dump(mode="json")
    encoded["dataset_version"] = version_for(encoded)
    return validate_bundle(encoded)


def ingest(root, *, selection=None, refresh=False):
    root = Path(root)
    store = Snapshots(root / "snapshots")
    if refresh:
        if selection is not None:
            raise ValueError("Refresh and explicit cache selection are exclusive")
        selected = {}
        with make_client() as client:
            for source, url, params in SOURCE_PLANS:
                key, _, _ = store.fetch(client, source, url, params=params)
                selected[source] = key
        immutable(
            root / "selections" / f"{digest(selected)}.json", json.dumps(selected, sort_keys=True).encode()
        )
    else:
        if selection is None:
            raise ValueError("Select cached snapshot receipts explicitly or use --refresh")
        selected = json.loads(Path(selection).read_text())
    snapshots = {source: store.read(key) for source, key in selected.items()}
    for source, (_, receipt, _) in snapshots.items():
        verify_receipt(source, receipt)
    spec = json.loads((ROOT / "fixtures/curated_event.json").read_text())
    b = assemble(snapshots, spec)
    path = root / "datasets" / f"{b.dataset_version}.json"
    immutable(path, (b.model_dump_json(indent=2) + "\n").encode())
    return b, path


def load_bundle(path):
    return validate_bundle(json.loads(Path(path).read_text()))
