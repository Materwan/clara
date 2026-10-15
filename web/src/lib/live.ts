// The live event stream: reminders, notifications, approvals, alerts and server states as they come. It reconnects
// by itself; the server keeps this browser's cursor, so nothing is missed while away.

import { api, stream } from './api';
import { live, session, toast } from './state.svelte';

const SHOWN: Record<string, string> = { reminder: '⏰', notification: '🔔', task: '📋', schedule: '🗓', alert: '⚠️', update: '⬆️' };
let running = false;

export async function loadPending(): Promise<void> {
  try {
    live.approvals = (await api.get('/v1/approvals', { status: 'pending' })).approvals;
    live.alerts = (await api.get('/v1/admin/alerts')).active;
  } catch {
    /* shown on the pages that need them */
  }
}

export async function listen(): Promise<void> {
  if (running) return;
  running = true;
  await loadPending();
  let wait = 1000;
  while (session.signedIn) {
    try {
      for await (const event of stream('GET', '/v1/events/stream')) {
        live.connected = true;
        wait = 1000;
        handle(event);
      }
    } catch {
      /* the server went away: try again */
    }
    live.connected = false;
    await new Promise((resolve) => setTimeout(resolve, wait));
    wait = Math.min(wait * 2, 30_000);
  }
  running = false;
}

function handle(event: any): void {
  if (event.type === 'server') {
    live.server = event.state ?? event.payload?.state ?? 'running';
    return;
  }
  live.feed.unshift(event);
  live.feed.splice(50);
  if (event.type === 'approval' || event.type === 'approval_resolved') {
    loadPending();
    live.refresh++;
    return;
  }
  if (event.type === 'alert') loadPending();
  const icon = SHOWN[event.type];
  if (icon) {
    toast(`${icon} ${event.title && event.type !== 'reminder' ? event.title + ': ' : ''}${event.text}`, event.type === 'alert' ? 'error' : 'info');
    if (document.hidden && 'Notification' in window && Notification.permission === 'granted') {
      new Notification(event.title || 'Clara', { body: event.text });
    }
  }
  live.refresh++;
}
