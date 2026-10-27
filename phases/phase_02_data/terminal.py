"""Data subcommands for the existing PharmaX diagnostic terminal."""

import argparse
import json
from pathlib import Path
from contracts import DataQuery
from phases.phase_01_foundation.config import load_env
from phases.phase_01_foundation.http import make_client, ServiceError
from .pipeline import ingest, load_bundle
from .store import DataStore, local_query, join_network
from .validation import ID_FIELDS

ROOT = Path(__file__).resolve().parent


def explore(bundle, *, store=None, reporter=None, partner=None):
    mode = (
        "LIVE-ZETARIS"
        if store
        else ("FIXTURE / ALL SIMULATED" if bundle.mode == "fixture" else "CACHED / NO NETWORK")
    )
    lines = [
        f"PharmaX data | {mode} | version={bundle.dataset_version}",
        f"Scenario date={bundle.scenario_as_of}; fictional planning inputs, not a historical backtest.",
    ]

    def read(collection, **filters):
        q = DataQuery(collection=collection, limit=100, **filters)
        return (
            store.query(q)
            if store
            else local_query(bundle, q, mode="fixture" if bundle.mode == "fixture" else "cached")
        ).items

    def cite(record):
        ev = record.evidence
        return f"kind={ev.kind} evidence={ev.evidence_id} source={ev.source_url} observed={ev.observed_at.isoformat()}"

    for r in read("trade_flows", reporter_code=reporter, partner_code=partner):
        lines.append(
            f"TRADE {r.period} {r.reporter_name}({r.reporter_code}/{r.reporter_iso}) <- {r.partner_name}({r.partner_code}/{r.partner_iso}) HS{r.hs_code} {r.commodity_description} {r.flow} USD={r.value_usd} qty={r.quantity} unit={r.quantity_unit}; "
            + cite(r)
        )
    shortages = read("shortages")
    lines.append(
        f"FDA SHORTAGES {len(shortages)} stored package records; current revisions, not historical as-of evidence"
    )
    for r in shortages[:5]:
        lines.append(
            f"SHORTAGE {r.generic_name} NDC={r.package_ndc} initial={r.initial_posting_date} updated={r.update_date} status={r.status} package_availability={r.availability}; "
            + cite(r)
        )
    for r in read("events"):
        lines.append(
            f"EVENT {r.event_id} {r.starts_on} {r.location}: {r.title}; {r.established} Unknown: {r.uncertain_implications}; "
            + cite(r)
        )
    for r in read("hospitals"):
        lines.append(f"HOSPITAL {r.hospital_id}: {r.name}; " + cite(r))
    for r in read("inventory"):
        lines.append(
            f"STOCK {r.hospital_id} {r.product_id}: on_hand={r.on_hand} reserved={r.reserved} {r.quantity_unit} expiry={r.expiry_date} quality={r.quality_status}; "
            + cite(r)
        )
    for r in read("purchase_orders"):
        lines.append(
            f"ORDER {r.purchase_order_id}: {r.supplier_id} -> {r.hospital_id} product={r.product_id} {r.quantity} {r.quantity_unit} expected={r.expected_delivery}; "
            + cite(r)
        )
    for r in read("shipments"):
        lines.append(
            f"SHIPMENT {r.shipment_id}: {r.status} expected={r.expected_arrival} origin={r.origin_country_iso} exposure={r.disruption_event_ids} modeled_temperature={r.temperature_min_c}..{r.temperature_max_c}C light_protected={r.protects_from_light}; "
            + cite(r)
        )
    if store:
        paths = store.network()
    else:
        groups = {
            c: {getattr(r, ID_FIELDS[c]): r.model_dump(mode="json") for r in getattr(bundle, c)}
            for c in ("suppliers", "products", "purchase_orders", "shipments", "hospitals")
        }
        paths = join_network(groups)
    for path in paths:
        lines.append(
            "PATH "
            + " -> ".join(
                path[f]
                for f in ("supplier_id", "product_id", "purchase_order_id", "shipment_id", "hospital_id")
            )
            + f" kind={path['kind']} evidence={path['evidence_ids']}"
        )
    lines.extend("COVERAGE " + c for c in bundle.coverage)
    lines.append("No forecast, risk score, clinical substitution, or mitigation outcome has been calculated.")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    ingestion = sub.add_parser("ingest")
    ingestion.add_argument("--root", type=Path, default=Path(".runtime/pharmax-p2"))
    source = ingestion.add_mutually_exclusive_group(required=True)
    source.add_argument("--refresh", action="store_true")
    source.add_argument("--selection", type=Path)
    for name in ("publish", "explore", "query"):
        p = sub.add_parser(name)
        choice = p.add_mutually_exclusive_group(required=True)
        choice.add_argument("--dataset", type=Path)
        if name != "publish":
            choice.add_argument("--fixture", action="store_true")
        p.add_argument("--zetaris-env-file", help="Explicit existing private configuration; read in place.")
        if name == "publish":
            p.add_argument(
                "--initialize",
                action="store_true",
                help="First setup only: create isolated pharmax_p2_data container.",
            )
        else:
            p.add_argument("--live-zetaris", action="store_true")
        if name in ("explore", "query"):
            p.add_argument("--reporter", type=int)
            p.add_argument("--partner", type=int)
        if name == "query":
            p.add_argument("--collection", required=True)
            p.add_argument("--product")
            p.add_argument("--hospital")
            p.add_argument("--record")
            p.add_argument("--limit", type=int, default=20)
    args = parser.parse_args(argv)
    try:
        if args.command == "ingest":
            bundle, path = ingest(args.root, selection=args.selection, refresh=args.refresh)
            print(
                f"INGEST {'LIVE' if args.refresh else 'CACHED / NO NETWORK'} version={bundle.dataset_version} path={path}"
            )
            print("\n".join(bundle.coverage))
            return 0
        fixture = getattr(args, "fixture", False)
        if fixture and getattr(args, "live_zetaris", False):
            parser.error("Fixture mode cannot use live Zetaris")
        bundle = load_bundle(ROOT / "fixtures/bundle.json" if fixture else args.dataset)
        live = args.command == "publish" or getattr(args, "live_zetaris", False)

        def run(store=None):
            if args.command == "publish":
                if args.initialize:
                    store.execute("CREATE SCHEMASTORE CONTAINER pharmax_p2_data", 1)
                print("PUBLISH PASS " + json.dumps(store.publish(), sort_keys=True))
                print("NETWORK PASS " + json.dumps(store.network(), sort_keys=True))
            elif args.command == "explore":
                print(explore(bundle, store=store, reporter=args.reporter, partner=args.partner))
            else:
                query = DataQuery(
                    collection=args.collection,
                    reporter_code=args.reporter,
                    partner_code=args.partner,
                    product_id=args.product,
                    hospital_id=args.hospital,
                    record_id=args.record,
                    limit=args.limit,
                )
                page = (
                    store.query(query)
                    if store
                    else local_query(bundle, query, mode="fixture" if fixture else "cached")
                )
                print(page.model_dump_json(indent=2))

        if live:
            if bundle.mode == "fixture":
                parser.error("Fixture datasets cannot contact Zetaris")
            load_env(args.zetaris_env_file, prefix="ZETARIS_")
            with make_client() as client:
                run(DataStore(client, bundle))
        else:
            run()
        return 0
    except ServiceError as exc:
        print(f"{'BLOCKED' if exc.blocked else 'FAIL'} {exc.code}")
        return 2 if exc.blocked else 1
    except (ValueError, OSError, KeyError):
        print("FAIL DATA_VALIDATION_OR_ARTIFACT_ERROR; no fixture fallback")
        return 1
