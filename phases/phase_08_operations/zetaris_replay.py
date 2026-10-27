"""Real file-source federation into the existing strict replay importer.

Source staging/registration is explicit; offline mode never constructs a client.
Only public manufacturer metadata and fictional hospital CSVs enter staging.
"""

import argparse
import csv
from hashlib import sha256
import json
import os
from pathlib import Path
from uuid import uuid4
import httpx
from phases.phase_01_foundation.config import load_env
from phases.phase_01_foundation.http import request_json,ServiceError
from phases.phase_01_foundation.zetaris import Zetaris
from phases.phase_06_helene.pipeline import OPERATIONS, replay_checkpoint
from phases.phase_05_product.service import OperatorService
from .ingestion import REQUIRED, replay_importer


def encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False).encode().hex()


def materialize(rows, folder, expected):
    """No source strings become code; require exact schema and immutable feed bytes."""
    if not rows or len(rows) > 100:
        raise ValueError("ZETARIS_JOIN_EMPTY_OR_OVER_BUDGET")
    feeds = {}
    sources = {}
    for source_id, product_id, manufacturer_hex, allocations_hex, feed, hospital_hex in rows:
        if product_id != expected.operations.products[0].product_id:
            raise ValueError("ZETARIS_PRODUCT_MISMATCH")
        source = json.loads(bytes.fromhex(manufacturer_hex))
        actual = next((s for s in expected.admission.sources if s.evidence.evidence_id == source_id), None)
        if actual is None or source != actual.model_dump(mode="json"):
            raise ValueError("ZETARIS_MANUFACTURER_PROVENANCE_MISMATCH")
        if json.loads(bytes.fromhex(allocations_hex)) != [
            a.model_dump(mode="json") for a in expected.admission.allocations
        ]:
            raise ValueError("ZETARIS_ALLOCATION_MISMATCH")
        sources[source_id] = source
        if feed not in {*REQUIRED, "clocks"}:
            raise ValueError("ZETARIS_UNEXPECTED_FEED")
        content = bytes.fromhex(hospital_hex)
        if len(content) > 4 * 1024 * 1024 or (feed in feeds and feeds[feed] != content):
            raise ValueError("ZETARIS_FEED_CONFLICT_OR_BUDGET")
        feeds[feed] = content
    if set(feeds) != {*REQUIRED, "clocks"} or len(sources) != len(expected.admission.sources):
        raise ValueError("ZETARIS_SOURCE_COVERAGE_INCOMPLETE")
    folder.mkdir(parents=True, exist_ok=True)
    for feed, content in feeds.items():
        path = folder / (feed + (".json" if feed == "clocks" else ".csv"))
        if path.exists() and path.read_bytes() != content:
            raise ValueError("IMMUTABLE_REMOTE_INPUT_CONFLICT")
        path.write_bytes(content)
    return dict(
        manufacturer=list(sources.values()), feed_hashes={k: sha256(v).hexdigest() for k, v in feeds.items()}
    )


def execute(output, store, *, private_env=None, operations=OPERATIONS, day="2024-10-01", mode="live"):
    output, store = Path(output), Path(store)
    if output.exists():
        raise ValueError("FRESH_ZETARIS_EVIDENCE_DIRECTORY_REQUIRED")
    output.mkdir(parents=True)
    expected = replay_checkpoint(day, operations=operations, importer=replay_importer)
    if mode == "offline":
        OperatorService(store).helene_store.save(expected)
        proof = dict(
            mode="offline-sample",
            status="passed",
            network_requests=0,
            investigation_id=expected.investigation_id,
            limitations=["Not live Zetaris execution"],
        )
        (output / "proof.json").write_text(json.dumps(proof, indent=2) + "\n")
        return expected, proof
    if mode != "live":
        raise ValueError("EXPLICIT_DATA_MODE_REQUIRED")
    load_env(str(private_env) if private_env else None, prefix="ZETARIS_")
    namespace = "pharmax_final_" + expected.dataset_version[:12]
    stage = output / "staged"
    stage.mkdir()
    product = expected.operations.products[0].product_id
    with (stage / "manufacturer.csv").open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["source_id", "product_id", "manufacturer_hex", "allocations_hex"])
        for source in expected.admission.sources:
            writer.writerow(
                [
                    source.evidence.evidence_id,
                    product,
                    encode(source.model_dump(mode="json")),
                    encode([a.model_dump(mode="json") for a in expected.admission.allocations]),
                ]
            )
    with (stage / "hospital.csv").open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["product_id", "feed", "hospital_hex"])
        for feed in [*REQUIRED, "clocks"]:
            name = feed + (".json" if feed == "clocks" else ".csv")
            writer.writerow([product, feed, (Path(operations) / day / name).read_bytes().hex()])
    audit = []
    with httpx.Client(trust_env=False, follow_redirects=False, timeout=60) as client:
        adapter = Zetaris(client)
        token = os.environ.get("ZETARIS_API_KEY")
        if not token:
            login, _ = request_json(
                client,
                "POST",
                adapter.base + "/api/auth/login",
                json={"username": os.environ["ZETARIS_USERNAME"], "password": os.environ["ZETARIS_PASSWORD"]},
            )
            token = login["idToken"]
        headers = {"Authorization": "Bearer " + token, "X-Org-ID": adapter.org, "X-Request-ID": str(uuid4())}
        listing, _ = request_json(
            client,
            "GET",
            adapter.base + "/api/proxy/ndp-files/list",
            params={"path": "file-store"},
            headers=headers,
        )
        if not any(row.get("fileName") == namespace for row in listing):
            # This supported NDP mutation can return an empty successful body.
            created=client.post(
                adapter.base + "/api/proxy/ndp-files/createdir",
                json={"path": "file-store/" + namespace},
                headers=headers,
            )
            if not 200<=created.status_code<300 or len(created.content)>64000:
                raise ServiceError('ZETARIS_SOURCE_DIRECTORY_CREATE_FAILED')
        files, _ = request_json(
            client,
            "GET",
            adapter.base + "/api/proxy/ndp-files/list",
            params={"path": "file-store/" + namespace},
            headers=headers,
        )
        for name in ("manufacturer.csv", "hospital.csv"):
            if not any(row.get("fileName") == name for row in files):
                request_json(
                    client,
                    "POST",
                    adapter.base + "/api/proxy/ndp-files/save",
                    data={"path": "file-store/" + namespace},
                    files={"files": (name, (stage / name).read_bytes(), "text/csv")},
                    headers=headers,
                )

        def query(sql, limit=100):
            response = adapter.submit_sql(sql, limit=limit)
            if response.get("error") or "data" not in response:
                raise ValueError("ZETARIS_SQL_EXECUTION_FAILED")
            audit.append(dict(sql=sql, response=response))
            (output / "queries.json").write_text(json.dumps(audit, indent=2) + "\n")
            return response

        version = query("SHOW SERVER VERSION")
        existing = query("SHOW LIGHTNING DATABASES")
        if not any(str(row[0]).lower() == namespace for row in existing["data"]):
            query(
                f"CREATE LIGHTNING DATABASE {namespace} DESCRIBE BY 'PharmaX public manufacturer and fictional hospital inputs'"
            )
        tables = query("SHOW LIGHTNING TABLES " + namespace)
        for name in ("manufacturer", "hospital"):
            if not any(name == str(cell).lower() for row in tables["data"] for cell in row):
                query(
                    f"CREATE LIGHTNING FILESTORE TABLE {name} FROM {namespace} FORMAT CSV OPTIONS "
                    f'(path "dffs://{namespace}/{name}.csv", header "true", delimiter ",")'
                )
        sql = (
            f"SELECT m.source_id,m.product_id,m.manufacturer_hex,m.allocations_hex,h.feed,h.hospital_hex "
            f"FROM {namespace}.manufacturer m JOIN {namespace}.hospital h ON m.product_id=h.product_id "
            "ORDER BY m.source_id,h.feed LIMIT 100"
        )
        joined = query(sql)
    lineage = materialize(joined["data"], output / "returned-inputs" / day, expected)
    case = replay_checkpoint(day, operations=output / "returned-inputs", importer=replay_importer)
    # Imported evidence includes its actual returned-file location. Compare bytes,
    # not location-dependent evidence IDs; the same strict importer validates both.
    for feed, actual_hash in lineage["feed_hashes"].items():
        name = feed + (".json" if feed == "clocks" else ".csv")
        if actual_hash != sha256((Path(operations) / day / name).read_bytes()).hexdigest():
            raise ValueError("REMOTE_FEED_DIFFERS_FROM_REGISTERED_SOURCE")
    OperatorService(store).helene_store.save(case)
    proof = dict(
        mode="live-zetaris",
        status="passed",
        trace_id="zetaris-" + uuid4().hex,
        endpoint=adapter.base,
        organization_id=adapter.org,
        server=version,
        namespace=namespace,
        source_tables=["manufacturer", "hospital"],
        query=sql,
        response_schema=joined.get("headers"),
        returned_rows=len(joined["data"]),
        result_sha256=sha256(json.dumps(joined, sort_keys=True).encode()).hexdigest(),
        source_lineage=lineage,
        investigation_id=case.investigation_id,
        source_query_used_for="Returned hospital CSVs admitted by strict replay_importer and shared FEFO; returned manufacturer provenance checked and delivered to native specialists",
        forecasts=[
            dict(hospital=f.hospital_id, unmet=f.total_unmet, analysis_id=f.analysis_id)
            for f in case.forecasts
        ],
        classification="Actual local Zetaris file-source join; real manufacturer metadata, simulated hospital operations; reconstructed historical custody",
    )
    (output / "proof.json").write_text(json.dumps(proof, indent=2) + "\n")
    return case, proof


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mode", choices=["live", "offline"], required=True)
    p.add_argument("--private-env", type=Path)
    p.add_argument("--operations", type=Path, default=OPERATIONS)
    p.add_argument("--checkpoint", default="2024-10-01")
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--store-root", type=Path, required=True)
    args = p.parse_args()
    case, proof = execute(
        args.output_dir,
        args.store_root,
        private_env=args.private_env,
        operations=args.operations,
        day=args.checkpoint,
        mode=args.mode,
    )
    print(json.dumps({k: proof[k] for k in ("mode", "status", "investigation_id")}, indent=2))


if __name__ == "__main__":
    main()
