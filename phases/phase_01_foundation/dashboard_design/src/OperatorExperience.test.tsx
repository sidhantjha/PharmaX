// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import fixtures from '../tests/fixtures/operator-workspace-http.json';
import { createOperatorClient } from './operatorApi';
import { OperatorExperience } from './OperatorExperience';
import { ActionReviewForm } from './OperatorViews';
import type { ActionDraft } from './operatorWorkspace';

vi.mock('./GlobeScene',async()=>{
  const React=await import('react');
  return {GlobeScene:React.forwardRef(function Globe({signals,onSelect}:{signals:{id:string;title:string;hospital?:boolean}[];onSelect:(s:unknown)=>void},ref){
    React.useImperativeHandle(ref,()=>({focus(){},focusPoint(){},reset(){},zoomIn(){},zoomOut(){}}));
    return <div data-testid="connected-globe">{signals.map(s=><button key={s.id} onClick={()=>onSelect(s)}>{s.hospital?`Inspect simulated hospital: ${s.title}`:s.title}</button>)}</div>;
  })};
});
afterEach(()=>{cleanup();localStorage.clear();});
const bodies:Record<string,unknown>={'/health':fixtures.health,'/investigations':fixtures.list};
for(const [id,body] of Object.entries(fixtures.cases))bodies['/investigations/'+id]=body;
for(const [id,body] of Object.entries(fixtures.forecasts))bodies['/investigations/'+id+'/forecast']=body;
for(const [id,body] of Object.entries(fixtures.actions))bodies['/investigations/'+id+'/actions']=body;
function connection(){
  const fetcher=vi.fn(async(url:RequestInfo|URL)=>{
    const path=new URL(String(url),'http://localhost').pathname.replace('/api/v1','');
    return new Response(JSON.stringify(bodies[path]),{status:bodies[path]?200:404});
  });
  return {client:createOperatorClient({fetcher}),fetcher};
}
describe('connected pharmaceutical console',()=>{
  it('loads the actual linked cases, switches calculations, and keeps the globe mounted',async()=>{
    const user=userEvent.setup(),{client}=connection();render(<OperatorExperience client={client}/>);
    await user.click(await screen.findByRole('button',{name:/CARBOPLATIN · SUPPLY RISK/}));
    const globe=await screen.findByTestId('connected-globe');
    expect(screen.getByText(/15 synthetic sessions exposed/)).toBeTruthy();
    expect(screen.getByText(/Previous option: 6 vials still unmet/)).toBeTruthy();
    await user.click(screen.getByRole('button',{name:/Original investigation 24/}));
    expect(screen.getByText(/12 synthetic sessions exposed/)).toBeTruthy();
    expect(screen.getByRole('heading',{name:'Review a hospital transfer'})).toBeTruthy();
    expect(screen.getByTestId('connected-globe')).toBe(globe);
    await user.click(screen.getByRole('button',{name:/Revised evidence · Latest 30/}));
    expect(screen.getByRole('heading',{name:'Review supplier delivery'})).toBeTruthy();
    expect(screen.getByRole('img',{name:'Unmet vials: baseline 0, disruption 30, mitigation 0'})).toBeTruthy();
  });
  it('grounds all hospital selections in saved inventory and identifies illustrative positions',async()=>{
    const user=userEvent.setup(),{client}=connection();render(<OperatorExperience client={client}/>);
    await screen.findByRole('button',{name:/CARBOPLATIN · SUPPLY RISK/});
    await user.click(await screen.findByRole('button',{name:'Inspect simulated hospital: Central Illinois Regional Hospital'}));
    const dialog=await screen.findByRole('dialog');
    expect(within(dialog).getByText('158 / 8 vials')).toBeTruthy();
    expect(within(dialog).getByText('150 vials')).toBeTruthy();
    expect(within(dialog).getByText(/not a real address/)).toBeTruthy();
    expect(within(dialog).getByText('Recipient / affected facility')).toBeTruthy();
  });
  it('shows a transport error without sample alerts, forecasts or mock fallback',async()=>{
    render(<OperatorExperience client={createOperatorClient({fetcher:async()=>new Response('{}',{status:503})})}/>);
    expect(await screen.findByRole('alert')).toBeTruthy();
    expect(screen.queryByRole('button',{name:/CARBOPLATIN · SUPPLY RISK/})).toBeNull();
    expect(screen.queryByText(/vials projected unmet/)).toBeNull();
  });
  it('requires human inputs and posts the exact internal review transition',async()=>{
    const user=userEvent.setup(), action=Object.values(fixtures.actions)[0].items[0] as ActionDraft;
    const saved={...action,status:'READY_FOR_REVIEW' as const,revision:action.revision+1};
    const fetcher=vi.fn(async()=>new Response(JSON.stringify(saved))), client=createOperatorClient({fetcher});
    render(<ActionReviewForm action={action} client={client} onSaved={vi.fn()}/>);
    expect((screen.getByRole('button',{name:'Mark internal draft reviewed'}) as HTMLButtonElement).disabled).toBe(true);
    await user.type(screen.getByLabelText('Your reviewer name'),'Test reviewer');
    await user.type(screen.getByLabelText('Review note'),'Fixture validation only');
    await user.click(screen.getByRole('checkbox',{name:'Draft reviewed for accuracy and simulation labels'}));
    await user.click(screen.getByRole('button',{name:'Mark internal draft reviewed'}));
    expect(await screen.findByRole('status')).toHaveProperty('textContent','Internal review saved. Dispatch remains unavailable.');
    const options=fetcher.mock.calls[0] as unknown as [string,RequestInit];
    expect(options[0]).toBe('/api/v1/investigations/'+action.investigation_id+'/actions/'+encodeURIComponent(action.action_id)+'/review');
    expect(JSON.parse(String(options[1].body))).toMatchObject({status:'READY_FOR_REVIEW',expected_revision:action.revision,operator:'Test reviewer'});
  });
  it('rejects wrong-case actions before exposing a review button',async()=>{
    const id=fixtures.list.items[0].investigation_id, source=bodies['/investigations/'+id+'/actions'];
    const modified=structuredClone(source) as {items:ActionDraft[]};modified.items[0].investigation_id='wrong-case';
    const client=createOperatorClient({fetcher:async(url)=>{
      const path=new URL(String(url),'http://localhost').pathname.replace('/api/v1','');
      return new Response(JSON.stringify(path.endsWith('/actions')?modified:bodies[path]));
    }});
    await expect(client.workspace(id)).rejects.toMatchObject({code:'INVALID_API_RESPONSE'});
  });
});
