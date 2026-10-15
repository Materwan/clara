// What the whole site shares: sign-in, the route, the live event stream, pending approvals, alerts, toasts.

export const session = $state({ signedIn: true, checked: false, token: '', approvals: 'all' });

export const route = $state({ path: '/chat', params: {} as Record<string, string>, query: new URLSearchParams() });

export const live = $state({
  connected: false,
  server: 'running',
  approvals: [] as any[],
  alerts: {} as Record<string, string>,
  feed: [] as any[],
  refresh: 0, // bumped when something changed that open pages may want to reload
});

export const toasts = $state({ list: [] as { id: number; text: string; kind: string }[] });

let next = 1;
export function toast(text: string, kind: 'info' | 'error' | 'ok' = 'info'): void {
  const id = next++;
  toasts.list.push({ id, text, kind });
  setTimeout(() => {
    const index = toasts.list.findIndex((item) => item.id === id);
    if (index >= 0) toasts.list.splice(index, 1);
  }, kind === 'error' ? 8000 : 4000);
}

export function readRoute(): void {
  const hash = location.hash.replace(/^#/, '') || '/chat';
  const [path, query] = hash.split('?', 2);
  const parts = path.split('/').filter(Boolean);
  route.path = '/' + (parts[0] ?? 'chat');
  route.params = parts[1] ? { id: decodeURIComponent(parts[1]) } : {};
  route.query = new URLSearchParams(query ?? '');
}

export function go(path: string): void {
  location.hash = '#' + path;
}
