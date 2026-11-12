import { test, expect } from '@playwright/test';
import { chatFixture } from './chat-fixture';

test('Library distinguishes saved audio progress from frame analysis', async ({ page }) => {
  await chatFixture(page);
  let phase = 'speech';
  let status = 'processing';
  await page.route('http://127.0.0.1:8765/rpc', async route => {
    if (route.request().method() === 'OPTIONS') return route.fallback();
    const request = route.request().postDataJSON();
    if (request.method !== 'sources.list') return route.fallback();
    return route.fulfill({ headers: { 'Access-Control-Allow-Origin': '*' }, json: { id: request.id, result: [
      { id: 'course-video', title: 'Course recording', filename: 'course.mp4', status,
        media_progress: { phase, analyzed: 3, total: 55, timestamp: 1800 } },
    ] } });
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'Library', exact: true }).click();
  await expect(page.getByText('Transcribing audio: 3 / 55 parts')).toBeVisible();
  await expect(page.getByRole('progressbar', { name: 'Transcribing audio' })).toHaveAttribute('value', '3');
  phase = 'scene_scan';
  await page.getByRole('button', { name: 'Refresh workspace', exact: true }).click();
  await expect(page.getByText('Finding screen changes…')).toBeVisible();
  await expect(page.getByRole('progressbar', { name: 'Finding screen changes' })).not.toHaveAttribute('value');
  phase = 'frames';
  await page.getByRole('button', { name: 'Refresh workspace', exact: true }).click();
  await expect(page.getByText('Reading video frames: 3 / 55 frames')).toBeVisible();
  status = 'ready';
  await page.getByRole('button', { name: 'Refresh workspace', exact: true }).click();
  await expect(page.getByRole('progressbar')).toHaveCount(0);
});
