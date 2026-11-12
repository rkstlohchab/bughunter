import {test,expect} from '@playwright/test';
import {chatFixture} from './chat-fixture';
import {openTool} from './navigation';

test('chat has three primary destinations and one top-bar tools menu',async({page})=>{
  await chatFixture(page);await page.goto('/');
  const nav=page.getByRole('navigation',{name:'Main navigation'});
  await expect(nav.getByRole('button')).toHaveCount(3);
  await expect(page.getByRole('button',{name:'Tools',exact:true})).toHaveCount(1);
  for(const name of ['Chat','Library','Settings']) await expect(nav.getByRole('button',{name,exact:true})).toBeVisible();
  await expect(page.getByRole('heading',{name:'What should I do?'})).toBeVisible();
  await page.screenshot({path:test.info().outputPath('chat-home.png'),fullPage:true});
  await openTool(page,'Tasks');
  await expect(page.getByRole('heading',{name:'Your tasks'})).toBeVisible();
});

for(const prompt of ['Run app checks','Check my knowledge base']) test(`${prompt} runs locally and saves results without a model call`,async({page})=>{
  const state=await chatFixture(page);await page.goto('/');
  await page.getByRole('textbox',{name:'Message',exact:true}).fill(prompt);
  await page.getByRole('button',{name:'Send',exact:true}).click();
  await expect(page.getByText('Local checks recorded.',{exact:true})).toBeVisible();
  await expect(page.getByRole('region',{name:'Task activity'})).toContainText('completed');
  expect(state.calls.filter(c=>c.method==='chat.autopilot')).toHaveLength(1);
  expect(state.calls.some(c=>c.method==='chat.agent')).toBe(false);
});
