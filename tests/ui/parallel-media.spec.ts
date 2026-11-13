import {expect,test} from '@playwright/test';
import {chatFixture} from './chat-fixture';
import {analysisProviders,pickAnalysisModel} from './analysis-fixture';

test('parallel Claude requires consent and persists a pinned model and delayed start',async({page})=>{
  await chatFixture(page);
  let config:any={enabled:true,vision_provider:'codex_subscription',codex_vision_model:'gpt-6.1-sol',codex_vision_consent:true,
    claude_vision_model:'sonnet',claude_vision_consent:false,claude_parallel_enabled:false,claude_parallel_after:0};
  const saved:any[]=[];
  await page.route('http://127.0.0.1:8765/rpc',async route=>{
    if(route.request().method()==='OPTIONS')return route.fallback();
    const {id,method,params={}}=route.request().postDataJSON();
    let result:any;
    if(method==='models.analysis')result={providers:analysisProviders};
    if(method==='media.status')result={config,installed_models:[],setup:{state:'idle'},test_job:{state:'idle'},vision_ready:false};
    if(method==='media.models')result={ready:true,models:[{id:'gpt-6.1-sol'},{id:'gpt-5.6-luna'}]};
    if(method==='media.configure'){saved.push(params);config={...config,...params};result=config;}
    if(result===undefined)return route.fallback();
    await route.fulfill({headers:{'Access-Control-Allow-Origin':'*'},json:{id,result}});
  });
  await page.goto('/');
  await page.getByRole('navigation',{name:'Main navigation'}).getByRole('button',{name:'Settings',exact:true}).click();await page.getByRole('tab',{name:'Video & learning',exact:true}).click();
  await page.getByText('Sampling and advanced options',{exact:true}).click();
  await page.getByRole('checkbox',{name:'Use Claude alongside ChatGPT',exact:false}).check();
  await expect(page.getByRole('button',{name:'Save video settings',exact:true})).toBeDisabled();
  await page.getByRole('combobox',{name:'Parallel Claude model',exact:true}).selectOption('claude-opus-4-7');
  await page.getByRole('checkbox',{name:'I consent to sending selected frames and nearby narration to Claude for parallel analysis.',exact:true}).check();
  await pickAnalysisModel(page,'GPT-5.6 Luna');
  await page.getByRole('combobox',{name:'Hard-to-read frame model',exact:false}).selectOption('gpt-6.1-sol');
  const before=Date.now()/1000;
  await page.getByRole('button',{name:'Schedule Claude in one hour',exact:true}).click();
  await page.getByRole('button',{name:'Save video settings',exact:true}).click();
  await expect.poll(()=>saved.length).toBe(1);
  expect(saved[0]).toMatchObject({claude_parallel_enabled:true,claude_vision_consent:true,claude_vision_model:'claude-opus-4-7',
    codex_vision_model:'gpt-5.6-luna',codex_detail_model:'gpt-6.1-sol'});
  expect(saved[0].claude_parallel_after).toBeGreaterThanOrEqual(before+3600);
});
