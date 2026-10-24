// The side panel of the chat (files and vault notes Clara wrote, QCMs): whether you last left it open or closed is kept in this browser.
// It starts closed; it opens by itself when something arrives, unless you closed it.

/** A workspace file Clara wrote in the conversation (GET /v1/conversations/{id}/panel). */
export interface PanelFile {
  path: string;
  created: string; // the first change made in the conversation
  updated: string;
  size: number;
}

/** A vault note Clara wrote in the conversation (same route): `created` if the conversation made it, `edited` if it changed one. */
export interface PanelNote {
  path: string;
  action: 'created' | 'edited';
  created: string;
  updated: string;
  size: number;
}

export type Choice = 'open' | 'closed' | null;

const KEY = 'clara-panel';

function remembered(): Choice {
  try {
    const saved = localStorage.getItem(KEY);
    return saved === 'open' || saved === 'closed' ? saved : null;
  } catch {
    return null; // nothing kept, or unreadable
  }
}

/** What you chose last (null: you never did). */
export const panel = $state<{ choice: Choice }>({ choice: remembered() });

export function remember(choice: 'open' | 'closed'): void {
  panel.choice = choice;
  try {
    localStorage.setItem(KEY, choice);
  } catch {
    /* not remembered: the page keeps it until reloaded */
  }
}

export const baseName = (path: string): string => path.split('/').pop() || path;

/** A note is named without its extension, as in Obsidian. */
export const noteName = (path: string): string => baseName(path).replace(/\.md$/i, '');

/** Where the Library shows a file of the workspace, or a note of the vault (its page reads these two addresses). */
export const libraryLink = (kind: 'file' | 'note', path: string): string =>
  kind === 'file' ? `#/library?path=${encodeURIComponent(path)}` : `#/library?view=vault&note=${encodeURIComponent(path)}`;

export function folderOf(path: string): string {
  const cut = path.lastIndexOf('/');
  return cut < 0 ? '' : path.slice(0, cut + 1);
}

/** When a card was made: the time today, the date and time before. */
export function stamp(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  const today = new Date().toDateString() === date.toDateString();
  return today
    ? date.toLocaleTimeString(undefined, { timeStyle: 'short' })
    : date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}
