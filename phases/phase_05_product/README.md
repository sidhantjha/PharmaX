# Integrated PharmaX operator backend

Use `uv run python scripts/pharmax_demo.py --scenario red-sea --mode replay` for the canonical integrated path and `--serve` for the existing server with `/api/v1` mounted. Set PHARMAX_NODE to Node 24 (verified with 24.22.1); system Node 18 cannot run the H-MEM bridge. Original/clean-onset July scenarios remain available. All feature code/tests stay in this phase; shared additive contracts are in `contracts/operator.py`.

See [frontend handoff](../../docs/PHASE_5_FRONTEND_HANDOFF.md), [audit](../../docs/PHASE_5_AUDIT.md), [boundary decisions](../../contracts/DECISIONS.md) and [actual evidence](../../docs/evidence/pharmax-phase5/). Run actual-process HTTP acceptance with `uv run python -m phases.phase_05_product.acceptance`. The default service has no inference or external action connector. Fresh sources and service diagnostics are explicit CLI operations, separate from deterministic offline replay.

Phase 5B adds bounded concurrent trade/disruption reads, ordered evidence reconciliation,
real invocation graphs and an operation-level decision ledger. See the
[architecture and twelve-role audit](../../docs/PHASE_5B_ARCHITECTURE.md).
Read `/api/v1/investigations/{id}/execution-graph` and `/decision-ledger` with existing
pagination. Source gates, H-MEM and mitigation choices remain sequential. Terminal
output reports actual recorded overlap, including an explicit no-overlap result.

Phase 5C adds `--conflict-demo` for receipt inspection, explicit disagreement records,
conditional forecast revision and H-MEM-informed mitigation rechecks. Run
`uv run python scripts/pharmax_demo.py --conflict-demo --output-dir .runtime/pharmax-phase5c`
with Node 24 selected through `PHARMAX_NODE`. See the
[evidence-reasoning note](../../docs/PHASE_5C_EVIDENCE_REASONING.md) for trust boundaries,
typed API routes and retained original/revised case evidence. Both cases remain in
the same immutable operator store; no reference dataset or frontend file is changed.

Final release candidate: [integration handoff](../../docs/PHARMAX_FINAL_BACKEND_HANDOFF.md). The expanded actual-process acceptance checks both scenario calculations, dependency ordering, recorded read-only overlap, H-MEM activity, revised endpoint projections and unauthorized live Ask responses.

The opt-in verification/dependency/mitigation native model loops are implemented and mock-tested, but genuine live acceptance remains blocked. See [live-specialist status and prerequisites](../../docs/PHARMAX_LIVE_SPECIALISTS.md). Ordinary replay and HTTP defaults remain deterministic.
