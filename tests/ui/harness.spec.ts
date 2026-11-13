import {expect,test} from '@playwright/test';
import type {Page} from '@playwright/test';
import {chatFixture} from './chat-fixture';

test('desktop readiness survives a failed supporting-tool catalog',async({page})=>{
 await harnessFixture(page);await page.route('http://127.0.0.1:8765/rpc',async route=>{
  if(route.request().method()==='OPTIONS')return route.fallback();const {id,method}=route.request().postDataJSON();if(method!=='setup.catalog')return route.fallback();
  await route.fulfill({headers:{'Access-Control-Allow-Origin':'*'},json:{id,error:{message:'Tool catalog temporarily unavailable'}}});
 });
 await page.goto('/');await page.getByRole('button',{name:'Settings',exact:true}).click();await page.getByRole('tab',{name:'Computer & apps',exact:true}).click();const panel=page.getByRole('region',{name:'Task harness settings'});
 await expect(panel.getByText('Available on this device',{exact:true})).toBeVisible();await expect(panel.getByRole('combobox',{name:'Supporting tool'})).toContainText('Tool list unavailable');await expect(panel.getByRole('button',{name:'Review installation'})).toBeDisabled();
});

async function harnessFixture(page:Page){
 const state=await chatFixture(page);let current:any=null;let setupReads=0;
 await page.route('http://127.0.0.1:8765/rpc',async route=>{
  if(route.request().method()==='OPTIONS')return route.fallback();
  const {id,method,params={}}=route.request().postDataJSON();let result:any;
  if(method==='harness.status')result={engine:{installed:true,ready:true,platform:'Linux',detail:'Desktop engine responds.'},capabilities:{app:'App'},stopped:state.stopped};
  else if(method==='harness.start'){
   state.calls.push({method,params});
   const session={id:params.id||'chat_harness',harness_id:'harness_fixture',title:params.message,messages:[{role:'user',content:params.message},{role:'assistant',content:'Which detected device should I use?'}]};
   current={id:'harness_fixture',run_id:'oprun_fixture',status:'awaiting_user',grants:params.grants,model:'fixture',session,activity:[{name:'app_action',status:'returned',result:'Detected device: fixture'}]};state.chats=[session];result=current;
  }else if(method==='harness.list')result=current?[{id:current.id,chat_id:current.session.id,title:current.session.title,status:current.status,model:current.model,background:true}]:[];
  else if(method==='harness.get'){result={...current,status:state.stopped?'cancelled':current.status};}
  else if(method==='harness.send'){
   state.calls.push({method,params});current={...current,status:'awaiting_user',session:{...current.session,messages:[...current.session.messages,{role:'user',content:params.message},{role:'assistant',content:'I will use the detected device.'}]}};state.chats=[current.session];result=current;
  }else if(method==='harness.stop'){state.calls.push({method,params});current.status='cancelled';result=current;}
  else if(method==='setup.catalog')result={items:[{id:'agent-desktop',label:'Desktop control engine',installed:true},{id:'adb',label:'Android device connection',installed:false}]};
  else if(method==='setup.plan')result={package:params.package,label:'Android device connection',platform:'Linux',command:'apt-get install adb',detail:'Uses the platform package manager.',ready_to_install:true};
  else if(method==='setup.install'){state.calls.push({method,params});result={id:'setup_fixture',status:'running',message:'Installing dependency'};}
  else if(method==='setup.status')result={id:'setup_fixture',status:++setupReads>1?'completed':'running',message:setupReads>1?'Executable found.':'Installing dependency',installed_verified:setupReads>1};
  else return route.fallback();
  await route.fulfill({headers:{'Access-Control-Allow-Origin':'*'},json:{id,result}});
 });
 return state;
}

test('computer task collects permissions once and asks follow-up inside persistent Chat',async({page})=>{
 const state=await harnessFixture(page);await page.goto('/');
 await page.getByRole('textbox',{name:'Message',exact:true}).fill('Open Notepad on Windows');await page.getByRole('button',{name:'Send',exact:true}).click();
 const setup=page.getByRole('region',{name:'Task permissions'});await expect(setup).toBeVisible();
 expect(state.calls.some(call=>call.method==='harness.start')).toBe(false);
 await setup.getByRole('button',{name:'Allow and start',exact:true}).click();
 await expect(page.getByText('Which detected device should I use?',{exact:true})).toBeVisible();
 expect(state.calls.find(call=>call.method==='harness.start')?.params.grants).toEqual(['app','computer']);
 await page.getByRole('textbox',{name:'Message',exact:true}).fill('Use the detected phone');await page.getByRole('button',{name:'Send',exact:true}).click();
 await expect(page.getByText('I will use the detected device.',{exact:true})).toBeVisible();
 expect(state.calls.filter(call=>call.method==='harness.start')).toHaveLength(1);
 expect(state.calls.filter(call=>call.method==='harness.send')).toHaveLength(1);
 await page.reload();await expect(page.getByText('I will use the detected device.',{exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Stop all',exact:true}).click();
 await expect(page.getByRole('textbox',{name:'Message',exact:true})).toBeDisabled();
 await expect(page.getByRole('button',{name:'Resume tasks',exact:true})).toBeVisible();
});

test('COD Android setup discovers first and never asks for an APK upload automatically',async({page})=>{
 const state=await harnessFixture(page);await page.goto('/');
 await page.getByRole('textbox',{name:'Message',exact:true}).fill('COD Android ESP');await page.getByRole('button',{name:'Send',exact:true}).click();
 await page.getByRole('button',{name:'Allow and start',exact:true}).click();
 await expect(page.getByText('Which detected device should I use?',{exact:true})).toBeVisible();
 expect(state.calls.find(call=>call.method==='harness.start')?.params.mode).toBe('game');
 expect(state.calls.some(call=>call.method==='gamelab.chat_start'||call.method==='autopilot.plan')).toBe(false);
 await expect(page.getByText(/attach.*APK/i)).toHaveCount(0);
});

test('Settings supports platform installation plan, progress and completion without a terminal',async({page})=>{
 const state=await harnessFixture(page);await page.goto('/');await page.getByRole('button',{name:'Settings',exact:true}).click();await page.getByRole('tab',{name:'Computer & apps',exact:true}).click();
 const panel=page.getByRole('region',{name:'Task harness settings'});
 await expect(panel.getByText('Available on this device',{exact:true})).toBeVisible();
 await panel.getByRole('combobox',{name:'Supporting tool'}).selectOption('adb');
 await panel.getByRole('button',{name:'Review installation',exact:true}).click();
 await expect(panel.getByText('apt-get install adb',{exact:true})).toBeVisible();
 expect(state.calls.some(call=>call.method==='setup.install')).toBe(false);
 await panel.getByRole('button',{name:'Install tool',exact:true}).click();
 await expect(panel.getByText('Executable found.',{exact:true})).toBeVisible();
 expect(state.calls.find(call=>call.method==='setup.install')?.params.approved).toBe(true);
});

for(const platform of ['Windows','Linux']){
 test(`Settings uses the ${platform} harness without Mac-only permission controls`,async({page})=>{
  await harnessFixture(page);
  await page.route('http://127.0.0.1:8765/rpc',async route=>{
   if(route.request().method()==='OPTIONS')return route.fallback();
   const {id,method}=route.request().postDataJSON();if(method!=='system.status')return route.fallback();
   await route.fulfill({headers:{'Access-Control-Allow-Origin':'*'},json:{id,result:{platform,emergency_stop:false}}});
  });
  await page.goto('/');await page.getByRole('button',{name:'Settings',exact:true}).click();await page.getByRole('tab',{name:'Computer & apps',exact:true}).click();
  await expect(page.getByRole('region',{name:'Task harness settings'})).toBeVisible();
  await expect(page.getByRole('heading',{name:'Mac permissions',exact:true})).toHaveCount(0);
 });
}

test('Tasks panel identifies the model, task serial and app without tool activity in Chat',async({page})=>{
 const state=await harnessFixture(page);let current:any;
 await page.route('http://127.0.0.1:8765/rpc',async route=>{
  if(route.request().method()==='OPTIONS')return route.fallback();
  const {id,method,params={}}=route.request().postDataJSON();
  if(method==='harness.start'){
   const session={id:'chat_cursor',harness_id:'harness_cursor',title:params.message,messages:[{role:'user',content:params.message}]};state.chats=[session];
   current={id:'harness_cursor',status:'running',session,grants:['app','computer'],model:'gpt-6.1-sol',activity:[],controller:{active:true,overlay_rendered:true,label:'GPT-6.1 Sol #3 · Calculator'}};
  }else if(method==='harness.stop'){current={...current,status:'cancelled',controller:{...current.controller,active:false,overlay_rendered:false}};}
  else if(method!=='harness.get')return route.fallback();
  await route.fulfill({headers:{'Access-Control-Allow-Origin':'*'},json:{id,result:current}});
 });
 await page.goto('/');await page.getByRole('textbox',{name:'Message',exact:true}).fill('Open Calculator');await page.getByRole('button',{name:'Send',exact:true}).click();await page.getByRole('button',{name:'Allow and start',exact:true}).click();
 await expect(page.getByText('GPT-6.1 Sol #3 · Calculator · Cursor visible',{exact:true})).toHaveCount(0);
 await expect(page.getByText('Working in background…',{exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Stop task',exact:true}).click();
 await expect(page.getByText('Working in background…',{exact:true})).toHaveCount(0);
});
