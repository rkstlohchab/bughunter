import {expect, test} from '@playwright/test';
import {chatFixture} from './chat-fixture';

async function connectionFixture(page: import('@playwright/test').Page) {
  const state = await chatFixture(page);
  const saved: any[] = [];
  await page.route('http://127.0.0.1:8765/rpc', async route => {
    if (route.request().method() === 'OPTIONS') return route.fallback();
    const {id, method, params = {}} = route.request().postDataJSON();
    let result: any;
    if (method === 'settings.connections') result = {openai_compatible:{provider:'openai_compatible', model:'custom-loaded', api_base_url:'https://fixture.example/v1'}};
    else if (method === 'settings.credentials') result = {openai:true, anthropic:true, deepseek:true, openai_compatible:true, remote_ollama:true};
    else if (method === 'models.discover') result = {ok:true, models:[{id:`${params.provider}-one`,label:'First available model'},{id:`${params.provider}-two`,label:'Second available model'}],detail:'2 models available.'};
    else if (method === 'settings.save') {saved.push(params); result=params;}
    else return route.fallback();
    await route.fulfill({headers:{'Access-Control-Allow-Origin':'*'},json:{id,result}});
  });
  return {state,saved};
}

test('every connection offers a model select and ChatGPT uses subscription setup', async ({page}) => {
  const {saved} = await connectionFixture(page);
  await page.goto('/');
  await page.getByRole('navigation',{name:'Main navigation'}).getByRole('button',{name:'Settings',exact:true}).click();
  const group = page.getByRole('group',{name:'Provider',exact:true});
  const cases = [
    ['Subscription', ['ChatGPT subscription','Claude subscription']],
    ['Paid APIs', ['OpenAI API','Anthropic API','DeepSeek']],
    ['My own models', ['Ollama on this Mac','Kaggle / custom AI','Ollama on another computer','CrucibleLLM']],
  ] as const;
  for (const [category,names] of cases) {
    await page.getByRole('button',{name:category,exact:true}).click();
    for (const name of names) {
      await group.getByRole('button',{name,exact:false}).click();
      await expect(page.getByRole('combobox',{name:'Model',exact:true})).toBeVisible();
      await expect(page.getByRole('option',{name:'Second available model'})).toHaveCount(1);
      await page.getByRole('combobox',{name:'Model',exact:true}).selectOption({label:'Second available model'});
      await expect(page.getByRole('textbox',{name:'Custom model identifier',exact:true})).toHaveCount(0);
    }
  }
  await page.getByRole('button',{name:'Subscription',exact:true}).click();
  await group.getByRole('button',{name:'ChatGPT subscription',exact:false}).click();
  await expect(page.getByText('Connection key',{exact:true})).toHaveCount(0);
  await expect(page.getByRole('textbox',{name:'Server address',exact:true})).toHaveCount(0);
  await page.getByRole('combobox',{name:'Model',exact:true}).selectOption('codex-two');
  await page.getByRole('button',{name:'Save',exact:true}).click();
  await expect.poll(() => saved.length).toBe(1);
  expect(saved[0].provider).toBe('codex');expect(saved[0].model).toBe('codex-two');
  await page.screenshot({path:test.info().outputPath('chatgpt-settings.png'),fullPage:true});
});

test('custom identifiers remain optional and saved endpoints survive provider changes',async({page})=>{
  await connectionFixture(page);await page.goto('/');
  await page.getByRole('navigation',{name:'Main navigation'}).getByRole('button',{name:'Settings',exact:true}).click();
  await page.getByRole('button',{name:'My own models',exact:true}).click();
  await page.getByRole('button',{name:'Kaggle / custom AI',exact:false}).click();
  await expect(page.getByRole('textbox',{name:'Server address',exact:true})).toHaveValue('https://fixture.example/v1');
  await page.getByRole('combobox',{name:'Model',exact:true}).selectOption('__custom__');
  await page.getByRole('textbox',{name:'Custom model identifier',exact:true}).fill('custom-new');
  await expect(page.getByRole('button',{name:'Save',exact:true})).toBeEnabled();
});

test('failed Full Disk Access probe does not claim a denied grant or show process dropdown',async({page})=>{
  await chatFixture(page);
  await page.addInitScript(()=>{
    (window as any).__TAURI_INTERNALS__={invoke:async(cmd:string,args:any)=>{
      if(cmd==='mac_permission_status')return {full_disk:null,screen_recording:true,accessibility:true,process:'/debug/path'};
      if(cmd==='rpc')return (await (await fetch('http://127.0.0.1:8765/rpc',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:1,...args})})).json()).result;
      return {};
    }};
  });
  await page.goto('/');
  await page.getByRole('navigation',{name:'Main navigation'}).getByRole('button',{name:'Settings',exact:true}).click();
  await page.getByRole('tab',{name:'Computer & apps',exact:true}).click();
  const panel=page.getByLabel('Mac permissions');
  await expect(panel.getByText('Granted',{exact:true})).toHaveCount(2);
  await expect(panel.getByText('Check in macOS Settings',{exact:true})).toBeVisible();
  await expect(panel.getByText('Not granted to this app',{exact:true})).toHaveCount(0);
  await expect(page.getByText('App being checked',{exact:true})).toHaveCount(0);
});
