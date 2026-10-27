# Phase 02 — pharmaceutical data and fictional Illinois supply network

Working pilot: immutable source snapshots, typed normalization, versioned datasets, isolated Zetaris SchemaStore publication, safe read tools and the existing diagnostic terminal. No inference, risk engine, agent orchestration, clinical substitution or mitigation outcome is implemented here.

## Setup and exploration

From the PharmaX worktree on `codex/pharmax-phase-2-data`:

```sh
uv sync --locked --all-groups
uv run pytest -q
uv run ruff check contracts phases scripts/verify.py
uv run python scripts/export_contracts.py --check
uv run python scripts/verify.py data explore --fixture
```

`uv` was available at `/tmp/pharmax-tools/bin/uv` in this session. All code uses the project `.venv` and existing lockfile. Fixture mode is entirely simulated and cannot create a network client or publish to Zetaris.

Inspect the committed **cached observed + simulated** reference dataset without any network:

```sh
uv run python scripts/verify.py data explore --dataset docs/evidence/pharmax-phase2/dataset.json
uv run python scripts/verify.py data query --dataset docs/evidence/pharmax-phase2/dataset.json --collection trade_flows --reporter 842 --partner 276 --limit 5
uv run python scripts/verify.py data query --dataset docs/evidence/pharmax-phase2/dataset.json --collection inventory --hospital sim-north
```

## Bounded fresh ingestion and cache replay

```sh
uv run python scripts/verify.py data ingest --root .runtime/pharmax-p2 --refresh
```

Four source GETs maximum: Comtrade (10 rows), FDA shortages (20 rows), recalls (3 rows), one pinned FDA document. Each response is at most 1 MB; HTTP defaults to 20 seconds/4-second connect; no retries or fixture fallback. The fresh command saves content-addressed raw bytes, receipts under `snapshots/`, a source-selection JSON under `selections/`, and a dataset under `datasets/`. A failure is nonzero, never an empty successful dataset. API filters and all field/relationship checks run before publication. Samples can be incomplete; inspect `coverage`.

Reuse one explicitly selected receipt map from `selections/` (replace `SELECTION_HASH` with its saved filename):

```sh
uv run python scripts/verify.py data ingest --root .runtime/pharmax-p2 --selection .runtime/pharmax-p2/selections/SELECTION_HASH.json
```

The session's equivalent map is `.runtime/pharmax-p2/source-selection.json`. Cached ingestion opens no HTTP client, verifies receipt/body hashes and approved source URLs/parameters, preserves observation timestamps, and reproduces the same dataset hash. Explicit refresh creates a new observation/version without overwriting previous raw responses or datasets. A normalized reference dataset supports offline queries on a fresh clone; rebuilding from raw sources requires the local raw cache or a bounded refresh. Raw cache bytes are intentionally not committed.

## Zetaris publication and validation

On a fresh Zetaris organization, create the isolated container once using `--initialize` on the publish command. Omit the flag in this already-initialized environment. A duplicate initializer is an explicit error, never a drop/replace.

```sh
uv run python scripts/verify.py data publish --dataset docs/evidence/pharmax-phase2/dataset.json --zetaris-env-file [redacted historical private path]
uv run python scripts/verify.py data explore --dataset docs/evidence/pharmax-phase2/dataset.json --live-zetaris --zetaris-env-file [redacted historical private path]
uv run python scripts/verify.py data query --dataset docs/evidence/pharmax-phase2/dataset.json --live-zetaris --zetaris-env-file [redacted historical private path] --collection trade_flows --reporter 842 --partner 276 --limit 5
```

Use the `datasets/VERSION.json` emitted by fresh ingestion to publish a later version. `publish` creates an append-only, hash-named view then reads **all 13 collections** back and compares every payload with local validated data; a repeat verifies the existing view. No DROP/REPLACE, infrastructure changes, credential copies, arbitrary query tools or municipal modifications. Registered pilot view:

```text
pharmax_p2_data.v_6a4a8d083742ed7ef0d8a2b1090898f5ab655f0b5b179701c20c8260ba377819
```

Storage is a persistent literal-backed SchemaStore view verified across HTTP requests. It is suitable for this **163-record pilot**, bounded to 500 total records, 100 per collection, and 900 KB registration SQL. It is not a scalable warehouse. The existing authenticated Zetaris transport is reused; only trusted compilers call it. Country/product/hospital/evidence reads are SELECT-only, bounded to 100 rows, with hex-encoded literals and validated identifiers.

A Spark self-join against the repeated literal view failed; `network()` instead reads the five relevant collections once (at most 100 rows) through Zetaris, validates complete payloads, then links explicit keys in Python. It returns supply relationships, never a forecast or scored action. No source fallback occurs on read errors.

## Source and scenario boundary

Real coverage: annual **2023** US imports of HS30 from **Germany and Ireland**, 20/35 matching carboplatin package records (initial posting **2023-04-28**, revisions observed in October 2026), three historical terminated recall records, and one dated **2023-07-21 FDA storm-damage statement**. India was requested but absent from this preview response; absence is not zero trade. Quantities are unknown because returned units are N/A, despite numeric zeros. No monthly/fine-category/drug-specific trade mapping is claimed.

Scenario anchor: **2023-07-24**, a historical illustration using currently retrieved revisions, **not an as-of backtest**. Exactly three fictional Illinois hospitals; primary `sim-carboplatin-450` is modeled 450 mg/45 mL (10 mg/mL), one unopened single-use vial per inventory unit. All supplier/country/product/order/shipment/hospital dependencies are simulated. Storage 20–25 C/light protection are modeled constraints, not validated clinical guidance.

| Hospital | Primary stock | Reserved | Historical daily use | PO | Expected delivery | Stored constraint |
|---|---:|---:|---:|---:|---|---|
| Central Illinois Regional Hospital | 12 | 8 | 6 vials | 60 | 2023-07-26 | Fictional Atlas delivery held by modeled dispatch disruption |
| North Illinois Medical Center | 80 | 20 | 4 vials | 40 | 2023-07-25 | Released primary stock; expiry 2023-09-30 |
| South Illinois Oncology Center | 100 | 10 | 5 vials | 30 | 2023-07-27 | Quarantined primary stock; expiry 2023-07-25 |

North also holds 200 modeled 150 mg/15 mL vials with a different product ID, with no approval for the primary product's orders. Nothing treats these as interchangeable. Seven daily consumption observations per hospital are simulated for July 17–23 (21 rows). Six modeled supplier approvals, three orders and three shipments are explicit. No available-stock forecast, successful transfer or clinical substitution has been hard-coded.

The real storm statement establishes a manufacturing-site incident and assessment, not a carboplatin impact. The fictional Atlas dispatch hold is a **separate simulated event**; no observed causal link is invented. See [data source coverage](../../docs/DATA_SOURCES.md), [shared interfaces](../../docs/PHARMAX_INTERFACES.md), [progress](../../docs/PROGRESS.md) and [Phase 3 handoff](../../docs/PHASE_3_HANDOFF.md).

## Contract examples and checks

```sh
uv run python -m phases.phase_02_data.fixture_export
uv run python scripts/export_contracts.py
uv run python scripts/export_contracts.py --check
uv run pytest -q
```

`fixtures/source_inputs.json` and `scenario.py` reproducibly generate the fully simulated `fixtures/bundle.json` and 13 `contracts/examples/data-*.json` pages. Live evidence is preserved separately in `docs/evidence/pharmax-phase2/`. Existing foundation tests remain active. No live model inference is required.
