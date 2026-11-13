import {test,expect} from '@playwright/test';
import {chatFixture} from './chat-fixture';
test.use({launchOptions:{args:['--use-fake-device-for-media-stream','--use-fake-ui-for-media-stream']}});

test('Settings shows only the selected category and preserves connection drafts',async({page})=>{
 await fixture(page);await page.goto('/');await page.getByRole('button',{name:'Settings',exact:true}).click();
 const nav=page.getByRole('tablist',{name:'Settings categories',exact:true});await expect(nav.getByRole('tab')).toHaveCount(6);
 await expect(page.getByRole('tabpanel')).toHaveCount(1);await expect(page.getByRole('tabpanel',{name:'AI connections',exact:true})).toBeVisible();
 await expect(page.getByRole('heading',{name:'Voice',exact:true})).toHaveCount(0);await expect(page.getByRole('heading',{name:'Appearance',exact:true})).toHaveCount(0);
 await page.getByRole('textbox',{name:'Your AI instructions (optional)'}).fill('Explain with diagrams');
 await nav.getByRole('tab',{name:'Voice & memory',exact:true}).click();
 await expect(page.getByRole('tabpanel')).toHaveCount(1);await expect(page.getByRole('heading',{name:'Connect your AI',exact:true})).toHaveCount(0);
 await expect(page.getByRole('heading',{name:'Voice & memory',exact:true})).toBeInViewport();
 const memory=page.getByRole('region',{name:'Conversation memory'});await expect(memory).toContainText('No manual selection is needed');await expect(memory.getByRole('checkbox',{name:'Automatically learn useful memories'})).toBeVisible();
 await nav.getByRole('tab',{name:'AI connections',exact:true}).click();await expect(page.getByRole('textbox',{name:'Your AI instructions (optional)'})).toHaveValue('Explain with diagrams');
 const labels=['AI connections','Computer & apps','Voice & memory','Plugins','Video & learning','General'];
 for(const label of labels){await nav.getByRole('tab',{name:label,exact:true}).click();await expect(page.getByRole('tabpanel')).toHaveCount(1);await expect(page.getByRole('tabpanel',{name:label,exact:true})).toBeVisible();}
 await page.screenshot({path:test.info().outputPath('settings-general-only.png'),fullPage:true});
 await nav.getByRole('tab',{name:'AI connections',exact:true}).click();await page.keyboard.press('ArrowRight');await expect(nav.getByRole('tab',{name:'Computer & apps'})).toBeFocused();await expect(nav.getByRole('tab',{name:'Computer & apps'})).toHaveAttribute('aria-selected','true');await page.keyboard.press('End');await expect(page.getByRole('tabpanel',{name:'General',exact:true})).toBeVisible();
 for(const width of [760,390]){await page.setViewportSize({width,height:900});for(const label of labels){await nav.getByRole('tab',{name:label,exact:true}).click();await expect(page.getByRole('tabpanel')).toHaveCount(1);expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);}}
});

async function fixture(page:any){
 const state=await chatFixture(page);const calls:any[]=[];let selected='tiny';let mode:any={execution_mode:'auto',background:true,web_search_mode:'auto',scope:'all',grants:['app','computer'],permissions_configured:false};let memory:any[]=[{id:'cm_fixture',chat_id:'chat_old',title:'Learning preferences',text:'USER: I prefer diagrams.'}];let enabled=true;let active:any=null;let feed=0;
 await page.route('http://127.0.0.1:8765/rpc',async(route:any)=>{
  if(route.request().method()==='OPTIONS')return route.fallback();
  const {id,method,params={}}=route.request().postDataJSON();let result:any;
  if(method.startsWith('harness.')||method.startsWith('memory.chat.')||method.startsWith('voice.')||method.startsWith('setup.'))calls.push({method,params});
  if(method==='harness.preferences')result=mode;
  else if(method==='harness.preferences.save'){mode={...params,permissions_configured:true};result=mode;}
  else if(method==='harness.list')result=active?[{...active,chat_id:active.session.id,title:active.session.title,model:'fixture-model',background:true}]:[];
  else if(method==='harness.start'){const session={id:'chat_background',harness_id:'harness_background',title:params.message,messages:[{role:'user',content:params.message}]};state.chats=[session];active={id:'harness_background',status:'running',session,grants:params.grants,background:true,activity:[{name:'file_read',status:'returned',result:'Receipt only in the task panel'}]};result=active;}
  else if(method==='harness.get')result=active;
  else if(method==='harness.stop'){active={...active,status:'cancelled'};result=active;}
  else if(method==='harness.status')result={engine:{ready:true,installed:true}};
  else if(method==='memory.chat.status')result={config:{enabled},items:memory};
  else if(method==='memory.chat.configure'){enabled=params.enabled;result={enabled};}
  else if(method==='memory.chat.remember'){memory.push({id:'note_fixture',text:params.text});result=memory.at(-1);}
  else if(method==='memory.chat.forget'){memory=memory.filter(r=>r.id!==params.id);result={deleted:true};}
  else if(method==='voice.status')result={selected,phase:'ready',models:[{id:'tiny',label:'Moonshine Tiny Streaming · English',ready:true,verification:'Local model loaded and processed silence.'},{id:'small',label:'Moonshine Small Streaming · English',ready:true}],setup_running:false};
  else if(method==='voice.select'){selected=params.model;result={selected,models:[{id:'small',ready:true}]};}
  else if(method==='voice.start')result={id:'voice_fixture',status:'ready'};
  else if(method==='voice.feed'){feed++;result={status:'ready',events:feed===1?[{seq:1,kind:'partial',text:'Um show the plan',line_id:1}]:feed===2?[{seq:2,kind:'final',text:'Um, show the plan',line_id:1}]:[]};}
  else if(method==='voice.stop')result={stopped:true,events:[]};
  else if(method==='setup.catalog')result={items:[{id:'agent-desktop',label:'Desktop engine',installed:true}]};
  else if(method==='setup.search')result={items:[{id:'registry_fixture',label:'Firefox',identifier:'firefox',kind:'cask'}]};
  else if(method==='setup.plan')result={package:params.package,label:'Firefox',command:'brew install --cask firefox',ready_to_install:true};
  else return route.fallback();
  await route.fulfill({headers:{'Access-Control-Allow-Origin':'*'},json:{id,result}});
 });return {state,calls};
}

test('composer selects per-model execution mode and new chats leave background tasks running',async({page})=>{
 const {calls}=await fixture(page);await page.goto('/');
 await page.getByRole('button',{name:'Agent settings',exact:true}).click();
 await page.getByRole('radio',{name:'Full access',exact:true}).click();
 await page.getByRole('button',{name:'Save access and settings',exact:true}).click();
 await page.getByRole('textbox',{name:'Message',exact:true}).fill('Open Calculator');await page.getByRole('button',{name:'Send',exact:true}).click();
 await expect(page.getByRole('region',{name:'Task permissions'})).toHaveCount(0);
 await expect(page.getByText('Working in background…',{exact:true})).toBeVisible();
 await page.screenshot({path:'artifacts/assistant-runtime-2026-11-13/chat-modes.png'});
 expect(calls.find(c=>c.method==='harness.start').params).toMatchObject({execution_mode:'full',background:true});
 await expect(page.getByText('Tool activity',{exact:true})).toHaveCount(0);await expect(page.getByText('Task access',{exact:true})).toHaveCount(0);await expect(page.getByText('Ready for your reply',{exact:true})).toHaveCount(0);
 await page.getByRole('button',{name:'New chat',exact:true}).click();await expect(page.getByRole('textbox',{name:'Message',exact:true})).toBeEnabled();
 expect(calls.filter(c=>c.method==='harness.stop')).toHaveLength(0);
 await page.getByRole('button',{name:'Background tasks',exact:true}).click();const tasks=page.getByRole('region',{name:'Background tasks'});await expect(tasks.getByText('Open Calculator',{exact:true})).toBeVisible();
 await tasks.getByRole('button',{name:'Details',exact:true}).click();await tasks.getByText('Execution receipts',{exact:true}).click();await expect(tasks).toContainText('Receipt only in the task panel');
});

test('Settings exposes local voice models, memory controls and registry app search',async({page})=>{
 const {calls}=await fixture(page);await page.goto('/');await page.getByRole('button',{name:'Settings',exact:true}).click();
 await page.getByRole('tab',{name:'Voice & memory',exact:true}).click();const voice=page.getByRole('region',{name:'Voice settings'});await expect(voice).toContainText('Moonshine Tiny Streaming');await expect(voice).toContainText('Moonshine Small Streaming');
 const memory=page.getByRole('region',{name:'Conversation memory'});await memory.getByText('Manage saved memory (1)',{exact:true}).click();await expect(memory).toContainText('USER: I prefer diagrams.');
 await memory.getByRole('button',{name:'Forget',exact:true}).click();await expect(memory.getByText('USER: I prefer diagrams.',{exact:true})).toHaveCount(0);
 await memory.getByText('Add a preference or correction (optional)',{exact:true}).click();await memory.getByRole('textbox',{name:'Preference or correction'}).fill('Use short explanations');await memory.getByRole('button',{name:'Save memory',exact:true}).click();await expect(memory).toContainText('Use short explanations');
 await page.getByRole('tab',{name:'Computer & apps',exact:true}).click();const apps=page.getByRole('region',{name:'Task harness settings'});await apps.getByRole('textbox',{name:'Find another app',exact:true}).fill('firefox');await apps.getByRole('button',{name:'Search apps',exact:true}).click();await apps.getByRole('button',{name:/Firefox firefox/}).click();await expect(apps).toContainText('brew install --cask firefox');
 expect(calls.filter(c=>c.method==='setup.install')).toHaveLength(0);
 await page.screenshot({path:'artifacts/assistant-runtime-2026-11-13/voice-memory-settings.png',fullPage:true});
});

test('teaching responses render diagrams and provide downloadable source',async({page})=>{
 const {state}=await fixture(page);state.chats=[{id:'chat_diagram',title:'Teach a flow',messages:[{role:'user',content:'Explain memory with a diagram'},{role:'assistant',content:'```mermaid\nflowchart LR\n  A[Your question] --> B[Relevant memory]\n  B --> C[Chosen model]\n```'}]}];await page.addInitScript(()=>localStorage.setItem('bughunter.chat.library','chat_diagram'));await page.goto('/');
 await expect(page.getByRole('img',{name:'Visual explanation generated from the diagram source'})).toBeVisible();await expect(page.getByRole('link',{name:'Download diagram'})).toHaveAttribute('href',/^data:image\/svg\+xml/);
 await page.getByRole('button',{name:'View source',exact:true}).click();await expect(page.locator('.teaching-diagram pre')).toContainText('Relevant memory');
});

test('microphone streams partials locally, cleans fillers, and Stop all closes recording',async({page,context})=>{
 const {calls}=await fixture(page);await context.grantPermissions(['microphone']);await page.goto('/');
 await page.getByRole('button',{name:'Voice input',exact:true}).click();await expect(page.getByText('Listening locally',{exact:true})).toBeVisible({timeout:12000});
 await expect(page.getByRole('textbox',{name:'Message',exact:true})).toHaveValue('show the plan');
 expect(calls.filter(c=>c.method==='voice.feed').length).toBeGreaterThan(0);
 await page.getByRole('button',{name:'Stop all',exact:true}).click();await expect(page.getByText('Listening locally',{exact:true})).toHaveCount(0);
 await expect.poll(()=>calls.filter(c=>c.method==='voice.stop').length).toBeGreaterThan(0);
});

test('Agent settings groups access, work location, scope and automatic search in one panel',async({page})=>{
 const {calls}=await fixture(page);await page.goto('/');await page.getByRole('button',{name:'Agent settings',exact:true}).click();const panel=page.getByRole('dialog',{name:'Agent settings',exact:true});
 await expect(panel.getByRole('radio',{name:'Auto',exact:true}).first()).toHaveAttribute('aria-checked','true');await expect(panel.getByRole('radio',{name:'Background',exact:true})).toHaveAttribute('aria-checked','true');
 await panel.getByRole('combobox',{name:'Apply agent settings to'}).selectOption('all');await panel.getByRole('radio',{name:'Foreground',exact:true}).click();await panel.getByRole('radio',{name:'Off',exact:true}).click();await panel.getByRole('button',{name:'Save access and settings',exact:true}).click();
 expect(calls.find(c=>c.method==='harness.preferences.save').params).toMatchObject({scope:'all',background:false,web_search_mode:'off'});
 await page.getByRole('button',{name:'Agent settings',exact:true}).click();await expect(panel.getByRole('combobox',{name:'Apply agent settings to'})).toContainText('This chat');await expect(panel.getByRole('combobox',{name:'Apply agent settings to'})).toContainText('This model');await page.keyboard.press('Escape');await expect(panel).toHaveCount(0);
 await expect(page.getByRole('combobox',{name:'Execution mode',exact:true})).toHaveCount(0);
});
