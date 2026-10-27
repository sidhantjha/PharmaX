// Adapted from archived scripts/hmem_bridge.mjs; upstream engine remains unmodified.
import fs from 'node:fs';
import { HMEM } from './vendor/hmem/src/index.ts';
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const hmem = new HMEM({ actor: 'pharmax', clock: () => input.clock, persistDir: input.persist_dir });
const writes = [];
const conflicts = [];
for (const finding of input.findings) {
  const content = JSON.stringify(finding);
  const existing = hmem.store.all().find(m => m.content === content);
  if (existing) { writes.push(existing.id); continue; }
  const before = hmem.store.all().map(m => [m, JSON.parse(m.content)]);
  const record = hmem.add({ content, type: 'factual', layer: 'working', confidence: 0.7,
    source: finding.source_ids.join(';'), tags: ['project:pharmax', finding.kind],
    chatId: input.scope, missionId: input.scope, goalRunId: input.scope,
    provenance: { origin: 'document', learnedAt: input.clock,
      label: 'Tool-validated investigation finding; heuristic confidence, not probability',
      refs: finding.evidence_ids }
  });
  // Compare typed claim identities, rather than applying numeric prose heuristics
  // to unrelated facilities/scenarios. Both records always remain intact.
  for (const [old, prior] of before) {
    if (prior.claim_key === finding.claim_key && prior.value !== finding.value) {
      hmem.ledger.log(record.id, 'conflict_detected', 'pharmax', {
        details: { with: old.id, reason: 'same claim key, divergent value; preserve both',
          claim_key: finding.claim_key }
      });
    }
  }
  writes.push(record.id);
}
for (const entry of hmem.ledger.all()) {
  if (entry.action === 'conflict_detected') conflicts.push(entry);
}
// Hard scope check before ranking, including the empty-store case.
if (hmem.store.all().some(m => m.chatId !== input.scope)) throw Error('MEMORY_SCOPE_MISMATCH');
const result = hmem.query(input.query, { topN: 5, threshold: 0, chatId: input.scope });
hmem.store.persist();
process.stdout.write(JSON.stringify({ engine: 'meterless-hmem-reference', writes,
  conflicts, result, ledger_stats: hmem.ledger.stats() }));
