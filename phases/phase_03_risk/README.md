# Phase 03 — deterministic supply risk and daily forecasting

Pure cached-data calculations, typed analytical tools and the existing terminal. No network, inference, database writes, optimization, transfers, clinical substitution, orchestration or final-report service. Original Phase 2 data is unchanged.

## Reproduce

From the assigned PharmaX worktree on `codex/pharmax-phase-3-risk`, based on verified `7178494`:

```sh
uv sync --locked --all-groups
uv run python scripts/risk_demo.py --output-dir .runtime/pharmax-p3
uv run python scripts/verify.py risk --output-dir .runtime/pharmax-p3-verify
uv run pytest -q
uv run ruff check contracts phases scripts/verify.py scripts/risk_demo.py scripts/export_contracts.py
uv run python scripts/export_contracts.py --check
```

This session uses `/tmp/pharmax-tools/bin/uv`; all execution uses the existing project `.venv`. The two terminal entry points produce byte-identical analysis JSON with pinned inputs. No clock or random values enter result identities. `--original-only` omits the separate demonstration overlay. `--horizon 1..60`, `--dataset PATH`, and `--overlay PATH` are explicit input controls. An incompatible overlay/dataset version fails; no fallback occurs.

`analysis.json` contains typed comparisons, input values, evidence references, daily stock points and retrospective trade context. `timeseries.csv` has 168 chart rows for default 6 comparisons × 2 scenarios × 14 days. These are analytical/debug artifacts, not a generated operational report. Full lot-level balances, physical/reserved/quarantined counts, expirations and rule explanations are in JSON. Committed examples and the terminal receipt are under `docs/evidence/pharmax-phase3/`.

## Actual default calculations (14 days, July 24–August 6, 2023)

| Starting condition | Hospital | Baseline unmet / shortage days | Disruption unmet / shortage days | First unmet baseline → disruption |
|---|---|---|---|---|
| Original Phase 2 | Central | 20 vials / 4 | 80 vials / 14 | July 24 → July 24 |
| Original Phase 2 | North | 0 / 0 | 0 / 0 | None → None |
| Original Phase 2 | South | 40 / 8 | 40 / 8 | July 24 → July 24 |
| Separate demo overlay | Central | 0 / 0 | 54 vials / 9 | None → July 29 |

Central's original 12 stock minus 8 reserved gives 4 usable vials for 6 daily demand: the first day has 2 unmet vials before any delivery change matters. Baseline still receives 60 vials July 26; the held-shipment case receives none. Thus the disruption adds 60 unmet vials and 10 unmet-demand days, but does not cause the original onset. South's quarantine-related shortfall is independent of Central's shipment; North remains supplied.

`fixtures/demo_overlay.json` is a separate, hash-versioned hypothetical starting condition with Central stock 38 and reservations still 8 (30 usable). The original dataset is not corrected, overwritten or replenished. The overlay exists because the original is unsuitable for a clean **onset** demonstration, though it already demonstrates worsened unmet demand. Baseline is Moderate (minimum closing buffer below 3 days); disrupted Central is High, first unmet July 29. It is not a purchase/transfer recommendation or successful mitigation.

## Calculation semantics

- Demand is integer ceiling of the mean of seven complete pre-anchor simulated daily-use records. Missing days, duplicate days or inconsistent identities fail rather than assuming zero. Zero demand gives null buffer days.
- Existing reservations are ring-fenced. Daily forecast demand means additional unreserved demand; this assumption may differ from operational reservation accounting and must be reviewed before real use.
- Lots remain usable through expiry day; at the start of the following day, remaining physical quantity and reservations expire. Quarantine and incompatible storage make stock unusable. Wrong product IDs/strengths are never substituted.
- Day order: expire → receive eligible modeled shipments → serve unreserved demand by FEFO, then lot ID. Unmet demand is counted once, not carried into tomorrow as a second demand.
- POs are not arrivals. Each registered shipment is credited at most once. Baseline ignores modeled holds only; disruption honors them. Both cases enforce approvals, dates and storage/light constraints. All arrivals are modeled, not physically confirmed.
- Incoming lots are explicitly assumed released/unreserved, expiring arrival date + 30 days by default. Actual shipment lot expiry is absent in Phase 2; this is an adjustable, labeled assumption.
- Shipment overrides explicitly hold or schedule hypothetical arrivals (including release of a held shipment). Dates before dispatch fail. Past-due dates are not recredited to starting stock. Overrides are applied identically in each comparison; comparisons isolate hold handling, and separate forecast calls can evaluate other input changes.
- Stockout means first unmet-demand day, not necessarily zero physical stock. `shortage_days` counts unmet days, `longest_shortage_run` counts contiguous days, and `right_censored` identifies ongoing shortage on the final modeled day. A null first date means none **within this horizon**.

## Trade and historical knowledge

The two observed 2023 US HS30 partner values sum to USD 55,146,159,753. Sample shares: Germany 34.669486%, Ireland 65.330514%. They are shares **within the observed sample**, not national import shares or a largest-partner ranking. Without a comparable world denominator, national shares/value coverage are null. Full HHI and national country-risk classification require complete comparable partner value coverage. Historical changes require one adjacent comparable prior period; none is present in the pilot.

Retrospective trade context is evaluated at 2026-10-24. July 2023 inventory projections use only available simulated network inputs. All 27 observed/derived Phase 2 evidence records are excluded from that historical forecast cutoff, including 2026 FDA revisions and later retrieval of the 2023 storm statement. Publication dates do not prove revised-source availability. Parent lineage is checked. The real US manufacturing assessment does not prove carboplatin or simulated India supplier exposure. `historical_backtest=false` is fixed in the forecast contract.

## Tools and rules

`tools.RiskTools` validates/copies the source bundle, reserves at most 20 analytical calls before execution, and returns detached results so caller mutations cannot alter later calculations. Requests are bounded to 60 days, 32 initial target lots and 16 target shipments; overlays have at most 20 stock/arrival edits. No arbitrary SQL, executable text or operational action parameters.

- `trade_exposure(context_as_of)` → TradeExposure; partial coverage returns explicit null country classification.
- `shipment_risk(shipment_id, scenario, overlay)` → ShipmentRisk.
- `inventory_forecast(ForecastRequest)` → InventoryForecast.
- `hospital_risk(ForecastRequest)` → HospitalRiskAssessment, including independent supplier shipment-exposure ratings.
- `scenario_comparison(ForecastRequest)` → ScenarioComparison.

Rules and limitations are in [RISK_MODEL.md](../../docs/RISK_MODEL.md). Shared contracts live in `contracts/risk.py`; the original Phase 1 tool allowlist and Phase 2 storage APIs are unchanged. All outputs are `kind=derived`, with algorithm version, dataset version, analysis hash, inputs/evidence, assumptions and limitations. Original stock evidence stays original even when a separate overlay changes an effective quantity.

## Schemas, examples and validation

```sh
uv run python -m phases.phase_03_risk.fixture_export
uv run python scripts/export_contracts.py
uv run python scripts/export_contracts.py --check
```

Five `contracts/examples/risk-*.json` examples are calculated from the cached reference dataset, preserving its observed/derived/simulated provenance. They are not fresh live observations or the fully simulated Phase 2 fixture mode. There are 28 total exported schemas. Tests cover numerical oracles, physical/demand balance, FEFO, expiry, quarantine, reservations, demand rounding, missed shipments, delay/release overrides, exact product identity, cutoff/parent knowledge, partial/complete trade coverage, rules, source immutability, typed examples, call limits and no-network reproducibility.

See [Phase 4 handoff](../../docs/PHASE_4_HANDOFF.md). No live services are required or rerun in this phase; prior Zetaris and Nemotron verification remains historical evidence. Separate Engine validation is still pending.
