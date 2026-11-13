import {expect,test} from '@playwright/test';
import {chatFixture} from './chat-fixture';

for(const message of ['Import my Rust course on my Mac','Set up local AI and a vision model on my Mac','Search my library for Apple Silicon lessons']) {
  test(`ordinary learning request stays in chat: ${message}`,async({page})=>{
    const state=await chatFixture(page);
    await page.goto('/');
    await page.evaluate(()=>{sessionStorage.setItem('gamelab.run_id','stale-game-run');sessionStorage.setItem('gamelab.scope','other-project:other-session');});
    await page.getByRole('textbox',{name:'Message',exact:true}).fill(message);
    await page.getByRole('button',{name:'Send',exact:true}).click();
    await expect.poll(()=>state.calls.filter(call=>call.method==='chat.agent').length).toBe(1);
    expect(state.calls.some(call=>call.method.startsWith('gamelab.'))).toBe(false);
  });
}

test('explicit game setup and its follow-up remain in one permissioned Chat task',async({page})=>{
  const state=await chatFixture(page);
  const calls:string[]=[];let session:any;
  await page.route('http://127.0.0.1:8765/rpc',async route=>{
    if(route.request().method()==='OPTIONS')return route.fallback();
    const {id,method,params={}}=route.request().postDataJSON();
    if(!method.startsWith('harness.'))return route.fallback();
    calls.push(method);
    if(method==='harness.start'){
      session={id:'chat_game',harness_id:'harness_game',title:params.message,messages:[{role:'user',content:params.message},{role:'assistant',content:'Scoped fixture reply'}]};
      state.chats=[session];
    }
    const result={id:'harness_game',status:'awaiting_user',session,activity:[],grants:['app']};
    await route.fulfill({headers:{'Access-Control-Allow-Origin':'*'},json:{id,result}});
  });
  await page.goto('/');
  await page.getByRole('textbox',{name:'Message',exact:true}).fill('/gamelab explicit fixture');
  await page.getByRole('button',{name:'Send',exact:true}).click();
  await page.getByRole('button',{name:'Allow and start',exact:true}).click();
  await expect(page.getByText('Scoped fixture reply')).toBeVisible();
  await page.getByRole('textbox',{name:'Message',exact:true}).fill('Continue that thread');
  await page.getByRole('button',{name:'Send',exact:true}).click();
  await expect.poll(()=>calls.filter(method=>method==='harness.send').length).toBe(1);
  expect(calls.filter(method=>method==='harness.start')).toHaveLength(1);
});
