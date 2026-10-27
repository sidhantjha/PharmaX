# Phase 01 — Foundation

Implemented: explicit configuration, safe bounded HTTP, existing Zetaris proxy adapter, Nightshift provider boundary, two public source reads, typed provenance, approved tool dispatch, metadata tracing and a diagnostic terminal. Feature tests live in `tests/` here.

`config.py` reads only process variables and an explicitly supplied private file. It does not search parent directories, execute dotenv content or copy credentials. Process values take precedence. Never run `docker compose config` into a report because it can expand credentials.

`zetaris.py` adapts the existing connector from `[redacted historical private path] and `query_zetaris.py`: same login, organization header and proxy route, with bounds and sanitized errors. Use the existing services. Query identifiers select approved SQL; arbitrary SQL is rejected before transport. No old dataset is read or changed by `connection_check`.

`nightshift.py` adapts the existing model adapter's bounded authenticated catalog and schema-validation pattern to the actual [Nightshift provider contract](https://www.meterless.ai/engines.html). It normalizes the base URL, sends Bearer auth plus `meterless-app` and `meterless-scope`, rejects placeholder tokens and has no NVIDIA route. Generic Meterless catalog modes do not establish Nemotron availability. Phase 1.5 adds native function calls selecting approved FDA/Zetaris reads, response model identity validation, explicit single-call cost authorization, and private LAN runtime support. These paths pass transport-mocked tests. After earlier HTTP 502/403 failures and a user-managed key refresh, authenticated discovery and one live native tool call passed; validated Nightshift routing metadata identified the NVIDIA Nemotron model. Historical failures remain preserved in the reports. The archived H-MEM bridge is distinct from Nightshift and requires a newer Node runtime; Engine support is pending.

`sources.py` reads official [FDA shortage records](https://open.fda.gov/apis/drug/drugshortages/how-to-use-the-endpoint/) and [UN Comtrade preview data](https://uncomtrade.org/docs/un-comtrade-api/). One request per source, 20-second HTTP timeout (4-second connect), one-megabyte decoded response cap, 1..5 rows, no retries. Comtrade public access and rate limits are enforced upstream: 401/403/429 are BLOCKED. Missing or malformed JSON is not a successful fixture. FDA status `Current` can coexist with package availability `Available`; normalization retains both without interpreting a local shortage.

`runtime.py` reuses the legacy reserve-before-call and filtered JSONL trace design, reduced to the three foundation reads. It emits start/completion events and keeps source evidence IDs. The terminal renderer replaces municipal specialist conversations with actual diagnostic results. Later agent reasoning is deliberately not ported into Phase 1.

Explicit offline mode loads `fixtures/sources.json`. Every fixture record is simulated, every tool event says fixture, and no HTTP client is constructed. The fictional fixture medicine is not a real FDA shortage claim.

Run from the repository root:

```sh
uv sync --locked --all-groups
uv run pytest -q
uv run python scripts/verify.py --offline
uv run python scripts/verify.py --env-file [redacted historical private path]
```

The live command is expected to exit 2 while Nightshift and Engine prerequisites remain blocked. Read `docs/SERVICE_CONNECTION_REPORT.md` for actual observed results and reproduction commands.

Phase 1.5 reproduction (one inference attempt; run only when authorized):

```sh
uv run python scripts/verify.py --env-file .env --model-smoke --allow-paid-model
```

This flag authorizes a possibly paid request; it does not assert a free route. The terminal emits validated tool activity, model identity and usage when available, plus retrieved FDA fields and citations. Python formats the evidence-backed answer; it does not make another inference. Source failure never becomes a fixture. The selected project `.env` currently lacks Zetaris organization configuration; the separate Phase 1 connection remains historical evidence. See `docs/PHASE_1_5_REPORT.md` for the actual attempt and blockers.


Phase 1.5 blocker-resolution update: `--zetaris-env-file [redacted historical private path] securely loads only missing Zetaris settings without overriding Meterless. Live approved `SELECT 1` is reverified. `--model-attempts 2` opts into one visible 1-second retry for HTTP 502/503, within a two-attempt ceiling; known permanent upstream NVIDIA 400/401/403/404 is never retried. The two authorized live resolution attempts exposed `nvidia_403` wrapped as 502; no live model-directed call succeeded. The native read disables tier split and thinking to bound runtime work. See the updated resolution report for the actual failure and next manual action.


Latest Phase 1.5 status: after the user-managed NVIDIA key refresh, one authorized live request through the unchanged adapter passed response/winning-route verification for `nvidia/nemotron-3.5-lightning-30b-a3b` (provider `nvidia`), returned a native FDA read request, and retrieved cited observed evidence. Zetaris `SELECT 1` also passed. Reported usage was 500 tokens. No retry or second inference was made; the separate Engine bridge remains pending. See the successful verification section in `docs/PHASE_1_5_REPORT.md`; prior failures remain historical evidence.
