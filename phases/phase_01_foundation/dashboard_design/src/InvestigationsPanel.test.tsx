// @vitest-environment jsdom
import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import fixtures from '../tests/fixtures/operator-http.json';
import { InvestigationsPanel } from './InvestigationsPanel';
import { createOperatorClient, type Investigation } from './operatorApi';

const cases = fixtures.cases as Record<string, unknown>;
const forecasts = fixtures.forecasts as Record<string, unknown>;
const original = fixtures.list.items.find(c => c.parent_id === null)!;
const revised = fixtures.list.items.find(c => c.parent_id !== null)!;
afterEach(cleanup);
function client(list = fixtures.list) {
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input).replace('/api/v1', '');
    const identity = path.split('/')[2]?.split('?')[0];
    const body = path === '/health' ? fixtures.health : path.startsWith('/investigations?') ? list : path.includes('/forecast?') ? forecasts[identity] : cases[identity];
    return new Response(JSON.stringify(body), { status: body ? 200 : 404 });
  });
  return { api: createOperatorClient({ fetcher }), fetcher };
}
describe('connected saved investigation drawer', () => {
  it('loads the real list and selected carboplatin data without substituting demo values', async () => {
    const user = userEvent.setup(); const { api, fetcher } = client(); render(<InvestigationsPanel client={api}/>);
    expect(screen.getByRole('status').textContent).toMatch(/Loading saved/);
    await screen.findByText('Backend connected · Saved cases');
    await user.click(screen.getByRole('button', { name: new RegExp(original.investigation_id) }));
    await screen.findByRole('heading', { name: 'Saved investigation' });
    const table = screen.getByRole('table');
    const disruption = within(table).getAllByRole('row').find(r => r.textContent?.includes('Central') && r.textContent?.includes('disruption'));
    expect(disruption?.textContent).toContain('24');
    expect(table.textContent).toMatch(/Carboplatin/i);
    expect(screen.getByText(/offline-replay · deterministic-local/)).toBeTruthy();
    expect(fetcher.mock.calls.some(([url]) => String(url).includes('/v1/intelligence'))).toBe(false);
    expect(screen.getByText(/State at case creation/)).toBeTruthy();
  });
  it('opens a revised case and follows its actual immutable parent ID', async () => {
    const user = userEvent.setup(); const { api } = client(); render(<InvestigationsPanel client={api}/>);
    await screen.findByText('Backend connected · Saved cases');
    await user.click(screen.getByRole('button', { name: new RegExp(revised.investigation_id) }));
    await screen.findByRole('heading', { name: 'Saved investigation' });
    expect(screen.getByText(revised.investigation_id)).toBeTruthy();
    await user.click(screen.getByText('Case revision history'));
    await user.click(screen.getByRole('button', { name: 'Open parent investigation' }));
    await waitFor(() => expect(screen.getByText(original.investigation_id)).toBeTruthy());
    expect(screen.queryByText(revised.investigation_id)).toBeNull();
  });
  it('shows a healthy empty operator store clearly', async () => {
    const { api } = client({ ...fixtures.list, items: [], total: 0 }); render(<InvestigationsPanel client={api}/>);
    expect(screen.getByRole('status').textContent).toMatch(/Loading/);
    await screen.findByText(/No saved investigations yet/);
    expect(screen.queryByText('Saved investigation')).toBeNull();
  });
  it('shows an offline/error state with an explicit retry', async () => {
    const fetcher = vi.fn(async () => { throw new TypeError('offline'); });
    render(<InvestigationsPanel client={createOperatorClient({ fetcher })}/>);
    expect((await screen.findByRole('alert')).textContent).toContain('Operator API unavailable');
    expect(screen.getByRole('button', { name: 'Retry connection' })).toBeTruthy();
    expect(screen.queryByText(original.investigation_id)).toBeNull();
  });
  it('rejects a detail failure without showing a mock forecast', async () => {
    const user = userEvent.setup(); const { api } = client();
    api.investigation = vi.fn(async () => { throw new Error('unavailable'); });
    render(<InvestigationsPanel client={api}/>);
    await screen.findByText('Backend connected · Saved cases');
    await user.click(screen.getByRole('button', { name: new RegExp(original.investigation_id) }));
    expect((await screen.findByRole('alert')).textContent).toContain('Investigation details are unavailable');
    expect(screen.queryByRole('table')).toBeNull();
  });
  it('ignores an old selection when another case is selected before its response arrives', async () => {
    const user = userEvent.setup(); const { api } = client(); const read = api.investigation;
    let finish: () => void = () => {};
    api.investigation = vi.fn((id, signal) => id === original.investigation_id ? new Promise<Investigation>(resolve => {
      finish = () => { read(id, signal).then(resolve).catch(() => {}); };
    }) : read(id, signal));
    render(<InvestigationsPanel client={api}/>);
    await screen.findByText('Backend connected · Saved cases');
    await user.click(screen.getByRole('button', { name: new RegExp(original.investigation_id) }));
    await user.click(screen.getByRole('button', { name: 'Back to investigations' }));
    await user.click(screen.getByRole('button', { name: new RegExp(revised.investigation_id) }));
    await screen.findByRole('heading', { name: 'Saved investigation' }); finish();
    expect(screen.getByText(revised.investigation_id)).toBeTruthy();
    expect(screen.queryByText(original.investigation_id)).toBeNull();
  });
});
