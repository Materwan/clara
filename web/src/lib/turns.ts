// A conversation cut into exchanges, for drawing it a few at a time (Chat.svelte). Each question starts an exchange, with
// the replies and tool results that follow it. Nothing in an exchange depends on the one before it, so the exchanges can be
// grouped one by one and the result is the same as grouping the whole history at once.
// The instructions of a skill Clara ran (a stored user row, no question) are left out here, which is the one door to the drawing: the run line of her call
// says it, so they are no bubble, no question for the answer after them, and no part of the grouping (the answer stays with the real question).

import { isRan } from './skills';

export function splitTurns<T extends { role: string; shown?: string }>(history: T[]): T[][] {
  const turns: T[][] = [];
  for (const item of history) {
    if (item.role === 'user' && isRan(item.shown)) continue;
    if (item.role === 'user' || turns.length === 0) turns.push([item]);
    else turns[turns.length - 1].push(item);
  }
  return turns;
}

/**
 * Is `last`, the end of the stored history, the question of the answer being followed (`resumed`: what the run's `resume`
 * event says was asked)? The server stores a question when it is sent, so a page opened mid-answer already has it: it is
 * drawn once, and the answer goes after it. The stored text may carry the documents the resume line leaves out, or be the
 * instructions of a command whose typed form is `shown`. A server that does not store it yet leaves another end: false.
 */
export function isKeptQuestion(last: { role: string; content?: string; shown?: string } | undefined, resumed: string): boolean {
  if (last?.role !== 'user') return false;
  return (last.content ?? '').includes(resumed) || last.shown === resumed;
}
