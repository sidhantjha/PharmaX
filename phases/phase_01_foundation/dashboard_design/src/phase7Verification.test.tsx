// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import capture from '../../../phase_07_reconciliation/tests/fixtures/verification.json';
import { EvidenceVerification } from './EvidenceVerification';

afterEach(()=>{cleanup();vi.unstubAllGlobals();});

it('inspects genuine backend discrepancy output and separates conditional supply',async()=>{
  vi.stubGlobal('fetch',vi.fn(async()=>new Response(JSON.stringify(capture))));
  const user=userEvent.setup();render(<EvidenceVerification caseId="saved-case" saved={()=>{}}/>);
  const conflict=await screen.findByText(/conflict · Matched shipment/);
  await user.click(conflict);
  expect(screen.getByText('300 bags require reconciliation')).toBeTruthy();
  expect(screen.getByText(/carrier proof of delivery, package counts/)).toBeTruthy();
  expect(screen.getAllByText(/Fictional .* · simulated/).length).toBeGreaterThan(0);
  expect(screen.getByText(/500 bags supported by accepted receiving records/)).toBeTruthy();
  const conditional=screen.getByText('Conditional supply · 300 bags');
  await user.click(conditional);
  const details=conditional.closest('details')!;
  expect(within(details).getByText(/sensitivity only, no mitigation authorization/)).toBeTruthy();
  expect(within(details).getByText('sim-nc-central: 0 unmet bags')).toBeTruthy();
});

it('launches a real API scenario request and navigates to the returned saved revision',async()=>{
  const saved=vi.fn(),fetcher=vi.fn(async(_url:unknown,options?:RequestInit)=>new Response(JSON.stringify(
    options?.method==='POST'?{investigation_id:'helene-saved-child',verification:capture}:capture)));
  vi.stubGlobal('fetch',fetcher);const user=userEvent.setup();
  render(<EvidenceVerification caseId="immutable-parent" saved={saved}/>);
  await screen.findByText(/500 bags supported/);
  await user.click(screen.getByRole('button',{name:'Case B · Shipped vs received'}));
  expect(saved).toHaveBeenCalledWith('helene-saved-child');
  const request=fetcher.mock.calls.find(([,o])=>o?.method==='POST');
  expect(JSON.parse(String(request?.[1]?.body))).toEqual({scenario:'B',parent_id:'immutable-parent'});
});

it('shows unavailable evidence without substituting a sample result',async()=>{
  vi.stubGlobal('fetch',vi.fn(async()=>new Response('{}',{status:503})));
  render(<EvidenceVerification caseId="unavailable" saved={()=>{}}/>);
  expect(await screen.findByRole('alert')).toHaveProperty('textContent',expect.stringContaining('unavailable'));
  expect(screen.queryByText(/500 bags supported/)).toBeNull();
});
