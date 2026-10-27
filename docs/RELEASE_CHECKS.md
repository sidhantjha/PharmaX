# Release checks

Publication remains gated on the clean-clone and security checks below. This file
will be completed with actual results before any public repository is created.

- Original Project-Dot4 worktree and remote preserved.
- Fresh export contains PharmaX phase code, contracts, synthetic inputs and sanitized recorded evidence; no municipal source/history or private runtime stores.
- Third-party originals excluded from Git; explicit source bootstrap uses pinned hashes.
- Historical source checkpoint: `fca4aeb4c5e5b0ce9e770be056c7aab3a22f7c3a` plus latest uncommitted frontend/chat implementation.
- Source-history Gitleaks scan: 64 reachable commits / 59 refs after fetch; ten findings reviewed as source-file hashes, with zero credential findings in current release candidates.
- Clean-clone commands, frontend build/tests, sample results, API/chat/citations, Mermaid rendering and final export scans: **pending**.

The source worktree's historical receipts report 523 backend and 53 frontend tests
passing. They are not substituted for this release's clean-clone check.
