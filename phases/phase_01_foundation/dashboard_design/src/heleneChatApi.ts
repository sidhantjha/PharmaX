export interface ChatCitation {reference:string;label:string;href:string}
export interface ChatExecution {tool:string;status:'complete';summary:string}
export interface ChatTurn {question:string;answer:string;citations:ChatCitation[];executions:ChatExecution[];limitations:string[]}
export interface ChatReply {schema_version:'helene-chat-1';investigation_id:string;conversation_id:string;mode:'offline-deterministic';model_executed:false;turns:ChatTurn[]}
export function validateChat(value:unknown,id:string):ChatReply {
  const v=value as ChatReply;
  if(!v||v.schema_version!=='helene-chat-1'||v.investigation_id!==id||v.mode!=='offline-deterministic'||v.model_executed!==false||!Array.isArray(v.turns)||!v.conversation_id)throw Error('Invalid investigation chat response');
  for(const t of v.turns){if(typeof t.answer!=='string'||typeof t.question!=='string'||!Array.isArray(t.citations)||!Array.isArray(t.executions)||!Array.isArray(t.limitations))throw Error('Invalid chat turn');for(const c of t.citations)if(typeof c.label!=='string'||!c.href.startsWith('/api/v1/helene/cases/'+encodeURIComponent(id)+'/chat-sources/')||/[\r\n]/.test(c.href))throw Error('Invalid source reference');}
  return v;
}
export const chatApi={async history(id:string,conversation:string,signal?:AbortSignal){const response=await fetch('/api/v1/helene/cases/'+encodeURIComponent(id)+'/chat/'+encodeURIComponent(conversation),{signal});if(!response.ok)throw Error('Saved conversation unavailable. Start a new conversation.');return validateChat(await response.json(),id);},async send(id:string,question:string,conversation_id?:string,signal?:AbortSignal){
  const response=await fetch('/api/v1/helene/cases/'+encodeURIComponent(id)+'/chat',{method:'POST',headers:{'Content-Type':'application/json'},signal,body:JSON.stringify({question,conversation_id,mode:'offline'})});
  if(!response.ok)throw Error(response.status===409?'Conversation changed. Start a new conversation.':`Investigation chat unavailable (${response.status}). No answer or model execution is assumed.`);
  return validateChat(await response.json(),id);
}};
