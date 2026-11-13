import type { Page } from '@playwright/test';

export async function chatFixture(page: Page, options: {completeAfter?: number; failOnce?: boolean} = {}) {
  const state = {calls: [] as {method:string;params:any}[], chats: [] as any[], reads:0, completeAfter:options.completeAfter ?? 2, stopped:false, failed:false};
  await page.route('http://127.0.0.1:8765/rpc', async route => {
    const headers = {'Access-Control-Allow-Origin':'*','Access-Control-Allow-Headers':'Content-Type, Authorization','Access-Control-Allow-Methods':'POST, OPTIONS'};
    if (route.request().method()==='OPTIONS') return route.fulfill({status:204,headers});
    const {id,method,params={}} = route.request().postDataJSON(); state.calls.push({method,params});
    let result:any = method.endsWith('.list') ? [] : {};
    if (method==='settings.get') result={provider:'ollama',model:'fixture'};
    if (method==='system.stop') state.stopped=true;
    if (method==='system.resume') state.stopped=false;
    if (method==='system.status') result={emergency_stop:state.stopped};
    if (method==='models.available') result={providers:[{provider:'ollama',label:'Local model',kind:'local',ready:true,models:['fixture'],active:true}],active:{provider:'ollama',model:'fixture'}};
    if (method==='chat.list') result=state.chats;
    if (method==='chat.get') result=state.chats.find(c=>c.id===params.id);
    if (method==='chat.agent') {
      const prior=state.chats.find(c=>c.id===params.id);
      const session={...prior,id:prior?.id || 'chat_one',title:prior?.title || params.message,project_id:null,run_id:'run_one',task_id:'run_one',task_ids:['run_one'],messages:[...(prior?.messages||[]),{role:'user',content:params.message},{role:'assistant',content:'',run_id:'run_one',pending:true}]};
      state.chats=[session]; result={session,run_id:'run_one',cursor:0};
    }
    if (method==='tasks.get') {
      if (options.failOnce && !state.failed) {state.failed=true;return route.fulfill({json:{id,error:{message:'Temporary connection failure'}},headers});}
      state.reads++;
      const status=state.stopped?'cancelled':params.id==='task_local'||state.reads>=state.completeAfter?'completed':'running';
      result={id:params.id,kind:'web_investigation',status,summary:status==='completed'?'Checked the saved evidence.':'Reading library references.',events:[{kind:'tool',text:'Retrieved 3 cited passages.'}],event_count:1,result:{observed:true}};
    }
    if (method==='chat.finalize' || method==='chat.stop') {
      if (method==='chat.stop') state.stopped=true;
      const terminal=state.stopped||state.reads>=state.completeAfter;
      const session=state.chats.find(c=>c.id===params.id);
      if (terminal) session.messages=session.messages.map((m:any)=>m.pending?{...m,pending:false,content:state.stopped?'Task cancelled.':'Here is the verified result.'}:m);
      result={terminal,session,status:state.stopped?'cancelled':terminal?'completed':'running'};
    }
    if (method==='chat.delete') {state.chats=state.chats.filter(c=>c.id!==params.id);result={deleted:true};}
    if (method==='chat.export') result={filename:'conversation.json',content:JSON.stringify(state.chats)};
    if(method==='chat.attachments.add')result={id:'chatref_attached',filename:params.filename,title:params.filename,kind:'text',has_images:false};
    if (method==='sources.import') result={id:'source_attached',filename:params.filename,status:'ready',chunk_count:1};
    if (method==='autopilot.plan') result={workflow: /knowledge base/i.test(params.message)?'knowledge_check':/app checks/i.test(params.message)?'app_checks':'chat',missing:[]};
    if (method==='chat.autopilot') {
      result={id:'chat_local',title:params.message,task_id:'task_local',task_ids:['task_local'],messages:[{role:'user',content:params.message},{role:'assistant',content:'Local checks recorded.'}]};state.chats=[result];
    }
    return route.fulfill({json:{id,result},headers});
  });
  return state;
}
