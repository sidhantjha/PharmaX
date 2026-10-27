// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import operations from '../../../phase_08_operations/tests/fixtures/operations.json';
import verification from '../../../phase_07_reconciliation/tests/fixtures/verification.json';
import capture from '../tests/fixtures/helene-api.json';
import { validateHelene } from './heleneApi';
import { ProductTraceability } from './ProductTraceability';
const data=validateHelene(Object.values(capture.cases)[1]);
afterEach(()=>{cleanup();vi.unstubAllGlobals();});
function mock(ops:unknown=operations,v:unknown=verification){vi.stubGlobal('fetch',vi.fn(async(url:unknown)=>new Response(JSON.stringify(String(url).endsWith('/operations')?ops:v))));}
it('renders stored SKU mappings, normalization and the matched discrepancy with inspectable sources',async()=>{
  mock();const user=userEvent.setup();render(<ProductTraceability data={data} status={null}/>);
  await screen.findByText('3 matched with assumptions · 0 unresolved');
  for(const sku of ['IV-NS1L','SAL-1000','NACL009-L'])expect(screen.getByRole('button',{name:sku+' ↗'})).toBeTruthy();
  expect(screen.getAllByText('Matched with assumptions · 1 case = 12 bags')).toHaveLength(3);
  expect(screen.getByText(/Normalized NDC 00338004904/)).toBeTruthy();
  expect(screen.getByText('300 bags unexplained')).toBeTruthy();
  const stages=screen.getByLabelText('Distinct supply stages');
  expect(within(stages).getAllByRole('button',{name:'800 bag ↗'})).toHaveLength(2);
  expect(within(stages).getByRole('button',{name:'500 bag ↗'})).toBeTruthy();
  await user.click(within(stages).getByRole('button',{name:'500 bag ↗'}));
  expect(screen.getByRole('dialog')).toBeTruthy();
  expect(within(screen.getByRole('dialog')).getAllByText(/Explicit synthetic hospital accepted receiving record/)[0]).toBeTruthy();
});
it('keeps ambiguous identifiers unresolved and never invents the 800/500 conflict',async()=>{
  const ops=structuredClone(operations);Object.assign(ops.readiness.identities[0],{review_required:true,canonical_ndc:null,manufacturer_code:null,classification:'unresolved',failures:['NDC_CONFLICT']});
  const v=structuredClone(verification);v.findings=v.findings.filter(f=>f.category!=='conflict');
  mock(ops,v);render(<ProductTraceability data={data} status={null}/>);
  await screen.findByText('2 matched with assumptions · 1 unresolved');
  expect(screen.getByText('Unresolved · NDC CONFLICT')).toBeTruthy();
  expect(screen.queryByText('300 bags unexplained')).toBeNull();
});
it('fails closed when source endpoints fail',async()=>{
  vi.stubGlobal('fetch',vi.fn(async()=>new Response('{}',{status:503})));
  render(<ProductTraceability data={data} status={null}/>);
  await screen.findByText('operations unavailable. No substitute evidence shown.');
  expect(screen.queryByRole('button',{name:'IV-NS1L ↗'})).toBeNull();
  expect(screen.queryByText('300 bags unexplained')).toBeNull();
});
