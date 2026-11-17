import type { Page } from '@playwright/test';

/** Advanced workspaces live in Settings → General (the top bar has no Tools menu). */
export async function openWorkspaces(page: Page) {
  await page.getByRole('navigation', {name: 'Main navigation'}).getByRole('button', {name: 'Settings', exact: true}).click();
  await page.getByRole('tab', {name: 'General', exact: true}).click();
  await page.getByRole('button', {name: 'Open advanced workspaces', exact: true}).click();
  return page.getByRole('dialog', {name: 'Advanced workspaces', exact: true});
}

export async function openTool(page: Page, name: string) {
  const dialog = await openWorkspaces(page);
  await dialog.getByRole('button', {name, exact: true}).click();
}

export async function openWeb(page: Page, section?: string) {
  await openTool(page, 'Web testing');
  if (section) await page.getByRole('navigation', {name: 'Web testing sections'}).getByRole('button', {name: section, exact: true}).click();
}
