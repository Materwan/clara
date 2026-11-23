// `/goal` on the page (docs/DECISIONS.md, « /goal »): the command typed in the box, the goal's status line, and how the events of a followed goal
// change it. The server owns the grammar (agent/goals.py `parse`): this only tells a goal command from a message, and words the state.

export type Goal = {
  condition: string; model: string; state: 'active' | 'paused' | 'met' | 'impossible'; reason: string;
  turns_done: number; max_turns: number; seconds_used: number; max_seconds: number; text: string; created_at: string; updated_at: string;
};

export const WAITING = 'waiting for your approval'; // the pause that the owner's answer to an approval ends (agent/goals.py WAITING)
export const SYNTAX = '[--turns N] [--minutes N] [--model provider:model] <condition> | clear | resume';

/** A box that starts with the word `/goal`, alone or followed by a space or a line break: it is sent to the goal route, never to the model. */
export const isGoalCommand = (text: string): boolean => /^\s*\/goal(\s|$)/.test(text);

/** What follows `/goal`, as the server reads it. */
export const goalArgs = (text: string): string => text.replace(/^\s*\/goal/, '').trim();

/** Does this command begin a goal (a condition after the flags)? The others (show, clear, resume, limits) need a goal to exist. */
export function startsGoal(args: string): boolean {
  const rest = args.replace(/^(?:--[a-z]+(?:=|\s+)\S+\s*)+/, '').trim().toLowerCase();
  return rest !== '' && rest !== 'clear' && rest !== 'resume';
}

/** A stored message of a goal (its `shown`: « Goal: <condition> » or « Goal, turn 3 of 10 »): drawn as a divider, never as something you wrote. */
export const goalLine = (shown: string | undefined): string | null =>
  typeof shown === 'string' && /^Goal: |^Goal, turn \d+ of \d+$/.test(shown) ? shown : null;

/** Where a followed goal's run begins in the stored history: the last row whose `shown` is the question the run says it started with (-1: none). */
export function goalRunStart(history: { role: string; shown?: string }[], resumed: string): number {
  if (!goalLine(resumed)) return -1;
  for (let i = history.length - 1; i >= 0; i--) if (history[i].role === 'user' && history[i].shown === resumed) return i;
  return -1;
}

const trim = (value: number): string => String(Number(value.toFixed(1)));

/** « turn 3 of 10 · 4.2 of 15 min » while it works (the turn under way), « 3 of 10 turns · 4.2 of 15 min » once it has stopped. */
export function counts(goal: Goal): string {
  const time = `${trim(goal.seconds_used / 60)} of ${trim(goal.max_seconds / 60)} min`;
  const turns = goal.state === 'active' ? `turn ${Math.min(goal.turns_done + 1, goal.max_turns)} of ${goal.max_turns}` : `${goal.turns_done} of ${goal.max_turns} turns`;
  return `${turns} · ${time}`;
}

export const turnLimit = (goal: Goal): boolean => goal.turns_done >= goal.max_turns;
export const timeLimit = (goal: Goal): boolean => goal.seconds_used >= goal.max_seconds;

/** The buttons of a paused goal. Resume is hidden while an approval waits (the goal goes on after your answer) and while a limit is reached (the server
 *  would refuse it: a limit has to be raised first). */
export function choices(goal: Goal): { resume: boolean; turns: boolean; minutes: boolean } {
  if (goal.state !== 'paused') return { resume: false, turns: false, minutes: false };
  const turns = goal.reason === 'turn limit' || turnLimit(goal);
  const minutes = goal.reason === 'time limit' || timeLimit(goal);
  return { resume: goal.reason !== WAITING && !turnLimit(goal) && !timeLimit(goal), turns, minutes };
}

/** The goal after an event of the followed stream: `goal_turn` (a turn starts) or `goal_state` (the whole goal, after a pause, a verdict, or between turns). */
export function afterEvent(goal: Goal | null, event: { type: string; turn?: number; max_turns?: number; goal?: Goal | null }): Goal | null {
  if (event.type === 'goal_state') return event.goal ?? null;
  if (event.type === 'goal_turn' && goal) return { ...goal, state: 'active', reason: '', turns_done: (event.turn ?? 1) - 1, max_turns: event.max_turns ?? goal.max_turns };
  return goal;
}
