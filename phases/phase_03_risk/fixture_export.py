"""Export computed cached-reference API examples; no network and no invented outputs."""

from pathlib import Path
from datetime import date
from contracts import ForecastRequest, ScenarioOverlay
from phases.phase_02_data.pipeline import load_bundle
from .tools import RiskTools


def main():
    root = Path(__file__).resolve().parents[2]
    bundle = load_bundle(root / "docs/evidence/pharmax-phase2/dataset.json")
    tools = RiskTools(bundle)
    overlay = ScenarioOverlay.model_validate_json(
        (Path(__file__).resolve().parent / "fixtures/demo_overlay.json").read_text()
    )
    examples = {
        "trade-exposure": tools.trade_exposure(date(2026, 10, 24)),
        "shipment-risk": tools.shipment_risk("ship-central"),
        "inventory-forecast": tools.inventory_forecast(
            ForecastRequest(hospital_id="sim-central", scenario="baseline")
        ),
        "hospital-risk-assessment": tools.hospital_risk(ForecastRequest(hospital_id="sim-north")),
        "scenario-comparison": tools.scenario_comparison(
            ForecastRequest(hospital_id="sim-central", overlay=overlay)
        ),
    }
    for name, result in examples.items():
        (root / "contracts/examples" / f"risk-{name}.json").write_text(
            result.model_dump_json(indent=2) + "\n"
        )
    print("Exported five calculated cached-data analytical examples; no live source/inference calls.")


if __name__ == "__main__":
    main()
