# PharmaX boundary decisions — v1 (2026-10-23)

## Final backend verification, separate authorization (2026-10-27)

Add a separate versioned `model-verification` read endpoint and immutable SQLite
run-status ledger. Preserve historical HeleneCase payloads. The existing trace
sheet may display actual receipts without a redesign. Unavailable, partial and
validated execution stay distinct; status inspection never dispatches inference.

The final backend brief authorizes one NEW investigation, preserving the expired
Session A receipts unchanged. Ceilings: 36 logical turns, three per existing role
(one bounded corrective continuation), 108 possible upstream attempts, 90,000
logical input/output tokens where observable, 900 seconds, concurrency two.
Reserve three possible upstream attempts before every POST. Retain at most twelve
logical request envelopes through completion plus sixty seconds, keeping potential
upstream traffic within 36 attempts per sliding minute (below published 40 RPM).
Internal failed-attempt usage remains unobservable, never fabricated.

Role-specific native output schemas and evidence-key validation narrow hospital
scope to inventory/demand; clinical limitations remain explicit. H-MEM recall must
enter downstream actual tool results and constrain candidate selection. Deterministic
calculations, pharmacy review and action permissions retain authority. The new CLI
does not authorize the production HTTP inference path. No commit/push/merge/deploy
is authorized in this session. Zetaris source registration must use accessible
staged source data; credentials stay in an explicitly selected private file.

## Session A finalization: rate-limited NVIDIA Build and retry exposure (2026-10-27)

The user expressly authorizes the free NVIDIA Build hosted prototyping endpoint,
using official no-cost FAQ/rate-limit guidance plus their active PharmaX-Test key
attestation. A nonexistent credit balance is not required for this rate-limited
tier. Paid partner endpoints and fallback remain forbidden. Review this explicit
evidence into an expiring backend-owned authorization, never an HTTP/env approval.

The user relaxes the single-upstream-attempt preference ONLY with a checked runtime
bound and reserved aggregate exposure. Preserve legacy single-attempt authorization
defaults. A separate bounded authorization requires an executable pre-dispatch
runtime guard and attempt reservation: at most 25 logical turns (one smoke plus
24 specialist turns), 75 possible upstream attempts, two concurrent turns, two
turns per existing role, no PharmaX retry. Actual internal retry counts remain null
unless observed; maximum exposure is not an actual-attempt receipt.

Extend the existing ModelSession native protocol with role profiles and a shared
budget, retaining its existing three-role defaults. Twelve Helene read scopes reuse
saved evidence, existing forecasts, reconciliation, readiness/identity, feasibility,
draft reads and H-MEM. No new specialist IDs, forecast engines or action authority.
Clinical quantitative outcomes remain unsupported for saline. Model findings are
schema- and citation-validated; all quantities and safety gates stay deterministic.

## Phase 7 operational reconciliation (2026-10-26)

The explicitly authorized Phase 7 brief extends Phase 6 through public interfaces.
`operational_claims.py` is the shared claim/finding boundary. Source organization
and originating fact are separate; agency guidance is its own fact, while repeated
manufacturing statements share Baxter origin. Hashes prove byte identity, not
authenticity. Exact product, account, order line, shipment, facility, unit, scope
and effective clocks gate comparison. Verified explicit supersession is required;
retrieval recency grants no priority.

HeleneCase gains optional `verification`, preserving old saved cases. Phase 7
produces child cases in the existing append-only store, using public Phase 6
`forecast_operations` and `mitigate`. Accepted receipts after the inventory snapshot
add a separately identified lot; earlier receipts receive no second credit.
Unconfirmed shipments stay out of validated forecasts. Conditional supply cannot
enable mitigation. Procurement fixtures remain synthetic. Reconstructed public
document availability uses explicit end-of-issue-day assumptions; strict replay
still requires custody proof. The targeted Helene UI edits are explicitly authorized
by this brief; `frontend/` and globe code remain untouched. No inference or dispatch.

The municipal C0–C7 boundaries remain in the verified archive. PharmaX v1 replaces them on this branch before its source adapters and consumer terminal are introduced. There are no existing PharmaX downstream consumers to migrate.

- `models.py` is authoritative; `diagnostic.schema.json` is generated, checked in and compared in CI. Separate exported schemas cover SourceEvidence, TradeFlow, DrugShortage, ToolRequest, ToolResult and InvestigationEvent, alongside the diagnostic response. Extra fields fail validation. No HTTP API is implemented.
- Evidence is `observed`, `derived` or `simulated`. Derived evidence requires parent IDs. Source IDs and content hashes are distinct: FDA package NDC is preserved when available but is not assumed to identify a unique record revision. A SHA-256 of canonical raw row JSON identifies content. Comtrade preserves a composite source key and classification; absent fields remain null.
- `observed_at` is timezone-aware retrieval time, never backdated to publication. `published_on` and `updated_on` are source calendar dates; invalid or absent dates normalize to null, with original values retained in raw snapshots. `version_available_at` stays null unless established separately. Phase 2 must not admit an unknown historical revision as known-at-cutoff proof.
- UN Comtrade v1 is fixed to one bounded public preview query: annual 2023, HS 30, reporter 842 (US), partner 0 (world), imports. `primaryValue` is aggregate USD, null if absent. `isReported` and `isAggregate` are retained, including false. No shipment, manufacturer or hospital links are inferred.
- A raw source row snapshot and its SHA-256 are retained locally for every live retrieved row. `raw_ref` is a local artifact reference, not a future public API URL. Future APIs must project private paths out of their responses.
- Requests select one of three named reads with a limit of 1..5. They cannot carry arbitrary SQL, URLs, shell commands or credentials. Zetaris uses a closed approved-query registry, initially only `SELECT 1`. Adding a pharmaceutical query requires a reviewed read-only SQL template and tests. This restriction is the read-only guarantee; string-prefix filtering is not treated as a SQL security boundary.
- Runtime reservations happen before each tool dispatch, including failed calls. Default diagnostic permits at most four tool dispatches (three normal checks and one optional model-directed check). Nightshift reserves at most one inference, with 160 output tokens and a fixed small input. No retry, fallback or unbounded conversation occurs. Unknown monetary cost blocks inference; a separately confirmed no-cost route is required.
- Event mode identifies actual transport: live, fixture or mock. Only observable metadata is traced. No raw model answers, reasoning, response error bodies, auth headers or credentials are logged. Structured model output may propose a read; Python validates scope before executing it. Native function-call support is not established by this JSON-request path.
- `DiagnosticReport` v1 is a future frontend boundary, with checks, normalized source samples, events and repository metadata. Example responses under `contracts/examples/` are synthetic fixtures, not live success claims. Status labels have fixed meanings: PASS = requested operation completed, FAIL = invalid/unexpected result, BLOCKED = unavailable prerequisite/access/rate limit or explicit offline skip.

## Phase 1.5 — Nightshift live tool read (2026-10-23)

- Shared v1 schemas remain unchanged. Nightshift alone accepts explicitly configured RFC1918 IPv4 HTTP origins for the Windows LAN runtime; Zetaris origin policy is unchanged. Public plaintext, userinfo, query strings and fragments remain rejected. No endpoint guessing or fallback.
- One explicitly authorized inference may use `--allow-paid-model`; without this flag the existing `PHARMAX_MODEL_NO_COST=1` assertion is still required. Authorization is not a claim of zero cost. One attempt, 160 output tokens, bounded response and no retries remain enforced before dispatch.
- The Phase 1.5 diagnostic uses one native `pharmax_read` function call mapped into the existing ToolRequest boundary. Only FDA sample or Zetaris connection check with limit 1 is accepted. Model response identity must match the selected discovered Nemotron ID (allowing NVIDIA's omitted namespace). A catalog entry alone does not verify execution; response identity is provider-reported, not an independent GPU audit.
- Only validated read arguments, model identity, token counts, statuses and evidence references are traced. Model prose and reasoning are discarded. The evidence-backed answer is formatted by Python from the returned FDA record, clearly labeled; no second model call or claim of model synthesis. Source data never becomes executable instructions.

## Phase 1.5 blocker resolution — bounded retry and layered config

- The native Nightshift read retains one attempt by default. Explicit `--model-attempts 2` authorizes at most two total POSTs per adapter instance, with one 1-second backoff only after HTTP 502/503. Each attempt reserves the shared inference budget before transport; success, other HTTP failures, timeouts, malformed responses and identity failures never retry. The same endpoint, model and payload are reused; no alternate provider/model or fixture fallback. After the live receipt exposed wrapped NVIDIA 403s, the final rule also excludes known upstream NVIDIA 400/401/403/404; that refinement is mock-verified without another live attempt.
- Failed attempts remain FAIL events even if a later attempt succeeds. Error diagnostics are a closed projection of known runtime codes and fixed upstream failure signals; raw messages, bodies and reasoning are discarded. Successful routing metadata is validated against the requested model and NVIDIA provider when supplied; missing provider metadata remains explicitly unverified.
- `--zetaris-env-file` explicitly loads only `ZETARIS_*` keys from an existing private file after project configuration. Process/project values keep precedence; Meterless settings cannot be overwritten, and no credentials are copied into the worktree. Shared v1 schemas remain unchanged.

- The native read explicitly disables thinking within its 160-token cap and sends `meterless-tier-split: off`, using the installed runtime passthrough contract. The catalog-selected explicit model pin is retained. Runtime-internal provider retries are outside the observable PharmaX POST counter; their exact count is not asserted.

## Phase 2 — pharmaceutical data and simulated supply boundary

Authorized by the user's explicit Phase 2 request, superseding the repository's earlier Phase-1-only scope for this task.

- Extend TradeFlow and DrugShortage additively with optional source fields; regenerate v1 exports. New contracts cover products, dated curated disruptions, suppliers, hospital inventories, daily consumption, approvals, purchase orders, shipments, a validated dataset and frontend pages. Existing Phase 1 tool authority is unchanged. New read tools are closed, bounded query compilers, never arbitrary SQL.
- Reuse public Phase 1 normalizers, hashing, HTTP, dotenv and Zetaris authenticated transport. A public trusted infrastructure transport method is extracted from the existing Zetaris adapter; SQL is produced only by Phase 2 reviewed registration/read compilers, never accepted from model requests. Connection-check query scope stays unchanged.
- Immutable raw response objects and retrieval receipts are content-addressed locally. Dataset versions derive from canonical normalized records and snapshot receipts. Cache replay preserves original observed times and verifies hashes, with no network. Explicit refresh never silently falls back; fixture mode is entirely simulated.
- Actual Zetaris probes verified persistent SCHEMASTORE views (`CREATE SCHEMASTORE VIEW ... WITH CONTAINER ... AS SELECT ...`) across HTTP requests. Filestore registration refused local paths with HTTP 400 `Invalid file path to access`; ordinary CREATE VIEW was session-local. Use bounded literal-backed immutable views in dedicated `pharmax_p2_data`, named by dataset hash, avoiding infrastructure changes, external services or municipal mutation. This is a small pilot storage mechanism; raw responses remain local. SQL values are hex-encoded UTF-8 literals, identifiers compiled from closed registries and hashes. No DROP/REPLACE or arbitrary SQL tools.
- Observed Comtrade 2023 US HS30 partner aggregates are country trade context only: no carboplatin shipment/supplier inference. Product/package shortage status remains distinct from package availability. FDA dates do not prove historical revision availability.
- Curated real disruptions store dated source claims and explicit unknown implications; reviewed curation is derived from observed documents. Any modeled shipment exposure is separately simulated. Exactly three fictional Illinois facilities and all their supply relationships are simulated. No risk score, forecast, clinical substitution or successful mitigation is stored.

## Phase 3 — deterministic risk and scenario boundary

The user's explicit Phase 3 authorization supersedes the earlier phase-only scope for this task. Original Phase 2 datasets, views and source evidence remain immutable.

- New version-3 contracts expose TradeExposure, ShipmentRisk, InventoryForecast, HospitalRiskAssessment and ScenarioComparison, with deterministic analysis hashes, input values/evidence IDs, explanations and chart-ready daily points. No storage layer, model inference, orchestration, mitigation planner or report-generation service.
- National trade shares require a comparable world denominator. Full HHI and national country-risk classes require exhaustive value coverage; two-partner sample shares alone do not establish either. Prior-period changes require adjacent comparable periods, matching scope/classification and temporally available evidence. Incomplete classifications are null with explicit reasons, never a fabricated Low. Country, supplier/shipment and hospital dimensions remain separate.
- Forecasts consume only the explicitly simulated network inputs available by the scenario cutoff. Observed/derived evidence is eligible historically only when the revision's availability (or retrieval) and parent lineage are at/before cutoff; publication dates never substitute. FDA 2026 revisions, 2023 annual trade retrieved in 2026 and the later-retrieved storm statement are retrospective context only, excluded from July 2023 forecasts. No storm-to-carboplatin dependency is invented.
- Daily order: expire lots after their expiry date (usable through expiry day), receive eligible modeled shipments, then fulfill additional unreserved demand by FEFO. Reservations remain ring-fenced; quarantined/wrong-product/incompatible storage stock is unavailable. Daily demand is ceiling of the mean of seven complete pre-anchor simulated days. No cross-strength substitution or PO/ship double counting. Incoming lot expiry is an explicit 30-day modeled shelf-life assumption, not observed supplier data.
- Baseline counterfactual uses scheduled deliveries, ignoring modeled holds only; disrupted case honors holds. All arrivals remain modeled, never confirmed real deliveries. Overrides explicitly change stock or shipment scheduling and carry an immutable overlay hash. No historical delivery is recredited to starting inventory. Shortage days count unmet-demand days, not the entire span between first/last shortage; right-censoring is explicit.
- Central's original 12 stock / 8 reserved must be evaluated unchanged. A separate demonstration overlay changes initial Central stock to 38 vials (30 unreserved) to isolate onset differences; it is a hypothetical starting condition, not an inventory recommendation, replenishment action or alteration of Phase 2 evidence.
- Rules are versioned, transparent review heuristics: hospital Critical for unmet demand within first 2 days, High for later unmet demand, Moderate for minimum end-day buffer below 3 days, otherwise Low. Shipment Critical if overdue at least 7 days with no modeled arrival, High if held/blocked or delayed over 2 days, Moderate for lesser delay, otherwise Low; supplier level is the maximum of its shipment levels. Country levels, when complete comparable coverage exists, use import shares <10%, <25%, <50%, otherwise Critical. These rules have no clinical/predictive calibration claim.

## Phase 4B — simulated mitigation boundary (2026-10-24)

The user's explicit Phase 4B request authorizes adaptive investigation and hypothetical feasibility checks. Existing producers, schemas and datasets remain intact; new `contracts/mitigation.py` models and additive exports define the frontend boundary before implementation.

- Typed opportunities refer to immutable source lots, supplier stocks, transfer permissions and delivery windows. Every operational addition is simulated, versioned, cited and subject to recursive availability at both scenario start and requested information cutoff. Drug-name similarity never establishes product/presentation equivalence; this pilot authorizes no cross-strength matching.
- Apply the existing Phase 3 clean-onset overlay to an isolated, content-addressed scenario snapshot with new simulated lineage; retain the original dataset hash and canonical overlay/hash in the result. Never edit the underlying bundle or overlay file. New deliveries receive independent simulated PO/shipment records; rescheduling overrides one existing shipment rather than adding another receipt.
- Reuse Phase 3 RiskTools for every hospital projection. Transfers in this pilot dispatch at scenario start; remove only eligible donor lot quantities then receive through the existing shipment simulator. Future transfer dispatch is unsupported and rejected explicitly. Require at least the engine's modeled 30-day shelf life after arrival; no actual short-expiry lot is silently extended. Dates are daily and receipts precede demand: arrival on the first unmet day counts as before that day's demand. Arrivals after initial projected shortage are rejected in this prevention pilot; late rescue requires a later policy extension.
- Evaluate one alternative per immutable snapshot, never cumulatively pool alternative supply. Validate quantity, lot restrictions, exact presentation, permissions, route capacity/lead time/handling, hold-release authority and reservation protection before projection. Recalculate all three hospitals; reject any donor daily unmet-demand increase or worsening risk category. Diagnostic projections of a rejected plan are labelled and never recommended. Rejected/unsupported options move zero admissible vials; unknown counterfactual calculations remain null.
- The coordinator discovers and selects untried alternatives from typed tools, records rejections in the existing H-MEM, retrieves that updated state and selects again. Offline choices use an explicit deterministic model fixture driven by returned candidates/results, not prewritten conversations or execution logs. Optional Nemotron choices use the existing Nightshift decision adapter with a four-request/1,200-output-token total budget, no retries/fallback; the live command is prepared but will not be run without separate authorization.
- Rank only evaluated feasible alternatives by remaining recipient unmet demand, then donor impact/risk and supplied quantity. This is a bounded evidence-backed comparison, not exhaustive mitigation optimization. All outputs require human review; no transfer, release, purchase or physical action executes. Independent South shortages and the undocumented Rocky Mount/carboplatin relationship remain explicit.

## Phase 4C — live intelligence boundary (2026-10-24)

The user's Phase 4C request authorizes bounded public-source acquisition, additive Zetaris event publication, and a separately versioned October simulation. It does not authorize paid inference, frontend changes, deployment or alteration of July 2023 inputs.

- Add `DisruptionSignal` and versioned intelligence response models without changing previous schemas. Official MARAD notices establish what the authority published, not independently verified attacks or medicine delays. GDELT ArticleList metadata is unverified discovery evidence; `seendate` is an index timestamp, not proof of publication. Unknown publication/event dates stay null. No article bodies, inaccessible pages, invented summaries, automatic source instructions or clinical substitutions.
- Reuse Phase 2 immutable raw snapshots/receipts and SQL literal compiler, Phase 1 bounded Zetaris transport, Phase 3 risk tools, Phase 4A trace/H-MEM and Phase 4B feasibility/replanning. Keep new feature code/tests inside Phase 4. SQLite is a small local revision/freshness/job registry; Zetaris immutable `li_<hash>` views use the existing dedicated PharmaX container. No view replacement/deletion, service changes or municipal access.
- Store semantic revision IDs independently of fetch timestamps. Repeat content preserves its first evidence and records another retrieval receipt; semantic status/claim changes append versions. Availability is retrieval-based unless the specific revision has independent availability evidence. Supersession is explicit; expiry and historical latest selection are derived at the requested cutoff, never retroactively applied.
- Match simulated route corridor, coarse geographic bounding boxes and timed legs deterministically. MATCHED means verified official advisory exposure of a simulated route, never an observed delay; media or incomplete time/geography yields POSSIBLE and cannot auto-trigger forecasts. Unsupported supplier/product/asset links remain absent. Unrelated, expired, cancelled, superseded or future evidence cannot change scenarios.
- The October planning anchor is an explicitly hypothetical day-start on/before actual retrieval, with a separate content-addressed dataset and physically plausible modeled voyage legs. Phase 3 only accepts day-start simulated operational inputs. Consequently, observed evidence stays in decision lineage at its true retrieval time; configurable arrival overlays are simulated assumptions linked to the signal in the intelligence response, not backdated observed forecast inputs. The unchanged baseline is always exposed. No evidence establishes an actual carboplatin cargo using the corridor.
- Poll MARAD at six hours and GDELT at thirty minutes by default; bounded retries are deferred with persistent backoff/Retry-After, never tight loops. Polling only queues new relevant semantic revisions, never calls models. An explicit worker claims an idempotent signal/scenario/config job before expensive local risk/agent work. A stopped local process is not continuous monitoring. Frozen replay uses retained receipts without HTTP or Zetaris access and is labelled accordingly.
- All clinical, inventory, route, supplier and mitigation relationships remain simulated. Delays are configurable sensitivity assumptions, not inferred facts. Reuse the actual Phase 4B checker on an isolated delayed snapshot, with all hospital consequences and rejection memory. Read-only services expose signals, freshness, citations, matches, calculations, chart points, activity and mitigations. Live Nightshift verification remains outside this no-paid-inference implementation run.

## Phase 5A — operator integration boundary (2026-10-24)

The explicit Phase 5A task authorizes this phase despite the older Phase-1-only scope in AGENTS.md. Additive contracts live in `contracts/operator.py`; implementation/tests stay in phase_05_product. Existing Phase 2–4 contracts and calculations remain authoritative. The existing loopback HTTP server gains an optional operator service, retaining its legacy read paths. Offline replay never creates a network client. Retained source receipts are re-parsed and hashed before investigation. Zetaris publication is an explicit live operation; replay carries its historical receipt, not a new connection claim.

Investigations are immutable versioned snapshots; actions use a separate append-only revision history. Deterministic specialist functions are identified as local tools, never live LLM agents. Public explanations describe checks and outputs, never hidden reasoning. Synthetic clinical schedules v1 allocate modeled unopened vial requirements across pseudonymous sessions, independently of actual patient doses. No therapeutic substitution is inferred. Review transitions require recorded operator attestations; dispatch is unavailable without an actual connector. Route geometry consists of source-labelled scenario bounding areas, never invented vessel positions. Existing vial-only forecasting does not imply validated support for solution bags; the second pilot remains gated on explicit unit contracts and sourced inputs.

## Phase 5B — concurrent execution and observability (2026-10-24)

The explicit Phase 5B request authorizes this work beyond the older phase-only scope.
The existing 5a.1 boundary gains optional execution_graph and decision_ledger fields,
with empty defaults for historical snapshots. Existing agent summaries remain intact.
Graph nodes describe actual function invocations; ledger entries describe public
executed operations, never hidden reasoning. Parent IDs and dependency IDs are local
to an immutable case. Tool start/end records are correlated within their task/thread.

Only read-only context specialists run in the bounded pool. Each gets an isolated
copy of the same versioned bundle and a fixed pre-dispatch tool allowance. Results
reconcile in declared order, regardless of completion order. Failed/timed-out tasks
contribute no findings. Deadline closure fences late trace events. Python cannot
forcibly stop a running thread; workers have no memory or mitigation write access.
The source/dependency/verification gate, scenario forecasts, candidate selection,
feasibility/rejection-memory loop, clinical allocation and draft preparation remain
ordered. The existing service execution lock and unique case memory partition
serialize writes. Evidence keeps observed/derived/simulated classifications;
recommendations remain explicit review-only outputs.

## Phase 5C — evidence reconciliation and adaptive revision (2026-10-24)

The explicit Phase 5C request authorizes this extension beyond the older phase scope.
New typed operational claims, independent inspection outputs, conflict resolutions,
conditional forecast alternatives and case revisions are additive. Old snapshots keep
empty defaults; their retained representation remains the integrity boundary.

Supplier/cache arrival communications are simulated receipts, never observed shipment
facts. The new phase-local fixture and separate planning-authority registry do not edit
historical datasets. Receipt hashes establish integrity only; adoption additionally
requires the exact receipt hash in that registry, supported document fields, matching
shipment/product/supplier and coherent available/effective times. Retrieval recency
never outranks effective time. Equal-effective authorized disagreements stay unresolved.
MARAD is observed regional-advisory evidence, never arrival corroboration.

New cases reference immutable parents and retain all competing claims/uncertainties.
Conditional projections reuse the existing October engine and original inventory;
updated knowledge is decision-time lineage, not evidence backdated to scenario start.
The original day-start network is reprojected conditionally, not reconstructed as a
historical backtest or executed transfer. Unresolved revisions withhold action drafts.
Prior rejections are retrieved from real H-MEM in a separate decision-context partition,
used to order investigation, and rechecked rather than treated as timeless prohibitions.
The previous recommendation is evaluated first if shortage remains, followed by actual
feasibility checks and deterministic ranking. No LLM calls or physical actions occur.

## Nightshift hardening — 2026-10-24

- User authorization covers backend transport repair and a single live inference only after independent no-cost confirmation. `PHARMAX_MODEL_NO_COST=1`, catalog zero prices and trial credits do not establish authorization. No live inference is authorized by these settings alone. A backend-owned, expiring authorization bound to the exact endpoint/model and reviewed evidence is required; HTTP callers cannot supply one. Existing explicit paid authorization remains a separate legacy option, never used by Ask PharmaX.
- Native read retries are removed, including the historical two-attempt option. Every failed inference consumes its reservation. No fallback, redirect following, proxy environment inheritance or automatic endpoint discovery is introduced. An explicitly configured CGNAT Windows origin is permitted because the verified Windows Wi-Fi listener uses that address range; no credentials are sent to guessed hosts.
- ChatRequest adds an optional execution_mode (default offline). ChatResponse adds an execution_state distinguishing deterministic offline replay, live model tool execution, unavailable model and unauthorized inference, with optional safe error_code. Existing fields and offline behavior remain compatible. Live questions require a saved case and select only evidence.read or forecast.read; Python validates the selection and formats the actual result, without a second inference or model-written quantitative claim.
- Runtime diagnostics perform only bounded TCP and health/catalog GETs. Catalog prices are informational, never approval. Tool plumbing tests inject explicitly synthetic authorization into mocked transports; they are not live execution receipts. Only closed error codes and validated tool selections/results are retained; provider text, hidden reasoning and credentials are discarded.

## Model-directed specialists — 2026-10-24

The explicit live-specialist request authorizes integration into existing Phase 5 roles.
The new opt-in case mode `retained-source-live-model` distinguishes live inference over
hash-checked retained documents from network-free offline replay and fresh source fetch.
Existing replay defaults and fields stay unchanged. No caller-supplied policy assertion
or HTTP body can authorize inference. Existing backend-owned route/account/runtime
approval remains mandatory before constructing a client, discovery or dispatch.

Verification and dependency use distinct prompts and closed role-specific tools in the
existing execution graph. Each selected tool result is sent back to the model before
it can choose a follow-up or submit a citation-bound assessment. Mandatory checks and
quantities remain deterministic; model assessments cannot replace gate results. The
existing mitigation planner gets a native tool-result conversation across choices;
feasibility, ranking, memory, clinical allocation and drafts retain final authority.
Only source/dependency reads fan out; stateful mitigation/H-MEM remains serialized.
A run-wide request budget is reserved atomically before dispatch, with no fallback or
retry. Public trace records retain validated tool arguments/results and closed summaries,
never raw model prose or hidden reasoning. Runtime control/account blockers must remain
explicit; mocks never count as actual model execution.

## Full-stack integration — 2026-10-26

The explicit integration request authorizes importing dashboard commit 3ef5829 into
an isolated worktree based on backend 150bef9, and adding a read-only API consumer
inside that Phase 1 dashboard. No backend producer or shared schema is changed.
The existing 5a.1 operator API remains authoritative; /v1/intelligence has separate
storage and is never used to infer whether operator investigations exist.

A small typed frontend client validates the exact response fields it renders against
the operator boundary and actual saved-case responses: version, scope/IDs, aware
clocks, pagination, numeric quantities and vial units. Captured test fixtures are
explicit projections of those responses. Other UI data remains labelled demo/sample;
no live result is fabricated on empty, invalid or unavailable API responses.

Only the Investigations drawer connects to health, saved-case list/detail and forecast
reads. Evidence, mitigation, clinical and agent summaries come from the immutable
snapshot. Snapshot action drafts are labelled at-creation state; current action reviews,
report writes, operational actions and chat remain unconnected. The original globe,
risk/review animations, themes and sample interactions are preserved. Vite proxies
/api/v1 locally; production bundles use relative requests and require a same-origin
reverse proxy. There are no source refreshes, model requests or paid external calls.

## Phase 6A — additive replay boundary — 2026-10-26

The user's Phase 6 implementation brief explicitly authorizes this shared boundary.
Wire version `6a.1` is separate from DataBundle 2.0, RiskMeta 3.0 and operator 5a.1.
Legacy models remain vial-only and Illinois-only. New Replay* subclasses in data.py
permit bag records solely inside ScenarioOperations, whose fixed Helene profile
requires the three sim-nc-* IDs in sim-nc-health-system (NC/USA). No names or geography
establish supplier exposure. No synthetic dataset is created in 6A.

Quantity is a whole unopened container with an explicit unit. A new Quantity contract
in risk.py never feeds bag values into *_vials fields. ReplayProduct requires complete
strength/volume/mass and reviewed identifier aliases; NDC 4-4-2, 5-3-2 and 5-4-1
segments normalize to 5-4-2 by padding that segment only. Ambiguous unsegmented codes
are rejected. No Baxter alias is pre-approved or represented as verified source data.

Admission policy helene-admission-v1 uses America/New_York end-of-day for date-only
documents (next local midnight, exclusively). Issue/effective/collection/version clocks
are separate. Strict replay requires independent observed availability proof for every
exact source version and all parent evidence. Reconstructed historical simulation may
use document-time assumptions only with explicit acknowledgment and a persistent
limitation. It never backdates observation or upgrades projections to observed stock.
Missing/future/cyclic parents, unresolved extraction, duplicate IDs, contradictory rows,
cross-scenario identities and unavailable operational exports fail admission.

Consumer migration inventory (deferred until each planned phase):
- 6B: source admission/PDF extractor produces SourceRevision and AllocationNotice;
  no PDF dependency, acquisition, hash verification of actual bytes or extractor in 6A.
- 6C: CSV importer produces ScenarioOperations and OperationalImportReceipt; CSV/EDI/
  FHIR labels describe input contracts, not connectivity or implemented adapters.
  Phase 2 validation/store/pipeline remain legacy-only; use a reviewed adapter in 6C.
- 6D: phase_03_risk forecast/shipments/core/tools/terminal need an explicit quantity
  adapter and unit-neutral ratings before accepting replay operations. Existing
  ShipmentRisk, InventoryForecast and ScenarioComparison retain vial semantics.
- 6E: phase_04_agents tools/specialists/mitigation/feasibility/memory and phase_05_product
  integration/reconciliation/revision/service/actions/clinical/reports/chat require
  scenario-aware dispatch. HospitalMetrics.unmet_vials, ForecastDelta.*_vials,
  ClinicalSession and ActionPackage stay vial-only; no bag case enters those APIs.
- 6F: operatorApi/operatorWorkspace/OperatorExperience/OperatorViews/GlobeScene and
  frontend unit validation require an explicit API/client migration together. Untouched.
- Schema exporter and pytest discovery change now; old fixtures and consumers must pass.

Receipt fields define independent catalog, inventory, consumption, purchasing, PO,
acknowledgment and ASN feeds, each with hash, mapping version and export/effective
clocks. Simulated exports retain actual export/retrieval times; a separate explicit
modeled_available_at gates their fictional timeline in either replay mode. This is
never historical availability proof for observed documents. Catalog product evidence
can share its matching catalog receipt clock; unbound evidence uses its own clock.
Missing quantities never default to zero. Allocation is a claim, never stock.
Freshness windows, purchase-baseline rounding, source retention/licensing, exact
availability custody, operational adapters and forecast integration remain 6B–6D work.

## Phase 6 completion — 2026-10-26

The explicit continuation brief authorizes 6B–6G and necessary existing dashboard
changes in this assigned worktree. Preserve frontend/ and other worktrees.
Public Baxter-authored PDFs are retained unchanged for this requested local research
prototype with origin/hashes; no redistribution license is asserted. Reconstructed
receipt assumptions are mandatory; strict historical remains gated by independent
custody proof. Retrieval timestamps are never backdated.

Extract the existing daily FEFO loop into a public unit-neutral simulation function
in phase_03_risk/forecast.py; legacy forecast calls that same function unchanged.
Phase 6 supplies validated container inputs and gets neutral points; no bag quantity
enters vial-only legacy models. New typed Helene case/API models live in replay.py,
under a versioned /api/v1/helene namespace mounted by the existing operator service.
Use the existing CaseStore connection/immutable snapshot architecture with separate
typed tables and review-only actions; do not coerce Helene into an oncology case.

Replay anchors follow conservative document end-of-day cutoffs. All operational
exports are independently generated, simulated and retain actual creation clocks.
Inventory freshness is 24 hours; demand requires seven contiguous complete prior
days; allocation entitlement is floor(March–August net bags / 184 * 7 * percent/100).
That is a documented fictional weekly purchasing convention, not Baxter's private
account calculation. Confirmed modeled ASN quantities are bounded by both independent
acknowledgments and this entitlement, never created from allocation alone. No ASN
means no forecast receipt. Unknown acknowledgment blocks recommendations involving
that order; unresolved critical stock/identity prevents numerical recommendations.
Current source claims and conditional recovery projections remain distinct.

Transfer candidates reuse the shared FEFO calculation and donor constraints; ranking
is calculated from actual remaining unmet demand, quantity moved and stable IDs.
H-MEM uses the existing bounded Node bridge with scenario/checkpoint/dataset scope;
observed evidence is exposed with reconstructed limitations, never backdated to pass
legacy memory guards. Existing specialist role names and public tool receipts remain.
No Zetaris/live-model/FHIR-server integration is implied by a local adapter.
# Phase 8 operationalization boundary (2026-10-27)

The shared FEFO engine accepts optional explicit incoming lot/expiry/quality
attributes, retaining legacy modeled shelf-life defaults for prior callers. Phase 8
receipts use their declared lot and expiry and enter on their simulated arrival day;
they never retroactively increase anchor-day stock. Early donor debit is conservative.

The explicitly authorized Phase 8 brief supersedes the historical Phase 1-only
scope. Add `contracts/operations.py` version 8.1 for identity and human-reviewed
simulation requests. Preserve existing snapshots and Phase 6/7 contracts. Phase 8
owns its code/tests and uses public import/forecast/verification interfaces.
Original identifiers and file hashes remain evidence. NDC conversion is segmented;
11-digit identifiers resolve only against a declared authoritative 10-digit catalog.
No name-based clinical equivalence, inferred GS1, or future 12-digit conversion.
An optional strict admission extension excludes unusable lots without changing
legacy admission defaults. Package counts remain explicit synthetic assumptions.

Operational events/reservations/movements are appended under SQLite BEGIN IMMEDIATE;
every mutation has an expected revision. Proposal reserves stock; authorization
does not debit it. Dispatch and receipt are explicitly synthetic movements. A single
workspace per canonical facility/product/lot/snapshot identity shares allocations
across saved case revisions; reformatting cannot reset reservations. A different
input basis for the same snapshot is rejected pending explicit reconciliation;
its forecasts use the existing engine and exclude unconfirmed inbound supply.
Self-declared local roles are demonstration attestations, not authenticated pharmacy
credentials. Live deployment requires authenticated identities and partner policy.
No physical action, paid inference, external messaging, merge or deployment.
