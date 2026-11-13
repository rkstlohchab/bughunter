import {test,expect} from '@playwright/test';
import {chatFixture} from './chat-fixture';

test('plugins connect GitHub and Vercel and support future MCP servers from Settings',async({page})=>{
 await chatFixture(page);const calls:any[]=[];let plugins:any[]=[];
 await page.route('http://127.0.0.1:8765/rpc',async route=>{
  if(route.request().method()==='OPTIONS')return route.fallback();const {id,method,params={}}=route.request().postDataJSON();let result:any;
  if(!method.startsWith('plugins.'))return route.fallback();calls.push({method,params});
  if(method==='plugins.list')result={presets:[{id:'github',name:'GitHub',url:'https://api.githubcopilot.com/mcp/',auth:'bearer',docs:'https://github.com/github/github-mcp-server'},{id:'vercel',name:'Vercel',url:'https://mcp.vercel.com',auth:'oauth',docs:'https://vercel.com/docs/mcp/vercel-mcp'}],items:plugins};
  else if(method==='plugins.add'){const row={...params,id:'plugin_fixture',status:'not_connected',tools:[],enabled:true};plugins=[row];result=row;}
  else if(method==='plugins.connect'){plugins=plugins.map(row=>({...row,status:'connected',tools:[{name:'list_repositories',annotations:{readOnlyHint:true}}]}));result=plugins[0];}
  else if(method==='plugins.tools')result={name:'GitHub',items:[{name:'list_repositories',description:'List repositories',annotations:{readOnlyHint:true}}]};
  else return route.fallback();await route.fulfill({headers:{'Access-Control-Allow-Origin':'*'},json:{id,result}});
 });
 await page.goto('/');await page.getByRole('button',{name:'Settings',exact:true}).click();await page.getByRole('tab',{name:'Plugins',exact:true}).click();const panel=page.getByRole('region',{name:'Plugins settings'});
 await expect(panel.getByRole('button',{name:'Add Vercel',exact:true})).toBeVisible();await panel.getByRole('button',{name:'Add GitHub',exact:true}).click();
 await panel.getByLabel('Access token',{exact:true}).fill('fixture-private-token');await panel.getByRole('button',{name:'Save plugin',exact:true}).click();
 await panel.getByRole('button',{name:'Connect',exact:true}).click();await expect(panel).toContainText('connected · 1 tools');
 await panel.getByRole('button',{name:'Browse tools',exact:true}).click();await expect(panel.getByText('list_repositories',{exact:true})).toBeVisible();
 await panel.getByRole('button',{name:'Add custom MCP plugin',exact:true}).click();await panel.getByRole('combobox',{name:'Connection',exact:true}).selectOption('stdio');await expect(panel.getByRole('textbox',{name:'Executable',exact:true})).toBeVisible();
 expect(calls.find(c=>c.method==='plugins.add').params).toMatchObject({name:'GitHub',auth:'bearer',transport:'http'});
});

test('chat uploads references privately and a text-only model offers a vision assistant',async({page})=>{
 const state=await chatFixture(page);let attachmentReads=0;
 await page.route('http://127.0.0.1:8765/rpc',async route=>{
  if(route.request().method()==='OPTIONS')return route.fallback();const {id,method,params={}}=route.request().postDataJSON();let result:any;
  if(method==='chat.attachments.add'){state.calls.push({method,params});attachmentReads++;result={id:'chatref_image',filename:params.filename,title:params.filename,kind:'image',has_images:true};}
  else if(method==='chat.attachments.capability')result={selected_image_transport:false,vision_model:'Qwen VL local',detail:'This chat model has no verified image input. Enable a local vision model or choose a cloud vision model.'};
  else return route.fallback();await route.fulfill({headers:{'Access-Control-Allow-Origin':'*'},json:{id,result}});
 });
 await page.goto('/');await page.getByRole('textbox',{name:'Message',exact:true}).fill('Explain this image');
 await page.getByLabel('Attach files',{exact:true}).setInputFiles({name:'reference.png',mimeType:'image/png',buffer:Buffer.from('fixture image')});
 await expect(page.getByText('Chat references only · Not added to Library',{exact:true})).toBeVisible();await expect(page.getByText(/This chat model has no verified image input/)).toBeVisible();
 await expect(page.getByRole('button',{name:'Send',exact:true})).toBeDisabled();await page.getByRole('combobox',{name:'Image understanding',exact:true}).selectOption('configured');await page.getByRole('button',{name:'Send',exact:true}).click();
 await expect(page.getByText('Here is the verified result.',{exact:true})).toBeVisible();expect(attachmentReads).toBe(1);expect(state.calls.filter(c=>c.method==='sources.import')).toHaveLength(0);
 const sent=state.calls.find(c=>c.method==='chat.agent')?.params;expect(sent).toMatchObject({source_ids:[],attachment_ids:['chatref_image'],vision_mode:'configured'});
});

test('effort slider uses a thin visual rail and retains a usable hit target',async({page})=>{
 await chatFixture(page);await page.route('http://127.0.0.1:8765/rpc',async route=>{
  if(route.request().method()==='OPTIONS')return route.fallback();const {id,method}=route.request().postDataJSON();if(method!=='models.available')return route.fallback();await route.fulfill({headers:{'Access-Control-Allow-Origin':'*'},json:{id,result:{active:{provider:'codex',model:'fixture-vision',reasoning_effort:'medium'},providers:[{provider:'codex',label:'ChatGPT',ready:true,models:['fixture-vision'],model_options:[{id:'fixture-vision',effort:{levels:['low','medium','high'],default:'medium'}}]}]}}});
 });
 await page.goto('/');await page.getByRole('button',{name:'Effort: Medium',exact:true}).click();const rail=page.locator('.effort-card-rail');expect((await rail.boundingBox())!.height).toBeLessThanOrEqual(7);expect((await page.getByRole('slider',{name:'Reasoning effort'}).boundingBox())!.height).toBeGreaterThanOrEqual(44);
});
