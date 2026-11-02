import { test, expect, type Page } from '@playwright/test';

async function mockApp(page: Page) {
  const calls: { method: string; params: Record<string,unknown> }[] = [];
  let chats: any[] = [];
  let lastChat: any = null;
  const source = {id:'source_guide',title:'Memory guide',status:'ready',chunk_count:3};
  await page.route('http://127.0.0.1:8765/rpc', async route => {
    if (route.request().method() === 'OPTIONS') {
      await route.fulfill({status:204,headers:{'Access-Control-Allow-Origin':'*','Access-Control-Allow-Headers':'Authorization, Content-Type','Access-Control-Allow-Methods':'POST, OPTIONS'}});
      return;
    }
    const {id, method, params = {}} = route.request().postDataJSON();
    calls.push({method,params});
    let result: any = {};
    if (method.endsWith('.list')) result = [];
    if (method === 'sources.list') result = [source];
    if (method === 'chat.list') result = chats;
    if (method === 'chat.get') result = lastChat;
    if (method === 'system.status') result = {emergency_stop:false};
    if (method === 'settings.get') result = {provider:'openai_compatible',model:'bughunter-moe'};
    if (method === 'chat.send') {
      lastChat = {id:'chat_test',title:'First question',source_ids:params.source_ids,messages:[
        {role:'user',content:params.message},{role:'assistant',content:'The guide explains the technique.'}]};
      chats = [{id:lastChat.id,title:lastChat.title}]; result = lastChat;
    }
    if (method === 'chat.delete') {chats = [];lastChat = null;result = {deleted:true};}
    await route.fulfill({json:{id,result},headers:{'Access-Control-Allow-Origin':'*'}});
  });
  return calls;
}

test('chat selects an indexed resource and sends its source ID to the backend', async ({page}) => {
  const calls = await mockApp(page);
  await page.goto('/');
  await expect(page.getByRole('heading',{name:'What should I help you do?'})).toBeVisible();
  await page.locator('.conversation-actions-left').getByRole('button',{name:'Knowledge'}).click();
  await page.getByRole('dialog',{name:'Choose from your Knowledge Library'}).getByRole('checkbox').check();
  await page.getByRole('button',{name:'Done (1/6)'}).click();
  await page.getByRole('textbox',{name:'Message AI'}).fill('What does this say?');
  await page.getByRole('button',{name:'Send or review task'}).click();
  await expect(page.getByText('The guide explains the technique.')).toBeVisible();
  expect(calls.find(call=>call.method==='chat.send')?.params.source_ids).toEqual(['source_guide']);
});

test('chat can hand off a memory research task without running it itself', async ({page}) => {
  const calls = await mockApp(page);
  await page.goto('/');
  await page.getByRole('textbox',{name:'Message AI'}).fill('Inspect a pointer in my test process');
  await page.getByRole('button',{name:'Send or review task'}).click();
  await expect(page.getByRole('dialog',{name:'Open memory tools'})).toBeVisible();
  expect(calls.some(call=>call.method.startsWith('ce.'))).toBeFalsy();
  await page.getByRole('button',{name:'Cancel'}).click();
});

test('Tasks is a clear primary section and advanced workspaces are optional', async ({page}) => {
  await mockApp(page);
  await page.goto('/');
  await page.setViewportSize({width:390,height:844});
  await expect(page.getByRole('navigation',{name:'Main navigation'}).getByRole('button',{name:'Tasks'})).toBeVisible();
  await page.getByRole('navigation',{name:'Main navigation'}).getByRole('button',{name:'Tasks'}).click();
  await expect(page.getByRole('heading',{name:'Your tasks'})).toBeVisible();
  await expect(page.getByRole('button',{name:'Advanced tools (optional)'})).toBeVisible();
});
