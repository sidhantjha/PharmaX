/** Actual HTTP acceptance using the exact frontend TypeScript client through Vite. */
import assert from 'node:assert/strict';
import { writeFile } from 'node:fs/promises';
import { createOperatorClient } from '../src/operatorApi.ts';
const origin = process.argv[2] || 'http://127.0.0.1:5174';
const api = createOperatorClient({ baseUrl: origin + '/api/v1' });
const health = await api.health();
const list = await api.investigations();
assert.equal(health.items[0].status, 'ok');
const original = list.items.find(c => c.scenario === 'red-sea' && c.parent_id === null);
const revised = list.items.find(c => c.parent_id === original?.investigation_id);
assert.ok(original && revised, 'Seed the documented offline conflict demo into this server store first');
const result = { checked_at: new Date().toISOString(), frontend_origin: origin,
  request_path: '/api/v1 via same-origin Vite proxy; frontend operatorApi.ts used unchanged',
  health: health.items[0], cases: [], live_inference_requests: 0 };
for (const [summary, expected] of [[original, 24], [revised, 30]]) {
  const detail = await api.investigation(summary.investigation_id);
  const forecasts = await api.forecasts(summary.investigation_id);
  const central = forecasts.filter(s => s.forecast.request.hospital_id === 'sim-central');
  const quantities = Object.fromEntries(central.map(s => [s.case, s.forecast.total_unmet]));
  assert.deepEqual(quantities, { baseline: 0, disruption: expected, mitigation: 0 });
  assert.equal(detail.investigation_id, summary.investigation_id);
  assert.ok(detail.medicines.some(m => /carboplatin/i.test(m.name) && m.quantity_unit === 'vial'));
  assert.ok(detail.evidence.some(e => e.kind === 'observed' && e.source === 'marad' && new URL(e.source_url).hostname === 'www.maritime.dot.gov'));
  assert.ok(detail.evidence.some(e => e.kind === 'simulated'));
  assert.ok(detail.agents.some(a => a.memory_ids.length > 0));
  assert.equal(detail.model_execution, 'deterministic-local');
  assert.ok(detail.actions.every(a => a.status === 'DRAFT' && !a.dispatch_available));
  result.cases.push({ investigation_id: detail.investigation_id, parent_id: detail.parent_id,
    mode: detail.mode, model_execution: detail.model_execution,
    unmet_vials: quantities, medicines: detail.medicines.map(m => m.name),
    observed_evidence: detail.evidence.filter(e => e.kind === 'observed').length,
    simulated_evidence: detail.evidence.filter(e => e.kind === 'simulated').length,
    agent_records: detail.agents.length, revision: detail.revision?.disposition || null });
}
if (process.argv[3]) await writeFile(process.argv[3], JSON.stringify(result, null, 2) + '\n');
console.log('PASS: actual frontend client -> Vite proxy -> operator API; original 0/24/0, revision 0/30/0; provenance, IDs and units consistent.');
