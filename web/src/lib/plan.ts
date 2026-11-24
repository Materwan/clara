// The plan Clara keeps for a conversation (docs/DECISIONS.md, « The plan pack »), read only: the words of its status marks, its progress line, and how
// the `plan` stream event and a read of GET /v1/conversations/{id}/plan change what the panel shows. The server owns the plan; the page never writes it.

export type Status = 'todo' | 'doing' | 'done' | 'dropped';
export type Step = { n: number; text: string; status: Status; note: string; outside: boolean };
export type Plan = { conversation: string; title: string; steps: Step[]; updated_at: string };

/** What a status is called, for the eye and for a screen reader. */
export const STATUS_WORDS: Record<Status, string> = { todo: 'To do', doing: 'In progress', done: 'Done', dropped: 'Dropped' };
export const statusWord = (status: string): string => STATUS_WORDS[status as Status] ?? status;

/** The mark on a step that Clara wrote after reading outside content (a mail, a page): neutral, it says when, not that it is bad. */
export const OUTSIDE = 'Written after outside content';

/** Counts of a plan. A dropped step is out of the work: it is not pending and not in the « of N ». `finished`: no step is left to do or in progress. */
export function progress(plan: Plan): { done: number; dropped: number; open: number; finished: boolean } {
  const count = (status: Status): number => plan.steps.filter((step) => step.status === status).length;
  const done = count('done');
  const dropped = count('dropped');
  const open = plan.steps.length - done - dropped;
  return { done, dropped, open, finished: plan.steps.length > 0 && open === 0 };
}

/** The header line: « 3 of 4 done · 1 dropped », and once nothing is left « All steps done » (« All steps dropped » when none was done). */
export function summary(plan: Plan): string {
  const { done, dropped, open, finished } = progress(plan);
  const tail = dropped && done ? ` · ${dropped} dropped` : '';
  if (finished) return done ? `All steps done${tail}` : 'All steps dropped';
  return `${done} of ${done + open} done${tail}`;
}

/** The plan after an event of the stream: `plan` carries the whole plan, or null when Clara removed it. Anything else leaves it alone. */
export function afterEvent(plan: Plan | null, event: { type: string; plan?: Plan | null }): Plan | null {
  return event.type === 'plan' ? (event.plan ?? null) : plan;
}

/** The plan after a read: an answer that began before an event of the stream (the counter moved since) is older than the event and is dropped. */
export const afterRead = (plan: Plan | null, found: Plan | null, startedAt: number, now: number): Plan | null => (startedAt === now ? found : plan);
