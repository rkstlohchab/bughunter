import {expect,test} from '@playwright/test';
import {chatFixture} from './chat-fixture';

test('three model categories expose provider models on hover, click and keyboard',async({page})=>{
  await chatFixture(page);
  let active={provider:'ollama',model:'local-one'};
  await page.route('http://127.0.0.1:8765/rpc',async route=>{
    if(route.request().method()==='OPTIONS')return route.fallback();
    const {id,method,params}=route.request().postDataJSON();
    if(!['models.available','models.select'].includes(method))return route.fallback();
    if(method==='models.select')active=params;
    await route.fulfill({headers:{'Access-Control-Allow-Origin':'*'},json:{id,result:method==='models.select'?{}:{active,providers:[
      {provider:'codex',label:'ChatGPT subscription',ready:true,models:['chatgpt-one']},
      {provider:'claude_code',label:'Claude subscription',ready:true,models:['sonnet']},
      {provider:'openai',label:'OpenAI API',ready:true,models:['api-one']},
      {provider:'ollama',label:'Local model',ready:true,models:['local-one']},
    ]}}});
  });
  await page.goto('/');const trigger=page.getByRole('button',{name:'Choose a model',exact:true});
  await trigger.click();
  const menu=page.getByRole('menu',{name:'AI connection types',exact:true});
  await expect(menu.getByRole('menuitem')).toHaveCount(3);
  await menu.getByRole('menuitem',{name:'Subscriptions',exact:false}).hover();
  await expect(page.getByRole('menuitemradio',{name:'chatgpt-one',exact:true})).toBeVisible();
  await page.getByRole('menuitemradio',{name:'chatgpt-one',exact:true}).click();
  await expect(trigger).toHaveAttribute('title','ChatGPT subscription · chatgpt-one');
  await trigger.click();await page.keyboard.press('ArrowDown');await page.keyboard.press('ArrowRight');
  await expect(page.getByRole('menuitemradio',{name:'api-one',exact:true})).toBeVisible();
  await page.getByRole('menuitemradio',{name:'api-one',exact:true}).click();
  await expect(trigger).toHaveAttribute('title','OpenAI API · api-one');
  await trigger.click();await menu.getByRole('menuitem',{name:'My own models',exact:false}).click();
  await page.getByRole('menuitemradio',{name:'local-one',exact:true}).click();
  await expect(trigger).toHaveAttribute('title','Local model · local-one');
});

test('top bar actions have visible labels and Stop all remains at the right',async({page})=>{
  await chatFixture(page);await page.goto('/');
  for(const width of [1380,760,390]){
    await page.setViewportSize({width,height:900});
    const header=page.locator('header.topbar');
    const stop=header.getByRole('button',{name:'Stop all',exact:true});
    await expect(stop).toBeInViewport();
    await expect(header.getByRole('button',{name:'Tools',exact:true})).toHaveText('Tools');
    await expect(header.getByRole('button',{name:'Refresh workspace',exact:true})).toHaveText('Refresh');
    const stopBox=await stop.boundingBox();const refreshBox=await header.getByRole('button',{name:'Refresh workspace',exact:true}).boundingBox();
    expect(stopBox!.x).toBeGreaterThan(refreshBox!.x);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await page.screenshot({path:test.info().outputPath(`navigation-${width}.png`),fullPage:true});
  }
});
