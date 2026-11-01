// A conversation cut into exchanges, for drawing it a few at a time (Chat.svelte). Each question starts an exchange, with
// the replies and tool results that follow it. Nothing in an exchange depends on the one before it, so the exchanges can be
// grouped one by one and the result is the same as grouping the whole history at once.

export function splitTurns<T extends { role: string }>(history: T[]): T[][] {
  const turns: T[][] = [];
  for (const item of history) {
    if (item.role === 'user' || turns.length === 0) turns.push([item]);
    else turns[turns.length - 1].push(item);
  }
  return turns;
}
