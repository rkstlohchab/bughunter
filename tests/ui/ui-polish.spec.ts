import { expect, test, type Page } from '@playwright/test';

async function stub(page: Page) {
  await page.route('http://127.0.0.1:8765/rpc', async route => {
    if (route.request().method() === 'OPTIONS') {
      await route.fulfill({ status: 204, headers: {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Headers': 'Content-Type, Authorization',
        'Access-Control-Allow-Methods': 'POST,OPTIONS',
      }});
      return;
    }
    const { id, method } = route.request().postDataJSON();
    const emptyMethods = new Set(['chat.list','redteam.cases','redteam.history','tor.sources','tor.pages']);
    let result: unknown = {};
    if (method.endsWith('.list') || emptyMethods.has(method)) result = [];
    if (method === 'settings.get') result = { provider:'openai_compatible',model:'bughunter-moe',api_base_url:'https://example.ngrok-free.app/v1',custom_system_prompt:'' };
    if (method === 'settings.credentials') result = { openai_compatible:true };
    if (method === 'tools.status') result = { burp:{available:false},nuclei:{available:true},browser:{available:true} };
    if (method === 'redteam.insights') result = {case_count:0,trial_count:0,verdict_counts:{},lessons:[]};
    if (method === 'tor.settings') result = { socks_port:9050,allow_remote_ai:false };
    if (method === 'tor.browser.config') result = {binary_path:'',driver_path:'',allow_scripts:false};
    if (method === 'tor.browser.status') result = {running:false};
    if (method === 'system.status') result = {emergency_stop:false};
    await route.fulfill({json:{id,result},headers:{'Access-Control-Allow-Origin':'*'}});
  });
}

test('all web testing sections are visible, with actual model rather than hard-coded billing', async ({page}) => {
  await stub(page); await page.goto('/');
  await page.getByRole('navigation',{name:'Main navigation'}).getByRole('button',{name:'Tasks',exact:true}).click();
  await page.getByRole('button',{name:'Advanced tools (optional)'}).click();
  for (const title of ['Start investigation','Targets & scope','Captured requests','Suggested checks','Approvals','Reports']) {
    await expect(page.getByRole('navigation',{name:'All web-testing tools'}).getByRole('button',{name:title})).toBeVisible();
  }
  await expect(page.getByText('Kaggle / custom AI · bughunter-moe')).toBeVisible();
  await expect(page.getByText('separate API billing')).toHaveCount(0);
  await expect(page.getByRole('region',{name:'Available web testing integrations'})).toBeVisible();
});

test('dark mode is the default and theme switch works in Settings', async ({page}) => {
  await stub(page); await page.goto('/');
  await expect(page.locator('html')).toHaveAttribute('data-theme','dark');
  await page.getByRole('button',{name:'Settings',exact:true}).click();
  await page.getByRole('button',{name:'Light mode'}).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme','light');
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-theme','light');
});

test('form content stays inside cards at desktop, tablet and narrow viewport', async ({page}) => {
  await stub(page); await page.goto('/');
  await page.getByRole('navigation',{name:'Main navigation'}).getByRole('button',{name:'Tasks',exact:true}).click();
  await page.getByRole('button',{name:'Advanced tools (optional)'}).click();
  for (const workspace of ['AI testing','Tor browser']) {
    await page.getByRole('navigation',{name:'Research workspaces'}).getByRole('button',{name:workspace}).click();
    for (const width of [1365,820,390]) {
      await page.setViewportSize({width,height:920});
      await expect.poll(async()=>page.evaluate(()=>{
        const nodes=Array.from(document.querySelectorAll<HTMLElement>('.redteam-layout .surface > .field,.redteam-layout .surface > .form-grid,.training-stack .surface > .field,.training-stack .surface > .form-grid,.training-stack .surface > .check-row'));
        return nodes.every(node=>{
          const card=node.closest('.surface'); if (!card) return true;
          const a=node.getBoundingClientRect(),b=card.getBoundingClientRect();
          return a.left >= b.left + 8 && a.right <= b.right - 8;
        });
      })).toBe(true);
    }
  }
});
