import {expect,test} from '@playwright/test';
import {chatFixture} from './chat-fixture';

test('a model click survives a null focus target in the desktop webview',async({page})=>{
 await chatFixture(page);let selected=false;
 await page.route('http://127.0.0.1:8765/rpc',async route=>{
  if(route.request().method()==='OPTIONS')return route.fallback();const {id,method,params}=route.request().postDataJSON();
  if(method==='models.select'){selected=true;return route.fulfill({headers:{'Access-Control-Allow-Origin':'*'},json:{id,result:params}});}
  if(method!=='models.available')return route.fallback();
  return route.fulfill({headers:{'Access-Control-Allow-Origin':'*'},json:{id,result:{active:{provider:'ollama',model:selected?'second':'first'},providers:[{provider:'ollama',label:'Local AI',ready:true,models:['first','second']}]}}});
 });
 await page.goto('/');const trigger=page.getByRole('button',{name:'Choose a model',exact:true});await trigger.click();
 const model=page.getByRole('menuitemradio',{name:'second',exact:true});
 await page.evaluate(()=>{document.activeElement?.dispatchEvent(new FocusEvent('focusout',{bubbles:true,relatedTarget:null}));});
 await expect(model).toBeVisible();await model.click();await expect.poll(()=>selected).toBe(true);await expect(trigger).toContainText('second');
});

test('versioned selection appears before a slow catalog refresh and offers provider effort levels',async({page})=>{
  await chatFixture(page);
  let active={provider:'codex',model:'gpt-6.1-sol',reasoning_effort:''};let selected=false;
  let release=()=>{};const gate=new Promise<void>(resolve=>{release=resolve;});
  const calls:any[]=[];
  const providers=[{provider:'codex',label:'ChatGPT subscription',ready:true,models:['gpt-6.1-sol'],model_options:[{id:'gpt-6.1-sol',label:'GPT-6.1 Sol',effort:{levels:['low','medium','high','xhigh','max','ultra'],default:'low'}}]},
    {provider:'claude_code',label:'Claude subscription',ready:true,models:['claude-opus-4-7','claude-sonnet-4-6'],model_options:[{id:'claude-opus-4-7',label:'Claude Opus 4.7',effort:{levels:['low','medium','high','xhigh','max']}},{id:'claude-sonnet-4-6',label:'Claude Sonnet 4.6',effort:{levels:['low','medium','high','max']}}]}];
  await page.route('http://127.0.0.1:8765/rpc',async route=>{
    if(route.request().method()==='OPTIONS')return route.fallback();
    const {id,method,params}=route.request().postDataJSON();
    if(method==='models.select'){calls.push(params);active={...params,reasoning_effort:params.reasoning_effort||''};selected=true;return route.fulfill({json:{id,result:active},headers:{'Access-Control-Allow-Origin':'*'}});}
    if(method!=='models.available')return route.fallback();
    if(selected)await gate;
    return route.fulfill({json:{id,result:{providers,active}},headers:{'Access-Control-Allow-Origin':'*'}});
  });
  await page.goto('/');const trigger=page.getByRole('button',{name:'Choose a model',exact:true});
  await expect(trigger).toContainText('GPT-6.1 Sol');
  await trigger.click();await page.getByRole('menuitem',{name:'Subscriptions',exact:false}).hover();
  await page.getByRole('menuitem',{name:'Claude subscription',exact:true}).hover();
  await expect(page.getByRole('menuitemradio',{name:'Claude Sonnet 4.6',exact:true})).toContainText('Low · Medium · High · Max');
  await page.getByRole('menuitemradio',{name:'Claude Sonnet 4.6',exact:true}).click();
  await expect(trigger).toContainText('Claude Sonnet 4.6',{timeout:1500});
  await page.getByRole('button',{name:/^Effort:/}).click();
  const effort=page.getByRole('slider',{name:'Reasoning effort',exact:true});
  await expect(effort).toHaveAttribute('max','3');
  const message=await page.getByRole('textbox',{name:'Message',exact:true}).boundingBox();
  const composer=page.locator('form.chat-composer');
  await expect(composer.getByRole('button',{name:'Choose a model',exact:true})).toBeVisible();
  const effortButton=composer.getByRole('button',{name:/^Effort:/});
  await expect(effortButton).toBeVisible();
  const effortBox=(await effortButton.boundingBox())!,composerBox=(await composer.boundingBox())!;
  expect(effortBox.y).toBeGreaterThan(message!.y+message!.height);
  expect(effortBox.y+effortBox.height).toBeLessThan(composerBox.y+composerBox.height);
  release();await effort.focus();await page.keyboard.press('Home');
  await expect.poll(()=>calls.length).toBe(2);await expect(effort).toBeEnabled();await effort.focus();await page.keyboard.press('ArrowRight');
  await expect.poll(()=>calls.length).toBe(3);
  expect(calls[2]).toEqual({provider:'claude_code',model:'claude-sonnet-4-6',reasoning_effort:'medium'});
  await expect(effort).toHaveAttribute('aria-valuetext','Medium');
  await page.keyboard.press('Escape');
  await trigger.click();await page.getByRole('menuitem',{name:'Subscriptions',exact:false}).hover();
  await expect(page.getByRole('menuitemradio',{name:'Claude Sonnet 4.6',exact:true})).toHaveAttribute('aria-checked','true');
});

test('Settings saves the chosen version and its supported effort together',async({page})=>{
  await chatFixture(page);const saves:any[]=[];
  const models=[{id:'claude-opus-4-7',label:'Claude Opus 4.7',effort:{levels:['low','medium','high','xhigh','max']}},
    {id:'claude-sonnet-4-6',label:'Claude Sonnet 4.6',effort:{levels:['low','medium','high','max']}}];
  let saved={provider:'claude_code',model:'claude-opus-4-7',reasoning_effort:'high'};
  await page.route('http://127.0.0.1:8765/rpc',async route=>{
    if(route.request().method()==='OPTIONS')return route.fallback();
    const {id,method,params={}}=route.request().postDataJSON();let result:any;
    if(method==='settings.get')result=saved;
    if(method==='models.discover')result={ok:true,models,detail:'Versioned test catalog'};
    if(method==='settings.save'){saves.push(params);saved=params;result=saved;}
    if(result===undefined)return route.fallback();
    return route.fulfill({json:{id,result},headers:{'Access-Control-Allow-Origin':'*'}});
  });
  await page.goto('/');await page.getByRole('navigation',{name:'Main navigation'}).getByRole('button',{name:'Settings',exact:true}).click();
  await page.getByRole('combobox',{name:'Model',exact:true}).selectOption('claude-sonnet-4-6');
  const effort=page.getByRole('slider',{name:'Reasoning effort',exact:true});
  await expect(page.getByRole('button',{name:'Use provider default effort',exact:true})).toHaveAttribute('aria-pressed','true');
  await expect(page.getByRole('button',{name:'Set effort to Extra high',exact:true})).toHaveCount(0);
  await effort.focus();await page.keyboard.press('Home');await page.keyboard.press('ArrowRight');
  await expect(effort).toHaveAttribute('aria-valuetext','Medium');await page.getByRole('button',{name:'Save',exact:true}).click();
  await expect.poll(()=>saves.length).toBe(1);
  expect(saves[0]).toMatchObject({provider:'claude_code',model:'claude-sonnet-4-6',reasoning_effort:'medium'});
});

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
  await trigger.click();await menu.getByRole('menuitem',{name:'Paid APIs',exact:true}).click();
  await page.getByRole('menuitem',{name:'OpenAI API',exact:true}).focus();await page.keyboard.press('ArrowRight');
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

test('search reaches every provider model and refresh reloads the connected catalog',async({page})=>{
  await chatFixture(page);let refreshed=false;const refreshes:any[]=[];
  const modelIds=Array.from({length:41},(_,index)=>`gemini-catalog-${index+1}`);
  await page.route('http://127.0.0.1:8765/rpc',async route=>{
    if(route.request().method()==='OPTIONS')return route.fallback();
    const {id,method,params={}}=route.request().postDataJSON();
    if(method!=='models.available')return route.fallback();
    if(params.refresh){refreshed=true;refreshes.push(params);}
    const models=[...modelIds,...(refreshed?['gemini-newly-available']:[])];
    return route.fulfill({json:{id,result:{active:{provider:'gemini',model:modelIds[0]},providers:[{provider:'gemini',label:'Gemini API',kind:'api',ready:true,models}]}},headers:{'Access-Control-Allow-Origin':'*'}});
  });
  await page.goto('/');await page.getByRole('button',{name:'Choose a model',exact:true}).click();
  await page.getByRole('menuitem',{name:/Paid APIs/}).click();
  await expect(page.getByRole('menuitemradio')).toHaveCount(41);
  const search=page.getByRole('searchbox',{name:'Search all models',exact:true});
  await search.fill('catalog-41');await expect(page.getByRole('menuitemradio',{name:'gemini-catalog-41',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Refresh available models',exact:true}).click();
  await expect.poll(()=>refreshes.length).toBe(1);await search.fill('newly-available');
  await expect(page.getByRole('menuitemradio',{name:'gemini-newly-available',exact:true})).toBeVisible();
  await search.fill('no-model-matches');await expect(page.getByText('No matching models. Refresh the list or connect the provider in Settings.')).toBeVisible();
});

test('effort slider commits keyboard changes, restores on reload and can use provider default',async({page})=>{
  await chatFixture(page);let active={provider:'codex',model:'gpt-6.1-sol',reasoning_effort:'medium'};const saves:any[]=[];
  await page.route('http://127.0.0.1:8765/rpc',async route=>{
    if(route.request().method()==='OPTIONS')return route.fallback();
    const {id,method,params={}}=route.request().postDataJSON();
    if(!['models.available','models.select'].includes(method))return route.fallback();
    if(method==='models.select'){active={...params};saves.push(params);}
    return route.fulfill({json:{id,result:method==='models.select'?active:{active,providers:[{provider:'codex',label:'ChatGPT subscription',ready:true,models:['gpt-6.1-sol'],model_options:[{id:'gpt-6.1-sol',label:'GPT-6.1 Sol',effort:{levels:['low','medium','high','xhigh','max','ultra'],default:'low'}}]}]}},headers:{'Access-Control-Allow-Origin':'*'}});
  });
  await page.goto('/');await page.getByRole('button',{name:/^Effort:/}).click();const slider=page.getByRole('slider',{name:'Reasoning effort',exact:true});
  await expect(slider).toHaveAttribute('aria-valuetext','Medium');await slider.focus();await page.keyboard.press('End');
  await expect.poll(()=>saves.length).toBe(1);expect(saves[0].reasoning_effort).toBe('ultra');
  await page.reload();await page.getByRole('button',{name:/^Effort:/}).click();await expect(slider).toHaveAttribute('aria-valuetext','Ultra');
  const rail=(await slider.boundingBox())!;await page.mouse.click(rail.x+20+(rail.width-40)*.8,rail.y+rail.height/2);
  await expect.poll(()=>saves.length).toBe(2);expect(saves[1].reasoning_effort).toBe('max');await expect(slider).toHaveAttribute('aria-valuetext','Max');
  await page.locator('.model-effort-card').screenshot({path:test.info().outputPath('effort-card-reference.png')});
  await page.getByRole('button',{name:'Use provider default effort',exact:true}).click();
  await expect.poll(()=>saves.length).toBe(3);expect(saves[2].reasoning_effort).toBe('');await expect(slider).toHaveAttribute('aria-valuetext','Default (Low)');
  await page.keyboard.press('Escape');
  for(const width of [1440,760,390]){
    await page.setViewportSize({width,height:960});const composer=page.locator('form.chat-composer');const effortButton=composer.getByRole('button',{name:/^Effort:/});await expect(effortButton).toBeInViewport();
    const box=(await effortButton.boundingBox())!;const input=(await composer.getByRole('textbox',{name:'Message',exact:true}).boundingBox())!;const frame=(await composer.boundingBox())!;
    const model=(await composer.getByRole('button',{name:'Choose a model',exact:true}).boundingBox())!;
    expect(box.y).toBeGreaterThan(input.y+input.height);
    for(const control of [box,model]){
      expect(control.x).toBeGreaterThan(frame.x);expect(control.x+control.width).toBeLessThan(frame.x+frame.width);
      expect(control.y).toBeGreaterThan(frame.y);expect(control.y+control.height).toBeLessThan(frame.y+frame.height);
    }
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await composer.screenshot({path:test.info().outputPath(`composer-controls-${width}.png`)});
  }
});

test('opening an offline local selection immediately shows connected provider models',async({page})=>{
  await chatFixture(page);
  await page.route('http://127.0.0.1:8765/rpc',async route=>{
    if(route.request().method()==='OPTIONS')return route.fallback();
    const {id,method}=route.request().postDataJSON();if(method!=='models.available')return route.fallback();
    return route.fulfill({json:{id,result:{active:{provider:'ollama',model:'offline-local'},providers:[
      {provider:'ollama',label:'Local model',ready:false,needs:'Ollama is offline.',models:[]},
      {provider:'codex',label:'ChatGPT subscription',ready:true,models:['gpt-6.1-sol'],model_options:[{id:'gpt-6.1-sol',label:'GPT-6.1 Sol'}]},
    ]}},headers:{'Access-Control-Allow-Origin':'*'}});
  });
  await page.goto('/');const choose=page.getByRole('button',{name:'Choose a model',exact:true});await expect(choose).toContainText('offline-local');await choose.click();
  await expect(page.getByRole('menu',{name:'ChatGPT subscription models',exact:true})).toBeVisible();
  await expect(page.getByRole('menuitemradio',{name:'GPT-6.1 Sol',exact:true})).toBeVisible();
});

test('a failed model catalog shows a retryable error instead of a blank menu',async({page})=>{
  await chatFixture(page);let attempts=0;
  await page.route('http://127.0.0.1:8765/rpc',async route=>{
    if(route.request().method()==='OPTIONS')return route.fallback();
    const {id,method}=route.request().postDataJSON();if(method!=='models.available')return route.fallback();
    attempts++;
    const payload=attempts===1?{id,error:{message:'Fixture discovery unavailable'}}:{id,result:{active:{provider:'codex',model:'gpt-6.1-sol'},providers:[{provider:'codex',label:'ChatGPT subscription',ready:true,models:['gpt-6.1-sol']} ]}};
    return route.fulfill({json:payload,headers:{'Access-Control-Allow-Origin':'*'}});
  });
  await page.goto('/');await page.getByRole('button',{name:'Choose a model',exact:true}).click();
  await expect(page.getByRole('alert')).toContainText('Could not load your provider models');
  await page.getByRole('button',{name:'Refresh available models',exact:true}).click();
  await expect(page.getByRole('menuitemradio',{name:'gpt-6.1-sol',exact:true})).toBeVisible();await expect(page.getByRole('alert')).toHaveCount(0);
});

test('model discovery still works when chat history fails to load',async({page})=>{
  await chatFixture(page);
  await page.route('http://127.0.0.1:8765/rpc',async route=>{
    if(route.request().method()==='OPTIONS')return route.fallback();
    const {id,method}=route.request().postDataJSON();
    if(method==='chat.list')return route.fulfill({json:{id,error:{message:'Fixture history unavailable'}},headers:{'Access-Control-Allow-Origin':'*'}});
    if(method!=='models.available')return route.fallback();
    return route.fulfill({json:{id,result:{active:{provider:'codex',model:'gpt-6.1-sol'},providers:[{provider:'codex',label:'ChatGPT subscription',ready:true,models:['gpt-6.1-sol']}]}},headers:{'Access-Control-Allow-Origin':'*'}});
  });
  await page.goto('/');await page.getByRole('button',{name:'Choose a model',exact:true}).click();
  await expect(page.getByRole('menuitemradio',{name:'gpt-6.1-sol',exact:true})).toBeVisible();
});

test('each provider opens its own models instead of expanding every catalog',async({page})=>{
  await chatFixture(page);
  await page.route('http://127.0.0.1:8765/rpc',async route=>{
    if(route.request().method()==='OPTIONS')return route.fallback();
    const {id,method}=route.request().postDataJSON();if(method!=='models.available')return route.fallback();
    return route.fulfill({json:{id,result:{active:{provider:'codex',model:'gpt-6.1-sol'},providers:[
      {provider:'codex',label:'ChatGPT subscription',ready:true,models:['gpt-6.1-sol']},
      {provider:'claude_code',label:'Claude subscription',ready:true,models:['claude-fable-5-2','claude-opus-5-5'],model_options:[{id:'claude-fable-5-2',label:'Claude Fable 5.2'},{id:'claude-opus-5-5',label:'Claude Opus 5.5'}]},
      {provider:'gemini_cli',label:'Google AI subscription',ready:true,models:Array.from({length:80},(_,i)=>`gemini-model-${i}`)},
    ]}},headers:{'Access-Control-Allow-Origin':'*'}});
  });
  await page.goto('/');await page.getByRole('button',{name:'Choose a model',exact:true}).click();
  await expect(page.getByRole('menuitemradio')).toHaveCount(1);await expect(page.getByRole('menuitemradio',{name:'Claude Fable 5.2',exact:true})).toHaveCount(0);
  await page.getByRole('menuitem',{name:'Claude subscription',exact:true}).hover();await expect(page.getByRole('menuitemradio')).toHaveCount(2);
  await expect(page.getByRole('menuitemradio',{name:'Claude Fable 5.2',exact:true})).toBeVisible();
  await expect(page.getByRole('menuitemradio',{name:'gpt-6.1-sol',exact:true})).toHaveCount(0);
  await page.getByRole('menuitem',{name:'Google AI subscription',exact:true}).click();await expect(page.getByRole('menuitemradio')).toHaveCount(80);
  await page.getByRole('searchbox',{name:'Search all models',exact:true}).fill('Fable 5.2');await expect(page.getByRole('menuitemradio')).toHaveCount(1);
});

test('long local names have their own row and explain why effort is automatic',async({page})=>{
  await chatFixture(page);const model='zfujicute/OmniCoder-Qwen3.5-9B-Claude-4.6-Opus-Uncensored-v2-GGUF:latest';
  await page.route('http://127.0.0.1:8765/rpc',async route=>{
    if(route.request().method()==='OPTIONS')return route.fallback();
    const {id,method}=route.request().postDataJSON();if(method!=='models.available')return route.fallback();
    return route.fulfill({json:{id,result:{active:{provider:'ollama',model},providers:[{provider:'ollama',label:'Local model',ready:false,needs:'Start Ollama.',models:[model],model_options:[{id:model,label:model,effort:{levels:[]}}]}]}},headers:{'Access-Control-Allow-Origin':'*'}});
  });
  await page.goto('/');const composer=page.locator('form.chat-composer');const choose=composer.getByRole('button',{name:'Choose a model',exact:true});
  await expect(choose).toHaveAttribute('title',`Local model · ${model}`);
  await expect(choose).toContainText('OmniCoder · 9B');await expect(choose).not.toContainText('Uncensored-v2-GGUF');
  await expect(composer.locator('.model-effort-card')).toHaveCount(0);
  await expect(composer.getByRole('slider')).toHaveCount(0);await expect(choose).not.toContainText('zfujicute/');
  await composer.getByRole('button',{name:'Effort: Automatic',exact:true}).click();await expect(page.getByText('This connection does not expose adjustable effort levels.',{exact:true})).toBeVisible();await page.keyboard.press('Escape');
  for(const width of [1440,760,390]){
    await page.setViewportSize({width,height:960});expect((await composer.boundingBox())!.height).toBeLessThan(190);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    await composer.screenshot({path:test.info().outputPath(`long-model-${width}.png`)});
  }
});

test('the explicit effort dropdown saves the selected level alongside the pill slider',async({page})=>{
  await chatFixture(page);let active={provider:'claude_code',model:'claude-fable-5-2',reasoning_effort:''};const saves:any[]=[];
  await page.route('http://127.0.0.1:8765/rpc',async route=>{
    if(route.request().method()==='OPTIONS')return route.fallback();const {id,method,params={}}=route.request().postDataJSON();
    if(!['models.available','models.select'].includes(method))return route.fallback();if(method==='models.select'){active=params;saves.push(params);}
    return route.fulfill({json:{id,result:method==='models.select'?active:{active,providers:[{provider:'claude_code',label:'Claude subscription',ready:true,models:[active.model],model_options:[{id:active.model,label:'Claude Fable 5.2',effort:{levels:['low','medium','high','xhigh','max'],default:'medium'}}]}]}},headers:{'Access-Control-Allow-Origin':'*'}});
  });
  await page.goto('/');await page.getByRole('button',{name:/^Effort:/}).click();const effort=page.getByRole('combobox',{name:'Effort level',exact:true});await expect(effort).toBeVisible();await effort.selectOption('high');
  await expect.poll(()=>saves.length).toBe(1);expect(saves[0]).toMatchObject({model:'claude-fable-5-2',reasoning_effort:'high'});
  await expect(page.getByRole('slider',{name:'Reasoning effort',exact:true})).toHaveAttribute('aria-valuetext','High');
  await expect(effort.locator('option[value=ultra]')).toHaveCount(0);await page.reload();await page.getByRole('button',{name:'Effort: High',exact:true}).click();await expect(effort).toHaveValue('high');
});
