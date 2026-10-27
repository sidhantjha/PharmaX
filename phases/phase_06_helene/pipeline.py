"""Admitted manufacturer + independent operations -> shared FEFO -> safe decisions."""

from datetime import date, datetime, timedelta, timezone
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

from contracts.replay import (
    ReplayCheckpoint,
    ScenarioReplayRequest,
    HeleneCase,
    HeleneForecast,
    document_time,
)
from contracts.models import SourceEvidence
from phases.phase_01_foundation.sources import digest
from phases.phase_03_risk.forecast import simulate_inventory
from .fixtures import DAYS
from .operational import import_operations
from .sources import load_sources

OPERATIONS = Path(__file__).parent / "tests/fixtures/operations"
LIMITATIONS = [
    "Reconstructed historical simulation: exact 2024 version custody unproven; fictional receipt at document end-of-day.",
    "All hospital operations, accounts, geography and supply timing are simulated; outputs are conditional calculations.",
    "No real ERP, EDI partner, FHIR server, live Zetaris query, live model inference or external action.",
    "No patient outcome estimate or clinical equivalence model for saline.",
]


def forecast_operations(ops, anchor, supplies, version, stock_changes=None, extra_arrivals=None):
    stock_changes = stock_changes or {}
    extra_arrivals = extra_arrivals or {}
    result = []
    for h in ops.hospitals:
        history = [r for r in ops.consumption if r.hospital_id == h.hospital_id]
        demand = (sum(r.quantity for r in history) + 6) // 7
        inventory = [r for r in ops.inventory if r.hospital_id == h.hospital_id]
        states = [
            dict(
                lot_id=r.lot_id,
                on_hand=r.on_hand,
                reserved=r.reserved,
                expiry_date=r.expiry_date,
                quarantined=r.quality_status != "released",
                handling=True,
            )
            for r in inventory
        ]
        debit = stock_changes.get(h.hospital_id, 0)
        for lot in sorted(states, key=lambda x: (x["expiry_date"], x["lot_id"])):
            if lot["quarantined"]:
                continue
            take = min(debit, lot["on_hand"] - lot["reserved"])
            lot["on_hand"] -= take
            debit -= take
        if debit:
            raise ValueError("TRANSFER_EXCEEDS_UNRESERVED_STOCK")
        arrivals = [s for s in supplies if s.hospital_id == h.hospital_id]
        arrivals += extra_arrivals.get(h.hospital_id, [])
        initial = sum(s["on_hand"] - s["reserved"] for s in states if not s["quarantined"])
        points = simulate_inventory(anchor, 14, states, arrivals, demand)
        first = next((p["day"] for p in points if p["unmet"]), None)
        values = dict(
            hospital_id=h.hospital_id,
            as_of=anchor,
            end_on=points[-1]["day"],
            expected_daily_demand=demand,
            initial_usable=initial,
            total_received=sum(p["received"] for p in points),
            total_unmet=sum(p["unmet"] for p in points),
            projected_stockout_date=first,
            points=points,
            evidence_ids=sorted(
                {r.evidence.evidence_id for r in [*history, *inventory]} | {s.evidence_id for s in arrivals}
            ),
            limitations=LIMITATIONS,
        )
        f = HeleneForecast(analysis_id="0" * 64, **values)
        result.append(
            f.model_copy(
                update={"analysis_id": digest(dict(dataset=version, values=f.model_dump(mode="json")))}
            )
        )
    return result


def reconcile_supply(ops, files, rows, anchor):
    current = [r for r in rows if r.kind == "observed" and r.effective_on <= anchor]
    allocation = max(current, key=lambda r: r.effective_on) if current else None
    acks = {a.purchase_order_id: a for a in ops.acknowledgments}
    supplies, decisions, missing = [], [], []
    acknowledged_totals = {}
    for po in ops.purchase_orders:
        ack = acks.get(po.purchase_order_id)
        if ack is None:
            missing.append("MISSING_ACKNOWLEDGMENT:" + po.purchase_order_id)
    for ship in ops.shipments:
        if ship.status != "in_transit" or ship.expected_arrival < anchor:
            missing.append("UNCONFIRMED_OR_PAST_DUE_ASN:" + ship.shipment_id)
            continue
        ack = acks.get(ship.purchase_order_id)
        if not ack or ack.status != "accepted" or ship.quantity > ack.quantity:
            raise ValueError("ASN_ACKNOWLEDGMENT_CONFLICT")
        acknowledged_totals[ship.purchase_order_id] = (
            acknowledged_totals.get(ship.purchase_order_id, 0) + ship.quantity
        )
        if acknowledged_totals[ship.purchase_order_id] > ack.quantity:
            raise ValueError("ASN_ACKNOWLEDGMENT_CONFLICT")
        baseline = [r for r in files["purchasing"] if r["facility_id"] == ship.hospital_id]
        months = {r["period_start"][:7] for r in baseline}
        entitlement = None
        if allocation:
            if months != {f"2024-{m:02}" for m in range(3, 9)}:
                raise ValueError("MISSING_ALLOCATION_BASELINE")
            purchases = sum(
                r.quantity - r.returned_quantity for r in ops.purchasing if r.hospital_id == ship.hospital_id
            )
            entitlement = int(
                Fraction(purchases * 7, 184) * Fraction(str(allocation.allocation_percent)) / 100
            )
        if entitlement is not None and ship.quantity > entitlement:
            missing.append("ALLOCATION_ASN_CONFLICT:" + ship.shipment_id)
            decisions.append(
                dict(
                    order_id=ship.purchase_order_id,
                    shipment_id=ship.shipment_id,
                    status="unresolved",
                    reason="Confirmed modeled ASN exceeds assumed account entitlement; no silent clamp.",
                    entitlement=entitlement,
                    quantity=ship.quantity,
                    source_ids=[allocation.source_revision_id],
                )
            )
            continue
        decisions.append(
            dict(
                order_id=ship.purchase_order_id,
                shipment_id=ship.shipment_id,
                status="accepted",
                quantity=ship.quantity,
                entitlement=entitlement,
                source_ids=[allocation.source_revision_id] if allocation else [],
                reason="Independent simulated acknowledgment + ASN; allocation is a constraint, not a receipt.",
            )
        )
        supplies.append(
            SimpleNamespace(
                hospital_id=ship.hospital_id,
                quantity=ship.quantity,
                modeled_arrival=ship.expected_arrival,
                shipment_id=ship.shipment_id,
                evidence_id=ship.evidence.evidence_id,
            )
        )
    return supplies, decisions, missing


def mitigate(ops, anchor, supplies, version, forecasts, unresolved, permissions):
    """Sequential global donor ledger; every candidate reruns the same inventory engine."""
    current = forecasts
    debits, extras, selected, candidates = {}, {}, [], []
    for target in sorted(forecasts, key=lambda f: (-f.total_unmet, f.hospital_id)):
        if not target.total_unmet:
            continue
        trial = []
        for donor in forecasts:
            if donor.hospital_id == target.hospital_id:
                continue
            available = next(f for f in current if f.hospital_id == donor.hospital_id).initial_usable
            donor_current = next(f for f in current if f.hospital_id == donor.hospital_id)
            for quantity in sorted(
                {
                    min(target.total_unmet, available),
                    min(target.total_unmet, available, donor_current.points[-1].closing_usable),
                }
            ):
                if quantity <= 0:
                    continue
                cid = f"{donor.hospital_id}-to-{target.hospital_id}-{quantity}"
                d = dict(debits)
                d[donor.hospital_id] = d.get(donor.hospital_id, 0) + quantity
                e = {k: list(v) for k, v in extras.items()}
                e.setdefault(target.hospital_id, []).append(
                    SimpleNamespace(
                        hospital_id=target.hospital_id,
                        quantity=quantity,
                        modeled_arrival=anchor + timedelta(days=1),
                        shipment_id=cid,
                        evidence_id=ops.inventory[0].evidence.evidence_id,
                    )
                )
                projected = forecast_operations(ops, anchor, supplies, version, d, e)
                donor_result = next(f for f in projected if f.hospital_id == donor.hospital_id)
                target_result = next(f for f in projected if f.hospital_id == target.hospital_id)
                from phases.phase_04_agents.feasibility import container_transfer_failures

                failures = container_transfer_failures(
                    ops,
                    donor_result,
                    target_result,
                    next(f for f in current if f.hospital_id == target.hospital_id),
                    anchor,
                    permissions,
                    unresolved,
                )
                candidate = dict(
                    candidate_id=cid,
                    kind="transfer",
                    source_id=donor.hospital_id,
                    hospital_id=target.hospital_id,
                    quantity=quantity,
                    quantity_unit="bag",
                    arrival_on=str(anchor + timedelta(days=1)),
                    feasible=not failures,
                    failures=failures,
                    remaining_unmet=target_result.total_unmet,
                    donor_unmet=donor_result.total_unmet,
                    donor_reserved=donor_result.points[-1].reserved,
                    analysis_ids=[f.analysis_id for f in projected],
                    requires_human_review=True,
                    conservation=dict(debited=quantity, recipient_receipt=quantity, conserved=True),
                )
                candidates.append(candidate)
                if not failures:
                    trial.append((candidate, projected, d, e))
        candidates.append(
            dict(
                candidate_id="external-" + target.hospital_id,
                kind="supplier_delivery",
                hospital_id=target.hospital_id,
                quantity=0,
                quantity_unit="bag",
                feasible=False,
                failures=["UNCONFIRMED_ALTERNATIVE_SUPPLY"],
                remaining_unmet=target.total_unmet,
            )
        )
        if trial:
            chosen, current, debits, extras = min(
                trial, key=lambda x: (x[0]["remaining_unmet"], x[0]["quantity"], x[0]["candidate_id"])
            )
            selected.append(chosen["candidate_id"])
    return current, candidates, selected


def replay_checkpoint(
    day, *, operations=OPERATIONS, sources=None, parent=None, strict=False, memory_root=None, node=None,
    importer=import_operations,
):
    if day not in DAYS:
        raise ValueError("UNKNOWN_CHECKPOINT")
    cutoff = document_time(date.fromisoformat(day))
    source_data = load_sources() if sources is None else sources
    admitted = [item for item in source_data if item[0].issue_date <= date.fromisoformat(day)]
    ops, receipts, files = importer(Path(operations) / day, cutoff)
    checkpoint_hash = digest(
        dict(
            operations=ops.model_dump(mode="json"),
            sources=[s.evidence.content_sha256 for s, _, _ in admitted],
        )
    )
    cp = ReplayCheckpoint(
        checkpoint_id="helene-" + day + "-" + checkpoint_hash[:12],
        scenario_id="helene-north-cove",
        cutoff=cutoff,
        mode="strict-historical" if strict else "reconstructed-historical-simulation",
        source_revision_ids=[s.revision_id for s, _, _ in admitted],
        import_ids=[r.import_id for r in receipts],
        parent_checkpoint_id=parent.checkpoint.checkpoint_id if parent else None,
        parent_cutoff=parent.checkpoint.cutoff if parent else None,
        parent_sha256=digest(parent.model_dump(mode="json")) if parent else None,
        limitations=[] if strict else LIMITATIONS,
    )
    admission = ScenarioReplayRequest(
        checkpoint=cp,
        reconstruction_acknowledged=not strict,
        sources=[s for s, _, _ in admitted],
        allocations=[r for _, rows, _ in admitted for r in rows],
        imports=receipts,
        products=ops.products,
    )
    version = digest(
        dict(operations=ops.model_dump(mode="json"), admission=admission.model_dump(mode="json"))
    )
    anchor = cutoff.date()
    supplies, reconciliation, unresolved = reconcile_supply(ops, files, admission.allocations, anchor)
    for row in admission.allocations:
        if row.kind == "projected":
            reconciliation.append(
                dict(
                    source_id=row.source_revision_id,
                    claim_id=row.notice_id,
                    status="conditional-projection-only",
                    allocation_percent=row.allocation_percent,
                    earliest_distribution_on=str(row.effective_on + timedelta(days=7)),
                    latest_typical_distribution_on=str(row.effective_on + timedelta(days=14)),
                    inventory_credit=0,
                    reason="Reported typical 1–2 week distribution lag; not a confirmed shipment or ETA.",
                )
            )
    forecasts = forecast_operations(ops, anchor, supplies, version)
    permissions = {r["facility_id"] for r in files["suppliers"] if r["transfers_allowed"] == "true"}
    mitigated, candidates, selected = mitigate(
        ops, anchor, supplies, version, forecasts, unresolved, permissions
    )
    actions = [
        dict(
            action_id="action-" + digest(dict(version=version, candidate=c["candidate_id"]))[:16],
            candidate_id=c["candidate_id"],
            quantity=c["quantity"],
            quantity_unit="bag",
            status="DRAFT",
            revision=1,
            dispatch_available=False,
            required_authorizations=["Human pharmacy review", "Donor release permission"],
            outstanding_verification=["Verify real inventory, pack identity and transport before action"],
            draft=f"Review simulated transfer of {c['quantity']} bags from {c['source_id']} to {c['hospital_id']}.",
        )
        for c in candidates
        if c["candidate_id"] in selected
    ]
    decisions = []
    for role, tool, values in [
        ("verification", "manufacturer.read", dict(source_ids=list(cp.source_revision_ids))),
        ("dependency", "catalog.read", dict(accounts=[c["account_id"] for c in files["catalog"]])),
        ("hospital", "inventory.forecast", dict(analysis_ids=[f.analysis_id for f in forecasts])),
        ("feasibility", "mitigation.evaluate", dict(candidates=len(candidates), selected=selected)),
        (
            "mitigation",
            "mitigation.rank",
            dict(selected=selected, ranking="remaining unmet, quantity, stable identity"),
        ),
        (
            "actions",
            "draft.review_only",
            dict(action_ids=[a["action_id"] for a in actions], dispatch_available=False),
        ),
    ]:
        decisions.append(
            dict(
                agent_id=role,
                tool=tool,
                execution="local-deterministic",
                result=values,
                classification="executed-operation",
                dataset_version=version,
            )
        )
    memory = {}
    if memory_root is not None:
        from .specialists import remember

        memory = remember(
            memory_root,
            day,
            version,
            cutoff,
            candidates,
            selected,
            node=node,
            prior_scope=parent.memory.get("scope") if parent else None,
        )
        decisions.append(
            dict(agent_id="memory", tool="hmem.exchange", execution="actual-node-reference", result=memory)
        )
    if parent:
        previous = []
        for old in parent.candidates:
            if old["candidate_id"] not in parent.selected:
                continue
            quantity = old["quantity"]
            arrival = SimpleNamespace(
                hospital_id=old["hospital_id"],
                quantity=quantity,
                modeled_arrival=anchor + timedelta(days=1),
                shipment_id="recheck-" + old["candidate_id"],
                evidence_id=ops.inventory[0].evidence.evidence_id,
            )
            try:
                projected = forecast_operations(
                    ops,
                    anchor,
                    supplies,
                    version,
                    {old["source_id"]: quantity},
                    {old["hospital_id"]: [arrival]},
                )
                donor = next(f for f in projected if f.hospital_id == old["source_id"])
                target = next(f for f in projected if f.hospital_id == old["hospital_id"])
                previous.append(
                    dict(
                        candidate_id=old["candidate_id"],
                        still_feasible=not donor.total_unmet,
                        remaining_unmet=target.total_unmet,
                        donor_unmet=donor.total_unmet,
                        analysis_ids=[f.analysis_id for f in projected],
                    )
                )
            except ValueError:
                previous.append(
                    dict(
                        candidate_id=old["candidate_id"],
                        still_feasible=False,
                        reason="Insufficient unreserved stock at revised checkpoint",
                    )
                )
        reconciliation.append(
            dict(
                parent_id=parent.investigation_id,
                changed_input_ids=[
                    r.import_id
                    for r in receipts
                    if {p.import_id: p.content_sha256 for p in parent.admission.imports}.get(r.import_id)
                    != r.content_sha256
                ]
                + [
                    s.revision_id
                    for s, _, _ in admitted
                    if {p.revision_id: p.evidence.content_sha256 for p in parent.admission.sources}.get(
                        s.revision_id
                    )
                    != s.evidence.content_sha256
                ],
                deltas=[
                    dict(
                        hospital_id=f.hospital_id,
                        previous_unmet=p.total_unmet,
                        revised_unmet=f.total_unmet,
                        delta=f.total_unmet - p.total_unmet,
                    )
                    for f, p in zip(forecasts, parent.forecasts)
                ],
                previous_options=previous,
                note="All current candidates recomputed; previous options retained in immutable parent.",
            )
        )
    evidence = [s.evidence for s, _, _ in admitted] + [r.evidence for r in receipts]
    evidence.append(
        SourceEvidence(
            evidence_id="replay-dataset-" + version,
            kind="derived",
            source="Admitted replay dataset",
            source_url="https://example.invalid/pharmax/replay/" + version,
            observed_at=datetime.now(timezone.utc),
            content_sha256=version,
            parent_ids=[e.evidence_id for e in evidence],
            note="Derived from observed documents plus simulated exports; reconstructed receipt limitations apply.",
        )
    )
    return HeleneCase(
        investigation_id="helene-" + day + "-" + version[:12],
        checkpoint=cp,
        admission=admission,
        operations=ops,
        dataset_version=version,
        created_at=datetime.now(timezone.utc),
        parent_id=parent.investigation_id if parent else None,
        evidence=evidence,
        forecasts=forecasts,
        mitigated=mitigated,
        candidates=candidates,
        selected=selected,
        actions=actions,
        decisions=decisions,
        memory=memory,
        reconciliation=reconciliation,
        unresolved=unresolved,
        map_nodes=[
            dict(
                id=r["facility_id"],
                title=r["name"],
                point=[float(r["longitude"]), float(r["latitude"])],
                classification="simulated; illustrative coordinates",
            )
            for r in files["facilities"]
        ],
    )
