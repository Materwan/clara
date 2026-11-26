// Sub agents (docs/DECISIONS.md, « Sub agents »): the words of a card in the side panel, and the check of the one setting the Admin page changes. The server owns
// the state of a sub agent (GET /v1/conversations/{id}/sub-agents); the page only draws it, and opens the sub agent's conversation, an ordinary one, on a click.

export type SubState = 'running' | 'done' | 'asked' | 'failed';
export type SubAgent = {
  id: string;
  title: string;
  state: SubState;
  report: string; // the start of its last report (one line)
  created_at: string;
  updated_at: string;
  waiting: number; // requests of its own that wait for you
  asking: string; // the first of them, in a few words
  written: number; // files and notes it wrote
};

export type Tone = 'ok' | 'warn' | 'danger' | 'run';

/** The state badge. A request that waits for you comes first: whatever it is doing, it needs you. */
export function badge(sub: SubAgent): { word: string; tone: Tone } {
  if (sub.waiting > 0) return { word: 'Asks you', tone: 'warn' };
  switch (sub.state) {
    case 'running':
      return { word: 'Running', tone: 'run' };
    case 'asked':
      return { word: 'Asked', tone: 'warn' };
    case 'failed':
      return { word: 'Failed', tone: 'danger' };
    default:
      return { word: 'Done', tone: 'ok' };
  }
}

/** How long ago, in words ("2 minutes ago"). A date in the future (a clock a little ahead) or one that is not a date is « just now ». */
export function elapsed(iso: string, now = Date.now()): string {
  const seconds = (now - Date.parse(iso)) / 1000;
  if (!(seconds >= 45)) return 'just now';
  const count = (n: number, unit: string): string => `${n} ${unit}${n === 1 ? '' : 's'} ago`;
  if (seconds < 90 * 60) return count(Math.round(seconds / 60), 'minute');
  if (seconds < 36 * 3600) return count(Math.round(seconds / 3600), 'hour');
  return count(Math.round(seconds / 86400), 'day');
}

/** The muted line under the title: what it is waiting for, or what it did and when ("Reported 2 minutes ago · 1 file written"). */
export function cardLine(sub: SubAgent, now = Date.now()): string {
  const wrote = sub.written > 0 ? `${sub.written} file${sub.written === 1 ? '' : 's'} written` : '';
  if (sub.waiting > 0) {
    const more = sub.waiting > 1 ? ` (and ${sub.waiting - 1} more)` : '';
    return [`Waiting for your permission${sub.asking ? `: ${sub.asking}` : ''}${more}`, wrote].filter(Boolean).join(' · ');
  }
  const when = elapsed(sub.updated_at, now);
  const said = {
    running: `Working, started ${when}`,
    asked: `Asked a question ${when}`,
    failed: `Stopped ${when}`,
    done: `Reported ${when}`,
  }[sub.state] ?? when;
  return [said, wrote].filter(Boolean).join(' · ');
}

// ---------------------------------------------------------------------------------------------------- the Admin setting
export const KEY = 'sub_agents_max'; // the runtime setting: sub agents of one conversation that work at the same time
export const MIN = 1;
export const MAX = 20;

/** What is wrong with what was typed for the setting, or '' (a whole number from 1 to 20: the server refuses anything else). */
export function limitProblem(text: string): string {
  const typed = text.trim();
  const value = /^\d+$/.test(typed) ? Number(typed) : NaN;
  return value >= MIN && value <= MAX ? '' : `Write a whole number from ${MIN} to ${MAX}.`;
}
