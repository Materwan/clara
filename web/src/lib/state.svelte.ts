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

export const toasts = $state({ list: [] as { id: number; text: string; kind: string; icon: string }[] });

// The drawer of narrow screens: the navigation rail (it holds the list of conversations too).
export const ui = $state({ rail: false });

// Whether that drawer is in play: below this width (the same as app.css) it slides over the page instead of sitting beside it.
export const narrow = $state({ rail: false });

// Bumped when a conversation changed (an answer ended, a title was set) so the list in the rail reloads.
export const conversations = $state({ changed: 0 });

export function watchViewport(): () => void {
  const rail = matchMedia('(max-width: 860px)');
  const update = (): void => {
    narrow.rail = rail.matches;
    if (!rail.matches) ui.rail = false; // a drawer left open by a window that grew has nothing to be a drawer of
  };
  update();
  rail.addEventListener('change', update);
  return () => rail.removeEventListener('change', update);
}

let next = 1;
const timers = new Map<number, ReturnType<typeof setTimeout>>();

function arm(id: number, ms: number): void {
  clearTimeout(timers.get(id));
  timers.set(id, setTimeout(() => dismiss(id), ms));
}

export function toast(text: string, kind: 'info' | 'error' | 'ok' = 'info', icon = ''): void {
  const id = next++;
  toasts.list.push({ id, text, kind, icon: icon || (kind === 'error' ? 'warn' : kind === 'ok' ? 'check' : '') });
  arm(id, kind === 'error' ? 8000 : 4000);
}

export function dismiss(id: number): void {
  clearTimeout(timers.get(id));
  timers.delete(id);
  const index = toasts.list.findIndex((item) => item.id === id);
  if (index >= 0) toasts.list.splice(index, 1);
}

/** A toast waits while it is pointed at or has focus (a message you are reading must not vanish), and leaves soon after. */
export function holdToast(id: number): void {
  clearTimeout(timers.get(id));
}

export function releaseToast(id: number): void {
  if (toasts.list.some((item) => item.id === id)) arm(id, 2500);
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
