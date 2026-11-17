import {expect,test} from '@playwright/test';
import {chatFixture} from './chat-fixture';

test('game course follow-up preserves its task and exposes durable Stop evidence',async({page})=>{
 const state=await chatFixture(page);let current:any=null;
 let session:any={id:'game_session',state:'running',descriptor:{name:'Disposable test target',fixture:true,fields:{countdown:{address:'0x1000',vtype:'uint'}}},knowledge_plan_id:'plan_course',cleanup_operation_ids:['cleanup'],feature_runs:[{id:'feature',feature_id:'cooldown',enabled:true}],mappings:[{id:'mapping',name:'Saved countdown',revision:1,verification_status:'observed'}],operations:[{id:'scan',kind:'scan',status:'succeeded',result:{scan_id:'scan_1',progress:{phase:'partial',bytes_read:65536},total_matches:2,complete:false,reason:'unreadable region',results:[{address:'0x1000',value:60}],next_cursor:'next'}},{id:'read',kind:'read',status:'succeeded',result:{value:60}},{id:'cleanup',kind:'write',status:'queued'}]};
 await page.route('http://127.0.0.1:8765/rpc',async route=>{
  if(route.request().method()==='OPTIONS')return route.fallback();
  const {id,method,params={}}=route.request().postDataJSON();let result:any;
  if(method==='harness.status')result={engine:{ready:true},capabilities:{app:'App'},stopped:false};
  else if(method==='harness.start'){
   state.calls.push({method,params});const chat={id:'game_chat',harness_id:'harness_game',title:params.message,messages:[{role:'user',content:params.message},{role:'assistant',content:'Course plan attached; test process ready.'}]};
   current={id:'harness_game',mode:'game',execution_mode:'auto',status:'awaiting_user',grants:['app'],session:chat,activity:[]};state.chats=[chat];result=current;
  }else if(method==='harness.get')result=current;
  else if(method==='harness.list')result=[];
  else if(method==='harness.send'){
   state.calls.push({method,params});current.session.messages.push({role:'user',content:params.message},{role:'assistant',content:'Continuing the same game task.'});result=current;
  }else if(method==='gamelab.runtime.status')result={native_available:true,sessions:[session]};
  else if(method==='gamelab.scans.control'){state.calls.push({method,params});result={};}
  else if(method==='gamelab.features.stop'){state.calls.push({method,params});session={...session,feature_runs:[{id:'feature',feature_id:'cooldown',enabled:false}]};result={};}
  else if(method==='gamelab.sessions.stop'){
   state.calls.push({method,params});session={...session,state:'stopped',operations:[...session.operations,{id:'uncertain',kind:'write',status:'outcome_unknown'}]};result={};
  }else return route.fallback();
  await route.fulfill({headers:{'Access-Control-Allow-Origin':'*'},json:{id,result}});
 });
 await page.goto('/');await page.getByRole('textbox',{name:'Message',exact:true}).fill('Use my course to make unlimited kill cooldown in local Among Us');await page.getByRole('button',{name:'Send',exact:true}).click();
 await page.getByRole('button',{name:'Allow and start',exact:true}).click();
 const runtime=page.getByRole('region',{name:'Game runtime'});
 await expect(runtime).toBeVisible();await expect(runtime.getByRole('button',{name:'Read test value'})).toBeEnabled();
 await expect(runtime.getByText(/Search incomplete/)).toBeVisible();
 await runtime.getByRole('button',{name:'More scan results'}).click();
 expect(state.calls.find(c=>c.method==='gamelab.scans.control')?.params.cursor).toBe('next');
 await runtime.getByRole('button',{name:'Stop cooldown',exact:true}).click();
 await expect(runtime.getByText(/Updates stopped/)).toBeVisible();
 expect(state.calls.find(c=>c.method==='gamelab.features.stop')?.params.feature_id).toBe('cooldown');
 expect(state.calls.find(c=>c.method==='harness.start')?.params.mode).toBe('game');
 await page.getByRole('textbox',{name:'Message',exact:true}).fill('Use that lesson from the course');await page.getByRole('button',{name:'Send',exact:true}).click();
 await expect(page.getByText('Continuing the same game task.',{exact:true})).toBeVisible();
 expect(state.calls.filter(c=>c.method==='harness.start')).toHaveLength(1);
 expect(state.calls.filter(c=>c.method==='harness.send')).toHaveLength(1);
 await runtime.getByRole('button',{name:'Stop game session'}).click();
 await expect(runtime.getByText('Session: stopped',{exact:true})).toBeVisible();
 await expect(runtime.getByText(/this write will not be repeated/)).toBeVisible();
 await page.reload();await expect(page.getByRole('region',{name:'Game runtime'}).getByText(/this write will not be repeated/)).toBeVisible();
});
