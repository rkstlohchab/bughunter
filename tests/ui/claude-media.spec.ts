import {expect,test} from '@playwright/test';
import {chatFixture} from './chat-fixture';
import {analysisProviders,pickAnalysisModel} from './analysis-fixture';

test('Claude course vision has its own consent and model selection',async({page})=>{
  await chatFixture(page);
  const calls:{method:string;params:any}[]=[];
  let config={enabled:true,vision_provider:'codex_subscription',vision_model:'qwen3-vl:2b',codex_vision_model:'gpt-6.1-sol',codex_vision_consent:true,claude_vision_model:'sonnet',claude_vision_consent:false,frame_interval:30,max_frames:0,adaptive_frames:true,scene_threshold:.3,vision_enabled:true,speech_enabled:true};
  await page.route('http://127.0.0.1:8765/rpc',async route=>{
    if(route.request().method()==='OPTIONS')return route.fallback();
    const {id,method,params={}}=route.request().postDataJSON();calls.push({method,params});
    let result:any;
    if(method==='models.analysis')result={providers:analysisProviders};
    if(method==='media.status')result={config,claude_available:true,codex_available:true,installed_models:[],setup:{state:'idle'},test_job:{state:'idle'},vision_ready:false};
    if(method==='media.models')result={ready:true,models:params.provider==='claude_subscription'?[{id:'sonnet',label:'Sonnet'},{id:'opus',label:'Opus'}]:[{id:'gpt-6.1-sol',label:'GPT-6.1 Sol'}]};
    if(method==='media.configure'){config={...config,...params};result=config;}
    if(result===undefined)return route.fallback();
    return route.fulfill({headers:{'Access-Control-Allow-Origin':'*'},json:{id,result}});
  });
  await page.goto('/');
  await page.getByRole('navigation',{name:'Main navigation'}).getByRole('button',{name:'Settings',exact:true}).click();await page.getByRole('tab',{name:'Video & learning',exact:true}).click();
  await pickAnalysisModel(page,'Claude Opus 4.7');
  await expect(page.getByRole('button',{name:'Save video settings'})).toBeDisabled();
  await page.getByRole('checkbox',{name:'I consent to sending selected frames and nearby narration to Claude',exact:false}).check();
  await page.getByRole('button',{name:'Save video settings'}).click();
  await expect.poll(()=>calls.filter(c=>c.method==='media.configure').length).toBe(1);
  expect(calls.find(c=>c.method==='media.configure')?.params).toMatchObject({vision_provider:'claude_subscription',claude_vision_model:'claude-opus-4-7',claude_vision_consent:true,codex_vision_consent:true});
  expect(calls.some(c=>c.method==='settings.save')).toBe(false);
});
