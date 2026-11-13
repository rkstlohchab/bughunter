import {test,expect} from '@playwright/test';
import {chatFixture} from './chat-fixture';
import {analysisProviders,pickAnalysisModel} from './analysis-fixture';

test('Video AI offers three clear steps and saves a Gemini version with effort',async({page})=>{
 await chatFixture(page);let config:any={enabled:true,vision_provider:'codex_subscription',codex_vision_model:'gpt-6.1-sol',codex_vision_consent:true,vision_effort:'medium',frame_interval:30,max_frames:0,adaptive_frames:true,vision_enabled:true,speech_enabled:true};const saves:any[]=[];
 await page.route('http://127.0.0.1:8765/rpc',async route=>{
  if(route.request().method()==='OPTIONS')return route.fallback();const {id,method,params={}}=route.request().postDataJSON();let result:any;
  if(method==='models.analysis')result={providers:analysisProviders};
  if(method==='media.status')result={config,ffmpeg:true,whisper:true,speech_model:true,installed_models:[],setup:{state:'idle'},test_job:{state:'idle'}};
  if(method==='media.configure'){saves.push(params);config={...config,...params};result=config;}
  if(result===undefined)return route.fallback();return route.fulfill({json:{id,result},headers:{'Access-Control-Allow-Origin':'*'}});
 });
 await page.goto('/');await page.getByRole('button',{name:'Settings',exact:true}).click();await page.getByRole('tab',{name:'Video & learning',exact:true}).click();const panel=page.locator('.media-ai');
 for(const name of ['Choose the AI that reads your screens','Choose what to extract','Save and verify'])await expect(panel.getByRole('heading',{name,exact:true})).toBeVisible();
 await expect(panel.getByText('Current sampling',{exact:true})).toBeVisible();
 await expect(panel.getByRole('combobox',{name:'Hard-to-read frame model'})).toBeHidden();
 await pickAnalysisModel(page,'Gemini 3.8 Flash');await panel.getByRole('button',{name:'Set effort to Low',exact:true}).click();
 await expect(panel.getByRole('button',{name:'Save video settings'})).toBeDisabled();
 await panel.getByRole('checkbox',{name:'I consent to sending selected frames and nearby narration to Google AI for video analysis.',exact:true}).check();
 await panel.getByRole('button',{name:'Save video settings'}).click();await expect.poll(()=>saves.length).toBe(1);
 expect(saves[0]).toMatchObject({vision_provider:'gemini_subscription',gemini_vision_model:'gemini-3.8-flash',vision_effort:'low',hosted_vision_consent:true});
 for(const width of [1440,760,390]){await page.setViewportSize({width,height:1000});await expect(panel.getByRole('button',{name:'Choose analysis model',exact:true})).toBeVisible();expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);}
 await page.screenshot({path:'artifacts/remaining-analysis-2026-11-13/video-ai-mobile.png',fullPage:true});
});
