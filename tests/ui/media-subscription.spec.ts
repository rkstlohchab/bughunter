import {expect, test} from '@playwright/test';

async function mediaFixture(page: import('@playwright/test').Page, hosted=false) {
  const calls:{method:string;params:any}[]=[];
  let config={enabled:true,vision_provider:hosted?'codex_subscription':'local',vision_model:'custom-vision:latest',codex_vision_model:'gpt-6.1-sol',codex_vision_consent:hosted,frame_interval:12,max_frames:0,adaptive_frames:true,scene_threshold:0.3,vision_enabled:true,speech_enabled:true};
  await page.route('http://127.0.0.1:8765/rpc',async route=>{
    const headers={'Access-Control-Allow-Origin':'*','Access-Control-Allow-Headers':'Content-Type, Authorization','Access-Control-Allow-Methods':'POST, OPTIONS'};
    if(route.request().method()==='OPTIONS')return route.fulfill({status:204,headers});
    const {id,method,params={}}=route.request().postDataJSON();calls.push({method,params});
    let result:any={};
    if(method==='media.status')result={config,codex_available:true,ollama_bin:true,ollama_running:true,installed_models:['custom-vision:latest'],vision_ready:true,ffmpeg:true,whisper:true,speech_model:true,setup:{state:'idle',detail:'',target:null}};
    if(method==='media.models')result={ready:true,models:[{id:'gpt-6.1-sol',label:'GPT-6.1 Sol'},{id:'subscription-vision',label:'Available subscription model'}],detail:'Subscription connected.'};
    if(method==='media.configure'){config={...config,...params};result=config;}
    if(method.endsWith('.list'))result=[];
    if(method==='models.available')result={providers:[],active:{provider:'ollama',model:'local-chat'}};
    await route.fulfill({headers,json:{id,result}});
  });
  // Render the component in isolation, independent of Settings navigation.
  await page.route('**/media-ui-fixture',route=>route.fulfill({contentType:'text/html',body:`<html><body><div id="fixture"></div><script type="module">
    import React from '/node_modules/.vite/deps/react.js';
    import {createRoot} from '/node_modules/.vite/deps/react-dom_client.js';
    import MediaAI from '/src/MediaAI.tsx';
    createRoot(document.getElementById('fixture')).render(React.createElement(MediaAI,{sources:[],mode:'settings'}));
  </script></body></html>`}));
  await page.goto('/media-ui-fixture');
  return calls;
}

test('subscription frame analysis requires explicit consent and preserves the local model',async({page})=>{
  const calls=await mediaFixture(page);
  await expect(page.getByRole('combobox',{name:'Vision model',exact:true})).toHaveValue('custom-vision:latest');
  expect(calls.filter(c=>c.method==='media.models')).toHaveLength(0);
  await page.getByRole('combobox',{name:'Frame analysis backend'}).selectOption('codex_subscription');
  await expect(page.getByRole('option',{name:'Available subscription model'})).toHaveCount(1);
  await expect(page.getByText('Selected frames and nearby narration are sent to ChatGPT through Codex.',{exact:false})).toBeVisible();
  const consent=page.getByRole('checkbox',{name:'I consent to sending selected frames',exact:false});
  await expect(consent).not.toBeChecked();
  await expect(page.getByRole('button',{name:'Save video settings'})).toBeDisabled();
  await consent.check();
  await page.getByRole('combobox',{name:'ChatGPT vision model'}).selectOption('subscription-vision');
  await page.getByRole('button',{name:'Save video settings'}).click();
  await expect.poll(()=>calls.filter(c=>c.method==='media.configure').length).toBe(1);
  const saved=calls.find(c=>c.method==='media.configure')!.params;
  expect(saved).toMatchObject({vision_provider:'codex_subscription',codex_vision_model:'subscription-vision',codex_vision_consent:true,vision_model:'custom-vision:latest'});
  expect(calls.some(c=>c.method==='settings.save')).toBe(false);
  await page.getByRole('combobox',{name:'Frame analysis backend'}).selectOption('local');
  await expect(page.getByRole('combobox',{name:'Vision model',exact:true})).toHaveValue('custom-vision:latest');
});

test('saved subscription consent loads and models refresh only on load or Recheck',async({page})=>{
  const calls=await mediaFixture(page,true);
  await expect(page.getByRole('checkbox',{name:'I consent to sending selected frames',exact:false})).toBeChecked();
  await expect.poll(()=>calls.filter(c=>c.method==='media.models').length).toBe(1);
  await expect.poll(()=>calls.filter(c=>c.method==='media.status').length,{timeout:6000}).toBeGreaterThan(1);
  expect(calls.filter(c=>c.method==='media.models')).toHaveLength(1);
  await page.getByRole('button',{name:'Recheck',exact:true}).click();
  await expect.poll(()=>calls.filter(c=>c.method==='media.models').length).toBe(2);
});
