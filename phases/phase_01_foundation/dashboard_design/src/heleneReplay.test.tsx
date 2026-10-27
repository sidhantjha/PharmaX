// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import capture from '../tests/fixtures/helene-api.json';
import { HeleneExperience, ScenarioExperience } from './HeleneExperience';
import { validateHelene } from './heleneApi';

vi.mock('./GlobeScene',async()=>{const React=await import('react');return {GlobeScene:React.forwardRef(function Globe({signals,onSelect}:{signals:{id:string;title:string;hospital?:boolean}[];onSelect:(s:unknown)=>void},ref){React.useImperativeHandle(ref,()=>({focus(){},focusPoint(){},reset(){},zoomIn(){},zoomOut(){}}));return <div data-testid="helene-globe">{signals.map(s=><button key={s.id} onClick={()=>onSelect(s)}>{s.hospital?`Inspect simulated hospital: ${s.title}`:s.title}</button>)}</div>;})};});
afterEach(()=>{cleanup();localStorage.clear();sessionStorage.clear();history.replaceState(null,'','/');vi.unstubAllGlobals();});
const cases=Object.values(capture.cases).sort((a,b)=>a.checkpoint.cutoff.localeCompare(b.checkpoint.cutoff));
function mockApi(){
  const fetcher=vi.fn(async(url:RequestInfo|URL,options?:RequestInit)=>{
    const path=new URL(String(url),'http://localhost').pathname;
    if(path==='/api/v1/helene/cases')return new Response(JSON.stringify(capture.listing));
    if(options?.method==='POST'){
      const a=cases.flatMap(c=>c.actions).find(a=>path.includes(a.action_id));
      if(a){const body=JSON.parse(String(options.body));return new Response(JSON.stringify({...a,status:body.status,revision:body.expected_revision+1}));}
    }
    const c=cases.find(c=>path==='/api/v1/helene/cases/'+c.investigation_id);
    return new Response(JSON.stringify(c??{}),{status:c?200:404});
  });vi.stubGlobal('fetch',fetcher);return fetcher;
}
it('defaults to October 1 and navigates saved checkpoints without remounting the globe',async()=>{
  const fetcher=mockApi(),user=userEvent.setup();Element.prototype.scrollIntoView=vi.fn();window.scrollTo=vi.fn();render(<HeleneExperience/>);
  const current=cases.find(c=>c.investigation_id.includes('2024-10-01'))!;
  const total=current.forecasts.reduce((s,f)=>s+f.total_unmet,0);
  await screen.findByRole('button',{name:`Projected unmet \u00b7 bags ${total}`});
  const globe=screen.getByTestId('helene-globe');
  await user.click(screen.getByRole('button',{name:'Investigation'}));
  expect(screen.getByRole('heading',{name:'Situation'})).toBeTruthy();
  expect(screen.getAllByRole('tab')).toHaveLength(3);
  await user.click(screen.getByRole('tab',{name:'2 · Agent activity'}));
  expect(screen.getByRole('heading',{name:'Agent activity'})).toBeTruthy();
  expect(screen.queryByRole('heading',{name:'Forecast'})).toBeNull();
  await user.click(screen.getByRole('tab',{name:'1 · Situation'}));
  await user.click(screen.getByRole('button',{name:'2024-09-29'}));
  await vi.waitFor(()=>expect(fetcher.mock.calls.some(([u])=>String(u).endsWith(cases[0].investigation_id))).toBe(true));
  expect(screen.getByTestId('helene-globe')).toBe(globe);
  await user.click(screen.getByRole('button',{name:'Back to overview'}));
  await screen.findByRole('button',{name:`Projected unmet \u00b7 bags ${cases[0].forecasts.reduce((s,f)=>s+f.total_unmet,0)}`});
  expect(screen.getByTestId('helene-globe')).toBe(globe);
});
it('shows canonical stock and original documents in a full-page evidence view',async()=>{
  mockApi();const user=userEvent.setup();Element.prototype.scrollIntoView=vi.fn();window.scrollTo=vi.fn();render(<HeleneExperience/>);
  const current=cases.find(c=>c.investigation_id.includes('2024-10-01'))!;
  await user.click(await screen.findByRole('button',{name:'Inspect simulated hospital: Piedmont Central'}));
  const dialog=screen.getByRole('dialog');
  expect(within(dialog).getAllByText(`${current.forecasts[0].initial_usable} bags`)[0]).toBeTruthy();
  expect(within(dialog).getByText(/on hand \/ .* reserved/)).toBeTruthy();
  await user.click(within(dialog).getByRole('button',{name:'Close panel'}));
  await user.click(screen.getByRole('button',{name:'Investigation'}));
  await user.click(screen.getByRole('button',{name:'Source details'}));
  expect(screen.getByRole('dialog')).toBeTruthy();
  expect(screen.getAllByRole('link',{name:/Open original document/}).length).toBe(current.admission.sources.length);
  expect(screen.getByRole('dialog')).toBeTruthy();
});
it('keeps internal action review explicit and sends the optimistic saved revision',async()=>{
  const fetcher=mockApi(),user=userEvent.setup();render(<HeleneExperience/>);
  await user.click(await screen.findByRole('button',{name:/BAXTER NORTH COVE/}));
  await user.click(screen.getByRole('tab',{name:'3 · Decisions'}));
  expect(within(screen.getAllByRole('table')[0]).getAllByRole('row')).toHaveLength(5);
  await user.click(screen.getByText(/View all alternatives/));
  expect(screen.getAllByRole('table')).toHaveLength(2);
  await user.click(screen.getAllByRole('button',{name:/Review action · .* bags · DRAFT/})[0]);
  const dialog=screen.getByRole('dialog');
  expect(within(dialog).getByText('From')).toBeTruthy();
  expect(within(dialog).getByText('To')).toBeTruthy();
  expect(within(dialog).getByRole('heading',{name:'Review requirements'})).toBeTruthy();
  await user.type(within(dialog).getByLabelText('Reviewer'),'Prototype reviewer');
  await user.click(within(dialog).getByRole('checkbox'));
  await user.click(within(dialog).getByRole('button',{name:'Mark ready for review'}));
  await within(dialog).findByText(/READY FOR REVIEW/);
  const post=fetcher.mock.calls.find(([,o])=>o?.method==='POST');
  expect(JSON.parse(String(post?.[1]?.body))).toMatchObject({expected_revision:1,status:'READY_FOR_REVIEW',attested:true});
  expect(within(dialog).getByText('No external dispatch is available.')).toBeTruthy();
});
it('rejects incompatible units and offers only Helene primary navigation',async()=>{
  const bad=structuredClone(cases[0]);bad.forecasts[0].quantity_unit='vial';
  expect(()=>validateHelene(bad)).toThrow('HELENE_FORECAST_INVALID');
  mockApi();render(<ScenarioExperience/>);
  await screen.findByRole('button',{name:/Projected unmet/});
  expect(screen.queryByRole('button',{name:'Hurricane Helene'})).toBeNull();
  expect(screen.queryByText(/Red Sea/)).toBeNull();
  expect(screen.getByRole('button',{name:'Switch to dark mode'})).toBeTruthy();
});
