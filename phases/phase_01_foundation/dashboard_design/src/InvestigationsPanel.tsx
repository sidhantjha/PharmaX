import React, { useEffect, useState } from 'react';
import { createOperatorClient, type CaseSummary, type ForecastSeries, type Investigation, type OperatorClient } from './operatorApi';
import './operator.css';

const api = createOperatorClient();
function date(value: string) { return new Date(value).toLocaleString(); }
function sourceLink(value: string) { try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) ? url.href : undefined; } catch { return undefined; } }
export function InvestigationsPanel({ client = api }: { client?: OperatorClient }) {
  const [version, refresh] = useState(0);
  const [offset, setOffset] = useState(0);
  const [cases, setCases] = useState<CaseSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [selected, select] = useState<string | null>(null);
  const [detail, setDetail] = useState<{ snapshot: Investigation; forecasts: ForecastSeries[] } | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState(false);
  useEffect(() => {
    const request = new AbortController(); setLoading(true); setError(false);
    Promise.all([client.health(request.signal), client.investigations(offset, request.signal)])
      .then(([, result]) => { if (!request.signal.aborted) { setCases(result.items); setTotal(result.total); } })
      .catch(() => { if (!request.signal.aborted) setError(true); })
      .finally(() => { if (!request.signal.aborted) setLoading(false); });
    return () => request.abort();
  }, [client, offset, version]);
  useEffect(() => {
    setDetail(null); setDetailError(false);
    if (!selected) return;
    const request = new AbortController(); setDetailLoading(true);
    Promise.all([client.investigation(selected, request.signal), client.forecasts(selected, request.signal)])
      .then(([snapshot, forecasts]) => {
        // The forecast units must resolve to the exact product in this saved case.
        if (forecasts.some(f => !snapshot.medicines.some(m => m.product_id === f.forecast.request.product_id && m.quantity_unit === 'vial'))) throw new Error('Unknown forecast units');
        if (!request.signal.aborted) setDetail({ snapshot, forecasts });
      })
      .catch(() => { if (!request.signal.aborted) setDetailError(true); })
      .finally(() => { if (!request.signal.aborted) setDetailLoading(false); });
    return () => request.abort();
  }, [client, selected, version]);
  if (loading) return <p className="detail-copy" role="status">Loading saved investigations…</p>;
  if (error) return <div className="operator-state" role="alert"><h3>Operator API unavailable</h3><p>The backend may be offline or its response could not be validated. Demo views remain available; no sample case has been substituted.</p><button className="secondary-button" onClick={() => refresh(v => v + 1)}>Retry connection</button></div>;
  const snapshot = detail?.snapshot.investigation_id === selected ? detail.snapshot : undefined;
  return <section className="operator-cases" aria-label="Saved operator investigations">
    <p className="operator-connection">Backend connected · Saved cases</p>
    <p className="detail-copy">Observed advisory evidence is separate from simulated hospital and shipment assumptions. The map and other dashboard screens remain demo views.</p>
    {!selected ? <>
      <h3>Investigations <span>({total})</span></h3>
      {total === 0 && <p className="detail-copy" role="status">No saved investigations yet. Create a case with your operator, then refresh this view.</p>}
      {cases.map(c => <button className="register-row operator-case" key={c.investigation_id} onClick={() => select(c.investigation_id)}>
        <span>{c.scenario === 'red-sea' ? 'Red Sea investigation' : c.scenario}<small>{date(c.created_at)} · {c.parent_id ? 'Revision' : 'Original case'}</small><small>{c.investigation_id}</small></span><span>{c.status}</span>
      </button>)}
      <div className="operator-pagination"><button className="secondary-button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 20))}>Previous cases</button><button className="secondary-button" disabled={offset + cases.length >= total} onClick={() => setOffset(offset + 20)}>Next cases</button></div>
      <button className="secondary-button" onClick={() => refresh(v => v + 1)}>Refresh investigations</button>
    </> : <>
      <button className="secondary-button" onClick={() => select(null)}>Back to investigations</button>
      {detailLoading && <p className="detail-copy" role="status">Loading investigation details…</p>}
      {detailError && <div role="alert"><p className="detail-copy">Investigation details are unavailable. No demo result has been substituted.</p><button className="secondary-button" onClick={() => refresh(v => v + 1)}>Retry details</button></div>}
      {snapshot && detail && <article>
        <h3>Saved investigation</h3><p className="operator-id">{snapshot.investigation_id}</p>
        <dl className="detail-list"><div><dt>Status</dt><dd>{snapshot.status}</dd></div><div><dt>Saved</dt><dd>{date(snapshot.created_at)}</dd></div><div><dt>Execution</dt><dd>{snapshot.mode} · {snapshot.model_execution}</dd></div></dl>
        <p className="detail-copy">{snapshot.objective}</p>
        <h4>Hospital forecasts · vials</h4>
        <p className="detail-copy">Calculated from simulated operational inputs. Baseline, disruption and selected mitigation are separate scenarios.</p>
        <div className="operator-table-wrap"><table><caption>Modeled unmet demand</caption><thead><tr><th scope="col">Hospital / case</th><th scope="col">Unmet vials</th></tr></thead><tbody>
          {detail.forecasts.map(f => <tr key={f.case + f.forecast.request.hospital_id}><th scope="row">{snapshot.hospitals.find(h => h.hospital_id === f.forecast.request.hospital_id)?.name || f.forecast.request.hospital_id}<small>{f.case} · {snapshot.medicines.find(m => m.product_id === f.forecast.request.product_id)?.name}</small></th><td>{f.forecast.total_unmet}</td></tr>)}
        </tbody></table></div>
        <details><summary>Evidence and provenance ({snapshot.evidence.length})</summary><p>MARAD supports regional advisory risk; it does not prove a pharmaceutical shipment delay.</p>{snapshot.evidence.map(e => <div className="operator-record" key={e.evidence_id}><strong>{e.kind} · {e.source}</strong><p>{sourceLink(e.source_url) ? <a href={sourceLink(e.source_url)} target="_blank" rel="noreferrer">{e.source_url}</a> : e.source_url}</p><small>Retrieved {date(e.observed_at)}</small><code>{e.evidence_id}</code><code>SHA-256 {e.content_sha256}</code></div>)}</details>
        <details><summary>Mitigation candidates and feasibility</summary><p>{snapshot.mitigation.recommendation || 'No recommendation available.'}</p>{snapshot.mitigation.evaluations.map(e => <div className="operator-record" key={e.evaluation_id}><strong>{e.candidate.candidate_id} · {e.feasible ? 'Feasible for review' : 'Rejected'}</strong><p>{e.remaining_unmet} remaining unmet vials</p>{e.failures.map((f, i) => <p key={f.code + i}>{f.code}: {f.detail}</p>)}</div>)}</details>
        <details><summary>Agent records ({snapshot.agents.length})</summary><p>These saved execution records do not establish a live LLM run.</p>{snapshot.agents.map(a => <div className="operator-record" key={a.agent_id}><strong>{a.agent_id} · {a.status}</strong><p>{a.execution}: {a.explanation}</p><small>{a.evidence_ids.length} evidence references · {a.memory_ids.length} memory IDs</small></div>)}</details>
        <details><summary>Case revision history</summary><p>{snapshot.revision?.decision_summary || 'Original immutable investigation.'}</p>{snapshot.parent_id && <button className="secondary-button" onClick={() => select(snapshot.parent_id)}>Open parent investigation</button>}</details>
        <details><summary>Snapshot action drafts ({snapshot.actions.length})</summary><p>State at case creation. Reviews and dispatch are not connected in this screen; no action can be sent here.</p>{snapshot.actions.map(a => <p key={a.action_id}>{a.kind} · {a.quantity} {a.quantity_unit} · {a.status}</p>)}</details>
        <details><summary>Synthetic clinical exposure</summary>{snapshot.clinical_impact.map(c => <p key={c.case + c.hospital_id}>{c.case} / {c.hospital_id}: {c.unique_exposed_patients} unique modeled patients</p>)}<p>Simulated scheduling exposure, not observed patient outcomes or clinical advice.</p></details>
        <details><summary>Limitations</summary>{snapshot.limitations.map((s, i) => <p key={i}>{s}</p>)}</details>
      </article>}
    </>}
  </section>;
}
