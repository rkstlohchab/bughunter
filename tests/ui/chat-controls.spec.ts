import {expect,test} from '@playwright/test';
import {chatFixture} from './chat-fixture';

test('global stop is visible in Chat, Library and Settings and Resume does not replay work',async({page})=>{
  const state=await chatFixture(page,{completeAfter:1000});
  await page.goto('/');
  await page.getByRole('textbox',{name:'Message'}).fill('Explain the saved evidence');
  await page.getByRole('button',{name:'Send',exact:true}).click();
  await expect.poll(()=>state.calls.filter(c=>c.method==='chat.agent').length).toBe(1);
  await page.getByRole('button',{name:'Stop all',exact:true}).click();
  await expect(page.getByRole('button',{name:'Resume tasks',exact:true})).toBeInViewport();
  await expect(page.getByRole('textbox',{name:'Message'})).toBeDisabled();
  for(const name of ['Library','Settings','Chat']){
    await page.getByRole('navigation',{name:'Main navigation'}).getByRole('button',{name,exact:true}).click();
    await expect(page.getByRole('button',{name:'Resume tasks',exact:true})).toBeInViewport();
  }
  await page.getByRole('button',{name:'Resume tasks',exact:true}).click();
  await expect(page.getByRole('button',{name:'Stop all',exact:true})).toBeInViewport();
  expect(state.calls.filter(c=>c.method==='chat.agent')).toHaveLength(1);
  expect(state.calls.filter(c=>c.method==='system.stop')).toHaveLength(1);
  expect(state.calls.filter(c=>c.method==='system.resume')).toHaveLength(1);
  await page.screenshot({path:test.info().outputPath('chat-stop.png'),fullPage:true});
});

test('Settings checks the native process, refreshes grants on return, and has no Task controls',async({page})=>{
  const state=await chatFixture(page);
  await page.addInitScript(()=>{
    (window as any).permissionsFixture={full_disk:true,screen_recording:false,accessibility:null,process:'/Applications/BugHunterClaudex.app/Contents/MacOS/bughunter-claudex'};
    (window as any).__TAURI_INTERNALS__={invoke:async(cmd:string,args:any)=>{
      if(cmd==='mac_permission_status')return (window as any).permissionsFixture;
      if(cmd==='rpc'){
        const res=await fetch('http://127.0.0.1:8765/rpc',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:1,...args})});
        return (await res.json()).result;
      }
      return {};
    }};
  });
  await page.goto('/');
  await page.getByRole('navigation',{name:'Main navigation'}).getByRole('button',{name:'Settings',exact:true}).click();
  await page.getByRole('tab',{name:'Computer & apps',exact:true}).click();
  const panel=page.locator('.permission-checks');
  await expect(panel.getByText('Granted',{exact:true})).toHaveCount(1);
  await expect(panel.getByText('Not granted to this app',{exact:true})).toHaveCount(1);
  await expect(panel.getByText('Unable to verify',{exact:true})).toHaveCount(1);
  await page.evaluate(()=>{(window as any).permissionsFixture={...(window as any).permissionsFixture,screen_recording:true,accessibility:true};window.dispatchEvent(new Event('focus'));});
  await expect(panel.getByText('Granted',{exact:true})).toHaveCount(3);
  await expect(panel.getByRole('button',{name:'Open settings',exact:true})).toHaveCount(0);
  await expect(page.getByText('Task controls',{exact:true})).toHaveCount(0);
  expect(state.calls.filter(c=>c.method==='system.autosetup').every(c=>c.params.install_missing===false)).toBe(true);
  await page.screenshot({path:test.info().outputPath('settings-granted.png'),fullPage:true});
});

test('guide collects website scope once and returns task results in Chat',async({page})=>{
  const state=await chatFixture(page);
  let started:any;const project={id:'project_chat',name:'Public fixture',scope:['https://example.test/'],read_only_paths:['/']};
  const session={id:'chat_review',project_id:project.id,title:'Website review',task_id:'audit_chat',task_ids:['audit_chat'],messages:[{role:'user',content:'Audit my authorized website'},{role:'assistant',content:'Public review started.'}]};
  await page.route('http://127.0.0.1:8765/rpc',async route=>{
    if(route.request().method()==='OPTIONS')return route.fallback();
    const {id,method,params}=route.request().postDataJSON();let result:any;
    if(method==='autopilot.plan') result={workflow:'website',requires_approval:true,missing:['Choose a target'],explanation:'Review public pages.'};
    else if(method==='projects.list')result=started?[project]:[];
    else if(method==='chat.task_start'){started=params;state.chats=[session];result=session;}
    else if(method==='tasks.get')result={id:'audit_chat',kind:'website_audit',status:'completed',result:{pages:[{path:'/'}],requests_sent:1}};
    else if(method==='chat.task_result')result={...session,messages:[...session.messages,{role:'assistant',content:'Observed 1 public page. No vulnerability was automatically confirmed.'}]};
    else return route.fallback();
    await route.fulfill({headers:{'Access-Control-Allow-Origin':'*'},json:{id,result}});
  });
  await page.goto('/');
  await page.getByRole('navigation',{name:'Main navigation'}).getByRole('button',{name:'Library',exact:true}).click();
  await page.getByRole('button',{name:'Tools',exact:true}).click();
  await page.getByRole('button',{name:'Review a website',exact:false}).click();
  await expect(page.getByRole('textbox',{name:'Message'})).toHaveValue('Audit my authorized website');
  await page.getByRole('button',{name:'Send',exact:true}).click();
  const setup=page.getByRole('region',{name:'Task setup'});
  await expect(setup).toBeVisible();
  await expect(setup.getByRole('button',{name:'Start task',exact:true})).toBeDisabled();
  expect(started).toBeUndefined();
  await setup.getByLabel('Website address').fill('https://example.test/');
  await setup.getByRole('checkbox',{name:'I have permission',exact:false}).check();
  await setup.getByRole('button',{name:'Start task',exact:true}).click();
  await expect(page.getByText('Observed 1 public page. No vulnerability was automatically confirmed.',{exact:true})).toBeVisible();
  expect(started).toMatchObject({approved:true,max_pages:12,render_public:false,website:{url:'https://example.test/',read_only_paths:['/']}});
  await expect(page.getByRole('button',{name:'Stop all',exact:true})).toBeInViewport();
});
