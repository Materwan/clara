// Approve all next: the grants of the conversation on screen. The page that pressed the button beats every few seconds
// to keep them alive; when it is closed, reloaded or left, the beat stops and the server ends them (and this page asks
// it to at once). A grant is never resumed by another page: each page load has its own `tab`.

import { api } from './api';
import { toast } from './state.svelte';

export type Grant = { id: number; tool: string; class: string; calls: number; max_calls: number; seconds_left: number; pressed_by: string };

const tab = typeof crypto?.randomUUID === 'function' ? crypto.randomUUID() : `tab-${Math.random().toString(36).slice(2)}${Date.now()}`;
export const grants = $state<{ conversation: string | null; items: Grant[] }>({ conversation: null, items: [] });
let timer: ReturnType<typeof setInterval> | undefined;

function take(data: { grants?: Grant[]; ended?: { tool: string; calls: number; reason: string }[] }): void {
  grants.items = data.grants ?? [];
  for (const ended of data.ended ?? []) toast(`Approve all next for ${ended.tool} ended: ${ended.reason} (${ended.calls} calls).`);
  if (!grants.items.length) clear();
}

function clear(): void {
  if (timer !== undefined) clearInterval(timer);
  timer = undefined;
  grants.conversation = null;
  grants.items = [];
}

async function beat(): Promise<void> {
  const conversation = grants.conversation;
  if (!conversation) return;
  try {
    const data = await api.put(`/v1/conversations/${conversation}/grants`, { tab });
    if (grants.conversation === conversation) take(data);
  } catch {
    /* the next beat tries again; the server ends the grant if none gets through */
  }
}

/** Press the button on a waiting request: it is allowed, and the next ones of its tool in its conversation too. */
export async function press(approvalId: number, conversation: string): Promise<{ status: string }> {
  if (grants.conversation && grants.conversation !== conversation) stop();
  const data = await api.post(`/v1/approvals/${approvalId}/grant`, { tab });
  grants.conversation = conversation;
  grants.items = data.grants;
  if (timer === undefined) timer = setInterval(beat, (data.beat ?? 10) * 1000);
  return data.approval;
}

/** Stop every grant of the conversation: the button, a conversation left, the page closed. */
export function stop(): void {
  const conversation = grants.conversation;
  clear();
  if (conversation) void api.leave(`/v1/conversations/${conversation}/grants`);
}

if (typeof window !== 'undefined') window.addEventListener('pagehide', stop);
