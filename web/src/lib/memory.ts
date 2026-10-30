// What deleting a project, a schedule or a hook says about the facts it keeps: every one goes, live or not (the database
// removes them all), so the count is every fact of its memory, and the live ones are told apart from the rest.
export function keptNote(kept: { facts: number; total: number }): string {
  const { facts, total } = kept;
  if (!total) return '';
  const noun = total === 1 ? 'fact' : 'facts';
  if (facts === total) return ` The ${total} ${noun} it remembers ${total === 1 ? 'is' : 'are'} deleted too.`;
  return ` The ${total} ${noun} it keeps ${total === 1 ? 'is' : 'are'} deleted too: ${facts} live, ${total - facts} expired or replaced.`;
}
