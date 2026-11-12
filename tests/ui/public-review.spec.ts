import {expect, test} from '@playwright/test';
import {chatFixture} from './chat-fixture';
import {openTool, openWeb} from './navigation';

test('retired AI workspace is absent and public rendering remains an explicit option', async ({page}) => {
  const state = await chatFixture(page);
  await page.addInitScript(() => localStorage.setItem('bughunter.project', 'public-project'));
  const project = {id:'public-project',name:'Public fixture',scope:['https://example.test/'],authorization:'Public review',read_only_paths:['/','/assets/*']};
  let started: any;
  const task = {id:'audit',kind:'website_audit',status:'completed',result:{pages:[{path:'/',status:200,evidence_ids:['traffic-fixture']}],requests_sent:3,
    observations:[{title:'HSTS header not observed',detail:'Review the deployment.',path:'/',evidence_ids:['traffic-fixture']}],
    public_render:{title:'Public fixture',text:'Public page text.',message:'Fresh public session. Blocked dependencies may prevent rendering.',blocked_requests:[{}]},assets:[{}]}};
  await page.route('http://127.0.0.1:8765/rpc', async route => {
    if(route.request().method()==='OPTIONS') return route.fallback();
    const {id,method,params} = route.request().postDataJSON();
    let result:any;
    if(method==='projects.list') result=[project];
    else if(method==='projects.get') result=project;
    else if(method==='tasks.list') result=[];
    else if(method==='training.workspaces') result=[];
    else if(method==='website.audit.start'){started=params;result=task;}
    else return route.fallback();
    await route.fulfill({json:{id,result},headers:{'Access-Control-Allow-Origin':'*'}});
  });
  await page.goto('/');
  await page.getByRole('button',{name:'Tools',exact:true}).click();
  const menu=page.getByRole('dialog',{name:'Tools',exact:true});
  await expect(menu.getByRole('button',{name:'AI testing',exact:true})).toHaveCount(0);
  await menu.getByText('Open individual workspaces',{exact:true}).click();
  await menu.getByRole('button',{name:'Training lab',exact:true}).click();
  await expect(page.getByRole('button',{name:'AI red team',exact:false})).toHaveCount(0);
  await expect(page.getByRole('button',{name:'Cheat Engine',exact:false}).last()).toBeVisible();
  await openWeb(page);
  const render=page.getByRole('checkbox',{name:'Render the public start page',exact:false});
  await expect(render).not.toBeChecked();
  await render.check();
  await page.getByRole('checkbox',{name:'I own this target',exact:false}).check();
  await page.getByRole('button',{name:'Run authorized audit',exact:true}).click();
  await expect(page.getByText('HSTS header not observed',{exact:true})).toBeVisible();
  expect(started).toMatchObject({project_id:'public-project',approved:true,render_public:true});
  await page.getByText('Public page snapshot and coverage',{exact:true}).click();
  await expect(page.getByText('Public page text.',{exact:true})).toBeVisible();
  await page.screenshot({path:test.info().outputPath('public-review.png'),fullPage:true});
  expect(state.calls.some(c=>c.method.startsWith('redteam.')||c.method.startsWith('corpus.'))).toBe(false);
});
