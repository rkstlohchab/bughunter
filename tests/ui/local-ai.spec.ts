import {expect, test} from '@playwright/test';
import {chatFixture} from './chat-fixture';
import {analysisProviders,localProvider,pickAnalysisModel} from './analysis-fixture';

async function localFixture(page: import('@playwright/test').Page) {
  const state=await chatFixture(page);
  let config={enabled:true,vision_provider:'local',vision_model:'qwen3-vl:2b',codex_vision_model:'gpt-6.1-sol',codex_vision_consent:false,frame_interval:12,max_frames:0,adaptive_frames:true,scene_threshold:0.3,vision_enabled:true,speech_enabled:true};
  const models=[{id:'qwen3-vl:2b',name:'Qwen3-VL 2B',roles:['vision','chat'],installed:true},
    ...[2,4,8].map(size=>({id:`huihui_ai/qwen3-vl-abliterated:${size}b-instruct`,name:`Qwen3-VL Abliterated ${size}B (uncensored)`,roles:['vision','chat'],installed:false,download_bytes:size*1024**3,memory_note:size===8?'16 GB or more recommended.':'One model at a time.'})),
    {id:'chat-only:latest',name:'Installed text model',roles:['chat'],installed:true}];
  const calls:{method:string;params:any}[]=[];
  let setup:any={state:'idle',detail:'No installation underway'};
  let testJob:any={state:'idle'};
  let verification:any=null;
  await page.route('http://127.0.0.1:8765/rpc',async route=>{
    if(route.request().method()==='OPTIONS')return route.fallback();
    const {id,method,params={}}=route.request().postDataJSON();calls.push({method,params});
    let result:any;
    if(method==='models.analysis')result={providers:[...analysisProviders,localProvider(models)]};
    else if(method==='media.catalog')result={models,disk:{free_bytes:50*1024**3,reserve_bytes:5*1024**3}};
    else if(method==='media.status')result={config,ollama_bin:true,ollama_running:true,installed_models:['qwen3-vl:2b','chat-only:latest'],vision_ready:!!verification,verification,test_job:testJob,ffmpeg:true,whisper:true,speech_model:true,setup};
    else if(method==='media.configure'){config={...config,...params};result=config;}
    else if(method==='media.setup'){setup={state:'running',detail:'Downloading model layers',completed_bytes:1024**3,total_bytes:2*1024**3,percent:50};result=setup;}
    else if(method==='media.test'){verification={ok:true,provider:'local',model:config.vision_model};testJob={state:'ready',detail:'Real-image vision test passed.'};result={state:'started'};}
    else return route.fallback();
    await route.fulfill({headers:{'Access-Control-Allow-Origin':'*'},json:{id,result}});
  });
  await page.goto('/');
  await page.getByRole('navigation',{name:'Main navigation'}).getByRole('button',{name:'Settings',exact:true}).click();await page.getByRole('tab',{name:'Video & learning',exact:true}).click();
  return {state,calls};
}

test('local image and chat selections are separate and readiness requires a test',async({page})=>{
  const {state,calls}=await localFixture(page);
  const panel=page.locator('.media-ai');
  const vision=panel.getByRole('button',{name:'Choose analysis model',exact:true});
  await vision.click();await page.getByRole('menuitem',{name:'My own models',exact:false}).click();
  await expect(page.getByRole('menuitemradio')).toHaveCount(4);
  await expect(page.getByRole('menuitemradio',{name:'Installed text model',exact:false})).toHaveCount(0);
  await page.keyboard.press('Escape');
  await expect(panel.getByText('Choose a model and test an image',{exact:true})).toBeVisible();
  await panel.getByRole('button',{name:'Test selected frame model'}).click();
  await expect(panel.getByText('Image test passed',{exact:true})).toBeVisible();
  expect(calls.some(call=>call.method==='media.test')).toBe(true);
  await panel.getByRole('button',{name:'Set up local tools',exact:true}).click();
  await panel.getByRole('combobox',{name:'Local chat model',exact:true}).selectOption('chat-only:latest');
  await panel.getByRole('button',{name:'Use selected local chat model'}).click();
  await expect.poll(()=>state.calls.filter(call=>call.method==='models.select').length).toBe(1);
  expect(state.calls.find(call=>call.method==='models.select')!.params).toEqual({provider:'ollama',model:'chat-only:latest'});
  await expect(vision).toContainText('Qwen3-VL 2B');
  await pickAnalysisModel(page,'Qwen3-VL Abliterated 8B (uncensored)','My own models');
  await expect(panel.getByText('Choose a model and test an image',{exact:true})).toBeVisible();
  await expect(panel.getByText('16 GB or more recommended.',{exact:false})).toBeVisible();
  await expect(panel.getByRole('button',{name:'Download selected local model',exact:true})).toBeDisabled();
  await panel.getByRole('checkbox',{name:'I approve downloading and installing',exact:false}).check();
  await panel.getByRole('button',{name:'Download selected local model',exact:true}).click();
  await expect(panel.getByText('Downloading model layers',{exact:false})).toBeVisible();
  expect(calls.find(call=>call.method==='media.setup')!.params).toEqual({target:'vision',approved:true,model_id:'huihui_ai/qwen3-vl-abliterated:8b-instruct',role:'vision'});
  await expect(panel.getByRole('progressbar',{name:'Model download progress'})).toHaveAttribute('value','50');
  await page.screenshot({path:'artifacts/complete-learning-2026-11-12/ui/settings-local.png',fullPage:true});
});

test('Stop all invalidates setup continuation while configuration is in flight',async({page})=>{
  const {calls}=await localFixture(page);
  let release!:()=>void;
  const pending=new Promise<void>(resolve=>{release=resolve;});
  let configuring=false;
  await page.route('http://127.0.0.1:8765/rpc',async route=>{
    if(route.request().method()==='OPTIONS')return route.fallback();
    const {id,method}=route.request().postDataJSON();
    if(method!=='media.configure')return route.fallback();
    configuring=true;await pending;
    await route.fulfill({headers:{'Access-Control-Allow-Origin':'*'},json:{id,result:{}}});
  });
  await page.getByRole('button',{name:'Test selected frame model'}).click();
  await expect.poll(()=>configuring).toBe(true);
  await page.getByRole('button',{name:'Stop all',exact:true}).click();
  release();
  await expect(page.locator('.media-ai').getByText('All tasks are stopped.',{exact:false})).toBeVisible();
  await expect(page.getByRole('button',{name:'Test selected frame model'})).toBeDisabled();
  expect(calls.filter(call=>call.method==='media.test')).toHaveLength(0);
  await page.getByRole('button',{name:'Resume tasks',exact:true}).click();
  await expect(page.getByRole('button',{name:'Test selected frame model'})).toBeEnabled();
});
