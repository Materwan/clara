// Talking to Clara: same-origin requests with the session cookie and the X-Clara-Web header (which another site
// cannot add, so the cookie alone is never enough), JSON bodies, `{error: {code, message}}` errors, and server-sent
// events read from a fetch stream (EventSource cannot send the header).

import { TimedOut, retrying, within } from './retry';
import { session } from './state.svelte';

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message);
  }
}

const HEADERS = { 'X-Clara-Web': '1', 'X-Clara-Client': 'web' };

/** How a read waits and asks again. The other methods are not repeated (a repeated write could be done twice) and wait as long as they need. */
export type ReadOptions = {
  /** ms to wait for one answer before giving up on it (default 30 s). */
  timeout?: number;
  /** How many more times to ask after a dropped connection, a timeout or a proxy answering while Clara restarts (default 2). */
  retries?: number;
};
const READ_TIMEOUT = 30_000;
const READ_RETRIES = 2;
const RESTARTING = new Set([502, 503, 504]); // what the proxy in front of Clara answers while she restarts or cannot be reached

async function attempt<T>(method: string, path: string, body: unknown, signal: AbortSignal | undefined, timeout: number): Promise<T> {
  try {
    return await within(timeout, signal, async (cancel) => {
      const response = await fetch(path, {
        method,
        credentials: 'same-origin',
        headers: body === undefined ? HEADERS : { ...HEADERS, 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: cancel,
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
    });
  } catch (error) {
    // status 0: the request never got an answer (the browser's own words, "Failed to fetch", say nothing to you)
    if (error instanceof TimedOut) throw new ApiError(0, 'timeout', 'Clara did not answer in time: try again.');
    if (error instanceof TypeError) throw new ApiError(0, 'network', 'Clara cannot be reached: check the connection and try again.');
    throw error;
  }
}

const worthAskingAgain = (error: unknown): boolean => error instanceof ApiError && (error.status === 0 || RESTARTING.has(error.status));

async function request<T>(method: string, path: string, body?: unknown, signal?: AbortSignal, read: ReadOptions = {}): Promise<T> {
  if (method !== 'GET') return attempt<T>(method, path, body, signal, 0);
  return retrying(() => attempt<T>(method, path, body, signal, read.timeout ?? READ_TIMEOUT), {
    retries: read.retries ?? READ_RETRIES,
    retryable: worthAskingAgain,
    cancelled: () => signal?.aborted ?? false,
  });
}

export const api = {
  get: <T = any>(path: string, params?: Record<string, string | number | boolean | undefined>, read?: ReadOptions) => {
    const query = params
      ? '?' + new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== '').map(([k, v]) => [k, String(v)])).toString()
      : '';
    return request<T>('GET', path + (query === '?' ? '' : query), undefined, undefined, read);
  },
  post: <T = any>(path: string, body: unknown = {}) => request<T>('POST', path, body),
  put: <T = any>(path: string, body: unknown = {}) => request<T>('PUT', path, body),
  patch: <T = any>(path: string, body: unknown = {}) => request<T>('PATCH', path, body),
  del: <T = any>(path: string) => request<T>('DELETE', path),
  /** A DELETE that goes through even as the page closes (keepalive), and whose failure nobody reads. */
  leave: (path: string) => fetch(path, { method: 'DELETE', credentials: 'same-origin', headers: HEADERS, keepalive: true }).catch(() => undefined),
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
