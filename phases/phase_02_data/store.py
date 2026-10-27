"""Small immutable Zetaris views and read-only closed query plans; no arbitrary SQL tools."""

import json
import re
from contracts import DataQuery, DataPage
from phases.phase_01_foundation.zetaris import Zetaris
from phases.phase_01_foundation.sources import digest
from .validation import COLLECTIONS, ID_FIELDS, validate_bundle

NAMESPACE = "pharmax_p2_data"


def table_name(version):
    if not re.fullmatch("[a-f0-9]{64}", version):
        raise ValueError("Invalid dataset version")
    return NAMESPACE + ".v_" + version


def literal(value):
    # No source strings, SQL escaping rules, delimiters or identifiers can become code.
    return "decode(unhex('" + str(value).encode().hex() + "'), 'UTF-8')"


def envelope(collection, record):
    payload = record.model_dump(mode="json")
    ev = record if collection == "evidence" else record.evidence
    return dict(
        collection=collection,
        record_id=getattr(record, ID_FIELDS[collection]),
        kind=ev.kind,
        reporter_code=str(payload.get("reporter_code", "")),
        partner_code=str(payload.get("partner_code", "")),
        product_id=payload.get("product_id") or "",
        hospital_id=payload.get("hospital_id") or "",
        supplier_id=payload.get("supplier_id") or "",
        purchase_order_id=payload.get("purchase_order_id") or "",
        payload=json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
    )


def registration_sql(bundle):
    b = validate_bundle(bundle)
    name = table_name(b.dataset_version).split(".")[1]
    rows = [envelope(c, r) for c in COLLECTIONS for r in getattr(b, c)]
    if any(len(getattr(b, c)) > 100 for c in COLLECTIONS):
        raise ValueError("Pilot collections are bounded to 100 records each")
    if not 1 <= len(rows) <= 500:
        raise ValueError("Pilot publication bounded to 500 records")
    columns = list(rows[0])
    values = ",".join("(" + ",".join(literal(row[k]) for k in columns) + ")" for row in rows)
    sql = (
        f"CREATE SCHEMASTORE VIEW {name} WITH CONTAINER {NAMESPACE} AS SELECT * FROM VALUES {values} AS data("
        + ",".join(columns)
        + ")"
    )
    if len(sql.encode()) > 900_000:
        raise ValueError("Pilot registration exceeds SQL budget")
    return sql, rows


def compile_read(version, query):
    q = DataQuery.model_validate(query)
    predicates = ["collection=" + literal(q.collection)]
    for field in ("reporter_code", "partner_code", "product_id", "hospital_id", "record_id"):
        val = getattr(q, field)
        if val is not None:
            predicates.append(field + "=" + literal(val))
    return (
        f"SELECT payload FROM {table_name(version)} WHERE "
        + " AND ".join(predicates)
        + f" ORDER BY record_id LIMIT {q.limit}"
    )


def network_sql(version):
    # One bounded read avoids Spark Catalyst attribute errors on repeated literal-view self joins.
    return (
        f"SELECT collection, payload FROM {table_name(version)} WHERE collection IN "
        "('suppliers','products','purchase_orders','shipments','hospitals') "
        "ORDER BY collection, record_id LIMIT 100"
    )


def join_network(groups):
    result = []
    for ship in groups["shipments"].values():
        po = groups["purchase_orders"][ship["purchase_order_id"]]
        supplier = groups["suppliers"][ship["supplier_id"]]
        product = groups["products"][ship["product_id"]]
        hospital = groups["hospitals"][ship["hospital_id"]]
        if any(ship[f] != po[f] for f in ("supplier_id", "product_id", "hospital_id")):
            raise ValueError("Remote network relationship mismatch")
        result.append(
            dict(
                supplier_id=supplier["supplier_id"],
                product_id=product["product_id"],
                purchase_order_id=po["purchase_order_id"],
                shipment_id=ship["shipment_id"],
                hospital_id=hospital["hospital_id"],
                kind=ship["evidence"]["kind"],
                evidence_ids=[r["evidence"]["evidence_id"] for r in (supplier, product, po, ship, hospital)],
            )
        )
    return sorted(result, key=lambda r: r["shipment_id"])


def evidence_closure(bundle, ids):
    evidence = {e.evidence_id: e for e in bundle.evidence}
    pending = list(ids)
    complete = set()
    while pending:
        key = pending.pop()
        if key in complete:
            continue
        complete.add(key)
        pending.extend(evidence[key].parent_ids)
    return [evidence[key] for key in sorted(complete)]


def local_query(bundle, query, *, mode="cached"):
    b = validate_bundle(bundle)
    q = DataQuery.model_validate(query)
    selected = []
    for r in getattr(b, q.collection):
        row = envelope(q.collection, r)
        if all(
            getattr(q, f) is None or row[f] == str(getattr(q, f))
            for f in ("reporter_code", "partner_code", "product_id", "hospital_id", "record_id")
        ):
            selected.append(r)
    selected.sort(key=lambda r: getattr(r, ID_FIELDS[q.collection]))
    selected = selected[: q.limit]
    ids = {r.evidence.evidence_id if q.collection != "evidence" else r.evidence_id for r in selected}
    return DataPage(
        dataset_version=b.dataset_version,
        mode=mode,
        query=q,
        items=selected,
        evidence=evidence_closure(b, ids),
        returned_count=len(selected),
        truncated=None if len(selected) == q.limit else False,
    )


class DataStore:
    def __init__(self, client, bundle):
        self.adapter = Zetaris(client)
        self.bundle = validate_bundle(bundle)
        if self.bundle.mode == "fixture":
            raise ValueError("Fixture mode cannot contact Zetaris")
        self.table = table_name(self.bundle.dataset_version)
        self.calls = 0

    def execute(self, sql, limit=100):
        if self.calls >= 40:
            raise ValueError("Zetaris request budget exhausted")
        self.calls += 1
        return self.adapter.submit_sql(sql, limit=limit)

    def publish(self):
        if self.bundle.mode == "fixture":
            raise ValueError("Synthetic fixture mode cannot publish to live Zetaris")
        sql, rows = registration_sql(self.bundle)
        existing = self.execute("SHOW DATASOURCE VIEWS", 100)
        # Registry response shape differs between versions; exact view token only.
        view = self.table.split(".")[1]
        exists = any(view == str(cell).lower() for row in existing["data"] for cell in row)
        if not exists:
            self.execute(sql, 1)
        result = self.execute(f"SELECT COUNT(*) AS records FROM {self.table}", 1)
        if result["data"] not in ([[len(rows)]], [[str(len(rows))]]):
            raise ValueError("Zetaris publication row count mismatch")
        # Read every collection through the same read-only registry and compare full typed payloads.
        for collection in COLLECTIONS:
            page = self.query(DataQuery(collection=collection, limit=100))
            expected = local_query(self.bundle, DataQuery(collection=collection, limit=100))
            if [r.model_dump(mode="json") for r in page.items] != [
                r.model_dump(mode="json") for r in expected.items
            ]:
                raise ValueError("Zetaris publication payload mismatch: " + collection)
        return dict(
            table=self.table,
            record_count=len(rows),
            dataset_version=self.bundle.dataset_version,
            payload_sha256=digest(rows),
            verified_collections=len(COLLECTIONS),
        )

    def query(self, query):
        q = DataQuery.model_validate(query)
        result = self.execute(compile_read(self.bundle.dataset_version, q), q.limit)
        # Validate against the authoritative collection type, preserving observed/simulated provenance.
        model = type(getattr(self.bundle, q.collection)[0]) if getattr(self.bundle, q.collection) else None
        records = [model.model_validate_json(row[0]) for row in result["data"]] if model else []
        if len(records) != len(result["data"]):
            raise ValueError("Unexpected records in empty collection")
        ids = {r.evidence.evidence_id if q.collection != "evidence" else r.evidence_id for r in records}
        return DataPage(
            dataset_version=self.bundle.dataset_version,
            mode="live-zetaris",
            query=q,
            items=records,
            evidence=evidence_closure(self.bundle, ids),
            returned_count=len(records),
            truncated=None if len(records) == q.limit else False,
        )

    def network(self):
        result = self.execute(network_sql(self.bundle.dataset_version), 100)
        groups = {c: {} for c in ("suppliers", "products", "purchase_orders", "shipments", "hospitals")}
        for collection, payload in result["data"]:
            value = json.loads(payload)
            groups[collection][value[ID_FIELDS[collection]]] = value
        for collection in groups:
            expected = {
                getattr(r, ID_FIELDS[collection]): r.model_dump(mode="json")
                for r in getattr(self.bundle, collection)
            }
            if expected != groups[collection]:
                raise ValueError("Zetaris network read incomplete or altered")
        return join_network(groups)
