import {test, expect, type Page} from '@playwright/test';

async function mockBackend(page: Page) {
  const calls: {method:string; params: Record<string,any>}[]=[];
  await page.route('http://127.0.0.1:8765/rpc', async route=>{
    if(route.request().method()==='OPTIONS') return route.fulfill({status:204,headers:{'Access-Control-Allow-Origin':'*','Access-Control-Allow-Headers':'Content-Type, Authorization','Access-Control-Allow-Methods':'POST, OPTIONS'}});
    const {id,method,params={}} = route.request().postDataJSON();calls.push({method,params});
    let result:any={};
    if(method.endsWith('.list')) result=[];
    if(method==='sources.list') result=[{id:'src1',title:'Research notes',status:'ready',chunk_count:2}];
    if(method==='system.status') result={emergency_stop:false};
    if(method==='settings.get') result={provider:'openai_compatible',model:'bughunter-moe'};
    if(method==='agents.available') result={};
    if(method==='plans.create') result={id:'plan1',status:'draft'};
    if(method==='plans.run') result={id:'plan1',status:'awaiting_approval'};
    if(method==='chat.send') result={id:'chat1',title:'Conversation',source_ids:params.source_ids,messages:[{role:'user',content:params.message},{role:'assistant',content:'I reviewed the cited notes.'}]};
    return route.fulfill({json:{id,result},headers:{'Access-Control-Allow-Origin':'*'}});
  });
  return calls;
}

test('only four primary sections and advanced tools are optional',async({page})=>{
  await mockBackend(page);await page.goto('/');
  for(const name of ['Chat','Tasks','Knowledge','Settings']) await expect(page.getByRole('navigation',{name:'Main navigation'}).getByRole('button',{name,exact:true})).toBeVisible();
  await expect(page.getByRole('navigation',{name:'Main navigation'}).getByRole('button',{name:'Workspaces'})).toHaveCount(0);
  await page.getByRole('navigation',{name:'Main navigation'}).getByRole('button',{name:'Tasks'}).click();
  await expect(page.getByRole('button',{name:'Advanced tools (optional)'})).toBeVisible();
});

test('chat uses selected knowledge and one composer task selector',async({page})=>{
  const calls=await mockBackend(page);await page.goto('/');
  await page.getByRole('button',{name:'Knowledge',exact:true}).last().click();
  await page.getByRole('dialog',{name:'Choose from your Knowledge Library'}).getByRole('checkbox').check();
  await page.getByRole('button',{name:'Done (1/6)'}).click();
  await page.getByRole('textbox',{name:'Message AI'}).fill('Explain the uploaded research');
  await page.getByRole('button',{name:'Send or review task'}).click();
  await expect(page.getByText('I reviewed the cited notes.')).toBeVisible();
  expect(calls.find(c=>c.method==='chat.send')?.params.source_ids).toEqual(['src1']);
});

test('web audit requires confirmation rather than silently executing',async({page})=>{
  const calls=await mockBackend(page);await page.goto('/');
  await page.getByRole('textbox',{name:'Message AI'}).fill('Audit my authorized website');
  await page.getByRole('button',{name:'Send or review task'}).click();
  await expect(page.getByRole('dialog',{name:'Autonomous website review'})).toBeVisible();
  expect(calls.some(c=>c.method==='website.audit.start')).toBe(false);
});
