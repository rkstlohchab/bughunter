import {expect,test} from '@playwright/test';
import type {Page} from '@playwright/test';
import {chatFixture} from './chat-fixture';

export const analysisProviders=[
  {provider:'claude_code',label:'Claude subscription',kind:'subscription',ready:true,models:['claude-opus-4-7','claude-sonnet-4-6'],model_options:[{id:'claude-opus-4-7',label:'Claude Opus 4.7',effort:{levels:['low','medium','high','xhigh','max']}},{id:'claude-sonnet-4-6',label:'Claude Sonnet 4.6',effort:{levels:['low','medium','high','max']}}]},
  {provider:'codex',label:'ChatGPT subscription',kind:'subscription',ready:true,models:['gpt-6.1-sol'],model_options:[{id:'gpt-6.1-sol',label:'GPT-6.1 Sol',effort:{levels:['low','medium','high','xhigh','max','ultra'],default:'low'}}]},
  {provider:'gemini_cli',label:'Google AI subscription',kind:'subscription',ready:false,needs:'Sign in with Google first.',models:['gemini-3.8-flash'],model_options:[{id:'gemini-3.8-flash',label:'Gemini 3.8 Flash',effort:{levels:['low','medium','high']}}]},
  {provider:'ollama',label:'Local AI',kind:'local',ready:true,models:['qwen3-vl:2b'],model_options:[{id:'qwen3-vl:2b',label:'Qwen local',detail:'Installed · 1.7 GiB',effort:{levels:[]}}]},
];
async function resourceFixture(page:Page){
  await chatFixture(page);const calls:{method:string;params:any}[]=[];
  const sources=[{id:'notes',title:'Saved notes',filename:'notes.txt',status:'ready',analysis_model:null as any},{id:'lesson',title:'Saved lesson',filename:'lesson.txt',status:'ready',analysis_model:null as any},{id:'other',title:'Other resource',filename:'other.txt',status:'ready',analysis_model:null as any}];
  await page.route('http://127.0.0.1:8765/rpc',async route=>{
    if(route.request().method()==='OPTIONS')return route.fallback();
    const {id,method,params={}}=route.request().postDataJSON();calls.push({method,params});let result:any;
    if(method==='sources.list')result=sources;
    if(method==='sources.get')result={...sources.find(source=>source.id===params.id),chunks:[]};
    if(method==='media.status')result={config:{vision_provider:'claude_subscription',claude_vision_model:'claude-opus-4-7'},setup:{state:'idle'}};
    if(method==='models.analysis')result={providers:analysisProviders};
    if(method==='sources.models'){for(const source of sources)if(params.ids.includes(source.id))source.analysis_model=params.analysis_model;result={count:params.ids.length};}
    if(method==='sources.import'||method==='courses.import')result={id:'imported',status:'queued'};
    if(method==='sources.coverage')result={kind:'text',source_status:'ready',audio:{completed_parts:0,total_parts:0,transcript_passages:1,unclear_total:0,unclear_passages:[]},visual:{plan_available:false,planned_frames:null,completed_planned_frames:null,saved_visual_observations:0,unreadable_total:0,unreadable_intervals:[],maximum_sampling_spacing_seconds:null,pending_ranges:[]},methods:{candidate_steps:[],candidate_steps_total:0,mentioned_tools:[]},warnings:[],windows:[]};
    if(result===undefined)return route.fallback();
    return route.fulfill({headers:{'Access-Control-Allow-Origin':'*'},json:{id,result}});
  });
  await page.goto('/');await page.getByRole('button',{name:'Library',exact:true}).click();return {calls,sources};
}
async function pick(page:Page,name:string,category='Subscriptions'){
  await page.getByRole('button',{name:'Choose analysis model',exact:true}).click();
  await page.getByRole('menuitem',{name:category,exact:false}).click();
  const provider=analysisProviders.find(provider=>provider.model_options.some(option=>option.label===name))?.label;
  if(provider)await page.getByRole('menuitem',{name:provider,exact:true}).hover();
  await page.getByRole('menuitemradio',{name,exact:true}).click();
}
const opus={provider:'claude_subscription',model:'claude-opus-4-7'};

test('an imported resource has a direct model action and saves its effort',async({page})=>{
  const {calls}=await resourceFixture(page);await page.getByRole('button',{name:'Change model for Saved lesson',exact:true}).click();
  await pick(page,'GPT-6.1 Sol');await page.getByRole('button',{name:'Set effort to High',exact:true}).click();
  await page.getByRole('button',{name:'Save selected models'}).click();
  await expect.poll(()=>calls.filter(call=>call.method==='sources.models').length).toBe(1);
  expect(calls.find(call=>call.method==='sources.models')?.params).toEqual({ids:['lesson'],analysis_model:{provider:'codex_subscription',model:'gpt-6.1-sol',effort:'high'}});
  await page.getByRole('button',{name:'Change model for Saved lesson',exact:true}).click();
  await expect(page.getByRole('button',{name:'Choose analysis model',exact:true})).toContainText('GPT-6.1 Sol');
  await expect(page.getByRole('slider',{name:'Reasoning effort',exact:true})).toHaveAttribute('aria-valuetext','High');
  expect(calls.some(call=>['sources.retry','media.control'].includes(call.method))).toBe(false);
});

test('local model list shows installation details without hosted sharing',async({page})=>{
  const {calls}=await resourceFixture(page);await page.getByRole('button',{name:'Change model for Saved notes',exact:true}).click();
  await page.getByRole('button',{name:'Choose analysis model',exact:true}).click();await page.getByRole('menuitem',{name:'My own models',exact:false}).click();
  await expect(page.getByRole('menuitemradio',{name:'Qwen local',exact:true})).toContainText('Installed');await page.getByRole('menuitemradio',{name:'Qwen local',exact:true}).click();
  await expect(page.getByText('Images are analyzed on this Mac.',{exact:false})).toBeVisible();await page.getByRole('button',{name:'Save selected models'}).click();
  await expect.poll(()=>calls.filter(call=>call.method==='sources.models').length).toBe(1);
  expect(calls.find(call=>call.method==='sources.models')?.params.analysis_model).toEqual({provider:'local',model:'qwen3-vl:2b'});
});

test('all three connection groups and unavailable Gemini versions remain visible',async({page})=>{
  await resourceFixture(page);await page.getByRole('button',{name:'Upload / add link'}).click();
  await page.getByRole('button',{name:'Choose analysis model',exact:true}).click();
  await expect(page.getByRole('menu',{name:'AI connection types',exact:true}).getByRole('menuitem')).toHaveCount(3);
  await page.getByRole('menuitem',{name:'Subscriptions',exact:false}).hover();
  await page.getByRole('menuitem',{name:'Google AI subscription',exact:true}).hover();
  await expect(page.getByRole('menuitemradio',{name:'Gemini 3.8 Flash',exact:true})).toBeDisabled();
  await expect(page.getByText('Sign in with Google first.',{exact:true})).toBeVisible();
  await page.getByRole('menuitem',{name:'Claude subscription',exact:true}).hover();
  await expect(page.getByRole('menuitemradio',{name:'Claude Sonnet 4.6',exact:true})).toContainText('Low · Medium · High · Max');
});

test('batch saves only checked resources and can follow the default again',async({page})=>{
  const {calls,sources}=await resourceFixture(page);await page.getByRole('checkbox',{name:'Select Saved notes',exact:true}).check();await page.getByRole('checkbox',{name:'Select Saved lesson',exact:true}).check();
  await page.getByRole('button',{name:'Choose model',exact:true}).click();await pick(page,'Claude Opus 4.7');await page.getByRole('button',{name:'Save selected models'}).click();
  await expect.poll(()=>calls.filter(call=>call.method==='sources.models').length).toBe(1);expect(calls.find(call=>call.method==='sources.models')?.params).toEqual({ids:['notes','lesson'],analysis_model:opus});expect(sources[2].analysis_model).toBeNull();
  await page.getByRole('button',{name:'Choose model',exact:true}).click();await page.getByRole('button',{name:'Save selected models'}).click();
  await expect.poll(()=>calls.filter(call=>call.method==='sources.models').length).toBe(2);expect(calls.filter(call=>call.method==='sources.models')[1].params.analysis_model).toBeNull();
});

test('individual resource model persists when reopened',async({page})=>{
  const {calls}=await resourceFixture(page);await page.getByRole('button',{name:'Saved notes',exact:true}).click();await pick(page,'Claude Opus 4.7');await page.getByRole('button',{name:'Save resource model'}).click();
  await expect.poll(()=>calls.filter(call=>call.method==='sources.models').length).toBe(1);await expect(page.getByText('Current choice: Claude subscription · claude-opus-4-7')).toBeVisible();
  await page.getByRole('button',{name:'Close dialog',exact:true}).click();await page.getByRole('button',{name:'Saved notes',exact:true}).click();await expect(page.getByRole('button',{name:'Choose analysis model',exact:true})).toContainText('Claude Opus 4.7');
});

for(const mode of ['files','link','folder','zip','text'])test(`import ${mode} carries the selected version`,async({page})=>{
  const {calls}=await resourceFixture(page);await page.getByRole('button',{name:'Upload / add link'}).click();await pick(page,'Claude Opus 4.7');
  if(mode==='link'){await page.getByRole('button',{name:'Paste a link',exact:true}).click();await page.getByRole('textbox',{name:'Public link'}).fill('https://example.com/lesson');}
  else if(mode==='text'){await page.getByRole('button',{name:'Paste text',exact:true}).click();await page.getByRole('textbox',{name:'Resource text'}).fill('Saved transcript evidence');}
  else if(mode==='files')await page.locator('input[type=file]').setInputFiles([{name:'one.txt',mimeType:'text/plain',buffer:Buffer.from('one')},{name:'two.txt',mimeType:'text/plain',buffer:Buffer.from('two')}]);
  else{await page.getByRole('button',{name:'Large course / folder',exact:true}).click();await page.getByRole('textbox',{name:'Absolute file path'}).fill(mode==='zip'?'/fixtures/course.zip':'/fixtures/course');}
  await page.getByRole('button',{name:'Import resource',exact:true}).click();await expect.poll(()=>calls.filter(call=>['sources.import','courses.import'].includes(call.method)).length).toBe(mode==='files'?2:1);
  expect(calls.filter(call=>['sources.import','courses.import'].includes(call.method)).every(call=>JSON.stringify(call.params.analysis_model)===JSON.stringify(opus))).toBe(true);
});
