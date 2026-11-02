import type { RpcResults } from './types';
export type RecordData = Record<string, any>;

declare global { interface Window { __TAURI_INTERNALS__?: unknown } }

let requestId = 0;
export function rpc<M extends keyof RpcResults>(method: M, params?: RecordData): Promise<RpcResults[M]>;
export function rpc<T = any>(method: string, params?: RecordData): Promise<T>;
export async function rpc<T = any>(method: string, params: RecordData = {}): Promise<T> {
  if (window.__TAURI_INTERNALS__) {
    const { invoke } = await import('@tauri-apps/api/core');
    return invoke<T>('rpc', { method, params });
  }
  const response = await fetch(`${import.meta.env.VITE_RPC_URL || 'http://127.0.0.1:8765'}/rpc`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${import.meta.env.VITE_RPC_TOKEN || ''}` },
    body: JSON.stringify({ id: ++requestId, method, params }),
  });
  if (!response.ok) throw new Error(`Backend returned ${response.status}. Check that the local backend is running with the matching session token.`);
  const payload = await response.json();
  if (payload.error) throw new Error(payload.error.message || String(payload.error));
  return payload.result;
}

export function asList(value: any): RecordData[] {
  if (Array.isArray(value)) return value;
  for (const key of ['items', 'results', 'sources', 'traffic', 'tests', 'actions', 'findings', 'runs', 'projects', 'tools']) {
    if (Array.isArray(value?.[key])) return value[key];
  }
  return [];
}

export function pretty(value: any): string {
  if (typeof value === 'string') return value;
  if (value == null) return '';
  return JSON.stringify(value, null, 2);
}

export function httpText(request: RecordData | undefined): string {
  if (!request) return 'No message captured.';
  const first = request.method ? `${request.method} ${request.url || '/'}` : `HTTP ${request.status || request.status_code || ''}`;
  const headers = Array.isArray(request.headers) ? request.headers.map((h: any) => Array.isArray(h) ? h.join(': ') : `${h.name}: ${h.value}`).join('\n') : Object.entries(request.headers || {}).map(([key, value]) => `${key}: ${value}`).join('\n');
  return `${first}\n${headers}\n\n${pretty(request.body ?? request.content ?? '')}`;
}

export async function fileBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error('Could not read the selected file.'));
    reader.onload = () => resolve(String(reader.result).split(',')[1]);
    reader.readAsDataURL(file);
  });
}
