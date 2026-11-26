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

/** A file Clara handed over at the end of an answer (the `present` event, and `presented` on a stored message). */
export interface Presented {
  kind?: 'workspace' | 'vault' | 'drive';
  ref?: string; // what Clara named: a workspace path, `vault:<note>` or `drive:<id>`; the same file is one card
  path: string; // a workspace path, a note's path, or a Drive file's id
  size: number | null;
  origin?: string | null; // where a workspace file came from, when it came from outside and you have not trusted it
  name?: string; // a Drive file's name, when this server knows it
  url?: string; // a Drive file's link
  missing?: boolean; // the file is not there any more (a conversation opened again later)
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

/** The sections of the panel that fold. The plan card does not. */
export type Section = 'subs' | 'made';

const foldKey = (section: Section): string => `clara-panel-${section}`; // one key for each section, never for each conversation

function foldedBefore(section: Section): boolean {
  try {
    return localStorage.getItem(foldKey(section)) === 'folded';
  } catch {
    return false; // nothing kept, or unreadable: every section starts unfolded
  }
}

/** Which sections you folded. Display only: nothing the model sees, no gate rule. */
export const folded = $state<Record<Section, boolean>>({ subs: foldedBefore('subs'), made: foldedBefore('made') });

export function fold(section: Section, value: boolean): void {
  folded[section] = value;
  try {
    localStorage.setItem(foldKey(section), value ? 'folded' : 'open');
  } catch {
    /* not remembered: the page keeps it until reloaded */
  }
}

/** Whether `after` holds a card that `before` did not: it has just reached its section (a section you folded opens again). */
export function arrived<T>(before: T[], after: T[], key: (item: T) => string): boolean {
  const known = new Set(before.map(key));
  return after.some((item) => !known.has(key(item)));
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
