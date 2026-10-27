# PharmaX Phase 4A

A bounded investigation coordinator, three specialist workers, public Phase 2/3 tools and a real local Meterless H-MEM engine. Decisions and actual tool execution emit public JSON events; no fictional conversations or model reasoning are logged. See [decisions](DECISIONS.md).

## Runtime prerequisites and replay

Python 3.12 with the repository `.venv`, `uv`, and **Node >=22.22** with experimental TypeScript transforms. Node 18 cannot run this engine. No npm packages are needed by the vendored engine or conformance runner. To install an isolated Node executable if required:

```sh
npm install --prefix /tmp/pharmax-tools node@22.22.0
export PHARMAX_NODE=/tmp/pharmax-tools/node_modules/node/bin/node
uv sync --locked --all-groups
uv run python scripts/investigate.py --scenario demo
uv run pytest -q
uv run ruff check contracts phases scripts/verify.py scripts/investigate.py
```

The demo defaults to the pinned **cached** Phase 2 observed+simulated dataset and an explicitly labelled deterministic fixture decision policy. Zero network or inference. It writes `.runtime/pharmax-p4a/investigation.json` (schema `4a.1`) and H-MEM `state.json`/append-only `ledger.jsonl` under a hashed session partition. Use `--session NAME` for another isolated investigation; identical findings in the same partition are idempotent. `--output-dir` chooses the trace/memory root. `--objective 'trade only: assess country exposure'` shows selective offline dispatch.

```sh
uv run python scripts/investigate.py --scenario demo --mode fixture
uv run python scripts/investigate.py --historical --as-of 2023-07-24T23:59:59+00:00
```

These return exit 2 (incomplete): the inherited all-simulated fixture lacks comparable trade query scope; the historical investigation excludes trade and FDA revisions unavailable at the cutoff. Neither switches to later data. July 2023 inventory forecasting remains a simulated scenario, never a historical backtest. The contemporary cached demo returns exit 0 with Central baseline/disruption unmet 20/80 vials, North 0/0 and South 40/40. Unknown manufacturing link, national denominator and previous period remain explicit.

## One bounded live investigation

Select existing private files explicitly; never copy their contents. Confirm the route is no-cost (`PHARMAX_MODEL_NO_COST=1`), or use `--allow-paid-model` **only with explicit cost authorization**.

```sh
uv run python scripts/investigate.py --scenario demo --mode live-zetaris --live-model \
  --env-file .env \
  --zetaris-env-file [redacted historical private path] \
  --session live-phase4a --output-dir .runtime/pharmax-p4a-live
```

This runs at most two Nemotron decision POSTs (plan, review), not a provider smoke. No catalog rediscovery, retries, direct NVIDIA calls, model quantity calculation or silent cached fallback. Each response must identify the pinned Nemotron model and Nightshift's NVIDIA winning route. Public events project model/provider and measured token usage only. Live Zetaris queries read existing views; nothing is published or changed. Missing credentials, unavailable routes and inconsistent rows produce a structured blocked/incomplete result and nonzero exit.

## Official H-MEM conformance

```sh
HMEM_IMPL="$PWD/phases/phase_04_agents/vendor/hmem/src/index.ts" \
  "$PHARMAX_NODE" --experimental-transform-types --disable-warning=ExperimentalWarning \
  phases/phase_04_agents/vendor/hmem/conformance/runner.ts
```

The same engine class is imported by `bridge.mjs` during investigations. All **13/13 official checks pass**; Python integration tests additionally test the actual bridge, persisted memory, conflicts, session isolation, evidence parent cutoffs and reinjection into mocked Nightshift transport. [Pinned upstream specification](https://github.com/Meterless/Meterless/blob/1742dec56a66e58eef9dda2d2b865823758ca8cd/engines/hmem/AGENTS.md). `vendor/hmem/UPSTREAM.json` contains per-file SHA-256 checksums. The reference is a deterministic demonstration engine, not production durability infrastructure.

## Phase 4B: adaptive mitigation and replanning

The same terminal now extends the coordinator with simulated supply discovery, typed feasibility tools, actual H-MEM rejection capture/retrieval, and investigation of another alternative. Existing Phase 4A entry points remain compatible. Read [mitigation decisions](../../contracts/DECISIONS.md#phase-4b--simulated-mitigation-boundary-2026-10-24).

```sh
export PHARMAX_NODE=/tmp/pharmax-tools/node_modules/node/bin/node
uv run python scripts/investigate.py --mitigation --scenario original --session original-demo
uv run python scripts/investigate.py --mitigation --scenario clean-onset --session clean-demo
```

Defaults: pinned cached Phase 2 data; explicitly labelled deterministic model fixture; six candidate evaluations; twenty tool calls; 90-second shared tool deadline. Zero network/model inference. Node >=22.22 is still required for genuine H-MEM. `--output-dir`, `--as-of`, `--horizon-days`, `--candidate-budget 1..6` and `--tool-budget 1..30` expose bounded controls. Smaller budgets return incomplete; `--mode fixture/live-zetaris` is rejected for mitigation because its catalog is pinned to the observed+simulated cached dataset. All operational inputs/outputs are simulated even though that base dataset also contains real retrospective source context.

The catalog in `fixtures/mitigation_options.json` has content-hashed simulated supplier inventory, route/transfer permissions and proposed interventions, not precomputed outcomes. The model fixture selects the largest untried proposal, consumes retrieved rejection memory and revises its next choice. Changes to the returned proposals change the choices. Actual constraints and Phase 3 calculations determine feasibility and outcomes. Source records are treated as evidence; approved tools accept typed parameters and perform simulations only. A fixed list of candidates is a bounded search space, not a scripted execution trace or exhaustive inventory optimizer.

Original case: baseline/disruption **20/80** unmet vials; evaluated 24-vial North transfer leaves **56**, onset **July 28**, **10** affected days, Central **Critical→High**. North stays **0 unmet/Low**, with **20** unreserved vials at horizon end and its **20 reserved vials protected**. South remains **40 unmet/Critical**.

Clean-onset overlay: baseline/disruption **0/54** unmet vials; 36-vial simulated supplier delivery leaves **18**, onset **August 4**, **3** affected days, Central **High→High**. The 24-vial transfer is independently feasible but leaves **30** unmet. Both preserve North/South outcomes. Supplier delivery is late for the original case and is rejected there. Both outcomes retain residual shortages.

The 80-vial wrong-strength proposal fails exact product/presentation matching. A 60-vial North transfer would create **16** unmet donor vials and worsen donor risk, so it is rejected and moves **zero admissible vials**. Atlas release lacks release authority; South stock is quarantined and lacks the required incoming shelf life. Their diagnostic forecasts cannot become accepted recommendations.

Results compare **single alternatives independently**; they do not combine transfers with supplier deliveries, optimize quantities, claim all stock is exhausted, or add improvements from different alternatives. Combining individually feasible actions needs a separately conserved plan and another forecast; it may improve the outcome and remains future work. Expedited late rescue and future-dated transfer departures are also unsupported and explicitly rejected. This bounded prevention workflow treats arrivals at day start before demand and requires at least 30 days of remaining shelf life to preserve Phase 3's modeled expiry assumption. These are simulated operational assumptions, not real service commitments.

Shared frontend boundary `4b.1` is in `contracts/mitigation.py`, with five additive schema exports (33 total). It includes original/canonical overlay hashes, versioned scenario snapshots, constraints, proposed/admissible quantities, simulated supply balance, all hospital impacts, baseline/disruption references and projected daily inventory charts. Rejected static constraints have null diagnostic projections and unchanged effective metrics; donor-rejected diagnostic projections are explicitly labelled. The terminal JSON validates against `mitigation-result.schema.json`. Source documents/datasets and earlier contract schemas remain unchanged.

### Prepared live command — do not run without authorization

This command was **not run** during Phase 4B. Use only after separate live-test authorization, with a confirmed no-cost route or explicit permission to add `--allow-paid-model`.

```sh
PHARMAX_MODEL_NO_COST=1 uv run python scripts/investigate.py --mitigation --scenario clean-onset \
  --session authorized-live-p4b --live-model --candidate-budget 4 --tool-budget 20 \
  --env-file .env --output-dir .runtime/pharmax-p4b-authorized-live
```

Set `PHARMAX_MODEL_NO_COST=1` only when the route is actually confirmed no-cost; it is not a billing control. Maximum **four inference POSTs**, **300 output tokens each / 1,200 total**, **24 KB serialized decision input per request**, **32 KB response per request**, **60-second request timeout**, zero automatic retries/fallback. Calls stop earlier on a model stop, failure, deadline or exhausted candidates. Nemotron selects typed candidate IDs; it never supplies quantities or forecast outcomes. The existing adapter validates response model and NVIDIA winning-route metadata and projects token usage. Operational tools remain cache-only. A four-candidate live run may end incomplete and should be reported as such; do not automatically authorize another call. No provider smoke is needed.

Fresh runs with identical input/memory snapshots produce identical structured investigator results. Actual terminal `completed_at` records execution time. Reusing a session recalls prior rejections and may change search order, while revalidating each candidate; use a fresh `--session` and empty output directory to replay the initial decision sequence.
