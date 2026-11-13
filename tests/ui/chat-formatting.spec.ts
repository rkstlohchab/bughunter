import {expect,test} from '@playwright/test';
import {chatFixture} from './chat-fixture';

const answer='## Observed result\n\n**Bold** and *italic*, ~~old~~, `inline_code` and ==highlighted==.\n\n- First item\n- Second item\n\n> A quoted passage.\n\n```python\ndef hello():\n    return "world"\n```\n\n| Tool | State |\n| --- | --- |\n| Reader | Ready |\n\n$E=mc^2$\n\n<script>window.chatInjected=true</script>\n\n[Unsafe](javascript:alert(1))';

async function savedChat(page:any,long=false){
 const state=await chatFixture(page);
 const messages:any[]=[];
 for(let i=0;i<(long?8:1);i++)messages.push({role:'user',content:`Prompt number ${i+1}`},{role:'assistant',content:long?`Reply ${i+1}\n\n`+('A detailed explanation of the observed result.\n\n').repeat(20):answer});
 state.chats=[{id:'chat_formatted',title:'Formatting and scroll fixture',messages}];
 await page.addInitScript(()=>localStorage.setItem('bughunter.chat.library','chat_formatted'));
 await page.goto('/');await expect(page.locator('.conversation-transcript')).toBeVisible();return state;
}

test('renders Markdown, highlighted code, tables, math and safe text',async({page,context})=>{
 await context.grantPermissions(['clipboard-read','clipboard-write']);
 await savedChat(page);
 const response=page.locator('.conversation-message.assistant');
 await expect(response.getByRole('heading',{name:'Observed result'})).toBeVisible();
 await expect(response.locator('strong')).toContainText('Bold');
 await expect(response.locator('mark')).toHaveText('highlighted');
 await expect(response.locator('table')).toContainText('Reader');
 await expect(response.locator('.katex')).toHaveCount(1);
 await expect(response.locator('pre code .hljs-keyword').first()).toHaveText('def');
 await response.getByRole('button',{name:'Copy code',exact:true}).click();
 expect(await page.evaluate(()=>navigator.clipboard.readText())).toBe('def hello():\n    return "world"\n');
 expect(await page.evaluate(()=>(window as any).chatInjected)).toBeUndefined();
 expect(await response.getByRole('link',{name:'Unsafe',exact:true}).getAttribute('href')).toBe('');
 await expect(page.locator('.conversation-author')).toHaveText(['You','Assistant']);
 await expect(page.locator('.conversation-avatar')).toHaveCount(0);
});

for(const viewport of [{width:1380,height:900},{width:760,height:560},{width:390,height:780}]){
 test(`conversation scroll and prompt dashes keep controls visible at ${viewport.width}×${viewport.height}`,async({page})=>{
  await page.setViewportSize(viewport);await savedChat(page,true);
  const top=page.locator('.topbar');const composer=page.locator('.chat-compose');const stream=page.locator('.conversation-scroll');
  await expect(page.getByRole('button',{name:'Stop all',exact:true})).toBeInViewport();
  await expect(page.locator('.chat-model-controls .model-picker-btn')).toBeInViewport();
  await expect(page.getByRole('textbox',{name:'Message',exact:true})).toBeInViewport();
  const original={top:await top.boundingBox(),composer:await composer.boundingBox()};
  const nav=page.getByRole('navigation',{name:'Jump between your prompts'});
  await expect(nav.getByRole('button')).toHaveCount(8);
  await nav.getByRole('button',{name:/Go to prompt 1:/}).click();
  await expect.poll(()=>stream.evaluate(element=>element.scrollTop)).toBeLessThan(40);
  await nav.getByRole('button',{name:/Go to prompt 6:/}).click();
  await expect.poll(()=>nav.locator('[aria-current="step"]').getAttribute('aria-label')).toMatch(/prompt 6:/);
  await stream.hover();await page.mouse.wheel(0,700);
  await expect(page.getByRole('button',{name:'Stop all',exact:true})).toBeInViewport();
  expect(await page.evaluate(()=>window.scrollY)).toBe(0);
  expect((await top.boundingBox())!.y).toBeCloseTo(original.top!.y,0);
  expect((await composer.boundingBox())!.y).toBeCloseTo(original.composer!.y,0);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
 });
}

test('a finalized reply has no visible completion summary or repeated assistant event',async({page})=>{
 await chatFixture(page,{completeAfter:1});await page.goto('/');
 await page.getByRole('textbox',{name:'Message',exact:true}).fill('Explain this document');await page.getByRole('button',{name:'Send',exact:true}).click();
 await expect(page.getByText('Here is the verified result.',{exact:true})).toBeVisible();
 await expect(page.locator('.conversation-message.assistant')).toHaveCount(1);
 await expect(page.getByText('Checked the saved evidence.',{exact:true})).toHaveCount(0);
 await expect(page.locator('.agent-live details')).toHaveCount(0);
});
