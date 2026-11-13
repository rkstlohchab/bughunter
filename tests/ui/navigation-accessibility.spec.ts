import { expect, test } from '@playwright/test';
import { chatFixture } from './chat-fixture';

test('task guide and manual workspaces fit at minimum desktop height', async ({page}) => {
  await chatFixture(page);
  await page.setViewportSize({width: 1380, height: 560});
  await page.goto('/');
  const nav = page.getByRole('navigation', {name: 'Main navigation'});
  await expect(nav.getByRole('button')).toHaveCount(3);
  await page.getByRole('button', {name: 'Tools', exact: true}).click();
  const guide = page.getByRole('dialog', {name: 'Tools', exact: true});
  await expect(guide.getByRole('button', {name: 'Review a website', exact: false})).toBeVisible();
  await guide.getByText('Open individual workspaces', {exact: true}).click();
  await guide.getByLabel('Find a workspace').fill('prompt');
  await guide.getByRole('button', {name: 'Prompt editor', exact: true}).click();
  await expect(page.getByRole('button', {name: 'Switch to light theme'})).toBeInViewport();
  await expect(page.getByRole('button', {name: 'Stop all', exact: true})).toBeInViewport();
  await nav.getByRole('button', {name: 'Library', exact: true}).click();
  await expect(page.getByText('Adding sources builds searchable knowledge;', {exact: false})).toBeVisible();
});

test('model selection supports keyboard navigation and long names without overflow', async ({page}) => {
  await chatFixture(page);
  const longName = 'workspace/Long-Local-Research-Model-With-Extended-Context-Q8_0:latest';
  let active = longName;
  await page.route('http://127.0.0.1:8765/rpc', async route => {
    if (route.request().method() === 'OPTIONS') return route.fallback();
    const {id, method, params} = route.request().postDataJSON();
    if (method === 'models.select') active = params.model;
    if (!['models.available', 'models.select'].includes(method)) return route.fallback();
    await route.fulfill({headers: {'Access-Control-Allow-Origin': '*'}, json: {id, result: method === 'models.select' ? {} : {
      providers: [{provider: 'ollama', label: 'Local model', ready: true, models: [longName, 'compact-model']},
                  {provider: 'custom', label: 'Custom provider', ready: false, models: [], needs: 'Add your endpoint in Settings.'}],
      active: {provider: 'ollama', model: active},
    }}});
  });
  await page.goto('/');
  const trigger = page.getByRole('button', {name: 'Choose a model', exact: true});
  await expect(trigger).toHaveAttribute('title', `Local model · ${longName}`);
  await trigger.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('menuitem', {name: 'My own models', exact: false})).toBeFocused();
  await page.keyboard.press('ArrowRight');
  await expect(page.getByRole('menuitemradio', {name: longName, exact: true})).toBeFocused();
  await page.keyboard.press('ArrowDown');
  await expect(page.getByRole('menuitemradio', {name: 'compact-model'})).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(trigger).toBeFocused();
  await expect(page.getByRole('menu')).toHaveCount(0);
  await trigger.click();
  await page.keyboard.press('ArrowRight');
  await page.keyboard.press('ArrowDown');
  await page.keyboard.press('Enter');
  await expect(trigger).toHaveAttribute('title', 'Local model · compact-model');
  await trigger.click();
  await page.keyboard.press('ArrowRight');
  await page.keyboard.press('ArrowLeft');
  await page.keyboard.press('ArrowDown');
  await page.keyboard.press('ArrowRight');
  await expect(page.getByRole('menuitem', {name: 'Configure Custom provider'})).toBeFocused();
  await page.keyboard.press('Escape');
  // Restore the long label to check truncation in both desktop and narrow layouts.
  await trigger.click();
  await page.keyboard.press('ArrowRight');
  await page.getByRole('menuitemradio', {name: longName, exact: true}).click();
  for (const width of [1380, 760, 390]) {
    await page.setViewportSize({width, height: 778});
    await trigger.click();
    await page.keyboard.press('ArrowRight');
    const menu = await page.locator('.provider-model-menu').boundingBox();
    expect(menu).not.toBeNull();
    expect(menu!.x).toBeGreaterThanOrEqual(0);
    expect(menu!.x + menu!.width).toBeLessThanOrEqual(width);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.keyboard.press('Escape');
    await page.screenshot({path: test.info().outputPath(`chat-${width}-dark.png`), fullPage: true});
  }
  await page.setViewportSize({width: 1380, height: 778});
  await page.getByRole('button', {name: 'Switch to light theme'}).click();
  await page.screenshot({path: test.info().outputPath('chat-desktop-light.png'), fullPage: true});
});

test('chat search explains no matches and the narrow history panel can be dismissed', async ({page}) => {
  const state = await chatFixture(page);
  state.chats.push({id: 'saved', title: 'Review food coupons', messages: []});
  await page.goto('/');
  await page.getByRole('textbox', {name: 'Search chats'}).fill('no matching conversation');
  await expect(page.getByText('No matching chats', {exact: true})).toBeVisible();
  await expect(page.getByText('No chats yet', {exact: true})).toHaveCount(0);
  await page.getByRole('button', {name: 'Clear search', exact: true}).click();
  await expect(page.getByRole('button', {name: 'Review food coupons', exact: true})).toBeVisible();
  await page.setViewportSize({width: 390, height: 720});
  const toggle = page.getByRole('button', {name: 'Chat history', exact: true});
  await toggle.click();
  await expect(page.getByRole('complementary', {name: 'Saved conversations'})).toBeVisible();
  await page.getByRole('button', {name: 'Close chat history', exact: true}).click();
  await expect(toggle).toBeFocused();
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await toggle.click();
  await page.getByRole('textbox', {name: 'Search chats'}).focus();
  await page.keyboard.press('Escape');
  await expect(toggle).toBeFocused();
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
});
