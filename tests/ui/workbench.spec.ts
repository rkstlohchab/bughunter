import {openWeb} from './navigation';
import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';

async function backend(page: Page, seed: Record<string, any[]> = {}, pause?: {method: string; promise: Promise<void>}) {
  const data: Record<string, any[]> = { projects: [], sources: [], traffic: [], tests: [], actions: [], findings: [], runs: [], ...seed };
  let stopped = false;
  const calls: { method: string; params: any }[] = [];
  await page.route('http://127.0.0.1:8765/rpc', async route => {
    const request = route.request();
    if (request.method() === 'OPTIONS') { await route.fulfill({ status: 204, headers: { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Methods': 'POST, OPTIONS', 'Access-Control-Allow-Headers': 'Authorization, Content-Type' } }); return; }
    const { id, method, params } = request.postDataJSON();
    calls.push({ method, params });
    if (pause?.method === method) await pause.promise;
    let result: any = {};
    if (method.endsWith('.list')) result = data[method.split('.')[0]] || [];
    if (method === 'system.status') result = { version: '0.1.0', emergency_stop: stopped };
    if (method === 'system.stop') { stopped = true; result = {stopped:true}; }
    if (method === 'system.resume') { stopped = false; result = {resumed:true}; }
    if (method === 'projects.delete') { data.projects = data.projects.filter(p => p.id !== params.id); for (const kind of ['actions','traffic','tests','findings','runs']) data[kind] = data[kind].filter(r => r.project_id !== params.id); result = {deleted:true}; }
    if (method === 'tasks.start') {result = {id:'run_ui',project_id:params.project_id,status:'running',prompt:params.prompt,events:[{time:Date.now()/1000,type:'progress',text:'Searching cited learning material.'}]}; data.runs.unshift(result);}
    if (method === 'sources.import') {result = {id:'import_ui',title:params.filename || params.url,status:'queued'}; data.sources.push(result);}
    if (method === 'projects.save') { result = { ...params, id: 'project_ui' }; data.projects.push(result); }
    if (method === 'actions.approve') { const item = data.actions.find(a => a.id === params.id); item.status = 'approved'; result = item; }
    if (method === 'actions.execute') { const item = data.actions.find(a => a.id === params.id); item.status = 'completed'; result = item; }
    if (method === 'settings.get') result = { provider: 'claude_code', model: 'opus', custom_system_prompt: '' };
    if (method === 'tools.status') result = { claude: { available: true, version: 'test fixture' } };
    if (method === 'sources.get') result = { ...data.sources.find(s => s.id === params.id), chunks: [{ id: 'chunk_ui', text: '<img src=x onerror="window.wasInjected=true">', locator: 'page 3' }] };
    await route.fulfill({ json: { id, result }, headers: { 'Access-Control-Allow-Origin': '*' } });
  });
  return calls;
}

test('project creation retains explicit target scope and authorization', async ({ page }) => {
  const calls = await backend(page);
  await page.goto('/');
  await openWeb(page, 'Targets & scope');
  await page.getByRole('button', { name: 'New project' }).click();
  await page.getByLabel('Project name').fill('Controlled shop');
  await page.getByLabel('Website or API URLs').fill('https://shop.example.test');
  await page.getByLabel('Permission and program rules').fill('Owned test environment; use designated test accounts.');
  await page.getByRole('button', { name: 'Save project' }).click();
  await expect(page.getByRole('heading', { name: 'Controlled shop' })).toBeVisible();
  expect(calls.find(c => c.method === 'projects.save')?.params).toMatchObject({ scope: ['https://shop.example.test'], allow_private: false, requests_per_second: 1, max_requests: 1000 });
  expect(calls.some(c => c.method === 'actions.execute')).toBe(false);
});

test('state-changing request needs explicit approval before a separate send', async ({ page }) => {
  const calls = await backend(page, { projects: [{ id: 'p', name: 'Shop', scope: ['https://shop.example.test'] }], actions: [{ id: 'a', project_id: 'p', status: 'pending_approval', effect: 'state_change', reason: 'Updates cart state', session_id: 'owned-account-a', request_hash: 'immutable-fixture', request: { method: 'POST', url: 'https://shop.example.test/cart', headers: { 'Content-Type': 'application/json' }, body: '{"quantity":2}' } }] });
  await page.goto('/');
  await openWeb(page, 'Approvals');
  await page.getByRole('button', { name: /POST pending approval/ }).click();
  await expect(page.getByText('immutable-fixture')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Send once' })).toHaveCount(0);
  expect(calls.some(c => c.method === 'actions.execute')).toBe(false);
  await page.getByRole('button', { name: 'Approve exact request' }).click();
  await expect(page.getByRole('button', { name: 'Send once' })).toBeVisible();
  expect(calls.some(c => c.method === 'actions.execute')).toBe(false);
  await page.getByRole('button', { name: 'Send once' }).click();
  await expect.poll(() => calls.filter(c => c.method === 'actions.execute').length).toBe(1);
  expect(calls.find(c => c.method === 'actions.approve')?.params).toEqual({ id: 'a' });
});

test('source excerpts are rendered as data and remain readable on narrow screens', async ({ page }) => {
  await backend(page, { sources: [{ id: 's', title: 'Untrusted lesson', filename: 'lesson.md', status: 'ready', chunk_count: 1 }] });
  await page.goto('/');
  await page.getByRole('button', { name: 'Library', exact: true }).click();
  await page.getByRole('button', { name: 'Untrusted lesson', exact: true }).click();
  await expect(page.locator('.knowledge-reader-text').getByText('<img src=x onerror="window.wasInjected=true">')).toBeVisible();
  expect(await page.evaluate(() => (window as any).wasInjected)).toBeUndefined();
  await page.getByRole('button', { name: 'Close dialog' }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await expect(page.getByRole('heading', { name: 'Library', exact: true })).toBeVisible();
});

test('pending approvals remain reachable while a task is running', async ({page}) => {
  const calls = await backend(page, {projects:[{id:'p',name:'Shop',scope:['https://shop.example.test']}],runs:[{id:'r',status:'running'}],actions:[{id:'a',project_id:'p',status:'pending_approval',request:{method:'POST',url:'https://shop.example.test/cart',body:'{}'}}]});
  await page.goto('/'); await openWeb(page, 'Approvals');
  await page.locator('.test-row').click();
  await page.getByRole('button',{name:'Approve exact request'}).click();
  expect(calls.some(c => c.method === 'actions.approve')).toBe(true);
  expect(calls.some(c => c.method === 'actions.execute')).toBe(false);
  await page.getByRole('button',{name:'Stop all',exact:true}).click();
  await expect.poll(() => calls.some(c => c.method === 'system.stop')).toBe(true);
});

test('Stop all switches to Resume, survives refresh, and does not restart a run', async ({page}) => {
  const calls = await backend(page,{projects:[{id:'p',name:'Owned shop',scope:['https://example.test']}]});
  await page.goto('/');
  await openWeb(page);
  await page.getByRole('button',{name:'Stop all',exact:true}).click();
  await expect(page.getByRole('button',{name:'Resume requests',exact:true})).toBeVisible();
  await expect(page.getByRole('button',{name:'Stop all',exact:true})).toHaveCount(0);
  await expect(page.getByRole('button',{name:'Start investigation',exact:true})).toBeDisabled();
  await page.getByRole('button',{name:'Refresh workspace'}).click();
  await expect(page.getByRole('button',{name:'Stop all',exact:true})).toHaveCount(0);
  await page.reload();
  await openWeb(page);
  await expect(page.getByRole('button',{name:'Resume requests',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Resume requests',exact:true}).click();
  await expect(page.getByRole('button',{name:'Stop all',exact:true})).toBeVisible();
  await expect(page.getByRole('button',{name:'Resume requests',exact:true})).toHaveCount(0);
  expect(calls.some(c => c.method==='runs.start' || c.method==='runs.resume')).toBe(false);
});

test('simple investigation creates a scoped target and shows activity without opening details', async ({page}) => {
  const calls = await backend(page);
  await page.goto('/');
  await expect(page.getByRole('button',{name:'Captured requests',exact:true})).toBeHidden();
  await openWeb(page);
  await page.getByLabel('Target URL').fill('https://shop.example.test');
  await page.getByLabel('Permission and program rules').fill('Owned test shop; no payment processing.');
  await page.getByRole('button',{name:'Start investigation',exact:true}).click();
  await expect(page.getByText('Searching cited learning material.',{exact:true})).toBeVisible();
  expect(calls.filter(c => c.method==='tasks.start')).toHaveLength(1);
  expect(calls.find(c => c.method==='projects.save')?.params.read_only_paths).toEqual([]);
  await page.screenshot({path:test.info().outputPath('simple-investigation.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(() => document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:test.info().outputPath('simple-investigation-mobile.png'),fullPage:true});
});

test('deleting a project needs confirmation and keeps the other project and shared library', async ({page}) => {
  const calls = await backend(page,{projects:[{id:'p',name:'Delete me',scope:['https://example.test']},{id:'keep',name:'Keep me',scope:['https://keep.test']}],sources:[{id:'s',title:'Shared lesson',status:'ready'}]});
  await page.goto('/');
  await openWeb(page, 'Targets & scope');
  await page.getByLabel('Project', {exact:true}).selectOption('p');
  const row = page.locator('.project-row').filter({hasText:'Delete me'});
  await row.getByRole('button',{name:'Delete project',exact:true}).click();
  await page.getByRole('button',{name:'Keep project',exact:true}).click();
  expect(calls.some(c => c.method==='projects.delete')).toBe(false);
  await row.getByRole('button',{name:'Delete project',exact:true}).click();
  await page.getByRole('dialog').getByRole('button',{name:'Delete project',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Delete me',exact:true})).toHaveCount(0);
  await expect(page.getByRole('heading',{name:'Keep me',exact:true})).toBeVisible();
  await expect(page.getByLabel('Project', {exact:true})).toHaveValue('keep');
  await page.getByRole('button',{name:'Library',exact:true}).click();
  await expect(page.getByRole('button',{name:'Shared lesson',exact:true})).toBeVisible();
});

test('library accepts website links and multiple files including ZIP', async ({page}) => {
  const calls = await backend(page);
  await page.goto('/');
  await page.getByRole('button',{name:'Library',exact:true}).click();
  await page.getByRole('button',{name:'Upload / add link',exact:true}).click();
  await page.getByRole('button',{name:'Paste a link',exact:true}).click();
  await page.getByLabel('Public link').fill('https://example.com/course.zip');
  await page.getByRole('dialog').getByRole('button',{name:'Import resource',exact:true}).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(calls.find(c => c.method==='sources.import')?.params.url).toBe('https://example.com/course.zip');
  await page.getByRole('button',{name:'Upload / add link',exact:true}).click();
  await page.getByRole('button',{name:'Upload files',exact:true}).click();
  await page.locator('input[type=file]').setInputFiles([{name:'notes.zip',mimeType:'application/zip',buffer:Buffer.from('ZIP intake fixture')},{name:'code.py',mimeType:'text/plain',buffer:Buffer.from('print("only text")')}]);
  await page.getByRole('dialog').getByRole('button',{name:'Import resource',exact:true}).click();
  await expect.poll(() => calls.filter(c => c.method==='sources.import').length).toBe(3);
  expect(calls.filter(c => c.method==='sources.import').map(c => c.params.filename)).toEqual([undefined,'notes.zip','code.py']);
});

test('custom system prompt saves with other settings and never replaces the base prompt client-side', async ({ page }) => {
  const calls = await backend(page);
  await page.goto('/');
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  const box = page.getByLabel('Your AI instructions (optional)');
  await expect(box).toHaveValue('');
  await box.fill('Always write findings in formal British English.');
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await expect.poll(() => calls.filter(c => c.method === 'settings.save').length).toBe(1);
  const saved = calls.find(c => c.method === 'settings.save')!;
  expect(saved.params.custom_system_prompt).toBe('Always write findings in formal British English.');
  // reloading re-fetches settings.get, which this fixture does not echo the custom prompt back from -
  // confirming the field is driven by the server value, not left stuck with stale local-only text.
  await page.reload();
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await expect(page.getByLabel('Your AI instructions (optional)')).toHaveValue('');
});

test('settings can pause outbound work without starting a run', async ({page}) => {
  const calls = await backend(page); await page.goto('/');
  await page.getByRole('button',{name:'Settings',exact:true}).click();
  await page.getByText('Task controls',{exact:true}).click();
  await page.getByRole('button',{name:'Pause outbound requests',exact:true}).click();
  await expect(page.getByRole('button',{name:'Resume outbound requests',exact:true})).toBeVisible();
  expect(calls.some(c => c.method === 'system.stop')).toBe(true);
  expect(calls.some(c => c.method === 'runs.start')).toBe(false);
});
