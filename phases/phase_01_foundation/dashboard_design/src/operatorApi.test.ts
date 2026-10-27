// @vitest-environment node
import { describe, expect, it, vi } from 'vitest';
import fixtures from '../tests/fixtures/operator-http.json';
import { createOperatorClient } from './operatorApi';

const id = fixtures.list.items[0].investigation_id;
const detail = Object.values(fixtures.cases)[0];
const forecast = Object.values(fixtures.forecasts)[0];
const wire = (body: unknown, status = 200) => vi.fn(async (_url: RequestInfo | URL, _options?: RequestInit) => new Response(JSON.stringify(body), { status }));

describe('operator contract client', () => {
  it('reads actual captured list, snapshot and forecast shapes through /api/v1', async () => {
    const fetcher = wire(fixtures.list); const api = createOperatorClient({ fetcher });
    expect((await api.investigations()).items[0].investigation_id).toBe(id);
    expect(fetcher.mock.calls[0][0]).toBe('/api/v1/investigations?limit=20&offset=0');
    expect((await createOperatorClient({ fetcher: wire(detail) }).investigation(id)).mode).toBe('offline-replay');
    expect((await createOperatorClient({ fetcher: wire(forecast) }).forecasts(id)).find(s => s.case === 'disruption' && s.forecast.request.hospital_id === 'sim-central')?.forecast.total_unmet).toBe(30);
  });
  it('retains an available empty list rather than consulting the legacy registry', async () => {
    const fetcher = wire({ ...fixtures.list, total: 0, items: [] });
    expect((await createOperatorClient({ fetcher }).investigations()).items).toEqual([]);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it.each([400, 404, 500, 503])('reports HTTP %s without fallback', async status => {
    const fetcher = wire({ error: 'DO-NOT-DISPLAY-PRIVATE-BODY' }, status);
    await expect(createOperatorClient({ fetcher }).investigation(id)).rejects.toMatchObject({ code: 'HTTP_ERROR', status });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it.each(['schema', 'items', 'timestamp', 'pagination', 'legacy'])('rejects invalid %s list envelopes', async kind => {
    const body: Record<string, unknown> = structuredClone(fixtures.list);
    if (kind === 'schema') body.schema_version = '4c.1';
    if (kind === 'items') body.items = {};
    if (kind === 'timestamp') body.as_of = '2026-10-26';
    if (kind === 'pagination') body.offset = 1;
    if (kind === 'legacy') body.resource = 'signals';
    await expect(createOperatorClient({ fetcher: wire(body) }).investigations()).rejects.toMatchObject({ code: 'INVALID_API_RESPONSE' });
  });
  it.each(['identity', 'units', 'evidence'])('rejects inconsistent %s detail', async kind => {
    const body = structuredClone(detail);
    if (kind === 'identity') body.items[0].investigation_id = 'wrong-case';
    if (kind === 'units') body.items[0].medicines[0].quantity_unit = 'mg';
    if (kind === 'evidence') body.items[0].evidence[0].kind = 'verified-shipment';
    await expect(createOperatorClient({ fetcher: wire(body) }).investigation(id)).rejects.toMatchObject({ code: 'INVALID_API_RESPONSE' });
  });
  it('rejects wrong-case forecasts and string quantities', async () => {
    await expect(createOperatorClient({ fetcher: wire({ ...forecast, investigation_id: 'other' }) }).forecasts(id)).rejects.toMatchObject({ code: 'INVALID_API_RESPONSE' });
    const body = structuredClone(forecast); (body.items[0].forecast.total_unmet as unknown) = '30';
    await expect(createOperatorClient({ fetcher: wire(body) }).forecasts(id)).rejects.toMatchObject({ code: 'INVALID_API_RESPONSE' });
  });
  it('reports an unavailable resource separately from an empty list', async () => {
    const body = { ...fixtures.list, availability: 'unavailable', reason: 'not ready', items: [], total: 0 };
    await expect(createOperatorClient({ fetcher: wire(body) }).investigations()).rejects.toMatchObject({ code: 'RESOURCE_UNAVAILABLE' });
  });
  it('does not call an empty health response a healthy connection', async () => {
    await expect(createOperatorClient({ fetcher: wire({ ...fixtures.health, total: 0, items: [] }) }).health()).rejects.toMatchObject({ code: 'INVALID_API_RESPONSE' });
  });
  it('bounds stalled requests and supports cancellation', async () => {
    const fetcher: typeof fetch = (_url, options) => new Promise((_resolve, reject) => {
      options?.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true });
    });
    await expect(createOperatorClient({ fetcher, timeoutMs: 10 }).health()).rejects.toMatchObject({ code: 'TIMEOUT' });
    const controller = new AbortController();
    const pending = createOperatorClient({ fetcher }).health(controller.signal); controller.abort();
    await expect(pending).rejects.toMatchObject({ code: 'CANCELLED' });
  });
});
