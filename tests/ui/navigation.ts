import type { Page } from '@playwright/test';

export async function openTool(page: Page, name: string) {
  await page.getByRole('button', {name: 'Tools', exact: true}).click();
  await page.getByText('Open individual workspaces', {exact: true}).click();
  await page.getByRole('dialog', {name: 'Tools', exact: true}).getByRole('button', {name, exact: true}).click();
}

export async function openWeb(page: Page, section?: string) {
  await openTool(page, 'Web testing');
  if (section) await page.getByRole('navigation', {name: 'Web testing sections'}).getByRole('button', {name: section, exact: true}).click();
}
