"""Deterministic cached-data risk investigation, with chart-ready JSON and CSV."""

import argparse
import csv
from datetime import date
import json
from pathlib import Path
from contracts import ForecastRequest, ScenarioOverlay
from phases.phase_02_data.pipeline import load_bundle
from .tools import RiskTools

ROOT = Path(__file__).resolve().parents[2]


def calculate(dataset, *, horizon=14, overlay_file=None):
    b = load_bundle(dataset)
    tools = RiskTools(b)
    context = max(max(r.evidence.observed_at.date() for r in b.trade_flows), date(2023, 12, 31))
    trade = tools.trade_exposure(context)
    overlay = ScenarioOverlay.model_validate_json(Path(overlay_file).read_text()) if overlay_file else None
    comparisons = []
    for item in (None, overlay) if overlay is not None else (None,):
        for h in b.hospitals:
            comparisons.append(
                tools.scenario_comparison(
                    ForecastRequest(hospital_id=h.hospital_id, horizon_days=horizon, overlay=item)
                )
            )
    return dict(
        dataset_version=b.dataset_version,
        mode="cached; derived projections over simulated dependencies",
        historical_backtest=False,
        inference_calls=0,
        trade_exposure=trade.model_dump(mode="json"),
        comparisons=[c.model_dump(mode="json") for c in comparisons],
        evidence=[e.model_dump(mode="json") for e in b.evidence],
        context_events=[e.model_dump(mode="json") for e in b.events if e.evidence.kind != "simulated"],
    )


def render(value):
    trade = value["trade_exposure"]
    lines = [
        f"PharmaX risk | CACHED / DERIVED / SIMULATED DEPENDENCIES | dataset={value['dataset_version']}",
        "No inference, database writes, transfers or operational actions.",
        f"TRADE retrospective context_as_of={trade['context_as_of']} period={trade['period']} HS{trade['hs_code']} observed total USD={trade['observed_import_value_usd']:.2f}",
        f"COVERAGE complete={trade['complete_partner_coverage']} national_HHI={trade['national_hhi']} world_denominator={trade['world_import_value_usd']}",
    ]
    for c in trade["countries"]:
        lines.append(
            f"COUNTRY {c['country_iso']} sample_share={c['observed_sample_share']:.6%} national_share={c['national_import_share']} historical_change={c['historical_change_pct']} risk={c['country_rating']['level'] or 'Unclassified'} rule={c['country_rating']['rule_id']}"
        )
    for event in value["context_events"]:
        lines.append(
            f"CONTEXT EVENT {event['event_id']} date={event['starts_on']} kind={event['evidence']['kind']} source={event['evidence']['source_url']} evidence={event['evidence']['evidence_id']}; product impact unknown; excluded from 2023 forecasting."
        )
    for comparison in value["comparisons"]:
        name = comparison["overlay"]["overlay_id"] if comparison["overlay"] else "original-phase2"
        lines.append(
            f"CASE {name} overlay_version={comparison['overlay_version']} hospital={comparison['hospital_id']}"
        )
        for case in ("baseline", "disruption"):
            assessment = comparison[case]
            f = assessment["forecast"]
            lines.append(
                f"  {case}: initial_usable={f['initial_usable']} demand/day={f['expected_daily_demand']} received={f['total_received']} unmet={f['total_unmet']} vials shortage_days={f['shortage_days']} first_unmet={f['projected_stockout_date']} ending_buffer={f['ending_buffer_days']} risk={assessment['rating']['level']} rule={assessment['rating']['rule_id']} right_censored={f['right_censored']}"
            )
            for ship in f["shipment_risks"]:
                lines.append(
                    f"  SHIPMENT {ship['supplier_id']} -> {ship['shipment_id']} -> {ship['hospital_id']} kind=simulated basis={ship['arrival_basis']} modeled_arrival={ship['modeled_arrival']} events={ship['linked_simulated_event_ids']} shipment_risk={ship['rating']['level']} evidence={ship['input_evidence_ids']}"
                )
        lines.append(
            f"  CAUSAL delta_unmet={comparison['incremental_unmet']} delta_shortage_days={comparison['incremental_shortage_days']}; {comparison['causal_explanation']}"
        )
        if comparison["hospital_id"] == "sim-central":
            lines.append("  TIME SERIES day baseline_close baseline_unmet disruption_close disruption_unmet")
            for b, d in zip(
                comparison["baseline"]["forecast"]["points"], comparison["disruption"]["forecast"]["points"]
            ):
                lines.append(
                    f"  {b['day']} {b['closing_usable']} {b['unmet']} {d['closing_usable']} {d['unmet']}"
                )
    lines.append(
        "LIMITS: partial country sample; no global HHI or ranking; no carboplatin trade linkage; forecasts use only simulated inputs, not later FDA revisions. Reservations are ring-fenced and arrivals have modeled shelf lives."
    )
    return "\n".join(lines)


def export(value, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "analysis.json").write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    with (out / "timeseries.csv").open("w", newline="") as stream:
        fields = [
            "overlay",
            "hospital",
            "scenario",
            "day",
            "opening_usable",
            "received",
            "expired_physical",
            "demand",
            "served",
            "unmet",
            "closing_usable",
            "buffer_days",
        ]
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for comparison in value["comparisons"]:
            for scenario in ("baseline", "disruption"):
                for point in comparison[scenario]["forecast"]["points"]:
                    writer.writerow(
                        dict(
                            overlay=comparison["overlay_version"] or "original",
                            hospital=comparison["hospital_id"],
                            scenario=scenario,
                            **{f: point[f] for f in fields[3:]},
                        )
                    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "docs/evidence/pharmax-phase2/dataset.json")
    parser.add_argument("--horizon", type=int, choices=range(1, 61), default=14)
    parser.add_argument(
        "--overlay", type=Path, default=Path(__file__).resolve().parent / "fixtures/demo_overlay.json"
    )
    parser.add_argument("--original-only", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    try:
        value = calculate(
            args.dataset, horizon=args.horizon, overlay_file=None if args.original_only else args.overlay
        )
        print(render(value))
        if args.output_dir:
            export(value, args.output_dir)
        return 0
    except (ValueError, OSError, KeyError):
        print("FAIL RISK_INPUT_OR_CUTOFF_VALIDATION; no fallback")
        return 1
