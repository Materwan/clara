// Modes (DECISIONS.md, « Modes instead of auto levels and rules »): the pure rules of the Modes page, kept apart from the
// components so that node can test them.

export type Verdict = 'approve' | 'ask' | 'refuse';
export const VERDICTS: Verdict[] = ['approve', 'ask', 'refuse'];
/** The words and colours of the three verdicts: the project's ok, warn and danger. */
export const KIND: Record<Verdict, 'ok' | 'warn' | 'danger'> = { approve: 'ok', ask: 'warn', refuse: 'danger' };

export type Line = { tool: string; class: string; target: string; verdict: Verdict; tainted: Verdict };
export type Uses = { conversations: number; automation_conversations: number; automations: number; projects: number };
export type Effective = { tool: string; class: string; verdict: Verdict; tainted: Verdict; line: string | null; from: 'mode' | 'default' };
export type ToolInfo = { name: string; description: string; classes: string[]; pack: string | null };

const PERMISSIVE: Record<Verdict, number> = { refuse: 0, ask: 1, approve: 2 };

/** What a tainted turn may be set to, given the normal verdict: never more permissive than it. */
export const taintedChoices = (normal: Verdict): Verdict[] => VERDICTS.filter((verdict) => PERMISSIVE[verdict] <= PERMISSIVE[normal]);

/** The tainted verdict after the normal one changed: it follows the normal one down, and stays where it was otherwise. */
export const taintedAfter = (normal: Verdict, tainted: Verdict): Verdict => (PERMISSIVE[tainted] > PERMISSIVE[normal] ? normal : tainted);

/** The line that decides a tool under one class, by itself: no target, the tool and the class both named. */
export const ownLine = (lines: Line[], tool: string, klass: string): Line | undefined =>
  lines.find((line) => line.tool === tool && line.class === klass && !line.target);

/** The exceptions per target of a tool under one class (`*@epita.fr`, `ws:private/**`). */
export const exceptionsOf = (lines: Line[], tool: string, klass: string): Line[] =>
  lines.filter((line) => line.tool === tool && line.class === klass && !!line.target);

/** The lines of a mode that no row of the table shows (a glob of tools, a class on its own, a target with no tool). */
export function otherLines(lines: Line[], tools: ToolInfo[]): Line[] {
  const rows = new Set(tools.flatMap((tool) => tool.classes.map((klass) => `${tool.name} ${klass}`)));
  return lines.filter((line) => !rows.has(`${line.tool} ${line.class}`));
}

/** The verdict of a tool under a class, as the server worked it out for this mode. */
export const effectiveOf = (rows: Effective[], tool: string, klass: string): Effective | undefined =>
  rows.find((row) => row.tool === tool && row.class === klass);

/** The tools of a class (a tool is listed under each of its classes), kept when the words of the filter are in its name,
 *  its description or one of its classes. */
export function toolsOf(tools: ToolInfo[], klass: string, filter: string): ToolInfo[] {
  const words = filter.trim().toLowerCase();
  return tools.filter((tool) => tool.classes.includes(klass) && (!words || `${tool.name} ${tool.description} ${tool.classes.join(' ')}`.toLowerCase().includes(words)));
}

const plural = (n: number, one: string, many = one + 's'): string => `${n} ${n === 1 ? one : many}`;

/** « Used by 3 conversations, 1 project, 0 automations » */
export const usedBy = (uses: Uses): string =>
  `Used by ${plural(uses.conversations + uses.automation_conversations, 'conversation')}, ${plural(uses.projects, 'project')}, ${plural(uses.automations, 'automation')}`;

/** The warning of a deletion: the four numbers the server counts and asks to be sent back. */
export const deleteWarning = (uses: Uses): string =>
  `${plural(uses.conversations, 'conversation')}, ${plural(uses.automation_conversations, 'automation conversation')}, ` +
  `${plural(uses.automations, 'automation')} and ${plural(uses.projects, 'project')} use this mode: they all move to the default mode.`;

/** The query string that confirms a deletion with the numbers that were shown. */
export const confirmQuery = (uses: Uses): string =>
  new URLSearchParams({
    conversations: String(uses.conversations),
    automation_conversations: String(uses.automation_conversations),
    automations: String(uses.automations),
    projects: String(uses.projects),
  }).toString();

export const sameUses = (a: Uses, b: Uses): boolean =>
  a.conversations === b.conversations && a.automation_conversations === b.automation_conversations && a.automations === b.automations && a.projects === b.projects;
