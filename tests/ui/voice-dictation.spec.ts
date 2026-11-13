import {test,expect} from '@playwright/test';
import {chatFixture} from './chat-fixture';
test.use({launchOptions:{args:['--use-fake-device-for-media-stream','--use-fake-ui-for-media-stream']}});

test('dictation updates the composer live, has no option checkboxes, and obeys spoken send',async({page,context})=>{
 const state=await chatFixture(page,{completeAfter:1});await context.grantPermissions(['microphone']);let stage=0;let stopped=0;
 await page.route('http://127.0.0.1:8765/rpc',async route=>{
  if(route.request().method()==='OPTIONS')return route.fallback();const {id,method}=route.request().postDataJSON();let result:any;
  if(method==='voice.status')result={selected:'tiny',models:[{id:'tiny',ready:true}]};
  else if(method==='voice.start')result={id:'voice_live',status:'ready'};
  else if(method==='voice.feed')result={status:'ready',events:stage===0?[{kind:'partial',line_id:1,seq:1,text:'Um open Library'}]:stage===1?[{kind:'final',line_id:1,seq:2,text:'Open Library and explain my course.'}]:stage===2?[{kind:'final',line_id:2,seq:3,text:'Send a message'}]:[]};
  else if(method==='voice.stop'){stopped++;result={stopped:true,events:[]};}
  else return route.fallback();await route.fulfill({headers:{'Access-Control-Allow-Origin':'*'},json:{id,result}});
 });
 await page.goto('/');await page.getByRole('button',{name:'Voice input',exact:true}).click();await expect(page.getByText('Listening locally',{exact:true})).toBeVisible({timeout:12000});
 await expect(page.getByRole('textbox',{name:'Message',exact:true})).toHaveValue('open Library');await expect(page.locator('.voice-status input[type=checkbox]')).toHaveCount(0);
 stage=1;await expect(page.getByRole('textbox',{name:'Message',exact:true})).toHaveValue('Open Library and explain my course.');stage=2;
 await expect.poll(()=>state.calls.filter(c=>c.method==='chat.agent'||c.method==='harness.start').length).toBeGreaterThan(0);
 expect(state.calls.find(c=>c.method==='chat.agent')?.params.message).toBe('Open Library and explain my course.');
 await expect(page.getByRole('textbox',{name:'Message',exact:true})).toHaveValue('');await expect(page.getByText('Listening locally',{exact:true})).toBeVisible();expect(stopped).toBe(0);
 await page.getByRole('button',{name:'Stop all',exact:true}).click();await expect.poll(()=>stopped).toBe(1);
});

test('saying send with an empty draft does not submit a request',async({page,context})=>{
 const state=await chatFixture(page);await context.grantPermissions(['microphone']);await page.route('http://127.0.0.1:8765/rpc',async route=>{
  if(route.request().method()==='OPTIONS')return route.fallback();const {id,method}=route.request().postDataJSON();let result:any;
  if(method==='voice.status')result={selected:'tiny',models:[{id:'tiny',ready:true}]};else if(method==='voice.start')result={id:'voice_empty'};else if(method==='voice.feed')result={status:'ready',events:[{kind:'final',seq:1,line_id:1,text:'send it'}]};else if(method==='voice.stop')result={events:[]};else return route.fallback();await route.fulfill({headers:{'Access-Control-Allow-Origin':'*'},json:{id,result}});
 });
 await page.goto('/');await page.getByRole('button',{name:'Voice input',exact:true}).click();await expect(page.getByText('Dictate a message before saying “send”.',{exact:true})).toBeVisible({timeout:12000});expect(state.calls.filter(c=>c.method==='chat.agent')).toHaveLength(0);
});

for(const command of ['send it','send the message'])test(`a combined dictated message and ${command} submits once`,async({page,context})=>{
 const state=await chatFixture(page,{completeAfter:1});await context.grantPermissions(['microphone']);
 await page.route('http://127.0.0.1:8765/rpc',async route=>{
  if(route.request().method()==='OPTIONS')return route.fallback();const {id,method}=route.request().postDataJSON();let result:any;
  if(method==='voice.status')result={selected:'medium',models:[{id:'medium',ready:true,label:'Moonshine Medium Streaming · English'}]};
  else if(method==='voice.start')result={id:'combined_voice',model_label:'Moonshine Medium Streaming · English'};
  else if(method==='voice.feed')result={status:'ready',events:[{kind:'final',seq:1,line_id:1,text:`Explain the diagram. ${command}.`}]};
  else if(method==='voice.stop')result={events:[]};else return route.fallback();await route.fulfill({headers:{'Access-Control-Allow-Origin':'*'},json:{id,result}});
 });
 await page.goto('/');await page.getByRole('button',{name:'Voice input',exact:true}).click();
 await expect.poll(()=>state.calls.filter(c=>c.method==='chat.agent').length).toBe(1);
 expect(state.calls.find(c=>c.method==='chat.agent')?.params.message).toBe('Explain the diagram');
 await expect(page.getByText('Moonshine Medium Streaming · English',{exact:true})).toBeVisible();
 await page.waitForTimeout(1200);expect(state.calls.filter(c=>c.method==='chat.agent')).toHaveLength(1);
});
