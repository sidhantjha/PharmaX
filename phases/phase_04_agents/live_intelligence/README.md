# Phase 4C live intelligence

A bounded source-to-decision pilot. Official advisory evidence establishes corridor exposure; every supplier, lot, route, shipment and hospital relationship is simulated. Delay days are configurable assumptions, never observed delay estimates. Physical actions require human review.

## Three-minute frozen demonstration

Use the repository `.venv` (`uv sync --locked --all-groups`) and Node >=22.22, setting `PHARMAX_NODE` if needed:

```bash
uv run python scripts/live_intelligence_demo.py --output-dir .runtime/p4c-demo-fresh
```

Default mode is explicitly **frozen offline replay**, using the retained real MARAD responses in `docs/evidence/pharmax-phase4c/manifest.json`. It executes local matching, Phase 3 forecasts, the Phase 4B adaptive coordinator, feasibility checks and real H-MEM writes/retrievals. Decisions use the labelled deterministic fixture policy. No HTTP, credentials or models are used. Use a fresh output directory for a fresh investigation; persisted jobs prevent duplicate execution.

The separately versioned October 24, 2026 scenario leaves the July 2023 replay unchanged. A fictional Gujarat supplier's 90-vial carboplatin shipment follows a modeled 30-day ocean/land journey via Nhava Sheva, Red Sea/Suez and Port Newark to fictional Central. Scheduled delivery is November 15. Under explicit 0/3/7-day assumptions, unmet demand is 0/0/24 vials. The seven-day case has four shortage days from November 18, risk High. A feasible 24-vial North transfer reduces unmet demand to zero and Central risk to Moderate; North and South remain at zero unmet. The checker rejects wrong-strength stock and a 180-vial North transfer (80 donor unmet). The independent 36-vial supplier option also prevents modeled unmet demand. These are independently compared options, not a combined plan or global optimum.

## Bounded public fetch

```bash
uv run python scripts/live_intelligence_demo.py --live-fetch --only-source marad --output-dir .runtime/p4c-live-new
```

This actually fetches the MARAD listing and pilot advisory (two requests), without silently substituting cached data on failure. Add `--publish-zetaris --zetaris-env-file /absolute/existing/private.env` to publish and query immutable evidence through the existing service. Credentials are never copied or logged. A failed publication is reported. Full public-source mode omits `--only-source`; it adds one bounded GDELT ArticleList JSON request, at most 12 indexed articles, with no article-body scraping. Maximum three source requests per refresh, 20-second timeout each, no immediate retries. Actual GDELT demonstration returned HTTP 429; no news retrieval success is claimed.

MARAD publication timestamp is unavailable in the retrieved page: null is intentional. September 23 is its effective date, not proof of publication or historical availability. All source evidence is bounded by actual retrieval time. GDELT `seendate` is indexing time, not a verified publication date. Media items remain unverified and cannot alone trigger this pilot's exposure-driven forecast workflow.

## Refresh and read service

```bash
uv run python scripts/live_intelligence_demo.py --refresh --output-dir .runtime/p4c-monitor
uv run python scripts/live_intelligence_demo.py --poll --marad-seconds 21600 --gdelt-seconds 1800 --output-dir .runtime/p4c-monitor
uv run python scripts/live_intelligence_demo.py --investigate-pending --output-dir .runtime/p4c-monitor
uv run python scripts/live_intelligence_demo.py --serve --port 8766 --output-dir .runtime/p4c-monitor
```

Polling must remain running to monitor sources. It records last checks, due times, failures, bounded exponential backoff and numeric Retry-After. It queues new MATCHED revisions but never starts inference or mitigation automatically. The separate pending worker executes at most one retained relevant signal with local tools and fixture decisions. Manual refresh obeys a five-second floor. Revisions and raw receipts are immutable; duplicate semantic content adds retrieval evidence without overwriting the original revision. Changed raw content with unchanged extracted claims is flagged `content_needs_review` for MARAD, retained for inspection and does not invent new claims or trigger model work.

Loopback read-only HTTP: `/v1/intelligence/{signals,sources,citations,matches,risk,forecasts,activity,mitigations}`. Optional `limit` (1–100) and aware `as_of` query parameters; reads never fetch or invoke models. POST is denied. The future frontend can proxy this service or call `IntelligenceService.read`. Results include schema version, freshness/history information, stale-decision status, simulated/calculated labels and citations. Old decisions remain auditable when an event expires or changes; they are explicitly stale. Historical queries exclude future evidence/runs and do not invent past source-status history.

`investigation.json` contains shared `IntelligenceRun` (4c.1), actual activity and Phase 4B `MitigationResult`. Chart points include scheduled, each delayed case and evaluated alternatives, with rejected diagnostic projections labelled inadmissible. Six additive schemas are exported (39 total). No frontend files changed.

## Verification and limits

See retained execution evidence and validation in `docs/evidence/pharmax-phase4c/`. The actual successful fetch was October 24, 2026 at 18:17:10.242651Z; Zetaris publication/readback verified one normalized revision. Earlier parser failure and GDELT 429 are preserved separately. The offline replay is never described as current retrieval.

No paid inference is authorized or used. The existing separately gated Nightshift/Nemotron adapter remains available in Phase 4A/4B, but this pilot does not claim a live model trace or routing receipt. Consult the parent phase README for the authorization-gated bounded live-model command. No model is invoked by polling or service reads. Operational feasibility is simulated; no shipment delay or carboplatin dependency is established by the real advisory. No manufacturing link to the Rocky Mount event is inferred.
