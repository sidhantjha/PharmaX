"""Immutable publication of already executed model receipts; no inference."""

import json
from pathlib import Path
from contracts.model_verification import ModelVerificationStatus
from .integration import RESPONSIBILITIES
from .verified_runtime import MODEL


def initialize(base):
    with base.connect() as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS helene_model_runs (sequence INTEGER PRIMARY KEY,case_id TEXT,run_id TEXT,payload TEXT)"
        )
        for action in ("UPDATE", "DELETE"):
            db.execute(
                f"CREATE TRIGGER IF NOT EXISTS immutable_model_runs_{action} BEFORE {action} ON helene_model_runs BEGIN SELECT RAISE(ABORT,'immutable'); END"
            )


def publish(service, run_dir, proof):
    root = Path(run_dir)
    summary = json.loads((root / "summary.json").read_text())
    service.helene_store.get(summary["investigation_id"])
    records = [json.loads((root / (role + ".json")).read_text()) for role in RESPONSIBILITIES]
    receipts = [m for r in records for m in r.get("model_receipts", [])]
    if len(receipts) > summary["actual_model_posts"] or len({r["response_id"] for r in receipts}) != len(
        receipts
    ):
        raise ValueError("MODEL_RECEIPT_COUNT_OR_ID_MISMATCH")
    if any(m["returned_model"] != MODEL or m["winning_route"]["provider"] != "nvidia" for m in receipts):
        raise ValueError("MODEL_RECEIPT_ROUTE_MISMATCH")
    trace = json.loads((root / "trace.json").read_text())
    hmem = json.loads((root / "hmem.json").read_text()) if (root / "hmem.json").exists() else {}
    payload = ModelVerificationStatus(
        investigation_id=summary["investigation_id"],
        available=True,
        mode="nightshift-live-model",
        run_id=summary["run_id"],
        status=summary["status"],
        roles_executed=sum(r.get("continuation_occurred") is True for r in records),
        findings_validated=sum(r["status"] == "complete" for r in records),
        model_requests=summary['actual_model_posts'],
        model=MODEL,
        roles=records,
        budget=summary["budget"],
        data_layer={
            k: proof[k]
            for k in (
                "mode",
                "trace_id",
                "endpoint",
                "server",
                "namespace",
                "source_tables",
                "query",
                "returned_rows",
                "response_schema",
                "result_sha256",
            )
        },
        hmem=hmem,
        graph=trace["graph"],
        trace=trace["events"],
        limitations=summary["limitations"],
    )
    initialize(service.store)
    with service.store.connect() as db:
        prior = db.execute(
            "SELECT payload FROM helene_model_runs WHERE run_id=? ORDER BY sequence DESC LIMIT 1",
            (payload.run_id,),
        ).fetchone()
        if not prior or prior[0] != payload.model_dump_json():
            db.execute(
                "INSERT INTO helene_model_runs(case_id,run_id,payload) VALUES(?,?,?)",
                (payload.investigation_id, payload.run_id, payload.model_dump_json()),
            )
    return payload


def read_status(service, identity):
    service.helene_store.get(identity)
    initialize(service.store)
    with service.store.connect() as db:
        row = db.execute(
            "SELECT payload FROM helene_model_runs WHERE case_id=? ORDER BY sequence DESC LIMIT 1",
            (identity,),
        ).fetchone()
    return (
        ModelVerificationStatus.model_validate_json(row[0])
        if row
        else ModelVerificationStatus(investigation_id=identity, available=False, mode="unavailable")
    )
