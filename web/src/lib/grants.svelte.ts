// Approve all next: the grants of the conversation on screen (this conversation only, 30 minutes, any tool; the server keeps
// them, so closing the page does not end them). Read when a conversation opens and after a change; Stop ends them.

import { api } from './api';
import { toast } from './state.svelte';

export type Grant = { id: number; tool: string; calls: number; seconds_left: number; pressed_by: string };

export const grants = $state<{ conversation: string | null; items: Grant[] }>({ conversation: null, items: [] });

function take(conversation: string, data: { grants?: Grant[]; ended?: { tool: string; calls: number; reason: string }[] }): void {
  if (grants.conversation !== conversation) return;
  grants.items = data.grants ?? [];
  for (const ended of data.ended ?? []) toast(`Approve all next for ${ended.tool} ended: ${ended.reason} (${ended.calls} calls).`);
}

/** Read what is on for a conversation (null: none is on screen). */
export async function load(conversation: string | null): Promise<void> {
  if (grants.conversation !== conversation) grants.items = [];
  grants.conversation = conversation;
  if (!conversation) return;
  try {
    take(conversation, await api.get(`/v1/conversations/${conversation}/grants`));
  } catch {
    /* the bar just stays as it was */
  }
}

/** Press the button on a waiting request: it is allowed, and the next ones of its tool in its conversation too. */
export async function press(approvalId: number, conversation: string): Promise<{ status: string }> {
  const data = await api.post(`/v1/approvals/${approvalId}/grant`);
  grants.conversation = conversation;
  grants.items = data.grants;
  return data.approval;
}

/** Stop every grant of the conversation on screen. */
export async function stop(): Promise<void> {
  const conversation = grants.conversation;
  if (!conversation) return;
  try {
    await api.del(`/v1/conversations/${conversation}/grants`);
    grants.items = [];
  } catch (error) {
    toast((error as Error).message, 'error');
  }
}
