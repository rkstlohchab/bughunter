import { test, expect } from '@playwright/test';
import { chatFixture } from './chat-fixture';
import { openTool } from './navigation';

for (const viewport of [{width:1440,height:960}, {width:375,height:812}, {width:812,height:375}]) {
  test(`prompt edits stay local and preserve technical content at ${viewport.width}px`, async ({page}) => {
    await page.setViewportSize(viewport);
    await page.emulateMedia({ reducedMotion: 'reduce' });
    if (viewport.width === 375) await page.addInitScript(() => localStorage.setItem('bughunter.theme', 'light'));
    const state = await chatFixture(page);
    await page.goto('/');
    await openTool(page, 'Prompt editor');
    await expect(page.getByRole('heading', {name:'Prompt editor'})).toBeVisible();
    const original = 'What the hell is causing this error?\nKeep "what the hell" and `kill(pid, 0)` unchanged.\nFind the health memory location.';
    await page.getByRole('textbox', {name:/Original prompt/}).fill(original);
    await page.getByRole('button', {name:'Polish tone', exact:true}).click();
    await expect(page.getByRole('textbox', {name:'Edited prompt', exact:true})).toHaveValue(original.replace('What the hell', 'What'));
    await expect(page.getByText('1 wording change. Review the result below.')).toBeVisible();
    await page.screenshot({path:test.info().outputPath('prompt-editor.png'),fullPage:true});
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    expect(JSON.stringify(state.calls)).not.toContain(original);
    expect(state.calls.some(c => /chat\.(agent|send|autopilot)|redteam\.(run|suggest)/.test(c.method))).toBe(false);
    await page.getByRole('textbox', {name:/Original prompt/}).fill('Explain kill cooldown memory in an offline test.');
    await expect(page.getByRole('textbox', {name:'Edited prompt', exact:true})).toHaveCount(0);
    await page.getByRole('button', {name:'Polish tone', exact:true}).click();
    await expect(page.getByRole('textbox', {name:'Edited prompt', exact:true})).toHaveValue('Explain kill cooldown memory in an offline test.');
    await expect(page.getByText('No matching tone phrases. Your wording is unchanged.')).toBeVisible();
  });
}
