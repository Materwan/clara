// What the forms of a schedule and of a hook have in common, in the same words: how often a run that failed is tried again, and the
// first line of a prompt for a summary. The values are the numbers the server takes (`attempts`).
export const ATTEMPTS = [
  { value: '1', label: 'Give up', hint: 'A model that is busy or unreachable ends the run.' },
  { value: '2', label: 'Once more', hint: 'Tries again once, after 1 minute.' },
  { value: '3', label: 'Twice more', hint: 'Tries again after 1 minute, then after 5.' },
];

export const RETRY_HELP = 'A run that already used a tool or wrote text is never run again, so its actions are not repeated.';

// A prompt as one short line of a summary: the first lines, cut where a word ends.
export function excerpt(text: string, limit = 140): string {
  const flat = text.replace(/\s+/g, ' ').trim();
  if (flat.length <= limit) return flat;
  return flat.slice(0, flat.lastIndexOf(' ', limit) > limit / 2 ? flat.lastIndexOf(' ', limit) : limit) + '…';
}

// A list in words: “a, b and c”.
export function inWords(items: string[]): string {
  if (items.length < 2) return items.join('');
  return `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`;
}
