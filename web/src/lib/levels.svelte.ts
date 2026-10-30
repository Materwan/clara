// Auto levels (DECISIONS.md, « Auto levels »): the ladder every selector draws, kept once for the whole page. The Auto mode
// page edits it and tells this store; a selector asks for it when it appears.

import { api } from './api';

export type Level = { position: number; name: string; key: string };
/** One class of one tool: a tool with several classes (a script with or without network) has a row for each, with its own default. */
export type ToolRow = {
  name: string;
  description: string;
  class: string;
  classes: string[];
  ask_from: number;
  approve_from: number;
  default_ask_from: number;
  default_approve_from: number;
  changed: boolean;
};
export type ClassRow = { class: string; description: string; ask_from: number; approve_from: number };
export type LevelsState = {
  levels: Level[];
  top: number;
  defaults: { chat: number; unattended: number };
  classes: ClassRow[];
  tools: ToolRow[];
};

export const ladder = $state<{ levels: Level[]; top: number; defaults: { chat: number; unattended: number }; loaded: boolean }>({
  levels: [],
  top: 5,
  defaults: { chat: 3, unattended: 3 },
  loaded: false,
});

/** Keep what the server answered (every route of the Auto mode page answers with the whole state). */
export function take(state: Pick<LevelsState, 'levels' | 'top' | 'defaults'>): void {
  ladder.levels = state.levels;
  ladder.top = state.top;
  ladder.defaults = state.defaults;
  ladder.loaded = true;
}

export async function loadLadder(): Promise<void> {
  if (ladder.loaded) return;
  take(await api.get('/v1/levels'));
}

export const nameOf = (position: number): string => ladder.levels.find((level) => level.position === position)?.name ?? '';
export const label = (position: number): string => `${position} · ${nameOf(position)}`;

/** The warning that goes with the last level: it is the one that suspends the raises. */
export const TOP_WARNING =
  'The last level runs every tool without asking and suspends the tainted-turn rule and the secret check in a chat: ' +
  'a web page, a mail or an issue that Clara reads could make her push, send or delete with no card at all.';

/** The cells of a tool across the ladder: refuse, ask or run at each level (level 0 only refuses, the last level only runs). */
export function cells(ask: number, approve: number, top: number): ('refuse' | 'ask' | 'run')[] {
  return Array.from({ length: top + 1 }, (_, level) => (level === 0 ? 'refuse' : level >= top || level >= approve ? 'run' : level >= ask ? 'ask' : 'refuse'));
}
