import { useEffect, useRef, useState } from 'react';
import { ArrowUp, MessageCircle, Plus } from 'lucide-react';
import { BrandMark } from './BrandMark';
import type { HeleneCase } from './heleneApi';
import { chatApi, type ChatTurn } from './heleneChatApi';

export function HeleneChat({data}:{data:HeleneCase}) {
  const [turns,setTurns]=useState<ChatTurn[]>([]),[question,setQuestion]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const [conversation,setConversation]=useState<string|undefined>();
  const end=useRef<HTMLDivElement>(null),request=useRef<AbortController|null>(null);
  const storageKey='pharmax-chat-'+data.investigation_id;
  useEffect(()=>{setTurns([]);setConversation(undefined);setError('');const saved=sessionStorage.getItem(storageKey);const abort=new AbortController();if(saved){setBusy(true);chatApi.history(data.investigation_id,saved,abort.signal).then(reply=>{if(!abort.signal.aborted){setConversation(reply.conversation_id);setTurns(reply.turns);}}).catch(()=>{if(!abort.signal.aborted){sessionStorage.removeItem(storageKey);setError('Saved conversation unavailable. A new conversation will start.');}}).finally(()=>{if(!abort.signal.aborted)setBusy(false);});}return()=>{abort.abort();request.current?.abort();};},[data.investigation_id,storageKey]);
  useEffect(()=>{end.current?.scrollIntoView({behavior:'smooth',block:'nearest'});},[turns,busy]);
  async function send(text:string){
    if(!text.trim()||busy)return;
    setError('');setBusy(true);setQuestion('');
    const abort=new AbortController();request.current=abort;
    const timeout=window.setTimeout(()=>{abort.abort();setBusy(false);setQuestion(text);setError('Request timed out. Reopen Chat to check saved history before resending.');},30000);
    try {const reply=await chatApi.send(data.investigation_id,text,conversation,abort.signal);setConversation(reply.conversation_id);sessionStorage.setItem(storageKey,reply.conversation_id);setTurns(reply.turns);}
    catch(e){if(!abort.signal.aborted){setError(e instanceof Error?e.message:'Chat unavailable');setQuestion(text);}}
    finally{clearTimeout(timeout);if(!abort.signal.aborted)setBusy(false);}
  }
  const suggestions=["What happened to Baxter's North Cove facility?",'Which hospitals face the greatest saline shortage?','Why were 800 units reportedly shipped but only 500 received?','What mitigation options were evaluated?','What happens to the forecast if another 120 bags arrive?'];
  return <section className="helene-chat" aria-label="Investigation chat"><div className="chat-page-top"><span><span className="status-dot accepted"/>Evidence-backed · No live model</span><button className="pill-button" disabled={busy} onClick={()=>{setConversation(undefined);setTurns([]);setError('');sessionStorage.removeItem(storageKey);}}><Plus size={14}/>New conversation</button></div><div className="conversation-scroll">{!turns.length&&<div className="chat-welcome"><span className="chat-brand-orbit"><BrandMark/></span><span className="eyebrow">ASK PHARMAX</span><h1>A clearer view.<br/>One question away.</h1><p>Explore this investigation’s evidence, forecasts and evaluated options.</p><div className="question-pills">{suggestions.map(s=><button key={s} disabled={busy} onClick={()=>send(s)}>{s}<MessageCircle size={14}/></button>)}</div></div>}{turns.map((turn,i)=><article className="conversation-turn" key={i}><div className="user-question"><span>You</span><p>{turn.question}</p></div><div className="assistant-answer"><span className="assistant-label"><BrandMark/>PharmaX <small>Investigation evidence</small></span><p>{turn.answer}</p><div className="citation-pills">{turn.citations.map(c=><a key={c.reference} href={c.href} target="_blank" rel="noreferrer">{c.label} ↗</a>)}</div><details className="turn-execution"><summary>{turn.executions.length} completed retrieval / calculation steps · 0 model calls</summary>{turn.executions.map((e,j)=><p key={j}>{e.tool} · {e.status} · {e.summary}</p>)}<p>{turn.limitations.join(' ')}</p></details></div></article>)}{busy&&<p role="status" className="chat-working">Retrieving the saved investigation and calculating from canonical evidence…</p>}<div ref={end}/></div><div className="composer-dock">{error&&<p role="alert" className="chat-error">{error}</p>}<form className="conversation-composer" onSubmit={e=>{e.preventDefault();send(question);}}><textarea aria-label="Message PharmaX" placeholder="Ask about this investigation…" rows={2} maxLength={2000} value={question} onChange={e=>setQuestion(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();send(question);}}}/><button type="submit" aria-label="Send message" disabled={busy||!question.trim()}><ArrowUp size={21}/></button></form><p>Answers use this saved checkpoint. Hypothetical arrivals never modify canonical records. </p></div></section>;
}
