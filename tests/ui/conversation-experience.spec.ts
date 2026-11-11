import {test,expect} from '@playwright/test';
import {chatFixture} from './chat-fixture';

test('chat retries progress errors and keeps the final result with recorded activity',async({page})=>{
  const state=await chatFixture(page,{failOnce:true}); await page.goto('/');
  await page.getByRole('textbox',{name:'Message',exact:true}).fill('Explain the saved evidence');
  await page.getByRole('button',{name:'Send',exact:true}).click();
  await expect(page.getByText('Here is the verified result.',{exact:true})).toBeVisible();
  await page.getByText('Activity and results (1)',{exact:true}).click();
  await expect(page.getByText('Retrieved 3 cited passages.',{exact:true})).toBeVisible();
  expect(state.calls.filter(c=>c.method==='chat.agent')).toHaveLength(1);
  expect(state.failed).toBe(true);
  const download=page.waitForEvent('download');
  await page.getByRole('button',{name:'Download conversation and results'}).click();
  expect((await download).suggestedFilename()).toBe('conversation.json');
});

test('reopening an active conversation restores polling and supports stopping',async({page})=>{
  const state=await chatFixture(page,{completeAfter:100});await page.goto('/');
  await page.getByRole('textbox',{name:'Message',exact:true}).fill('Review saved references');
  await page.getByRole('button',{name:'Send',exact:true}).click();
  await expect(page.getByRole('button',{name:'Stop task'})).toBeVisible();
  await page.reload();
  await expect(page.getByRole('button',{name:'Stop task'})).toBeVisible();
  await page.getByRole('button',{name:'Stop task'}).click();
  await expect(page.getByText('Task cancelled.',{exact:true})).toBeVisible();
  expect(state.calls.some(c=>c.method==='chat.stop')).toBe(true);
  await expect(page.getByRole('textbox',{name:'Message',exact:true})).toBeEnabled();
});

test('chat passes uploaded source IDs to the agent and rejects more than six files',async({page})=>{
  const state=await chatFixture(page);await page.goto('/');
  await page.getByLabel('Attach files',{exact:true}).setInputFiles({name:'notes.txt',mimeType:'text/plain',buffer:Buffer.from('Source fixture')});
  await expect(page.getByText('notes.txt',{exact:true})).toBeVisible();
  await page.getByRole('textbox',{name:'Message',exact:true}).fill('Explain my attachment');
  await page.getByRole('button',{name:'Send',exact:true}).click();
  await expect(page.getByText('Here is the verified result.',{exact:true})).toBeVisible();
  expect(state.calls.find(c=>c.method==='chat.agent')?.params.source_ids).toEqual(['source_attached']);
  const imports=state.calls.filter(c=>c.method==='sources.import').length;
  await page.getByLabel('Attach files',{exact:true}).setInputFiles(Array.from({length:7},(_,i)=>({name:`note-${i}.txt`,mimeType:'text/plain',buffer:Buffer.from('text')})));
  await expect(page.getByRole('alert')).toContainText('up to six');
  expect(state.calls.filter(c=>c.method==='sources.import')).toHaveLength(imports);
});

test('mobile history stays accessible without horizontal overflow',async({page})=>{
  await chatFixture(page,{completeAfter:1});await page.setViewportSize({width:390,height:844});await page.goto('/');
  await page.getByRole('textbox',{name:'Message',exact:true}).fill('Saved conversation');
  await page.getByRole('button',{name:'Send',exact:true}).click();
  await expect(page.getByText('Here is the verified result.',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Chat history'}).click();
  await expect(page.getByRole('complementary',{name:'Saved conversations'})).toBeVisible();
  await page.getByRole('button',{name:'New chat',exact:true}).click();
  await page.getByRole('button',{name:'Chat history'}).click();
  await page.getByRole('button',{name:'Saved conversation',exact:true}).click();
  await expect(page.getByText('Here is the verified result.',{exact:true})).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:test.info().outputPath('mobile-chat.png'),fullPage:true});
});
