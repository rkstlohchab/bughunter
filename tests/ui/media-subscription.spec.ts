import {expect, test} from '@playwright/test';
import {chatFixture} from './chat-fixture';
import {analysisProviders,localProvider,pickAnalysisModel} from './analysis-fixture';

async function mediaFixture(page: import('@playwright/test').Page, hosted=false) {
  await chatFixture(page);
  const calls:{method:string;params:any}[]=[];
  let config={enabled:true,vision_provider:hosted?'codex_subscription':'local',vision_model:'custom-vision:latest',codex_vision_model:'gpt-6.1-sol',codex_vision_consent:hosted,frame_interval:12,max_frames:0,adaptive_frames:true,scene_threshold:0.3,vision_enabled:true,speech_enabled:true};
  await page.route('http://127.0.0.1:8765/rpc',async route=>{
    const headers={'Access-Control-Allow-Origin':'*','Access-Control-Allow-Headers':'Content-Type, Authorization','Access-Control-Allow-Methods':'POST, OPTIONS'};
    if(route.request().method()==='OPTIONS')return route.fulfill({status:204,headers});
    const {id,method,params={}}=route.request().postDataJSON();calls.push({method,params});
    let result:any;
    if(method==='models.analysis')result={providers:[...analysisProviders,localProvider([{id:'custom-vision:latest',name:'Custom installed vision',roles:['vision'],installed:true}])]};
    if(method==='media.catalog')result={models:[{id:'custom-vision:latest',name:'Custom installed vision',roles:['vision'],installed:true}]};
    if(method==='media.status')result={config,codex_available:true,ollama_bin:true,ollama_running:true,installed_models:['custom-vision:latest'],vision_ready:true,ffmpeg:true,whisper:true,speech_model:true,setup:{state:'idle',detail:'',target:null}};
    if(method==='media.models')result={ready:true,models:[{id:'gpt-6.1-sol',label:'GPT-6.1 Sol'},{id:'subscription-vision',label:'Available subscription model'}],detail:'Subscription connected.'};
    if(method==='media.configure'){config={...config,...params};result=config;}
    if(result===undefined)return route.fallback();
    await route.fulfill({headers,json:{id,result}});
  });
  await page.goto('/');
  await page.getByRole('navigation',{name:'Main navigation'}).getByRole('button',{name:'Settings',exact:true}).click();await page.getByRole('tab',{name:'Video & learning',exact:true}).click();
  return calls;
}

test('subscription frame analysis requires explicit consent and preserves the local model',async({page})=>{
  const calls=await mediaFixture(page);
  await expect(page.getByRole('button',{name:'Choose analysis model',exact:true})).toContainText('Custom installed vision');
  expect(calls.filter(c=>c.method==='media.models')).toHaveLength(0);
  await pickAnalysisModel(page,'Available subscription model');
  await expect(page.getByText('Selected text and sampled frames are shared with ChatGPT subscription.',{exact:false})).toBeVisible();
  const consent=page.getByRole('checkbox',{name:'I consent to sending selected frames',exact:false});
  await expect(consent).not.toBeChecked();
  await expect(page.getByRole('button',{name:'Save video settings'})).toBeDisabled();
  await consent.check();
  await page.getByRole('button',{name:'Save video settings'}).click();
  await expect.poll(()=>calls.filter(c=>c.method==='media.configure').length).toBe(1);
  const saved=calls.find(c=>c.method==='media.configure')!.params;
  expect(saved).toMatchObject({vision_provider:'codex_subscription',codex_vision_model:'subscription-vision',codex_vision_consent:true,vision_model:'custom-vision:latest'});
  expect(calls.some(c=>c.method==='settings.save')).toBe(false);
  await pickAnalysisModel(page,'Custom installed vision','My own models');
  await expect(page.getByRole('button',{name:'Choose analysis model',exact:true})).toContainText('Custom installed vision');
});

test('saved subscription consent loads and models refresh only on load or Recheck',async({page})=>{
  const calls=await mediaFixture(page,true);
  await expect(page.getByRole('checkbox',{name:'I consent to sending selected frames',exact:false})).toBeChecked();
  await expect.poll(()=>calls.filter(c=>c.method==='media.models').length).toBe(1);
  await expect.poll(()=>calls.filter(c=>c.method==='media.status').length,{timeout:6000}).toBeGreaterThan(1);
  expect(calls.filter(c=>c.method==='media.models')).toHaveLength(1);
  await page.locator('.media-ai').getByRole('button',{name:'Recheck',exact:true}).click();
  await expect.poll(()=>calls.filter(c=>c.method==='media.models').length).toBe(2);
  await page.screenshot({path:'artifacts/complete-learning-2026-11-12/ui/settings-subscription.png',fullPage:true});
});

test('model controls stay disabled until the pending save completes',async({page})=>{
  const calls=await mediaFixture(page);
  let release!:()=>void;
  let configuring=false;
  const responseGate=new Promise<void>(resolve=>{release=resolve;});
  await page.route('http://127.0.0.1:8765/rpc',async route=>{
    if(route.request().method()==='OPTIONS')return route.fallback();
    const request=route.request().postDataJSON();
    if(request.method!=='media.configure')return route.fallback();
    configuring=true;
    await responseGate;
    await route.fallback();
  });
  await pickAnalysisModel(page,'GPT-6.1 Sol');
  await page.getByRole('checkbox',{name:'I consent to sending selected frames',exact:false}).check();
  await page.getByRole('button',{name:'Save video settings'}).click();
  await expect.poll(()=>configuring).toBe(true);
  await expect(page.getByRole('button',{name:'Choose analysis model',exact:true})).toBeDisabled();
  release();
  await expect.poll(()=>calls.filter(call=>call.method==='media.configure').length).toBe(1);
  await expect(page.getByRole('button',{name:'Save video settings'})).toBeEnabled();
  await expect(page.getByRole('button',{name:'Choose analysis model',exact:true})).toBeEnabled();
  await expect(page.getByRole('button',{name:'Choose analysis model',exact:true})).toContainText('GPT-6.1 Sol');
});
