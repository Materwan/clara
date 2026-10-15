// Talking to Clara: same-origin requests with the session cookie and the X-Clara-Web header (which another site
// cannot add, so the cookie alone is never enough), JSON bodies, `{error: {code, message}}` errors, and server-sent
// events read from a fetch stream (EventSource cannot send the header).

import { session } from './state.svelte';

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message);
  }
}

const HEADERS = { 'X-Clara-Web': '1', 'X-Clara-Client': 'web' };

async function request<T>(method: string, path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, {
    method,
    credentials: 'same-origin',
    headers: body === undefined ? HEADERS : { ...HEADERS, 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  });
  if (response.status === 401 && !path.endsWith('/web-login')) session.signedIn = false;
  if (!response.ok) {
    let code = 'error';
    let message = `HTTP ${response.status}`;
    try {
      const data = await response.json();
      code = data?.error?.code ?? code;
      message = data?.error?.message ?? message;
    } catch {
      /* not JSON */
    }
    throw new ApiError(response.status, code, message);
  }
  const text = await response.text();
  return (text ? JSON.parse(text) : {}) as T;
}

export const api = {
  get: <T = any>(path: string, params?: Record<string, string | number | boolean | undefined>) => {
    const query = params
      ? '?' + new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== '').map(([k, v]) => [k, String(v)])).toString()
      : '';
    return request<T>('GET', path + (query === '?' ? '' : query));
  },
  post: <T = any>(path: string, body: unknown = {}) => request<T>('POST', path, body),
  put: <T = any>(path: string, body: unknown = {}) => request<T>('PUT', path, body),
  patch: <T = any>(path: string, body: unknown = {}) => request<T>('PATCH', path, body),
  del: <T = any>(path: string) => request<T>('DELETE', path),
};

export async function download(path: string): Promise<Blob> {
  const response = await fetch(path, { credentials: 'same-origin', headers: HEADERS });
  if (!response.ok) throw new ApiError(response.status, 'error', `HTTP ${response.status}`);
  return response.blob();
}

export function saveBlob(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

/** Server-sent events from a fetch stream: one parsed object per `data:` line. */
export async function* stream(method: string, path: string, body?: unknown, signal?: AbortSignal): AsyncGenerator<any> {
  const response = await fetch(path, {
    method,
    credentials: 'same-origin',
    headers: body === undefined ? HEADERS : { ...HEADERS, 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  });
  if (response.status === 401) session.signedIn = false;
  if (!response.ok || !response.body) {
    let message = `HTTP ${response.status}`;
    try {
      message = (await response.json())?.error?.message ?? message;
    } catch {
      /* not JSON */
    }
    throw new ApiError(response.status, 'error', message);
  }
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value;
    let end: number;
    while ((end = buffer.indexOf('\n\n')) >= 0) {
      const block = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      for (const line of block.split('\n')) {
        if (line.startsWith('data: ')) yield JSON.parse(line.slice(6));
      }
    }
  }
}

export function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(',', 2)[1] ?? '');
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

export const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;

export function when(iso: string | null | undefined): string {
  if (!iso) return '—';
  const date = new Date(iso);
  return date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}

export function localInput(iso: string | null | undefined): string {
  if (!iso) return '';
  const date = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function size(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
}
