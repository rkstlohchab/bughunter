import {openTool} from './navigation';
import { expect, test } from '@playwright/test';
import type { Page } from '@playwright/test';

async function rpcStub(page: Page) {
  await page.route('http://127.0.0.1:8765/rpc', async route => {
    const request = route.request();
    if (request.method() === 'OPTIONS') { await route.fulfill({ status: 204, headers: { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Methods': 'POST, OPTIONS', 'Access-Control-Allow-Headers': 'Authorization, Content-Type' } }); return; }
    const { id, method } = request.postDataJSON();
    const result = method.endsWith('.list') ? [] : method === 'system.status' ? { version: '0.1.0', emergency_stop: false } : {};
    await route.fulfill({ json: { id, result }, headers: { 'Access-Control-Allow-Origin': '*' } });
  });
}

async function openCe(page: Page) {
  await page.goto('/');
  await openTool(page, 'Cheat Engine');
}

test('browser dev UI explains the bridge is desktop-only', async ({ page }) => {
  await rpcStub(page);
  await openCe(page);
  await expect(page.getByText('Desktop app only')).toBeVisible();
});

test('state-changing agent call waits for approval and shows the exact Lua', async ({ page }) => {
  await page.addInitScript(() => {
    const calls: string[] = [];
    (window as any).__ceCalls = calls;
    const cfg = { python_bin: 'python', mcp_script_path: 'x.py', ce_host: '127.0.0.1', ce_port: '17171', ce_auth_token: '', llm_base_url: 'http://127.0.0.1:11434/v1', llm_api_key: 'ollama', llm_model: 'm', system_prompt: 'p', auto_approve_readonly: true };
    (window as any).__TAURI_INTERNALS__ = {
      transformCallback: () => 0,
      invoke: async (cmd: string, args: any) => {
        calls.push(cmd + (cmd === 'ce_resolve_approval' ? ':' + args.approve : ''));
        if (cmd === 'rpc') return args.method === 'settings.memory_bridge' ? {supported:true,base_url:'http://127.0.0.1:11434/v1',model:'fixture',api_key:'',system_prompt:''} : (args.method.endsWith('.list') || args.method === 'training.workspaces') ? [] : args.method === 'system.status' ? { version: '0.1.0', emergency_stop: false } : {};
        if (cmd === 'ce_get_config') return cfg;
        if (cmd === 'ce_connect_mcp') return [{ name: 'read_memory', description: 'r', inputSchema: {} }, { name: 'evaluate_lua', description: 'l', inputSchema: {} }];
        if (cmd === 'ce_run_agent_prompt') return { status: 'awaiting_approval', trace: [{ kind: 'tool_result', name: 'read_memory', payload: 'ok:read' }, { kind: 'approval_required', name: 'evaluate_lua', payload: 'needs approval' }], approval: { call_id: 'c1', name: 'evaluate_lua', arguments: { code: 'return 42' }, reason: '`evaluate_lua` can change CE or target state' } };
        if (cmd === 'ce_resolve_approval') return { status: 'done', trace: [{ kind: 'assistant', name: 'm', payload: 'finished' }], approval: null };
        return {};
      },
    };
  });
  await rpcStub(page);
  await openCe(page);
  await page.getByRole('button', { name: 'Start memory helper' }).click();
  await expect(page.getByText('online, 2 tools')).toBeVisible();
  await page.getByPlaceholder(/offline test program stores its score/).fill('read then run lua');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  const dialog = page.getByRole('alertdialog', { name: 'Approval required' });
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText('return 42');
  await expect(page.getByRole('button', { name: 'Send', exact: true })).toBeDisabled();
  expect(await page.evaluate(() => (window as any).__ceCalls)).not.toContain('ce_resolve_approval:true');
  await dialog.getByRole('button', { name: 'Approve and run' }).click();
  await expect(page.getByText('finished')).toBeVisible();
  await expect(dialog).toBeHidden();
  expect(await page.evaluate(() => (window as any).__ceCalls)).toContain('ce_resolve_approval:true');
});

test('macOS backend identifies the built-in reader and hides Lua', async ({ page }) => {
  await page.addInitScript(() => {
    const cfg = { backend: 'mac_native', mac_script_path: '', python_bin: 'python3', mcp_script_path: '', ce_host: '127.0.0.1', ce_port: '17171', ce_auth_token: '', llm_base_url: 'http://127.0.0.1:11434/v1', llm_api_key: 'ollama', llm_model: 'm', system_prompt: '', auto_approve_readonly: true };
    (window as any).__TAURI_INTERNALS__ = {
      transformCallback: () => 0,
      invoke: async (cmd: string, args: any) => {
        if (cmd === 'rpc') return args.method === 'settings.memory_bridge' ? {supported:true,base_url:'http://127.0.0.1:11434/v1',model:'fixture',api_key:'',system_prompt:''} : (args.method.endsWith('.list') || args.method === 'training.workspaces') ? [] : args.method === 'system.status' ? { version: '0.1.0', emergency_stop: false } : {};
        if (cmd === 'ce_get_config') return cfg;
        if (cmd === 'ce_preflight') return [{ id: 'root', label: 'Root privileges', status: 'warn', detail: 'not root', fix: 'Start the app with sudo.' }, { id: 'python', label: 'Python', status: 'ok', detail: 'Python 3.12', fix: null }];
        return {};
      },
    };
  });
  await rpcStub(page);
  await openCe(page);
  await expect(page.getByRole('heading', { name: 'Memory helper' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Lua', exact: true })).toHaveCount(0);
  await expect(page.getByText(/Uses the built-in Mac memory reader/)).toBeVisible();
  await expect(page.getByText(/Before opening or changing another process, you approve it/)).toBeVisible();
  await page.getByText('Advanced memory options (optional)', {exact:true}).click();
  await expect(page.getByRole('button', {name:'Lua',exact:true})).toHaveCount(0);
});

test('recoverable model failure retains agent context and exposes retry', async ({ page }) => {
  await page.addInitScript(() => {
    const calls: string[] = [];
    (window as any).__bridgeCalls = calls;
    const cfg = { backend: 'cheat_engine', mac_script_path: '', python_bin: 'python', mcp_script_path: 'x.py', ce_host: '127.0.0.1', ce_port: '17171', ce_auth_token: '', llm_base_url: 'http://127.0.0.1:11434/v1', llm_api_key: 'ollama', llm_model: 'm', system_prompt: '', auto_approve_readonly: true };
    (window as any).__TAURI_INTERNALS__ = {
      transformCallback: () => 0,
      invoke: async (cmd: string, args: any) => {
        calls.push(cmd);
        if (cmd === 'rpc') return args.method === 'settings.memory_bridge' ? {supported:true,base_url:'http://127.0.0.1:11434/v1',model:'fixture',api_key:'',system_prompt:''} : (args.method.endsWith('.list') || args.method === 'training.workspaces') ? [] : args.method === 'system.status' ? { version: '0.1.0', emergency_stop: false } : {};
        if (cmd === 'ce_get_config') return cfg;
        if (cmd === 'ce_connect_mcp') return [];
        if (cmd === 'ce_run_agent_prompt') return { status: 'awaiting_retry', trace: [{ kind: 'notice', name: 'model_error', payload: 'model request failed: timeout' }], approval: null };
        if (cmd === 'ce_retry_agent') return { status: 'done', trace: [{ kind: 'assistant', name: 'm', payload: 'recovered' }], approval: null };
        return {};
      },
    };
  });
  await rpcStub(page);
  await openCe(page);
  await page.getByRole('button', { name: 'Start memory helper' }).click();
  await page.getByPlaceholder(/offline test program stores its score/).fill('read state');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  await expect(page.getByRole('alert', { name: 'Model request interrupted' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Send', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: 'Retry model request' }).click();
  await expect(page.getByText('recovered')).toBeVisible();
  await expect(page.getByRole('alert', { name: 'Model request interrupted' })).toBeHidden();
  expect((await page.evaluate(() => (window as any).__bridgeCalls)).filter((s: string) => s === 'ce_run_agent_prompt')).toHaveLength(1);
  expect(await page.evaluate(() => (window as any).__bridgeCalls)).toContain('ce_retry_agent');
});

test('failed approval response does not silently dismiss the approval dialog', async ({ page }) => {
  await page.addInitScript(() => {
    const cfg = { backend: 'cheat_engine', mac_script_path: '', python_bin: 'python', mcp_script_path: 'x.py', ce_host: '127.0.0.1', ce_port: '17171', ce_auth_token: '', llm_base_url: 'http://127.0.0.1:11434/v1', llm_api_key: 'ollama', llm_model: 'm', system_prompt: '', auto_approve_readonly: true };
    (window as any).__TAURI_INTERNALS__ = {
      transformCallback: () => 0,
      invoke: async (cmd: string, args: any) => {
        if (cmd === 'rpc') return args.method === 'settings.memory_bridge' ? {supported:true,base_url:'http://127.0.0.1:11434/v1',model:'fixture',api_key:'',system_prompt:''} : (args.method.endsWith('.list') || args.method === 'training.workspaces') ? [] : args.method === 'system.status' ? { version: '0.1.0', emergency_stop: false } : {};
        if (cmd === 'ce_get_config') return cfg;
        if (cmd === 'ce_connect_mcp') return [];
        if (cmd === 'ce_run_agent_prompt') return { status: 'awaiting_approval', trace: [], approval: { call_id: 'c1', name: 'write_memory', arguments: { address: '0x10' }, reason: 'state-changing tool' } };
        if (cmd === 'ce_resolve_approval') throw new Error('connection lost');
        return {};
      },
    };
  });
  await rpcStub(page);
  await openCe(page);
  await page.getByRole('button', { name: 'Start memory helper' }).click();
  await page.getByPlaceholder(/offline test program stores its score/).fill('read state');
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  const dialog = page.getByRole('alertdialog', { name: 'Approval required' });
  await expect(dialog).toBeVisible();
  await dialog.getByRole('button', { name: 'Approve and run' }).click();
  await expect(page.getByText(/connection lost/)).toBeVisible();
  await expect(dialog).toBeVisible();
});
