// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { HeleneChat } from './HeleneChat';
import { validateChat } from './heleneChatApi';
import { validateHelene } from './heleneApi';
import capture from '../tests/fixtures/helene-api.json';
const data=validateHelene(Object.values(capture.cases)[1]);
const reply={schema_version:'helene-chat-1',investigation_id:data.investigation_id,conversation_id:'conversation-'+'a'.repeat(32),mode:'offline-deterministic',model_executed:false,turns:[{question:'Which hospitals face the greatest saline shortage?',answer:'Piedmont Central: 172 unmet bags.',citations:[{reference:'forecast',label:'Canonical forecast',href:`/api/v1/helene/cases/${data.investigation_id}/chat-sources/forecast`}],executions:[{tool:'canonical.forecast.read',status:'complete',summary:'Retrieved actual saved forecast.'}],limitations:['No live model executed.']}]};
afterEach(()=>{cleanup();sessionStorage.clear();vi.unstubAllGlobals();});
it('sends an investigation-scoped question, renders citations and restores server history after navigation',async()=>{
  Element.prototype.scrollIntoView=vi.fn();
  const fetcher=vi.fn(async(_url:RequestInfo|URL,_options?:RequestInit)=>new Response(JSON.stringify(reply)));vi.stubGlobal('fetch',fetcher);
  const user=userEvent.setup();const view=render(<HeleneChat data={data}/>);
  await user.type(screen.getByRole('textbox',{name:'Message PharmaX'}),reply.turns[0].question);
  await user.click(screen.getByRole('button',{name:'Send message'}));
  expect(await screen.findByText('Piedmont Central: 172 unmet bags.')).toBeTruthy();
  expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toMatchObject({mode:'offline',question:reply.turns[0].question});
  expect(String(fetcher.mock.calls[0][0])).toContain(data.investigation_id+'/chat');
  expect(screen.getByRole('link',{name:/Canonical forecast/}).getAttribute('href')).toBe(reply.turns[0].citations[0].href);
  view.unmount();render(<HeleneChat data={data}/>);
  expect(await screen.findByText('Piedmont Central: 172 unmet bags.')).toBeTruthy();
  expect(String(fetcher.mock.calls[1][0])).toContain('/chat/conversation-');
  await user.click(screen.getByRole('button',{name:'New conversation'}));
  expect(screen.getByRole('heading',{name:'A clearer view. One question away.'})).toBeTruthy();
  expect(sessionStorage.length).toBe(0);
});
it('shows HTTP failures honestly and preserves the question for retry',async()=>{
  Element.prototype.scrollIntoView=vi.fn();vi.stubGlobal('fetch',vi.fn(async()=>new Response('{}',{status:503})));
  const user=userEvent.setup();render(<HeleneChat data={data}/>);
  await user.type(screen.getByRole('textbox'), 'What happened?');await user.click(screen.getByRole('button',{name:'Send message'}));
  expect(await screen.findByRole('alert')).toHaveProperty('textContent',expect.stringContaining('unavailable (503)'));
  expect(screen.getByRole('textbox')).toHaveProperty('value','What happened?');
  expect(screen.queryByText('Piedmont Central: 172 unmet bags.')).toBeNull();
});
it('rejects cross-case responses, invented inference and external citation targets',()=>{
  expect(()=>validateChat({...reply,investigation_id:'other'},data.investigation_id)).toThrow();
  expect(()=>validateChat({...reply,model_executed:true},data.investigation_id)).toThrow();
  const bad=structuredClone(reply);bad.turns[0].citations[0].href='javascript:alert(1)';
  expect(()=>validateChat(bad,data.investigation_id)).toThrow();
});
