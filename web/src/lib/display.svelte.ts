// What the list of conversations shows, chosen in the account menu and kept in this browser: the conversations a client
// started (Discord, the console) can be left out of it. A pinned conversation, a search and a link always reach them.

import { conversations } from './state.svelte';

export const CLIENTS = [
  { id: 'discord', label: 'Discord' },
  { id: 'console', label: 'Console' },
] as const;

export type Client = (typeof CLIENTS)[number]['id'];

const KEY = 'clara-display';

function remembered(): Record<Client, boolean> {
  const shown = { discord: true, console: true };
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) ?? '{}');
    for (const { id } of CLIENTS) if (saved?.[id] === false) shown[id] = false;
  } catch {
    /* nothing kept, or unreadable: everything shows */
  }
  return shown;
}

/** Whether the conversations of each client are listed (all of them, until you say otherwise). */
export const display = $state<Record<Client, boolean>>(remembered());

export function setShown(client: Client, shown: boolean): void {
  display[client] = shown;
  try {
    localStorage.setItem(KEY, JSON.stringify(display));
  } catch {
    /* not remembered: the page keeps it until reloaded */
  }
  conversations.changed++; // the list in the rail reloads
}

/** The clients to leave out of the list, as the `hide` parameter of the conversations route wants them. */
export const hidden = (): string => CLIENTS.filter(({ id }) => !display[id]).map(({ id }) => id).join(',');
