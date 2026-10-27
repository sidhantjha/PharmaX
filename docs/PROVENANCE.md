# Data, recorded execution and pre-existing work

Manufacturer notices are historical public documents. Hospital, supplier, local SKU, lot and transport records are synthetic. Historical byte availability and receipt timing are reconstructed. Proposed transfers did not occur; pharmacy and logistics review remain required. Clinical outcomes have not been established.

The default demo has two deliberately separate stores:

1. `.runtime/example-store`: three newly calculated sample inputs, with central unmet demand of 172, 72 and 0 bags as inventory changes by 0, 100 and 300 synthetic bags.
2. `.runtime/demo`: restored historical investigation and genuine saved model execution. Replay of its 27 requests dispatches zero new model calls. Its canonical network projection is 342 unmet bags, falling to 6 after evaluated transfers of 172 and 164 bags.

The conservative operational forecast excludes unconfirmed inbound supply and can differ from canonical replay. Accepted receipt evidence is distinct from a ship notice. Case B's 800 shipped / 500 accepted discrepancy does not establish the missing units' location or disposition.

## Sanitized receipt projections

The saved execution includes 12 executed roles, 11 validated findings, 27 actual Nightshift/NVIDIA Nemotron requests, 20 Zetaris product-linked rows, 11 H-MEM writes and five recalled records. Hospital remains rejected. Original response/receipt identifiers, timestamps, usage, validation decisions, model names, dependency relationships and factual findings are retained.

Machine-specific path prefixes and private host references were removed from published JSON projections. [The projection manifest](evidence/projection-manifest.json) records both original and published SHA-256 values. Recorded `tool_result_sha256` values describe original tool-result bytes; they must not be mistaken for hashes of sanitized JSON serializations. No new live execution, hidden reasoning, independent audit certification or direct Zetaris access by every agent is claimed.

Source records are evidence, never executable instructions. Offline fixture execution has no network fallback. The separate, explicit download bootstrap verifies pinned document hashes and stops on changed bytes. Live integration requires separately configured credentials and bounded backend authorization; the default HTTP application does not grant inference authorization.

## Pre-existing work and attribution

This is a fresh, PharmaX-only export from the completed frontend worktree built on backend checkpoint `fca4aeb4c5e5b0ce9e770be056c7aab3a22f7c3a`. Original Project-Dot4 history and municipal/CityPulse files are not included or modified.

The inspected source history contains October 21, 2026 CityPulse specification/planning commits preceding the October 22 cutoff. Those documents are excluded here. PharmaX reuses software development experience and adapted components from that project lineage; this fresh history is not a claim that every design idea was first created during the hackathon. The vendored H-MEM engine, adapted open-source UI primitives and dependencies pre-exist the submission and retain their notices.

Sidhant is the submitting maintainer. The source project's full contributor roster and exact individual responsibilities require maintainer confirmation before final HackOS attribution. Cursor usage is user-reported; no Cursor Agent/Space XAI execution transcript is claimed. Sponsor execution receipts establish only the recorded activity described above.

## Clean-export reproduction fix

The live Virginia guidance page no longer matches its retained hash. The release admission reader now skips records outside the selected cutoff before opening their bytes. Required sources still fail on missing/changed originals. The default October 1 demo downloads only the four manufacturer PDFs; later agency guidance is not admitted or silently substituted. Two focused regression tests cover this boundary.
