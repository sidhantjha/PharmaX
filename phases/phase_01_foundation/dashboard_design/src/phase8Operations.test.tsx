// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import capture from '../../../phase_08_operations/tests/fixtures/operations.json';
import { OperationalReview } from './OperationalReview';

afterEach(()=>{cleanup();vi.unstubAllGlobals();});

it('shows actual API identity, readiness, partial receipt and forecast records',async()=>{
  vi.stubGlobal('fetch',vi.fn(async()=>new Response(JSON.stringify(capture))));
  render(<OperationalReview caseId="saved-case"/>);
  expect(await screen.findByText('sim-nc-central: 136 unmet bags')).toBeTruthy();
  await userEvent.setup().click(screen.getByRole('button',{name:'Review action'}));
  expect(screen.getByText('transfer · CLOSED')).toBeTruthy();
  expect(screen.getByText(/120 received \/ 200 simulated dispatched/)).toBeTruthy();
  expect(screen.getByText(/Product identity · 3 source mappings/)).toBeTruthy();
});

it('requires an explicit reviewer before reserving a donor recommendation',async()=>{
  const fetcher=vi.fn(async()=>new Response(JSON.stringify(capture)));
  vi.stubGlobal('fetch',fetcher);const user=userEvent.setup();
  render(<OperationalReview caseId="saved-case"/>);
  await screen.findByText(/Conservative operational forecast/);
  await user.click(screen.getByRole('button',{name:'Review action'}));
  const button=screen.getAllByRole('button',{name:'Reserve and propose'})[0] as HTMLButtonElement;
  expect(button.disabled).toBe(true);
  await user.type(screen.getByLabelText('Operational reviewer'),'human-operator');
  expect(button.disabled).toBe(false);
  await user.click(button);
  await vi.waitFor(()=>expect(fetcher.mock.calls.length).toBeGreaterThan(1));
});

it('reports unavailable operational data without substituting stock',async()=>{
  vi.stubGlobal('fetch',vi.fn(async()=>new Response('{}',{status:503})));
  render(<OperationalReview caseId="missing"/>);
  expect(await screen.findByRole('alert')).toHaveProperty('textContent',expect.stringContaining('unavailable'));
  expect(screen.queryByText(/136 unmet bags/)).toBeNull();
});
